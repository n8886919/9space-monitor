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
from .entity import HubCameraEntity, HubSiteEntity, cameras, sites
from .hub_api import HubCamera, HubSite


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HubCamera], StateType | datetime]


def _epoch_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, timezone.utc) if value is not None else None


DESCRIPTIONS = (
    HubSensorDescription(key="snapshot_success_rate", translation_key="snapshot_success_rate",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda camera: camera.snapshot_success_rate),
    HubSensorDescription(key="snapshot_success_count", translation_key="snapshot_success_count",
        state_class=SensorStateClass.TOTAL_INCREASING, value_fn=lambda camera: camera.snapshot_success_count),
    HubSensorDescription(key="snapshot_failure_count", translation_key="snapshot_failure_count",
        state_class=SensorStateClass.TOTAL_INCREASING, value_fn=lambda camera: camera.snapshot_failure_count),
    HubSensorDescription(key="snapshot_consecutive_failures", translation_key="snapshot_consecutive_failures",
        state_class=SensorStateClass.MEASUREMENT, value_fn=lambda camera: camera.snapshot_consecutive_failures),
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
)


SITE_LAST_SEEN_DESCRIPTION = SensorEntityDescription(
    key="site_last_seen",
    translation_key="site_last_seen",
    device_class=SensorDeviceClass.TIMESTAMP,
)


async def async_setup_entry(hass, entry: HubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([
        HubSensor(entry, camera, description)
        for camera in cameras(entry.runtime_data.coordinator)
        for description in DESCRIPTIONS
    ] + [
        HubSiteLastSeenSensor(entry, site)
        for site in sites(entry.runtime_data.coordinator)
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
            attributes["snapshot_error"] = camera.snapshot_error or ""
        return attributes


class HubSiteLastSeenSensor(HubSiteEntity, SensorEntity):
    entity_description = SITE_LAST_SEEN_DESCRIPTION

    def __init__(self, entry: HubConfigEntry, site: HubSite) -> None:
        super().__init__(entry, site, SITE_LAST_SEEN_DESCRIPTION.key)

    @property
    def native_value(self) -> datetime | None:
        site = self.site
        return None if site is None else _epoch_ms(site.site_last_seen_at)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
