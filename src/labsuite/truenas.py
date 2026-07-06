"""The TrueNAS layer: file storage whose share ACLs are driven by AD groups.

A TrueNAS share exposes a dataset over SMB/NFS. Its ACL maps *AD groups* to an
access level (read / modify / full). A user's access to a share is the strongest
level granted to any AD group they are (effectively) a member of -- which is why
this layer only ever asks Active Directory for a user's effective groups and
never needs to know about Okta at all.
"""

from __future__ import annotations

from labsuite.adapters.base import StorageProvider
from labsuite.models import AccessLevel, Share


class TrueNAS(StorageProvider):
    """The reference in-memory :class:`StorageProvider` (a TrueNAS system).

    Swap this for :class:`labsuite.adapters.live.TrueNasApiStorageProvider` to
    drive a real TrueNAS -- the control plane never has to change.
    """

    def __init__(self, hostname: str = "nas01.lab.local") -> None:
        self.hostname = hostname
        self.shares: dict[str, Share] = {}

    def add_share(
        self,
        name: str,
        dataset: str,
        description: str = "",
        *,
        sensitive: bool = False,
    ) -> Share:
        share = Share(name=name, dataset=dataset, description=description, sensitive=sensitive)
        self.shares[name] = share
        return share

    def grant(self, share_name: str, group: str, level: AccessLevel) -> None:
        """Add/replace a group's entry in a share's ACL."""
        self.shares[share_name].acl[group] = level

    def get_share(self, name: str) -> Share:
        return self.shares[name]

    def list_shares(self) -> list[Share]:
        return list(self.shares.values())

    def level_for(self, share_name: str, groups: set[str]) -> tuple[AccessLevel, list[str]]:
        """Return (effective level, contributing groups) for a set of AD groups."""
        share = self.shares[share_name]
        best = AccessLevel.NONE
        via: list[str] = []
        for group, level in share.acl.items():
            if group in groups:
                via.append(group)
                best = max(best, level)
        # Only the groups that actually reach the winning level are the reason.
        via = sorted(g for g in via if share.acl[g] == best) if best > AccessLevel.NONE else []
        return best, via

    def shares_for(self, groups: set[str]) -> dict[str, AccessLevel]:
        """Every share the given groups can reach, with the effective level."""
        result: dict[str, AccessLevel] = {}
        for name in self.shares:
            level, _ = self.level_for(name, groups)
            if level > AccessLevel.NONE:
                result[name] = level
        return result

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "hostname": self.hostname,
            "shares": [s.to_dict() for s in self.shares.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> TrueNAS:
        nas = cls(hostname=data.get("hostname", "nas01.lab.local"))
        nas.shares = {s["name"]: Share.from_dict(s) for s in data.get("shares", [])}
        return nas
