"""Endpoint / device provisioning: image-per-role, and wipe-on-offboard."""

import json

from labsuite.models import Department, DeviceStatus
from labsuite.seed import build_lab


def test_onboard_images_a_mac_for_research():
    cp = build_lab()
    r = cp.onboard("Nadia Rahman", Department.BIO, "research-scientist")
    assert r.device is not None
    assert r.device["image"] == "mac-standard"
    assert r.device["platform"] == "macOS"
    assert r.device["model"] == "MacBook Pro 14"
    assert r.device["status"] == "assigned"


def test_lab_technician_gets_windows_build():
    cp = build_lab()
    r = cp.onboard("Casey Lab", Department.LAB, "lab-technician")
    assert r.device["image"] == "win-lab"
    assert r.device["platform"] == "Windows"


def test_it_admin_gets_admin_build():
    cp = build_lab()
    r = cp.onboard("Sam Admin", Department.IT, "it-admin")
    assert r.device["image"] == "mac-admin"


def test_offboard_flags_device_wipe_and_return():
    cp = build_lab()
    r = cp.onboard("Nadia Rahman", Department.BIO, "research-scientist")
    tag = r.device["asset_tag"]
    off = cp.offboard(r.username)
    assert off.device is not None
    assert off.device["asset_tag"] == tag
    assert off.device["status"] == DeviceStatus.WIPE_RETURN.value
    # The fleet reflects it.
    device = cp.endpoints.device_for(r.username)
    assert device.status is DeviceStatus.WIPE_RETURN


def test_seeded_fleet_has_one_device_per_hire():
    cp = build_lab()
    devices = cp.endpoints.list_devices()
    assert len(devices) == 14  # one per seeded person
    assert all(d.assignee for d in devices)


def test_fleet_survives_serialisation():
    cp = build_lab()
    restored = type(cp).from_dict(json.loads(json.dumps(cp.to_dict())))
    assert len(restored.endpoints.list_devices()) == len(cp.endpoints.list_devices())
