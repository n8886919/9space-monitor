"""On-demand snapshot camera platform for NVR Monitor."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import NvrMonitorConfigEntry
from .addon_api import AddonApiError
from .coordinator import AddonCoordinator
from .entity import camera_device_info
from .models import CameraConfig, cameras_from_entry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NvrMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one snapshot camera for each enabled camera subentry."""
    cameras = {
        camera.subentry_id: camera for camera in cameras_from_entry(entry)
    }
    for subentry_id, subentry in entry.subentries.items():
        if (camera := cameras.get(subentry_id)) is not None:
            async_add_entities(
                [AddonSnapshotCamera(entry, subentry, camera)],
                config_subentry_id=subentry_id,
            )


class AddonSnapshotCamera(CoordinatorEntity[AddonCoordinator], Camera):
    """Fetch a JPEG from the add-on only when Home Assistant requests it."""

    _attr_has_entity_name = True
    _attr_name = "Snapshot"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, entry.runtime_data.addon)
        self.entry = entry
        self.camera_config = camera
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_snapshot"
        )
        self._attr_device_info = camera_device_info(entry, subentry, camera)

    @property
    def available(self) -> bool:
        """Follow the latest add-on coordinator availability."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.camera_config.subentry_id in self.coordinator.data
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch an on-demand snapshot without prefetch or stream creation."""
        try:
            return await self.entry.runtime_data.addon.client.async_get_snapshot(
                self.camera_config.channel
            )
        except AddonApiError:
            return None
