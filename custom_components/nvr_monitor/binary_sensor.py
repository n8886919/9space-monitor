"""Binary sensors for NVR Monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NvrMonitorConfigEntry
from .entity import CameraMonitorEntity
from .models import CameraConfig, cameras_from_entry


@dataclass(frozen=True, kw_only=True)
class CameraBinaryDescription(BinarySensorEntityDescription):
    """Describe one camera binary sensor."""

    source: str
    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS = (
    CameraBinaryDescription(
        key="camera_rtsp_alive",
        translation_key="camera_rtsp_alive",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        source="service",
        value_fn=lambda data: bool(data.get("camera_rtsp_alive")),
    ),
    CameraBinaryDescription(
        key="nvr_live_video",
        translation_key="nvr_live_video",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        source="addon",
        value_fn=lambda data: data.get("nvr_live_video"),
    ),
    CameraBinaryDescription(
        key="camera_problem",
        translation_key="camera_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        source="problem",
        value_fn=lambda data: not (
            data.get("camera_rtsp_alive")
            and data.get("nvr_live_video")
        ),
    ),
    CameraBinaryDescription(
        key="recording_recent",
        translation_key="recording_recent",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        source="addon",
        value_fn=lambda data: data.get("recording_recent"),
    ),
    CameraBinaryDescription(
        key="recording_query_problem",
        translation_key="recording_query_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        source="addon",
        value_fn=lambda data: not bool(data.get("recording_query_ok")),
    ),
    CameraBinaryDescription(
        key="onvif_port",
        translation_key="onvif_port",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        source="service",
        value_fn=lambda data: bool(data.get("onvif_port")),
    ),
    CameraBinaryDescription(
        key="rtsp_port",
        translation_key="rtsp_port",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        source="service",
        value_fn=lambda data: bool(data.get("rtsp_port")),
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: NvrMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    cameras = {
        camera.subentry_id: camera for camera in cameras_from_entry(entry)
    }
    for subentry_id, subentry in entry.subentries.items():
        if (camera := cameras.get(subentry_id)) is None:
            continue
        async_add_entities(
            [
                CameraMonitorBinarySensor(entry, subentry, camera, description)
                for description in BINARY_SENSORS
            ],
            config_subentry_id=subentry_id,
        )


class CameraMonitorBinarySensor(CameraMonitorEntity, BinarySensorEntity):
    """A camera state or problem binary sensor."""

    entity_description: CameraBinaryDescription

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
        description: CameraBinaryDescription,
    ) -> None:
        coordinator = (
            entry.runtime_data.service
            if description.source == "service"
            else entry.runtime_data.addon
        )
        super().__init__(entry, subentry, camera, coordinator, description)

    def _merged_data(self) -> dict[str, Any]:
        service = (self.entry.runtime_data.service.data or {}).get(
            self.camera.subentry_id, {}
        )
        addon = (self.entry.runtime_data.addon.data or {}).get(
            self.camera.subentry_id, {}
        )
        return {**service, **addon}

    @property
    def available(self) -> bool:
        """Require camera service and add-on data for a combined problem."""
        if self.entity_description.source != "problem":
            return super().available
        service = self.entry.runtime_data.service.data or {}
        addon = self.entry.runtime_data.addon.data or {}
        return (
            self.entry.runtime_data.service.last_update_success
            and self.entry.runtime_data.addon.last_update_success
            and self.camera.subentry_id in service
            and self.camera.subentry_id in addon
        )

    @property
    def is_on(self) -> bool:
        """Return binary state."""
        if self.entity_description.source == "problem":
            data = self._merged_data()
        else:
            data = self.coordinator.data[self.camera.subentry_id]
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return mapping and probe details useful for debugging."""
        attributes = dict(super().extra_state_attributes)
        if self.entity_description.source in (
            "service",
            "addon",
            "problem",
        ):
            data = self._merged_data()
            attributes.update(
                {
                    "camera_rtsp_status": data.get("camera_rtsp_status"),
                    "camera_rtsp_error": data.get("camera_rtsp_error", ""),
                    "nvr_error": data.get("nvr_error", ""),
                    "checked_at": data.get("checked_at"),
                    "recording_error": data.get("recording_error", ""),
                    "last_recording": data.get("last_recording"),
                }
            )
        return attributes
