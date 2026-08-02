"""Base entities for NVR Monitor."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import NvrMonitorConfigEntry
from .const import DOMAIN
from .coordinator import AddonCoordinator, CameraServiceCoordinator
from .models import CameraConfig


def camera_device_info(
    entry: NvrMonitorConfigEntry, subentry: ConfigSubentry, camera: CameraConfig
) -> DeviceInfo:
    """Return device information shared by all camera entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{subentry.subentry_id}")},
        via_device=(DOMAIN, entry.entry_id),
        manufacturer="TP-Link Tapo",
        model=camera.model or None,
        name=camera.name,
    )


class CameraMonitorEntity(
    CoordinatorEntity[AddonCoordinator | CameraServiceCoordinator]
):
    """Base entity for one camera subentry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
        coordinator: AddonCoordinator | CameraServiceCoordinator,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.subentry = subentry
        self.camera = camera
        self.entity_description = description
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
        )
        self._attr_device_info = camera_device_info(entry, subentry, camera)

    @property
    def available(self) -> bool:
        """Return whether this camera has coordinator data."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.camera.subentry_id in self.coordinator.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Return stable camera mapping attributes."""
        return {
            "ip": self.camera.ip,
            "nvr_channel": self.camera.channel,
            "group": self.camera.group,
            "model": self.camera.model,
        }
