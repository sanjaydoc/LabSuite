"""Onboarding readiness checklist: is a hire day-one ready across every system?"""

from labsuite.models import Department
from labsuite.seed import build_lab


def test_established_user_is_fully_ready():
    cp = build_lab()
    c = cp.onboarding_checklist("anguyen")
    assert c["ready"] is True
    assert c["completion_pct"] == 100
    assert all(i["done"] for i in c["items"] if i["required"])


def test_fresh_hire_is_not_ready_until_mfa():
    cp = build_lab()
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    c = cp.onboarding_checklist("nrahman")
    assert c["ready"] is False
    mfa = next(i for i in c["items"] if i["item"].startswith("MFA"))
    assert mfa["done"] is False
    # account/groups/device/saas are provisioned on onboard already.
    assert next(i for i in c["items"] if i["item"].startswith("Laptop"))["done"]
    cp.enroll_mfa("nrahman")
    assert cp.onboarding_checklist("nrahman")["ready"] is True


def test_checklist_flags_missing_device():
    cp = build_lab()
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    cp.endpoints.wipe_and_return("nrahman")  # device reclaimed
    laptop = next(i for i in cp.onboarding_checklist("nrahman")["items"] if i["item"].startswith("Laptop"))
    assert laptop["done"] is False


def test_readiness_summary_counts_ready_users():
    cp = build_lab()
    s = cp.readiness_summary()
    assert s["total"] == sum(1 for u in cp.okta.list_users() if u.active)
    assert s["ready_count"] == s["total"]  # every seeded user is fully set up
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    s2 = cp.readiness_summary()
    assert s2["ready_count"] == s2["total"] - 1  # the fresh hire isn't ready


def test_checklist_unknown_user_raises():
    cp = build_lab()
    try:
        cp.onboarding_checklist("ghost")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")
