"""Current numeric and timestamp Hub sensors for HA Recorder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import HubConfigEntry
from .entity import HubCameraEntity, cameras
from .hub_api import HubCamera


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HubCamera], StateType | datetime]


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _epoch_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, timezone.utc) if value is not None else None


DESCRIPTIONS = (
    HubSensorDescription(
        key="recording_files_24h", translation_key="recording_files_24h",
        native_unit_of_measurement="files", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda camera: camera.recording_files_24h,
    ),
    HubSensorDescription(
        key="recording_coverage_24h", translation_key="recording_coverage_24h",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda camera: camera.recording_coverage_24h,
    ),
    HubSensorDescription(
        key="snapshot_latency", translation_key="snapshot_latency",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda camera: camera.snapshot_latency_ms,
    ),
    HubSensorDescription(
        key="snapshot_age", translation_key="snapshot_age",
        native_unit_of_measurement=UnitOfTime.SECONDS, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda camera: camera.last_good_age_seconds,
    ),
    HubSensorDescription(
        key="last_recording", translation_key="last_recording",
        device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda camera: _iso(camera.last_recording),
    ),
    HubSensorDescription(
        key="last_snapshot_attempt", translation_key="last_snapshot_attempt",
        device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda camera: _epoch_ms(camera.snapshot_timestamp_ms),
    ),
)


async def async_setup_entry(hass, entry: HubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([
        HubSensor(entry, camera, description)
        for camera in cameras(entry.runtime_data.coordinator)
        for description in DESCRIPTIONS
    ])


class HubSensor(HubCameraEntity, SensorEntity):
    entity_description: HubSensorDescription

    def __init__(self, entry: HubConfigEntry, camera: HubCamera, description: HubSensorDescription) -> None:
        super().__init__(entry, camera, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        camera = self.camera
        return None if camera is None else self.entity_description.value_fn(camera)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        attributes = dict(super().extra_state_attributes)
        camera = self.camera
        if camera is not None:
            attributes.update({
                "snapshot_error": camera.snapshot_error or "",
                "recording_error": camera.recording_error or "",
            })
        return attributes
