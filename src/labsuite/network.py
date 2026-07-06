"""Network segmentation: VLANs, device placement, and east-west firewall policy.

The one IT pillar the JD calls out that the identity stack doesn't cover:
*"Wi-Fi, VLANs, IoT segmentation, firewall rules."* This models the lab network
as a set of :class:`Segment` VLANs, the :class:`NetworkDevice` endpoints placed
on them, and a default-deny :class:`Network.policy` of allowed inter-segment
flows -- so you can answer "can device X reach subnet Y?" and flag a camera that
somehow landed on the Corp VLAN.

Like :mod:`labsuite.operations`, this is a concrete registry (not behind a
provider ABC): it is infrastructure state, not one of the four swappable layers.
"""

from __future__ import annotations

from dataclasses import dataclass

# Device kinds and the segment each one *belongs* on. A device whose kind maps to
# a segment other than the one it sits on is a segmentation anomaly (flagged).
DEVICE_KINDS = ("laptop", "workstation", "instrument", "camera", "badge-reader", "sensor", "printer", "guest")

EXPECTED_SEGMENT: dict[str, str] = {
    "laptop": "Corp",
    "workstation": "Corp",
    "instrument": "Lab",
    "printer": "Lab",
    "camera": "IoT",
    "badge-reader": "IoT",
    "sensor": "IoT",
    "guest": "Guest",
}


@dataclass
class Segment:
    """A VLAN / broadcast domain with a trust level and an internet posture."""

    name: str
    vlan_id: int
    cidr: str
    purpose: str = ""
    trust: str = "medium"  # high | medium | low | none
    internet: bool = True  # may reach the internet (egress)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "vlan_id": self.vlan_id,
            "cidr": self.cidr,
            "purpose": self.purpose,
            "trust": self.trust,
            "internet": self.internet,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Segment:
        return cls(
            name=data["name"],
            vlan_id=data["vlan_id"],
            cidr=data["cidr"],
            purpose=data.get("purpose", ""),
            trust=data.get("trust", "medium"),
            internet=data.get("internet", True),
        )


@dataclass
class NetworkDevice:
    """An endpoint on the network, placed on exactly one segment."""

    name: str
    mac: str
    kind: str  # one of DEVICE_KINDS
    segment: str  # Segment.name
    owner: str | None = None  # username, for user-attributed devices
    ip: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mac": self.mac,
            "kind": self.kind,
            "segment": self.segment,
            "owner": self.owner,
            "ip": self.ip,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NetworkDevice:
        return cls(
            name=data["name"],
            mac=data["mac"],
            kind=data["kind"],
            segment=data["segment"],
            owner=data.get("owner"),
            ip=data.get("ip", ""),
        )


class Network:
    """VLAN registry + device placement + a default-deny segmentation policy."""

    def __init__(self) -> None:
        self.segments: dict[str, Segment] = {}
        self.devices: list[NetworkDevice] = []
        # Allowed east-west flows as ordered (src, dst) segment pairs. A segment
        # can always reach itself; everything else is denied unless listed here.
        self.policy: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #
    def add_segment(
        self, name: str, vlan_id: int, cidr: str, purpose: str = "", *, trust: str = "medium", internet: bool = True
    ) -> Segment:
        seg = Segment(name, vlan_id, cidr, purpose, trust, internet)
        self.segments[name] = seg
        return seg

    def allow(self, src: str, dst: str) -> None:
        """Permit east-west traffic initiated from ``src`` toward ``dst``."""
        self.policy.add((src, dst))

    def add_device(
        self, name: str, mac: str, kind: str, segment: str, *, owner: str | None = None, ip: str = ""
    ) -> NetworkDevice:
        dev = NetworkDevice(name, mac, kind, segment, owner, ip)
        self.devices.append(dev)
        return dev

    def get_device(self, name: str) -> NetworkDevice | None:
        return next((d for d in self.devices if d.name == name), None)

    def move_device(self, name: str, segment: str) -> NetworkDevice | None:
        """Re-place a device onto a different VLAN (e.g. quarantine a rogue IoT)."""
        dev = self.get_device(name)
        if dev is None or segment not in self.segments:
            return None
        dev.segment = segment
        return dev

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def list_segments(self) -> list[Segment]:
        return sorted(self.segments.values(), key=lambda s: s.vlan_id)

    def list_devices(self) -> list[NetworkDevice]:
        return sorted(self.devices, key=lambda d: (d.segment, d.name))

    def can_reach(self, src: str, dst: str) -> tuple[bool, str]:
        """Would the firewall permit traffic from segment ``src`` to ``dst``?"""
        if src not in self.segments or dst not in self.segments:
            return False, "unknown segment"
        if src == dst:
            return True, "same segment (intra-VLAN)"
        if (src, dst) in self.policy:
            return True, f"firewall rule allows {src} → {dst}"
        return False, f"default-deny: no rule permits {src} → {dst}"

    def device_can_reach(self, device_name: str, dst: str) -> tuple[bool, str]:
        dev = self.get_device(device_name)
        if dev is None:
            return False, f"unknown device {device_name!r}"
        allowed, reason = self.can_reach(dev.segment, dst)
        return allowed, f"{dev.name} on {dev.segment}: {reason}"

    def flags(self) -> list[str]:
        """Segmentation anomalies a network admin would want surfaced."""
        out: list[str] = []
        for d in self.list_devices():
            if d.segment not in self.segments:
                out.append(f"{d.name}: on unknown segment {d.segment!r}")
                continue
            expected = EXPECTED_SEGMENT.get(d.kind)
            if expected and expected in self.segments and d.segment != expected:
                seg = self.segments[d.segment]
                # A low-trust device (camera/sensor) on a high-trust VLAN is the
                # classic IoT-segmentation failure -- call it out sharply.
                sev = "MISPLACED" if seg.trust in ("high", "medium") and expected == "IoT" else "off-segment"
                out.append(f"{d.name} ({d.kind}): {sev} on {d.segment} — expected {expected}")
        # Any segment that can reach a high-trust VLAN from a low/none-trust one.
        for src, dst in sorted(self.policy):
            s, t = self.segments.get(src), self.segments.get(dst)
            if s and t and s.trust in ("low", "none") and t.trust == "high":
                out.append(f"policy: low-trust {src} may reach high-trust {dst}")
        return out

    def summary(self) -> dict:
        return {
            "segments": [s.to_dict() for s in self.list_segments()],
            "devices": [d.to_dict() for d in self.list_devices()],
            "policy": sorted([list(p) for p in self.policy]),
            "flags": self.flags(),
            "device_count": len(self.devices),
        }

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "segments": [s.to_dict() for s in self.segments.values()],
            "devices": [d.to_dict() for d in self.devices],
            "policy": sorted([list(p) for p in self.policy]),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Network:
        net = cls()
        for s in data.get("segments", []):
            seg = Segment.from_dict(s)
            net.segments[seg.name] = seg
        net.devices = [NetworkDevice.from_dict(d) for d in data.get("devices", [])]
        net.policy = {(src, dst) for src, dst in data.get("policy", [])}
        return net
