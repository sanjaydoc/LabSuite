"""Just-in-time / break-glass admin: time-bound elevation that auto-expires."""

from labsuite.engine import ControlPlane
from labsuite.seed import build_lab

T = 1_000_000.0  # a fixed epoch so expiry is deterministic


def test_grant_elevates_downstream_access():
    cp = build_lab()
    assert cp.resolve_access("lpark").truenas.get("research-data") is None
    cp.grant_jit("lpark", "Domain-Admins", 60, "incident #42", now=T)
    # Domain-Admins carries FULL on research-data, so the grant confers it live.
    assert cp.resolve_access("lpark").truenas.get("research-data") == "full"


def test_grant_is_active_within_window():
    cp = build_lab()
    cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    st = cp.jit_status(now=T + 30 * 60)
    assert len(st["active"]) == 1
    assert st["active"][0]["remaining_minutes"] == 30


def test_sweep_before_expiry_is_noop():
    cp = build_lab()
    cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    assert cp.sweep_jit(now=T + 30 * 60) == []
    assert cp.resolve_access("lpark").truenas.get("research-data") == "full"


def test_sweep_after_expiry_reclaims_access():
    cp = build_lab()
    cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    expired = cp.sweep_jit(now=T + 61 * 60)
    assert len(expired) == 1
    assert cp.resolve_access("lpark").truenas.get("research-data") is None


def test_early_revoke_reclaims_access():
    cp = build_lab()
    g = cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    cp.revoke_jit(g.id)
    assert cp.resolve_access("lpark").truenas.get("research-data") is None


def test_overlapping_grants_keep_membership_until_last_expires():
    cp = build_lab()
    g1 = cp.grant_jit("lpark", "Domain-Admins", 30, now=T)
    cp.grant_jit("lpark", "Domain-Admins", 120, now=T)  # longer second grant
    cp.revoke_jit(g1.id)  # ending the first must NOT drop access
    assert cp.resolve_access("lpark").truenas.get("research-data") == "full"


def test_expired_unswept_raises_high_alert():
    cp = build_lab()
    cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    # action_center uses wall-clock now, which is far past T -> lapsed & unswept.
    alerts = cp.action_center()["alerts"]
    assert any(a["category"] == "break-glass" and a["severity"] == "high" for a in alerts)


def test_grant_unknown_user_raises():
    cp = build_lab()
    try:
        cp.grant_jit("ghost", "Domain-Admins", 60, now=T)
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_jit_survives_state_roundtrip():
    cp = build_lab()
    cp.grant_jit("lpark", "Domain-Admins", 60, now=T)
    restored = ControlPlane.from_dict(cp.to_dict())
    assert len(restored.jit.grants) == 1
    assert restored.jit.active(T + 10 * 60)[0].username == "lpark"
