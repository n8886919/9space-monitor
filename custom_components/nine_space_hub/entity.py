"""Shared Hub camera entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubConfigEntry
from .const import DOMAIN
from .coordinator import HubCoordinator
from .hub_api import HubCamera


def camera_key(site_id: str, camera_id: int) -> str:
    return f"{site_id}_{camera_id}"


def cameras(coordinator: HubCoordinator) -> list[HubCamera]:
    return [camera for site in (coordinator.data or {}).values() for camera in site.cameras]


def find_camera(coordinator: HubCoordinator, site_id: str, camera_id: int) -> HubCamera | None:
    site = (coordinator.data or {}).get(site_id)
    if site is None:
        return None
    return next((camera for camera in site.cameras if camera.camera_id == camera_id), None)


def camera_device_info(camera: HubCamera) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, camera_key(camera.site_id, camera.camera_id))},
        manufacturer="9Space",
        model="Monitor Hub Camera",
        name=f"{camera.site_name} {camera.label}",
    )


class HubCameraEntity(CoordinatorEntity[HubCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: HubConfigEntry, camera: HubCamera, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self.entry = entry
        self.site_id = camera.site_id
        self.camera_id = camera.camera_id
        self._attr_unique_id = f"{camera.site_id}_{camera.camera_id}_{key}"
        self._attr_device_info = camera_device_info(camera)

    @property
    def camera(self) -> HubCamera | None:
        return find_camera(self.coordinator, self.site_id, self.camera_id)

    @property
    def available(self) -> bool:
        return super().available and self.camera is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        return {"site_id": self.site_id, "camera_id": self.camera_id}
