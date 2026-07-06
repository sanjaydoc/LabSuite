"""The compliance layer: training records that gate access.

A per-user register of training status (current / expired / missing). Gated
resources (see ``policy.GATED_SHARES``) require the relevant training to be
*current*; if it is missing or has lapsed, access is denied even when the user's
AD groups would otherwise grant it -- and it is restored the moment the training
is completed again. This is the "auditable, scalable process" that ties HR
compliance to IT access.
"""

from __future__ import annotations

from labsuite.policy import GATED_SHARES

CURRENT = "current"
EXPIRED = "expired"
MISSING = "missing"


class ComplianceRegistry:
    """Per-user training records + the gating decision for a resource."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {}  # username -> {training: status}

    def assign_required(self, username: str, trainings: list[str]) -> None:
        """Register required trainings for a hire, pending completion (missing)."""
        rec = self.records.setdefault(username, {})
        for training in trainings:
            rec.setdefault(training, MISSING)

    def complete(self, username: str, training: str) -> None:
        """Mark a training current -- unlocks any access it was gating."""
        self.records.setdefault(username, {})[training] = CURRENT

    def expire(self, username: str, training: str) -> None:
        """Mark a training lapsed -- gated access relying on it is revoked."""
        self.records.setdefault(username, {})[training] = EXPIRED

    def status(self, username: str, training: str) -> str:
        """This user's status for a training (``missing`` if unrecorded)."""
        return self.records.get(username, {}).get(training, MISSING)

    def has_current(self, username: str, training: str) -> bool:
        """True if the user holds a current (non-lapsed) record for the training."""
        return self.status(username, training) == CURRENT

    def trainings_for(self, username: str) -> dict[str, str]:
        """A copy of the user's {training: status} map."""
        return dict(self.records.get(username, {}))

    @staticmethod
    def is_gated(share: str) -> bool:
        """True if the share requires training on top of its group ACL."""
        return share in GATED_SHARES

    def missing_for_share(self, username: str, share: str) -> list[str]:
        """Trainings a share requires that the user does not currently hold."""
        return [t for t in GATED_SHARES.get(share, []) if not self.has_current(username, t)]

    def all_records(self) -> dict[str, dict[str, str]]:
        """A deep copy of every user's training records."""
        return {u: dict(v) for u, v in self.records.items()}

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        """Serialise all training records to a dict."""
        return {"records": self.all_records()}

    @classmethod
    def from_dict(cls, data: dict) -> ComplianceRegistry:
        """Rebuild the registry from a serialised dict."""
        reg = cls()
        reg.records = {u: dict(v) for u, v in data.get("records", {}).items()}
        return reg
