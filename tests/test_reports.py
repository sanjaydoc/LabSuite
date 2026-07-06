"""CSV reports: access review, SaaS spend, and the audit trail."""

import csv
import io

from labsuite import reports
from labsuite.seed import build_lab


def _parse(text):
    return list(csv.reader(io.StringIO(text)))


def test_access_report_has_row_per_user():
    cp = build_lab()
    rows = _parse(cp.export_csv("access"))
    assert rows[0] == ["username", "department", "status", "mfa", "shares", "vms", "share_access", "proxmox_roles", "blocked"]
    assert len(rows) - 1 == len(cp.okta.list_users())
    anguyen = next(r for r in rows[1:] if r[0] == "anguyen")
    assert anguyen[3] == "yes"  # MFA enrolled


def test_saas_report_totals_are_consistent():
    cp = build_lab()
    rows = _parse(cp.export_csv("saas"))
    header, *body = rows
    for row in body:
        seats = int(row[2])
        monthly = float(row[3])
        assert round(float(row[1]) * seats, 2) == monthly
        assert round(monthly * 12, 2) == float(row[4])  # annual = 12x monthly


def test_audit_report_reflects_actions():
    cp = build_lab()
    before = len(_parse(cp.export_csv("audit")))
    cp.enroll_mfa("anguyen", actor="sanbu")
    after = _parse(cp.export_csv("audit"))
    assert len(after) > before
    assert any(row[2] == "mfa.enroll" for row in after[1:])


def test_unknown_report_raises():
    cp = build_lab()
    try:
        cp.export_csv("nope")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_reports_registry_is_exposed():
    assert set(reports.REPORTS) == {"access", "saas", "audit"}


def test_csv_quotes_fields_with_commas():
    cp = build_lab()
    # share_access joins entries with "; " and multiple shares -> a field with commas
    # would be quoted; verify the parser round-trips it back cleanly.
    text = cp.export_csv("access")
    rows = _parse(text)
    assert all(len(r) == len(rows[0]) for r in rows)  # every row has the same column count
