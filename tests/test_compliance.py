"""Compliance-gated access: training gates sensitive shares and auto-revokes."""

from labsuite.models import Department
from labsuite.seed import build_lab


def test_seeded_trained_user_has_gated_access():
    cp = build_lab()
    report = cp.resolve_access("talvarez")  # established in-vivo scientist, trained
    assert report.truenas.get("invivo-study-data") == "modify"
    assert report.blocked == {}
    assert report.trainings["IACUC"] == "current"


def test_fresh_hire_is_gated_until_trained():
    cp = build_lab()
    r = cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    # Required trainings start pending, so the gated share is withheld.
    assert all(s == "missing" for s in r.trainings.values())
    assert "invivo-study-data" in r.gated
    assert "invivo-study-data" not in r.truenas_access
    # ...but non-gated access is granted immediately (In-Vivo nested into Research).
    assert r.truenas_access.get("research-data") == "modify"

    decision = cp.check_truenas("nrahman", "invivo-study-data", "modify")
    assert not decision.allowed
    assert "IACUC" in decision.reason and "Biosafety" in decision.reason


def test_completing_training_unlocks_access():
    cp = build_lab()
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    cp.complete_training("nrahman", "IACUC")
    cp.complete_training("nrahman", "Biosafety")
    cp.enroll_mfa("nrahman")  # sensitive share also requires conditional-access MFA
    decision = cp.check_truenas("nrahman", "invivo-study-data", "modify")
    assert decision.allowed
    assert cp.resolve_access("nrahman").truenas.get("invivo-study-data") == "modify"


def test_lapsed_training_auto_revokes():
    cp = build_lab()
    cp.expire_training("talvarez", "Biosafety")  # established user's training lapses
    report = cp.resolve_access("talvarez")
    assert "invivo-study-data" in report.blocked
    assert "invivo-study-data" not in report.truenas
    assert not cp.check_truenas("talvarez", "invivo-study-data", "modify").allowed


def test_review_flags_expired_training():
    cp = build_lab()
    cp.expire_training("talvarez", "Biosafety")
    flags = " ".join(cp.access_review()["flags"])
    assert "EXPIRED" in flags
    assert "talvarez" in flags
