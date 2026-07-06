"""Access-review campaigns: turn the review report into an attestation.

A plain review *lists* entitlements; a campaign makes a human **certify or revoke**
each one and tracks completion, which is what auditors (SOC 2 / ISO 27001) actually
require. :class:`ReviewCampaign` records a per-user decision and computes progress;
the control plane drives the actual revocation when a reviewer says "remove".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReviewCampaign:
    """One attestation cycle covering a fixed set of users.

    ``decisions`` maps username -> {"status": pending|certified|revoked,
    "reviewer": str, "note": str}. A user starts ``pending`` and moves to
    ``certified`` (keep access) or ``revoked`` (strip it).
    """

    name: str
    subjects: list[str] = field(default_factory=list)
    decisions: dict[str, dict] = field(default_factory=dict)
    open: bool = True

    def start(self, usernames: list[str]) -> None:
        """Open the campaign over ``usernames``, resetting every decision to pending."""
        self.subjects = list(usernames)
        self.decisions = {u: {"status": "pending", "reviewer": "", "note": ""} for u in usernames}
        self.open = True

    def decide(self, username: str, status: str, reviewer: str, note: str = "") -> None:
        """Record a reviewer's decision (certified/revoked) for one subject."""
        if username not in self.decisions:
            raise KeyError(f"{username!r} is not in campaign {self.name!r}")
        self.decisions[username] = {"status": status, "reviewer": reviewer, "note": note}

    def status_of(self, username: str) -> str:
        """This subject's decision status (defaults to ``pending``)."""
        return self.decisions.get(username, {}).get("status", "pending")

    def progress(self) -> dict:
        """Counts of certified/revoked/pending plus a completion percentage."""
        total = len(self.subjects)
        certified = sum(1 for d in self.decisions.values() if d["status"] == "certified")
        revoked = sum(1 for d in self.decisions.values() if d["status"] == "revoked")
        pending = total - certified - revoked
        reviewed = certified + revoked
        pct = round(100 * reviewed / total) if total else 100
        return {
            "name": self.name,
            "open": self.open,
            "total": total,
            "certified": certified,
            "revoked": revoked,
            "pending": pending,
            "completion_pct": pct,
        }

    def to_dict(self) -> dict:
        """Serialise the campaign (name, subjects, decisions, open) to a dict."""
        return {"name": self.name, "subjects": list(self.subjects), "decisions": self.decisions, "open": self.open}

    @classmethod
    def from_dict(cls, data: dict) -> ReviewCampaign:
        """Rebuild a campaign from a serialised dict."""
        c = cls(name=data.get("name", ""))
        c.subjects = list(data.get("subjects", []))
        c.decisions = {u: dict(d) for u, d in data.get("decisions", {}).items()}
        c.open = data.get("open", True)
        return c
