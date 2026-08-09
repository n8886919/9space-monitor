"""Snapshot cameras supplied by 9Space Monitor Hub."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubConfigEntry
from .entity import HubCameraEntity, camera_device_info, cameras
from .hub_api import HubApiError


async def async_setup_entry(
    hass,
    entry: HubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HubSnapshotCamera(entry, camera) for camera in cameras(entry.runtime_data.coordinator)])


class HubSnapshotCamera(HubCameraEntity, Camera):
    _attr_content_type = "image/jpeg"

    def __init__(self, entry: HubConfigEntry, camera) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, entry.runtime_data.coordinator)
        self.entry = entry
        self.site_id = camera.site_id
        self.camera_id = camera.camera_id
        self._attr_name = camera.label
        self._attr_unique_id = f"{camera.site_id}_{camera.camera_id}_snapshot"
        self._attr_device_info = camera_device_info(camera)

    @property
    def available(self) -> bool:
        return super().available and self.camera is not None and self.camera.snapshot_available

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            return await self.entry.runtime_data.client.async_get_snapshot(self.site_id, self.camera_id)
        except HubApiError:
            return None
