"""Backup / DR health: snapshot + replication status per dataset and VM.

Answers the question a DR audit asks — "is everything actually being backed up,
and recently?" Each :class:`BackupRecord` tracks when a TrueNAS dataset or Proxmox
VM was last protected and on what schedule; a record whose last backup is older
than its schedule allows is **stale** and gets flagged.

Backup age is stored in hours (rather than a timestamp) so the seeded demo is
deterministic and the state serialises cleanly; ``run_backup`` resets it to 0.
"""

from __future__ import annotations

from dataclasses import dataclass

# How old (hours) a backup may be for each schedule before it counts as stale.
STALE_THRESHOLD_HOURS: dict[str, int] = {
    "hourly": 6,
    "daily": 36,
    "weekly": 192,
}


@dataclass
class BackupRecord:
    """One protected resource: a TrueNAS dataset or a Proxmox VM."""

    resource: str  # e.g. "tank/research" or "vm/101"
    kind: str  # "dataset" | "vm"
    schedule: str  # "hourly" | "daily" | "weekly"
    last_backup_hours: int  # hours since the last successful backup
    retention_days: int = 30
    target: str = "replication"  # replication | pbs | cloud

    @property
    def threshold(self) -> int:
        return STALE_THRESHOLD_HOURS.get(self.schedule, 36)

    def is_stale(self) -> bool:
        return self.last_backup_hours > self.threshold

    @property
    def status(self) -> str:
        return "stale" if self.is_stale() else "ok"

    def to_dict(self) -> dict:
        return {
            "resource": self.resource,
            "kind": self.kind,
            "schedule": self.schedule,
            "last_backup_hours": self.last_backup_hours,
            "retention_days": self.retention_days,
            "target": self.target,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BackupRecord:
        return cls(
            resource=data["resource"],
            kind=data["kind"],
            schedule=data["schedule"],
            last_backup_hours=data["last_backup_hours"],
            retention_days=data.get("retention_days", 30),
            target=data.get("target", "replication"),
        )


class BackupRegistry:
    """The backup/DR ledger across datasets and VMs."""

    def __init__(self) -> None:
        self.records: list[BackupRecord] = []

    def add(self, resource: str, kind: str, schedule: str, last_backup_hours: int,
            *, retention_days: int = 30, target: str = "replication") -> BackupRecord:
        rec = BackupRecord(resource, kind, schedule, last_backup_hours, retention_days, target)
        self.records.append(rec)
        return rec

    def get(self, resource: str) -> BackupRecord | None:
        return next((r for r in self.records if r.resource == resource), None)

    def run_backup(self, resource: str) -> BackupRecord | None:
        """Simulate running a backup now: reset the record's age to zero."""
        rec = self.get(resource)
        if rec is not None:
            rec.last_backup_hours = 0
        return rec

    def stale(self) -> list[BackupRecord]:
        return [r for r in self.records if r.is_stale()]

    def flags(self) -> list[str]:
        return [f"{r.resource} ({r.kind}): backup {r.last_backup_hours}h old — {r.schedule} schedule" for r in self.stale()]

    def health(self) -> dict:
        records = sorted(self.records, key=lambda r: (r.kind, r.resource))
        stale = self.stale()
        return {
            "records": [r.to_dict() for r in records],
            "total": len(records),
            "stale": len(stale),
            "protected_pct": round(100 * (len(records) - len(stale)) / len(records)) if records else 100,
            "flags": self.flags(),
        }

    def to_dict(self) -> dict:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, data: dict) -> BackupRegistry:
        reg = cls()
        reg.records = [BackupRecord.from_dict(r) for r in data.get("records", [])]
        return reg
