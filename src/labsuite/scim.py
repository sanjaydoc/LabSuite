"""The SCIM sync engine: the Okta -> Active Directory arrow.

Real Okta provisions downstream systems with SCIM 2.0 (System for Cross-domain
Identity Management): it pushes user create/update/deactivate and group-membership
changes to a target. This module reproduces that reconciliation:

* every active Okta user is upserted into AD;
* an Okta user who is deactivated (or removed) is deactivated in AD -- the same
  event that must cascade to a *same-day deprovision*;
* Okta group memberships are mirrored onto the matching AD security groups.

The reconcile is **idempotent**: run it twice with no intervening change and the
second run reports zero changes. That property is what makes a sync safe to run
on a schedule, which is exactly how you would operate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from labsuite.adapters.base import DirectoryProvider, IdentityProvider
from labsuite.audit import AuditLog


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    groups_synced: list[str] = field(default_factory=list)
    memberships_changed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.created
            or self.updated
            or self.deactivated
            or self.memberships_changed
        )

    def summary(self) -> str:
        return (
            f"created={len(self.created)} updated={len(self.updated)} "
            f"deactivated={len(self.deactivated)} "
            f"groups={len(self.groups_synced)} "
            f"membership_changes={len(self.memberships_changed)}"
        )

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "deactivated": self.deactivated,
            "groups_synced": self.groups_synced,
            "memberships_changed": self.memberships_changed,
            "changed": self.changed,
        }


class ScimSync:
    """Reconciles an :class:`IdentityProvider` into a :class:`DirectoryProvider`.

    Because it speaks only to the two *interfaces*, the exact same sync logic
    works whether the source is the in-memory Okta or a real Okta tenant, and
    whether the target is the in-memory AD or a real AD reached over LDAP.
    """

    def __init__(
        self, okta: IdentityProvider, ad: DirectoryProvider, audit: AuditLog | None = None
    ) -> None:
        self.okta = okta
        self.ad = ad
        self.audit = audit or AuditLog()

    def reconcile(self, *, actor: str = "system") -> SyncReport:
        report = SyncReport()
        source_usernames = {u.username for u in self.okta.list_users()}
        ad_usernames_before = {u.username for u in self.ad.list_users()}

        # 1. Users: push active Okta users into AD; deactivate the rest.
        for user in self.okta.list_users():
            username = user.username
            if user.active:
                is_new = username not in ad_usernames_before
                changed = self.ad.upsert_user(user)
                if is_new:
                    report.created.append(username)
                    self.audit.record(actor, "user.provision", username, "ad", detail="SCIM create")
                elif changed:
                    report.updated.append(username)
                    self.audit.record(actor, "user.update", username, "ad", detail="SCIM update")
            else:
                if self.ad.deactivate_user(username):
                    report.deactivated.append(username)
                    self.audit.record(actor, "user.deactivate", username, "ad", detail="SCIM deactivate")

        # An Okta user object deleted outright (not just deactivated) must also
        # be deprovisioned in AD.
        for user in self.ad.list_users():
            if user.username not in source_usernames and self.ad.is_active(user.username):
                if self.ad.deactivate_user(user.username):
                    report.deactivated.append(user.username)
                    self.audit.record(actor, "user.deactivate", user.username, "ad", detail="SCIM remove")

        # 2. Groups: mirror Okta group membership onto AD security groups.
        for group in self.okta.list_groups():
            name = group.name
            self.ad.ensure_group(name, group.description, ou="OU=Groups")
            report.groups_synced.append(name)
            active_members = {
                m for m in self.okta.members_of_group(name)
                if (u := self.okta.get_user(m)) is not None and u.active
            }
            if self.ad.set_group_members(name, active_members):
                report.memberships_changed.append(name)
                self.audit.record(actor, "group.sync", name, "ad", detail=f"{len(active_members)} members")

        self.audit.record(
            actor,
            "sync.reconcile",
            self.ad.domain,
            "control-plane",
            outcome="success" if report.changed else "noop",
            detail=report.summary(),
        )
        return report
