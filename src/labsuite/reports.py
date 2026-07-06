"""CSV reports — the artifacts an auditor or finance team actually asks for.

Each generator takes a :class:`~labsuite.engine.ControlPlane` and returns a CSV
string (stdlib ``csv`` only). :data:`REPORTS` maps a short kind name to its
generator so the CLI/API can offer them uniformly; add a new report by writing a
function and registering it there.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a circular import at runtime (engine imports nothing here)
    from labsuite.engine import ControlPlane


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def access_review_csv(cp: ControlPlane) -> str:
    """One row per user: their effective entitlements and any blocks."""
    rows = []
    for user in sorted(cp.okta.list_users(), key=lambda u: u.username):
        report = cp.resolve_access(user.username)
        rows.append([
            user.username,
            user.department.value,
            "active" if user.active else "disabled",
            "yes" if report.mfa_enrolled else "no",
            len(report.truenas),
            len(report.proxmox),
            "; ".join(f"{s}:{lvl}" for s, lvl in sorted(report.truenas.items())),
            "; ".join(sorted(report.proxmox.values())),
            "; ".join(sorted(report.blocked)),
        ])
    return _csv(
        ["username", "department", "status", "mfa", "shares", "vms", "share_access", "proxmox_roles", "blocked"],
        rows,
    )


def saas_csv(cp: ControlPlane) -> str:
    """One row per SaaS app: seat count and monthly/annual spend."""
    rows = []
    for app in sorted(cp.ops.saas.values(), key=lambda a: a.name):
        seats = len(app.assignees)
        rows.append([
            app.name,
            f"{app.monthly_cost_per_seat:.2f}",
            seats,
            f"{app.monthly_cost_per_seat * seats:.2f}",
            f"{app.monthly_cost_per_seat * seats * 12:.2f}",
            "; ".join(sorted(app.assignees)),
        ])
    return _csv(["app", "cost_per_seat_monthly", "seats", "monthly_total", "annual_total", "assignees"], rows)


def audit_csv(cp: ControlPlane) -> str:
    """The full audit trail, oldest first."""
    rows = [
        [f"{e.ts:.0f}", e.actor, e.action, e.target, e.system, e.outcome, e.detail]
        for e in cp.audit.tail(10_000)
    ]
    return _csv(["ts", "actor", "action", "target", "system", "outcome", "detail"], rows)


REPORTS: dict[str, Callable[[ControlPlane], str]] = {
    "access": access_review_csv,
    "saas": saas_csv,
    "audit": audit_csv,
}


def generate(cp: ControlPlane, kind: str) -> str:
    """Return the CSV for ``kind`` (raises KeyError for an unknown report)."""
    if kind not in REPORTS:
        raise KeyError(f"unknown report {kind!r}; choose from {', '.join(REPORTS)}")
    return REPORTS[kind](cp)
