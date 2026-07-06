"""The endpoint / device layer: managed laptops, imaged per role.

This is the endpoint half of onboarding -- "laptops imaged and shipped on day
one, ... departing employees deprovisioned same-day." A hire is assigned a
standardized build (see ``policy.IMAGE_CATALOG``) chosen by their role; at
offboarding the device is flagged **wipe & return**.

``DeviceFleet`` is the in-memory reference adapter; a real deployment would swap
in a Jamf/Kandji/Intune-backed provider (see ``adapters.live``).
"""

from __future__ import annotations

from labsuite.adapters.base import EndpointProvider
from labsuite.models import Device, DeviceStatus
from labsuite.policy import IMAGE_CATALOG


class DeviceFleet(EndpointProvider):
    """The managed laptop fleet."""

    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}  # asset_tag -> Device
        self._by_user: dict[str, str] = {}  # username -> asset_tag
        self._counter = 0

    def assign(self, username: str, image_name: str) -> Device:
        """Image and ship a laptop to ``username`` per the given image build."""
        image = IMAGE_CATALOG[image_name]
        # Reuse an existing device if the user already has one (re-image), else mint one.
        asset_tag = self._by_user.get(username)
        if asset_tag is None:
            self._counter += 1
            asset_tag = f"LT-{self._counter:04d}"
        device = Device(
            asset_tag=asset_tag,
            model=image.model,
            platform=image.platform,
            image=image.name,
            assignee=username,
            status=DeviceStatus.ASSIGNED,
            serial=f"C02{asset_tag.replace('-', '')}",
        )
        self.devices[asset_tag] = device
        self._by_user[username] = asset_tag
        return device

    def wipe_and_return(self, username: str) -> Device | None:
        """Flag the user's device for wipe & return (offboarding)."""
        asset_tag = self._by_user.get(username)
        if asset_tag is None:
            return None
        device = self.devices[asset_tag]
        device.status = DeviceStatus.WIPE_RETURN
        return device

    def device_for(self, username: str) -> Device | None:
        asset_tag = self._by_user.get(username)
        return self.devices.get(asset_tag) if asset_tag else None

    def list_devices(self) -> list[Device]:
        return list(self.devices.values())

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "counter": self._counter,
            "devices": [d.to_dict() for d in self.devices.values()],
            "by_user": dict(self._by_user),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DeviceFleet:
        fleet = cls()
        fleet._counter = data.get("counter", 0)
        fleet.devices = {d["asset_tag"]: Device.from_dict(d) for d in data.get("devices", [])}
        fleet._by_user = dict(data.get("by_user", {}))
        return fleet
