"""Just-in-time (break-glass) admin: time-bound elevation that auto-expires.

Standing admin rights are the biggest blast-radius risk in any estate. This models
least-privilege the right way: a user is elevated into a powerful group for a fixed
window, and the grant **expires on its own** (or is revoked early). The control
plane adds/removes the real group membership, so downstream TrueNAS + Proxmox
access follows the grant's lifecycle exactly.

Time is injectable (`now` epoch seconds) so expiry is deterministic in tests and
in the browser demo; production would use wall-clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Powerful groups a break-glass grant can elevate into, with a human blurb. These
# are real seeded groups whose ACLs already confer wide access, so a JIT grant
# produces genuine (and genuinely expiring) downstream reach.
ELEVATED_GROUPS: dict[str, str] = {
    "Domain-Admins": "Full datacenter + storage admin (TrueNAS FULL, Proxmox PVEAdmin)",
    "CI-Operators": "CI/CD pipeline admin on the Proxmox CI pool",
    "GPU-Cluster-Users": "GPU training cluster access",
}


@dataclass
class JitGrant:
    """One time-bound elevation of ``username`` into ``group``."""

    id: str
    username: str
    group: str
    reason: str
    granted_by: str
    granted_at: float
    ttl_minutes: int
    revoked: bool = False

    @property
    def expires_at(self) -> float:
        return self.granted_at + self.ttl_minutes * 60

    def is_active(self, now: float) -> bool:
        return not self.revoked and now < self.expires_at

    def remaining_minutes(self, now: float) -> int:
        return max(0, int((self.expires_at - now) // 60))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "group": self.group,
            "reason": self.reason,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "ttl_minutes": self.ttl_minutes,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> JitGrant:
        return cls(
            id=data["id"],
            username=data["username"],
            group=data["group"],
            reason=data.get("reason", ""),
            granted_by=data.get("granted_by", ""),
            granted_at=data["granted_at"],
            ttl_minutes=data["ttl_minutes"],
            revoked=data.get("revoked", False),
        )


class JitLedger:
    """The break-glass grant ledger (create / query / expire)."""

    def __init__(self) -> None:
        self.grants: list[JitGrant] = []
        self._counter = 0

    def create(self, username: str, group: str, ttl_minutes: int, reason: str, granted_by: str, *, now: float) -> JitGrant:
        self._counter += 1
        grant = JitGrant(
            id=f"JIT-{self._counter:04d}",
            username=username,
            group=group,
            reason=reason,
            granted_by=granted_by,
            granted_at=now,
            ttl_minutes=ttl_minutes,
        )
        self.grants.append(grant)
        return grant

    def get(self, grant_id: str) -> JitGrant | None:
        return next((g for g in self.grants if g.id == grant_id), None)

    def active(self, now: float) -> list[JitGrant]:
        return [g for g in self.grants if g.is_active(now)]

    def expired_unswept(self, now: float) -> list[JitGrant]:
        """Grants that have lapsed but whose membership hasn't been reclaimed yet."""
        return [g for g in self.grants if not g.revoked and now >= g.expires_at]

    def to_dict(self) -> dict:
        return {"grants": [g.to_dict() for g in self.grants], "counter": self._counter}

    @classmethod
    def from_dict(cls, data: dict) -> JitLedger:
        ledger = cls()
        ledger.grants = [JitGrant.from_dict(g) for g in data.get("grants", [])]
        ledger._counter = data.get("counter", len(ledger.grants))
        return ledger


def now_epoch() -> float:
    """Wall-clock now, isolated here so callers can inject a fixed time in tests."""
    return time.time()
