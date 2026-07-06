"""An append-only audit trail.

Every mutation (provisioning, deprovisioning, group change, sync) and every
access decision is recorded here. "Auditable processes" and "quarterly access
reviews" are explicit requirements of the role this project models, so the audit
log is a first-class citizen rather than an afterthought.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AuditEvent:
    actor: str  # who initiated it ("system", an admin, a service)
    action: str  # verb, e.g. "user.create", "access.check", "sync.reconcile"
    target: str  # object acted upon, e.g. a username or a share name
    system: str  # "okta" | "ad" | "truenas" | "proxmox" | "control-plane"
    outcome: str  # "success" | "denied" | "error" | "noop"
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "system": self.system,
            "outcome": self.outcome,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AuditEvent:
        return cls(
            actor=data["actor"],
            action=data["action"],
            target=data["target"],
            system=data["system"],
            outcome=data["outcome"],
            detail=data.get("detail", ""),
            ts=data.get("ts", 0.0),
        )


class AuditLog:
    """An ordered, append-only list of :class:`AuditEvent`."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(
        self,
        actor: str,
        action: str,
        target: str,
        system: str,
        outcome: str = "success",
        detail: str = "",
    ) -> AuditEvent:
        event = AuditEvent(actor, action, target, system, outcome, detail)
        self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def query(
        self,
        *,
        actor: str | None = None,
        system: str | None = None,
        action: str | None = None,
        target: str | None = None,
        outcome: str | None = None,
    ) -> list[AuditEvent]:
        """Return events matching every supplied filter."""
        result = self._events
        if actor is not None:
            result = [e for e in result if e.actor == actor]
        if system is not None:
            result = [e for e in result if e.system == system]
        if action is not None:
            result = [e for e in result if e.action == action]
        if target is not None:
            result = [e for e in result if e.target == target]
        if outcome is not None:
            result = [e for e in result if e.outcome == outcome]
        return result

    def tail(self, n: int = 20) -> list[AuditEvent]:
        return self._events[-n:]

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self._events]

    def load(self, rows: list[dict]) -> None:
        self._events = [AuditEvent.from_dict(r) for r in rows]
