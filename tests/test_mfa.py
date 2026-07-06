"""MFA + conditional access: sensitive shares require Okta Verify enrollment."""

from labsuite.engine import ControlPlane
from labsuite.models import Department
from labsuite.seed import build_lab


def test_seeded_user_is_mfa_enrolled_and_reaches_sensitive_data():
    cp = build_lab()
    report = cp.resolve_access("talvarez")  # established in-vivo scientist
    assert report.mfa_enrolled is True
    assert report.truenas.get("invivo-study-data") == "modify"
    assert "invivo-study-data" not in report.blocked


def test_fresh_hire_blocked_by_conditional_access_even_when_trained():
    cp = build_lab()
    # A pure MFA gate: legal-contracts is sensitive but NOT training-gated.
    r = cp.onboard("Ethan Cole", Department.LEGAL, "legal-counsel")
    assert "legal-contracts" in r.gated  # withheld pending MFA
    assert "legal-contracts" not in r.truenas_access

    report = cp.resolve_access("ecole")
    assert report.mfa_enrolled is False
    assert "MFA (Okta Verify)" in report.blocked["legal-contracts"]

    decision = cp.check_truenas("ecole", "legal-contracts", "full")
    assert not decision.allowed
    assert "conditional access" in decision.reason
    assert "MFA" in decision.reason


def test_enrolling_mfa_unlocks_sensitive_data():
    cp = build_lab()
    cp.onboard("Ethan Cole", Department.LEGAL, "legal-counsel")
    cp.enroll_mfa("ecole")
    report = cp.resolve_access("ecole")
    assert report.mfa_enrolled is True
    assert report.truenas.get("legal-contracts") == "full"
    assert cp.check_truenas("ecole", "legal-contracts", "full").allowed


def test_mfa_and_training_gates_compose_for_gated_sensitive_share():
    cp = build_lab()
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    # Training complete but no MFA -> still blocked, and by MFA specifically.
    cp.complete_training("nrahman", "IACUC")
    cp.complete_training("nrahman", "Biosafety")
    report = cp.resolve_access("nrahman")
    assert report.blocked["invivo-study-data"] == ["MFA (Okta Verify)"]
    # Enroll MFA -> fully unlocked.
    cp.enroll_mfa("nrahman")
    assert cp.resolve_access("nrahman").truenas.get("invivo-study-data") == "modify"


def test_non_sensitive_shares_unaffected_by_mfa():
    cp = build_lab()
    r = cp.onboard("Priya Shah", Department.BIO, "research-scientist")
    # research-data is not sensitive, so no MFA is required to reach it.
    assert r.truenas_access.get("research-data") == "modify"
    assert cp.check_truenas("pshah", "research-data", "modify").allowed


def test_enroll_mfa_is_audited():
    cp = build_lab()
    cp.onboard("Ethan Cole", Department.LEGAL, "legal-counsel")
    cp.enroll_mfa("ecole", actor="sanbu")
    events = [e for e in cp.audit.tail(20) if e.action == "mfa.enroll" and e.target == "ecole"]
    assert events and events[-1].actor == "sanbu"


def test_enroll_unknown_user_raises():
    cp = ControlPlane()
    try:
        cp.enroll_mfa("ghost")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for unknown user")


def test_mfa_survives_state_roundtrip():
    cp = build_lab()
    cp.onboard("Ethan Cole", Department.LEGAL, "legal-counsel")
    cp.enroll_mfa("ecole")
    restored = ControlPlane.from_dict(cp.to_dict())
    assert restored.okta.is_mfa_enrolled("ecole")
    assert restored.resolve_access("ecole").truenas.get("legal-contracts") == "full"
