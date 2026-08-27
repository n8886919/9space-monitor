"""Current Hub states; Home Assistant Recorder owns their history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .entity import HubCameraEntity, HubSiteEntity, cameras, sites
from .hub_api import HubCamera, HubSite


@dataclass(frozen=True, kw_only=True)
class HubBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[HubCamera], bool | None]


DESCRIPTIONS = (
    HubBinaryDescription(key="snapshot_success", translation_key="snapshot_success", value_fn=lambda camera: camera.snapshot_success),
)


SITE_DESCRIPTION = BinarySensorEntityDescription(
    key="site_reachable",
    translation_key="site_reachable",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)


async def async_setup_entry(hass, entry: HubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([
        HubBinarySensor(entry, camera, description)
        for camera in cameras(entry.runtime_data.coordinator)
        for description in DESCRIPTIONS
    ] + [
        HubSiteReachableBinarySensor(entry, site)
        for site in sites(entry.runtime_data.coordinator)
    ])


class HubBinarySensor(HubCameraEntity, BinarySensorEntity):
    entity_description: HubBinaryDescription

    def __init__(self, entry: HubConfigEntry, camera: HubCamera, description: HubBinaryDescription) -> None:
        super().__init__(entry, camera, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        camera = self.camera
        return None if camera is None else self.entity_description.value_fn(camera)

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None


class HubSiteReachableBinarySensor(HubSiteEntity, BinarySensorEntity):
    entity_description = SITE_DESCRIPTION

    def __init__(self, entry: HubConfigEntry, site: HubSite) -> None:
        super().__init__(entry, site, SITE_DESCRIPTION.key)

    @property
    def is_on(self) -> bool | None:
        site = self.site
        return None if site is None else site.site_reachable

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None
