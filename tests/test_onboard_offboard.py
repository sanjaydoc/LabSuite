"""Onboarding and same-day offboarding through the control plane."""

import pytest

from labsuite.models import Department
from labsuite.seed import DEMO_PASSWORD, build_lab


def test_onboard_grants_expected_access():
    cp = build_lab()
    result = cp.onboard("Nadia Rahman", Department.RESEARCH, "research-scientist")
    # Baseline + department groups.
    assert set(result.okta_groups) == {"Everyone", "Research"}
    # Research is nested into GPU-Cluster-Users -> reaches research storage + GPU VMs.
    assert result.truenas_access.get("research-data") == "modify"
    assert 101 in result.proxmox_access and result.proxmox_access[101] == "PVEVMUser"


def test_offboard_leaves_zero_residual_access():
    cp = build_lab()
    r = cp.onboard("Nadia Rahman", Department.RESEARCH, "research-scientist")
    assert r.truenas_access  # had access...
    off = cp.offboard(r.username)
    assert off.clean
    assert off.residual_truenas == {} and off.residual_proxmox == {}
    # Resolving access now returns nothing.
    report = cp.resolve_access(r.username)
    assert report.truenas == {} and report.proxmox == {}


def test_offboarded_user_cannot_login():
    cp = build_lab()
    r = cp.onboard("Nadia Rahman", Department.RESEARCH, "research-scientist")
    cp.okta.set_password(r.username, DEMO_PASSWORD)
    assert cp.login(r.username, DEMO_PASSWORD)  # works before
    cp.offboard(r.username)
    with pytest.raises(PermissionError):
        cp.login(r.username, DEMO_PASSWORD)  # rejected after


def test_unknown_role_is_rejected():
    cp = build_lab()
    with pytest.raises(KeyError):
        cp.onboard("Someone", Department.RESEARCH, "not-a-real-role")
