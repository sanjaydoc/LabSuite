"""Operations: SaaS provisioning + cost, and the equipment/inventory/vendor/safety flags."""

import json

from labsuite.models import Department
from labsuite.seed import build_lab


def test_onboard_provisions_saas_seats():
    cp = build_lab()
    r = cp.onboard("Ada Turing", Department.DATA_SCIENCE, "ml-scientist")
    # Baseline + role apps.
    assert set(r.saas) == {"Slack", "Google-Workspace", "1Password", "Benchling", "GitHub"}
    assert set(cp.ops.apps_for("aturing")) == set(r.saas)


def test_offboard_reclaims_all_seats():
    cp = build_lab()
    r = cp.onboard("Ada Turing", Department.DATA_SCIENCE, "ml-scientist")
    off = cp.offboard(r.username)
    assert set(off.saas_revoked) == set(r.saas)
    assert cp.ops.apps_for(r.username) == []


def test_monthly_spend_tracks_seats():
    cp = build_lab()
    before = cp.ops.monthly_spend()
    cp.onboard("Ada Turing", Department.DATA_SCIENCE, "ml-scientist")
    after = cp.ops.monthly_spend()
    assert after > before  # new seats cost money


def test_orphaned_seats_flagged_for_inactive_users():
    cp = build_lab()
    r = cp.onboard("Ada Turing", Department.DATA_SCIENCE, "ml-scientist")
    # Deactivate in Okta WITHOUT running offboard -> her seats are now orphaned.
    cp.okta.deactivate_user(r.username)
    orphans = cp.ops.orphaned_seats(cp._is_active)
    assert any(u == r.username for _app, u in orphans)


def test_operational_flags():
    cp = build_lab()
    s = cp.ops_summary()
    assert "Zeiss LSM 980 Confocal" in s["overdue_equipment"]  # negative maintenance
    assert any("DMEM" in x for x in s["low_stock"])
    assert any("Benchling" in x for x in s["upcoming_renewals"])
    assert any("Sharps" in x for x in s["open_safety"])


def test_operations_survive_serialisation():
    cp = build_lab()
    restored = type(cp).from_dict(json.loads(json.dumps(cp.to_dict())))
    assert restored.ops.monthly_spend() == cp.ops.monthly_spend()
    assert len(restored.ops.equipment) == len(cp.ops.equipment)
