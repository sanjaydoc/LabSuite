"""Access-review campaigns: certify/revoke attestation with progress tracking."""

from labsuite.engine import ControlPlane
from labsuite.seed import build_lab


def test_start_campaign_covers_active_users():
    cp = build_lab()
    p = cp.start_review_campaign("Q3 review")
    active = sum(1 for u in cp.okta.list_users() if u.active)
    assert p["total"] == active
    assert p["pending"] == active
    assert p["completion_pct"] == 0


def test_certify_and_revoke_move_progress():
    cp = build_lab()
    cp.start_review_campaign()
    cp.certify_user("anguyen", reviewer="sanbu")
    p = cp.revoke_user("rpatel", reviewer="sanbu", note="left team")
    assert p["certified"] == 1
    assert p["revoked"] == 1
    assert p["completion_pct"] > 0


def test_revoke_strips_entitlements():
    cp = build_lab()
    cp.start_review_campaign()
    assert cp.resolve_access("rpatel").truenas  # had access
    cp.revoke_user("rpatel", reviewer="sanbu")
    report = cp.resolve_access("rpatel")
    assert not report.truenas and not report.proxmox  # entitlements gone
    assert cp.campaign.status_of("rpatel") == "revoked"


def test_campaign_status_rows_reflect_decisions():
    cp = build_lab()
    cp.start_review_campaign()
    cp.certify_user("anguyen")
    st = cp.campaign_status()
    row = next(r for r in st["rows"] if r["username"] == "anguyen")
    assert row["status"] == "certified"


def test_pending_attestation_raises_an_alert():
    cp = build_lab()
    cp.start_review_campaign()
    cats = {a["category"] for a in cp.action_center()["alerts"]}
    assert "campaign" in cats


def test_campaign_survives_state_roundtrip():
    cp = build_lab()
    cp.start_review_campaign("Persist me")
    cp.certify_user("anguyen")
    restored = ControlPlane.from_dict(cp.to_dict())
    assert restored.campaign.name == "Persist me"
    assert restored.campaign.status_of("anguyen") == "certified"


def test_decide_unknown_user_raises():
    cp = build_lab()
    cp.start_review_campaign()
    try:
        cp.certify_user("ghost")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")
