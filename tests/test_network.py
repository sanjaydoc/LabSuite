"""Network segmentation: VLANs, device placement, and east-west firewall policy."""

from labsuite.engine import ControlPlane
from labsuite.network import Network
from labsuite.seed import build_lab


def test_seeded_segments_and_devices():
    cp = build_lab()
    s = cp.network_summary()
    names = {seg["name"] for seg in s["segments"]}
    assert {"Corp", "Lab", "IoT", "Guest", "Mgmt"} <= names
    assert s["device_count"] >= 12


def test_default_deny_east_west():
    cp = build_lab()
    assert cp.check_segmentation("Corp", "Lab")["allowed"]  # explicit allow
    assert not cp.check_segmentation("Lab", "Corp")["allowed"]  # not permitted
    assert not cp.check_segmentation("IoT", "Corp")["allowed"]  # IoT is isolated
    assert not cp.check_segmentation("Guest", "Mgmt")["allowed"]


def test_same_segment_and_unknown():
    net = build_lab().network
    assert net.can_reach("Lab", "Lab")[0]  # intra-VLAN always fine
    assert not net.can_reach("Corp", "Nope")[0]  # unknown segment
    assert "unknown" in net.can_reach("Corp", "Nope")[1]


def test_iot_device_cannot_reach_infra():
    net = build_lab().network
    allowed, reason = net.device_can_reach("cam-vivarium-1", "Mgmt")
    assert not allowed
    assert "IoT" in reason


def test_misplaced_camera_is_flagged():
    cp = build_lab()
    flags = cp.network_summary()["flags"]
    assert any("cam-server-room" in f and "MISPLACED" in f for f in flags)


def test_quarantine_clears_flag():
    cp = build_lab()
    cp.move_device("cam-server-room", "IoT")  # put it back where it belongs
    flags = cp.network_summary()["flags"]
    assert not any("cam-server-room" in f for f in flags)


def test_move_unknown_device_or_segment_returns_none():
    cp = build_lab()
    assert cp.move_device("ghost-device", "IoT") is None
    assert cp.move_device("cam-vivarium-1", "NoSuchVLAN") is None


def test_check_segmentation_is_audited():
    cp = build_lab()
    cp.check_segmentation("IoT", "Corp")
    events = [e for e in cp.audit.tail(10) if e.action == "network.check"]
    assert events and events[-1].outcome == "denied"


def test_network_survives_state_roundtrip():
    cp = build_lab()
    restored = ControlPlane.from_dict(cp.to_dict())
    assert len(restored.network.segments) == len(cp.network.segments)
    assert len(restored.network.devices) == len(cp.network.devices)
    assert restored.network.can_reach("Corp", "Lab")[0]
    assert not restored.network.can_reach("Lab", "Corp")[0]


def test_empty_network_has_no_flags():
    net = Network()
    net.add_segment("Corp", 10, "10.10.0.0/16", trust="high")
    net.add_device("laptop-1", "aa:bb", "laptop", "Corp")
    assert net.flags() == []
