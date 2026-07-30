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
from .entity import CameraMonitorEntity, camera_device_info
from .events import CameraEventTracker
from .models import CameraConfig, cameras_from_entry


@dataclass(frozen=True, kw_only=True)
class CameraBinaryDescription(BinarySensorEntityDescription):
    """Describe one camera binary sensor."""

    source: str
    value_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSORS = (
    CameraBinaryDescription(
        key="network_reachable",
        translation_key="network_reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        source="network",
        value_fn=lambda data: bool(data.get("reachable")),
    ),
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
        source="service",
        value_fn=lambda data: bool(data.get("nvr_live_video")),
    ),
    CameraBinaryDescription(
        key="camera_problem",
        translation_key="camera_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        source="problem",
        value_fn=lambda data: not (
            data.get("reachable")
            and data.get("camera_rtsp_alive")
            and data.get("nvr_live_video")
        ),
    ),
    CameraBinaryDescription(
        key="recording_recent",
        translation_key="recording_recent",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        source="recording",
        value_fn=lambda data: bool(data.get("recording_recent")),
    ),
    CameraBinaryDescription(
        key="recording_query_problem",
        translation_key="recording_query_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        source="recording",
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

EVENT_BINARY_SENSORS = (
    BinarySensorEntityDescription(
        key="motion_active",
        translation_key="motion_active",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    BinarySensorEntityDescription(
        key="video_loss",
        translation_key="video_loss",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="video_blind",
        translation_key="video_blind",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)

EVENT_CODE_BY_KEY = {
    "motion_active": "VideoMotion",
    "video_loss": "VideoLoss",
    "video_blind": "VideoBlind",
}


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
            ]
            + [
                CameraEventBinarySensor(entry, subentry, camera, description)
                for description in EVENT_BINARY_SENSORS
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
            else entry.runtime_data.recording
            if description.source == "recording"
            else entry.runtime_data.network
        )
        super().__init__(entry, subentry, camera, coordinator, description)

    def _merged_data(self) -> dict[str, Any]:
        network = (self.entry.runtime_data.network.data or {}).get(
            self.camera.subentry_id, {}
        )
        service = (self.entry.runtime_data.service.data or {}).get(
            self.camera.subentry_id, {}
        )
        recording = (self.entry.runtime_data.recording.data or {}).get(
            self.camera.subentry_id, {}
        )
        return {**network, **service, **recording}

    @property
    def available(self) -> bool:
        """Require both fast and slow data before declaring a combined problem."""
        if self.entity_description.source != "problem":
            return super().available
        network = self.entry.runtime_data.network.data or {}
        service = self.entry.runtime_data.service.data or {}
        return (
            self.camera.subentry_id in network
            and self.camera.subentry_id in service
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
            "recording",
            "problem",
        ):
            data = self._merged_data()
            attributes.update(
                {
                    "camera_rtsp_status": data.get("camera_rtsp_status"),
                    "camera_rtsp_error": data.get("camera_rtsp_error", ""),
                    "nvr_describe_status": data.get("nvr_describe_status"),
                    "nvr_setup_status": data.get("nvr_setup_status"),
                    "nvr_play_status": data.get("nvr_play_status"),
                    "nvr_error": data.get("nvr_error", ""),
                    "rtp_packets": data.get("nvr_rtp_packets"),
                    "rtp_timestamps": data.get("nvr_rtp_timestamps"),
                    "checked_at": data.get("checked_at"),
                    "recording_error": data.get("recording_error", ""),
                    "last_recording": data.get("last_recording"),
                    "last_recording_age_hours": data.get(
                        "last_recording_age_hours"
                    ),
                }
            )
        return attributes


class CameraEventBinarySensor(BinarySensorEntity):
    """A binary sensor driven by the Dahua event stream."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: NvrMonitorConfigEntry,
        subentry: ConfigSubentry,
        camera: CameraConfig,
        description: BinarySensorEntityDescription,
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
    def is_on(self) -> bool:
        """Return the latest event activation state."""
        return self.tracker.is_active(
            self.camera.channel, EVENT_CODE_BY_KEY[self.entity_description.key]
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return camera mapping and the last matching event."""
        code = EVENT_CODE_BY_KEY[self.entity_description.key]
        last_event = self.tracker.last_event(self.camera.channel, code) or {}
        return {
            "ip": self.camera.ip,
            "nvr_channel": self.camera.channel,
            "group": self.camera.group,
            "last_action": last_event.get("action"),
            "last_event_timestamp": last_event.get("ts"),
        }
