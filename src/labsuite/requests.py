"""Self-service access requests + approvals -- lightweight IGA.

A user requests membership in a group; a reviewer approves or denies. On
approval the group is granted (through the normal Okta -> AD path) and the
request is closed. Every step is audited, so access is granted through a
reviewable workflow rather than an out-of-band favour -- the "scalable,
auditable process" an IT owner is expected to build.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccessRequest:
    id: str
    requester: str
    group: str
    justification: str = ""
    status: str = "pending"  # pending | approved | denied
    decided_by: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requester": self.requester,
            "group": self.group,
            "justification": self.justification,
            "status": self.status,
            "decided_by": self.decided_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AccessRequest:
        return cls(
            id=data["id"],
            requester=data["requester"],
            group=data["group"],
            justification=data.get("justification", ""),
            status=data.get("status", "pending"),
            decided_by=data.get("decided_by", ""),
            note=data.get("note", ""),
        )


class RequestQueue:
    """The access-request queue."""

    def __init__(self) -> None:
        self.requests: dict[str, AccessRequest] = {}
        self._counter = 0

    def create(self, requester: str, group: str, justification: str = "") -> AccessRequest:
        self._counter += 1
        rid = f"REQ-{self._counter:04d}"
        req = AccessRequest(rid, requester, group, justification)
        self.requests[rid] = req
        return req

    def get(self, rid: str) -> AccessRequest | None:
        return self.requests.get(rid)

    def pending(self) -> list[AccessRequest]:
        return [r for r in self.requests.values() if r.status == "pending"]

    def all(self) -> list[AccessRequest]:
        return sorted(self.requests.values(), key=lambda r: r.id)

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {"counter": self._counter, "requests": [r.to_dict() for r in self.requests.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> RequestQueue:
        q = cls()
        q._counter = data.get("counter", 0)
        q.requests = {r["id"]: AccessRequest.from_dict(r) for r in data.get("requests", [])}
        return q
