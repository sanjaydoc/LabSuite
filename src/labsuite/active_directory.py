"""The Active Directory layer: users, groups, and permission resolution.

AD is provisioned *from* Okta by the SCIM sync engine, but it is not a passive
mirror -- it is where the real permission structure lives. Two AD-native features
matter here:

* **Nested groups.** An AD security group can contain other groups. A user who is
  a member of ``Research`` inherits every group that (transitively) contains
  ``Research``. ``effective_groups`` computes that transitive closure -- exactly
  the resolution TrueNAS and Proxmox rely on when they evaluate a group-based ACL.
* **Organizational Units (OUs).** Where objects live, used here to tag groups for
  documentation and review.

Okta-sourced groups are mirrored 1:1; nested-group relationships and OUs are
AD-side structure that an admin designs and maintains.
"""

from __future__ import annotations

from labsuite.adapters.base import DirectoryProvider
from labsuite.models import Group, User


class ActiveDirectory(DirectoryProvider):
    """The reference in-memory :class:`DirectoryProvider` (an AD domain).

    Swap this for :class:`labsuite.adapters.live.LdapDirectoryProvider` to drive a
    real AD via LDAP -- the control plane never has to change.
    """

    def __init__(self, domain: str = "lab.local") -> None:
        self.domain = domain
        self.users: dict[str, User] = {}
        self.groups: dict[str, Group] = {}

    # ------------------------------------------------------------------ #
    # Users (written by the sync engine)
    # ------------------------------------------------------------------ #
    def upsert_user(self, user: User) -> bool:
        """Create or update a user. Returns True if anything changed."""
        existing = self.users.get(user.username)
        incoming = User.from_dict(user.to_dict())  # defensive copy
        if existing is None:
            self.users[user.username] = incoming
            return True
        if existing.to_dict() != incoming.to_dict():
            self.users[user.username] = incoming
            return True
        return False

    def deactivate_user(self, username: str) -> bool:
        """Disable an AD account and strip its group memberships."""
        user = self.users.get(username)
        if user is None:
            return False
        changed = user.active
        user.active = False
        for group in self.groups.values():
            if username in group.members:
                group.members.discard(username)
                changed = True
        return changed

    def is_active(self, username: str) -> bool:
        user = self.users.get(username)
        return bool(user and user.active)

    def list_users(self) -> list[User]:
        return list(self.users.values())

    # ------------------------------------------------------------------ #
    # Groups
    # ------------------------------------------------------------------ #
    def ensure_group(self, name: str, description: str = "", ou: str | None = None) -> Group:
        group = self.groups.get(name)
        if group is None:
            group = Group(name=name, description=description, ou=ou)
            self.groups[name] = group
        else:
            if description and not group.description:
                group.description = description
            if ou and not group.ou:
                group.ou = ou
        return group

    def set_group_members(self, name: str, members: set[str]) -> bool:
        """Replace a group's direct user members. Returns True if it changed."""
        group = self.ensure_group(name)
        new_members = set(members)
        if group.members == new_members:
            return False
        group.members = new_members
        return True

    def nest_group(self, parent: str, child: str) -> None:
        """Make ``child`` a member group of ``parent`` (parent contains child)."""
        self.ensure_group(parent).member_groups.add(child)
        self.ensure_group(child)

    # ------------------------------------------------------------------ #
    # Permission resolution -- the heart of the AD layer
    # ------------------------------------------------------------------ #
    def direct_groups(self, username: str) -> set[str]:
        return {g.name for g in self.groups.values() if username in g.members}

    def effective_groups(self, username: str) -> set[str]:
        """All groups a user belongs to, following nesting transitively.

        A member of ``child`` is effectively a member of every ``parent`` that
        contains ``child`` (directly or through a chain). Cycles, if mis-defined,
        are handled safely.
        """
        if not self.is_active(username):
            return set()

        # Reverse the nesting graph: child -> parents that contain it.
        parents_of: dict[str, set[str]] = {}
        for group in self.groups.values():
            for child in group.member_groups:
                parents_of.setdefault(child, set()).add(group.name)

        effective = self.direct_groups(username)
        frontier = list(effective)
        while frontier:
            current = frontier.pop()
            for parent in parents_of.get(current, ()):
                if parent not in effective:
                    effective.add(parent)
                    frontier.append(parent)
        return effective

    def members_of(self, group_name: str) -> set[str]:
        """Every *active* user who is effectively a member of ``group_name``."""
        return {u for u in self.users if group_name in self.effective_groups(u)}

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "users": [u.to_dict() for u in self.users.values()],
            "groups": [g.to_dict() for g in self.groups.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ActiveDirectory:
        ad = cls(domain=data.get("domain", "lab.local"))
        ad.users = {u["username"]: User.from_dict(u) for u in data.get("users", [])}
        ad.groups = {g["name"]: Group.from_dict(g) for g in data.get("groups", [])}
        return ad
