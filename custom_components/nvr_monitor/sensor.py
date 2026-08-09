"""Sensors for NVR Monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import NvrMonitorConfigEntry
from .entity import CameraMonitorEntity, camera_device_info
from .events import CameraEventTracker
from .models import CameraConfig, cameras_from_entry


@dataclass(frozen=True, kw_only=True)
class CameraSensorDescription(SensorEntityDescription):
    """Describe one camera sensor."""

    source: str
    value_fn: Callable[[dict[str, Any]], StateType]


SERVICE_SENSORS = (
    CameraSensorDescription(
        key="diagnostic_status",
        translation_key="diagnostic_status",
        source="merged",
        value_fn=lambda data: (
            "camera_rtsp_problem"
            if not data.get("camera_rtsp_alive")
            else "nvr_no_video"
            if not data.get("nvr_live_video")
            else "ok"
        ),
    ),
    CameraSensorDescription(
        key="camera_rtsp_response",
        translation_key="camera_rtsp_response",
        source="service",
        native_unit_of_measurement="ms",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("camera_rtsp_ms"),
    ),
)

ADDON_SENSORS = (
    CameraSensorDescription(
        key="daily_online_rate",
        translation_key="daily_online_rate",
        source="addon",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("daily_online_rate"),
    ),
    CameraSensorDescription(
        key="nvr_live_video_disconnect_count_24h",
        translation_key="nvr_live_video_disconnect_count_24h",
        source="addon",
        native_unit_of_measurement="times",
        value_fn=lambda data: data.get("nvr_live_video_disconnect_count_24h"),
    ),
    CameraSensorDescription(
        key="recording_count_24h",
        translation_key="recording_count_24h",
        source="addon",
        native_unit_of_measurement="files",
        value_fn=lambda data: data.get("recording_files_24h"),
    ),
    CameraSensorDescription(
        key="recording_coverage_24h",
        translation_key="recording_coverage_24h",
        source="addon",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("recording_coverage_24h"),
    ),
    CameraSensorDescription(
        key="last_recording",
        translation_key="last_recording",
        source="addon",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            datetime.fromisoformat(data["last_recording"])
            if data.get("last_recording")
            else None
        ),
    ),
)

SENSORS = SERVICE_SENSORS + ADDON_SENSORS

EVENT_SENSORS = (
    SensorEntityDescription(
        key="motion_count_24h",
        translation_key="motion_count_24h",
        native_unit_of_measurement="events",
    ),
    SensorEntityDescription(
        key="last_motion",
        translation_key="last_motion",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="last_dahua_event",
        translation_key="last_dahua_event",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NvrMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    cameras = {
        camera.subentry_id: camera for camera in cameras_from_entry(entry)
    }
    for subentry_id, subentry in entry.subentries.items():
        if (camera := cameras.get(subentry_id)) is None:
            continue
        async_add_entities(
            [
                CameraMonitorSensor(entry, subentry, camera, description)
                for description in SENSORS
            ]
            + [
                CameraEventSensor(entry, subentry, camera, description)
                for description in EVENT_SENSORS
            ],
            config_subentry_id=subentry_id,
        )


class CameraMonitorSensor(CameraMonitorEntity, SensorEntity):
    """A camera metric sensor."""

    entity_description: CameraSensorDescription

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
        description: CameraSensorDescription,
    ) -> None:
        coordinator = (
            entry.runtime_data.addon
            if description.source == "addon"
            else entry.runtime_data.service
        )
        super().__init__(entry, subentry, camera, coordinator, description)

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self.entity_description.source == "merged":
            service = (self.entry.runtime_data.service.data or {}).get(
                self.camera.subentry_id, {}
            )
            addon = (self.entry.runtime_data.addon.data or {}).get(
                self.camera.subentry_id, {}
            )
            data = {**service, **addon}
        else:
            data = self.coordinator.data[self.camera.subentry_id]
        return self.entity_description.value_fn(data)

    @property
    def available(self) -> bool:
        """Require both coordinators for the merged diagnostic state."""
        if self.entity_description.source != "merged":
            return super().available
        service = self.entry.runtime_data.service
        addon = self.entry.runtime_data.addon
        return (
            service.last_update_success
            and addon.last_update_success
            and service.data is not None
            and addon.data is not None
            and self.camera.subentry_id in service.data
            and self.camera.subentry_id in addon.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return mapping and history confidence."""
        attributes = dict(super().extra_state_attributes)
        if self.entity_description.source == "addon":
            data = self.coordinator.data[self.camera.subentry_id]
            attributes.update(
                {
                    "query_ok": data.get("recording_query_ok"),
                    "error": data.get("recording_error", ""),
                    "checked_at": data.get("checked_at"),
                }
            )
        return attributes


class CameraEventSensor(SensorEntity):
    """A sensor derived from retained Dahua events."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
        description: SensorEntityDescription,
    ) -> None:
        self.entry = entry
        self.camera = camera
        self.tracker: CameraEventTracker = entry.runtime_data.events
        self.entity_description = description
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
        )
        self._attr_device_info = camera_device_info(entry, subentry, camera)

    async def async_added_to_hass(self) -> None:
        """Subscribe to new Dahua events."""
        self.async_on_remove(
            self.tracker.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> StateType:
        """Return the derived event value."""
        key = self.entity_description.key
        if key == "motion_count_24h":
            return self.tracker.count_starts_24h(
                self.camera.channel, "VideoMotion"
            )
        if key == "last_motion":
            event = self.tracker.last_event(
                self.camera.channel, "VideoMotion"
            )
            return (
                datetime.fromtimestamp(float(event["ts"]), timezone.utc)
                if event
                else None
            )
        event = self.tracker.last_event(self.camera.channel)
        return str(event["code"]) if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return camera mapping and last event details."""
        event = self.tracker.last_event(self.camera.channel) or {}
        return {
            "ip": self.camera.ip,
            "nvr_channel": self.camera.channel,
            "group": self.camera.group,
            "last_event_action": event.get("action"),
            "last_event_timestamp": event.get("ts"),
        }
