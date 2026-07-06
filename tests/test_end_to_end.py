"""Full-stack: login through Okta and resolve/enforce access across the chain."""

from labsuite.crypto import verify_token
from labsuite.seed import DEMO_PASSWORD, build_lab


def test_seeded_user_login_and_access():
    cp = build_lab()
    token = cp.login("anguyen", DEMO_PASSWORD)
    claims = verify_token(token, cp.okta.signing_secret)
    assert claims["sub"] == "anguyen"
    assert "Research" in claims["groups"]

    report = cp.resolve_access("anguyen")
    assert report.active
    assert "GPU-Cluster-Users" in report.ad_effective_groups  # via nesting
    assert report.truenas.get("research-data") == "modify"


def test_allow_and_deny_decisions():
    cp = build_lab()
    # A researcher can power a research VM but not migrate it.
    assert cp.check_proxmox("anguyen", 101, "vm.power").allowed
    assert not cp.check_proxmox("anguyen", 101, "vm.migrate").allowed
    # ...and cannot read the sensitive legal share.
    assert not cp.check_truenas("anguyen", "legal-contracts", "read").allowed
    # Legal counsel can.
    assert cp.check_truenas("epark", "legal-contracts", "read").allowed


def test_it_admin_has_datacenter_wide_proxmox():
    cp = build_lab()
    # Domain-Admins granted PVEAdmin at "/" -> applies to every VM.
    assert cp.check_proxmox("sanbu", 900, "node.admin").allowed


def test_access_review_flags_sensitive_and_admin():
    cp = build_lab()
    review = cp.access_review()
    flags = " ".join(review["flags"])
    assert "legal-contracts" in flags  # FULL on a sensitive share
    assert "PVEAdmin" in flags  # datacenter admin


def test_department_isolation():
    cp = build_lab()
    # Operations gets only baseline storage, no research/lab/compute.
    report = cp.resolve_access("dlevy")
    assert report.proxmox == {}
    assert "research-data" not in report.truenas
