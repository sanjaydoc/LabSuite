"""The Okta layer: identity / login and the authoritative source of truth.

In this stack Okta is *where identities live*. Users authenticate here, Okta
holds the authoritative group assignments, and everything downstream (Active
Directory, and through it TrueNAS + Proxmox) is provisioned *from* Okta via SCIM.

The public surface mirrors the concepts a real Okta admin works with -- users,
groups, group membership, deactivation, and token issuance after login -- so the
sync engine and control plane read the way the real operations would.
"""

from __future__ import annotations

from labsuite.adapters.base import IdentityProvider
from labsuite.crypto import hash_password, sign_token, verify_password
from labsuite.models import Department, Group, User


class OktaDirectory(IdentityProvider):
    """The reference in-memory :class:`IdentityProvider` (an Okta org).

    Swap this for :class:`labsuite.adapters.live.OktaApiIdentityProvider` to drive
    a real Okta tenant -- the control plane never has to change.
    """

    def __init__(self, *, signing_secret: str = "labsuite-dev-okta-secret") -> None:
        self.users: dict[str, User] = {}
        self.groups: dict[str, Group] = {}
        self._passwords: dict[str, str] = {}
        self._mfa: set[str] = set()  # usernames with MFA (Okta Verify) enrolled
        self.signing_secret = signing_secret

    # ------------------------------------------------------------------ #
    # MFA (conditional-access input)
    # ------------------------------------------------------------------ #
    def enroll_mfa(self, username: str) -> None:
        self._require(username)
        self._mfa.add(username)

    def is_mfa_enrolled(self, username: str) -> bool:
        return username in self._mfa

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #
    def create_user(
        self,
        username: str,
        display_name: str,
        email: str,
        department: Department,
        password: str,
        *,
        title: str = "",
        manager: str | None = None,
    ) -> User:
        if username in self.users:
            raise ValueError(f"user {username!r} already exists in Okta")
        user = User(
            username=username,
            display_name=display_name,
            email=email,
            department=department,
            title=title,
            manager=manager,
        )
        self.users[username] = user
        self._passwords[username] = hash_password(password)
        return user

    def deactivate_user(self, username: str) -> None:
        """Suspend the account (Okta 'deactivate') -- login stops immediately."""
        user = self._require(username)
        user.active = False

    def get_user(self, username: str) -> User | None:
        return self.users.get(username)

    def list_users(self) -> list[User]:
        return list(self.users.values())

    def _require(self, username: str) -> User:
        user = self.users.get(username)
        if user is None:
            raise KeyError(f"no such Okta user: {username!r}")
        return user

    # ------------------------------------------------------------------ #
    # Groups & membership (authoritative)
    # ------------------------------------------------------------------ #
    def ensure_group(self, name: str, description: str = "") -> Group:
        group = self.groups.get(name)
        if group is None:
            group = Group(name=name, description=description)
            self.groups[name] = group
        elif description and not group.description:
            group.description = description
        return group

    def add_to_group(self, username: str, group_name: str) -> None:
        self._require(username)
        self.ensure_group(group_name).members.add(username)

    def remove_from_group(self, username: str, group_name: str) -> None:
        group = self.groups.get(group_name)
        if group is not None:
            group.members.discard(username)

    def remove_from_all_groups(self, username: str) -> list[str]:
        """Strip every group membership; return the groups the user was in."""
        removed = [g.name for g in self.groups.values() if username in g.members]
        for group in self.groups.values():
            group.members.discard(username)
        return removed

    def list_groups(self) -> list[Group]:
        return list(self.groups.values())

    def members_of_group(self, group_name: str) -> set[str]:
        group = self.groups.get(group_name)
        return set(group.members) if group else set()

    def groups_for(self, username: str) -> set[str]:
        return {g.name for g in self.groups.values() if username in g.members}

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #
    def authenticate(self, username: str, password: str, *, now: float | None = None) -> str:
        """Verify credentials and return a signed session token.

        Raises ``PermissionError`` for an unknown user, a deactivated account, or
        a bad password -- the same failure surface a real IdP presents.
        """
        user = self.users.get(username)
        if user is None or not user.active:
            raise PermissionError("authentication failed")
        if not verify_password(password, self._passwords.get(username, "")):
            raise PermissionError("authentication failed")
        return self.issue_token(username, now=now)

    def issue_token(self, username: str, *, now: float | None = None) -> str:
        user = self._require(username)
        claims = {
            "sub": user.username,
            "email": user.email,
            "name": user.display_name,
            "groups": sorted(self.groups_for(username)),
        }
        return sign_token(claims, self.signing_secret, now=now)

    def set_password(self, username: str, password: str) -> None:
        self._require(username)
        self._passwords[username] = hash_password(password)

    # ------------------------------------------------------------------ #
    # SCIM source representation
    # ------------------------------------------------------------------ #
    def scim_users(self) -> list[dict]:
        """Render every user as a SCIM 2.0 ``User`` resource (sync source)."""
        return [self.to_scim_user(u.username) for u in self.users.values()]

    def to_scim_user(self, username: str) -> dict:
        user = self._require(username)
        given, _, family = user.display_name.partition(" ")
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": user.username,
            "active": user.active,
            "name": {"givenName": given, "familyName": family},
            "displayName": user.display_name,
            "title": user.title,
            "emails": [{"primary": True, "value": user.email}],
            "department": user.department.value,
            "groups": [{"value": g} for g in sorted(self.groups_for(username))],
        }

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "signing_secret": self.signing_secret,
            "users": [u.to_dict() for u in self.users.values()],
            "passwords": dict(self._passwords),
            "groups": [g.to_dict() for g in self.groups.values()],
            "mfa": sorted(self._mfa),
        }

    @classmethod
    def from_dict(cls, data: dict) -> OktaDirectory:
        okta = cls(signing_secret=data.get("signing_secret", "labsuite-dev-okta-secret"))
        okta.users = {u["username"]: User.from_dict(u) for u in data.get("users", [])}
        okta._passwords = dict(data.get("passwords", {}))
        okta.groups = {g["name"]: Group.from_dict(g) for g in data.get("groups", [])}
        okta._mfa = set(data.get("mfa", []))
        return okta
