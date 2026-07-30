"""Sensors for NVR Monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTime,
)
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


NETWORK_SENSORS = (
    CameraSensorDescription(
        key="online_rate_24h",
        translation_key="online_rate_24h",
        source="network",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("online_rate_24h"),
    ),
    CameraSensorDescription(
        key="offline_count_24h",
        translation_key="offline_count_24h",
        source="network",
        native_unit_of_measurement="times",
        value_fn=lambda data: data.get("offline_count_24h"),
    ),
    CameraSensorDescription(
        key="rtt_average_24h",
        translation_key="rtt_average_24h",
        source="network",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("rtt_avg_24h_ms"),
    ),
    CameraSensorDescription(
        key="jitter_average_24h",
        translation_key="jitter_average_24h",
        source="network",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("jitter_avg_24h_ms"),
    ),
    CameraSensorDescription(
        key="packet_loss_average_24h",
        translation_key="packet_loss_average_24h",
        source="network",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("packet_loss_avg_24h_pct"),
    ),
    CameraSensorDescription(
        key="history_observed",
        translation_key="history_observed",
        source="network",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("observed_hours"),
    ),
)

SERVICE_SENSORS = (
    CameraSensorDescription(
        key="diagnostic_status",
        translation_key="diagnostic_status",
        source="merged",
        value_fn=lambda data: (
            "offline"
            if not data.get("reachable")
            else "camera_rtsp_problem"
            if not data.get("camera_rtsp_alive")
            else "nvr_no_video"
            if not data.get("nvr_live_video")
            else "ok"
        ),
    ),
    CameraSensorDescription(
        key="nvr_first_packet",
        translation_key="nvr_first_packet",
        source="service",
        native_unit_of_measurement="ms",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("nvr_first_packet_ms"),
    ),
    CameraSensorDescription(
        key="nvr_probe_duration",
        translation_key="nvr_probe_duration",
        source="service",
        native_unit_of_measurement="ms",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("nvr_probe_ms"),
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

RECORDING_SENSORS = (
    CameraSensorDescription(
        key="recording_count_24h",
        translation_key="recording_count_24h",
        source="recording",
        native_unit_of_measurement="files",
        value_fn=lambda data: data.get("recording_count_24h"),
    ),
    CameraSensorDescription(
        key="recording_coverage_24h",
        translation_key="recording_coverage_24h",
        source="recording",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("recording_coverage_24h_pct"),
    ),
    CameraSensorDescription(
        key="recording_gap_count_24h",
        translation_key="recording_gap_count_24h",
        source="recording",
        native_unit_of_measurement="gaps",
        value_fn=lambda data: data.get("recording_gap_count_24h"),
    ),
    CameraSensorDescription(
        key="last_recording",
        translation_key="last_recording",
        source="recording",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            datetime.fromisoformat(data["last_recording"])
            if data.get("last_recording")
            else None
        ),
    ),
    CameraSensorDescription(
        key="last_recording_age",
        translation_key="last_recording_age",
        source="recording",
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda data: data.get("last_recording_age_hours"),
    ),
)

SENSORS = NETWORK_SENSORS + SERVICE_SENSORS + RECORDING_SENSORS

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
            entry.runtime_data.network
            if description.source == "network"
            else entry.runtime_data.recording
            if description.source == "recording"
            else entry.runtime_data.service
        )
        super().__init__(entry, subentry, camera, coordinator, description)

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self.entity_description.source == "merged":
            network = (self.entry.runtime_data.network.data or {}).get(
                self.camera.subentry_id, {}
            )
            service = (self.entry.runtime_data.service.data or {}).get(
                self.camera.subentry_id, {}
            )
            data = {**network, **service}
        else:
            data = self.coordinator.data[self.camera.subentry_id]
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return mapping and history confidence."""
        attributes = dict(super().extra_state_attributes)
        if self.entity_description.source == "network":
            data = self.coordinator.data[self.camera.subentry_id]
            attributes.update(
                {
                    "history_samples": data.get("history_samples"),
                    "observed_hours": data.get("observed_hours"),
                    "current_rtt_ms": data.get("rtt_avg_ms"),
                    "current_jitter_ms": data.get("jitter_ms"),
                    "current_packet_loss_pct": data.get("packet_loss_pct"),
                }
            )
        elif self.entity_description.source == "recording":
            data = self.coordinator.data[self.camera.subentry_id]
            attributes.update(
                {
                    "query_ok": data.get("recording_query_ok"),
                    "error": data.get("recording_error", ""),
                    "truncated": data.get("recording_truncated"),
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
