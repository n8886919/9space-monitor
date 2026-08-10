"""Current Hub states; Home Assistant Recorder owns their history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .entity import HubCameraEntity, cameras
from .hub_api import HubCamera


@dataclass(frozen=True, kw_only=True)
class HubBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[HubCamera], bool | None]


DESCRIPTIONS = (
    HubBinaryDescription(key="snapshot_success", translation_key="snapshot_success", value_fn=lambda camera: camera.snapshot_success),
)


async def async_setup_entry(hass, entry: HubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([
        HubBinarySensor(entry, camera, description)
        for camera in cameras(entry.runtime_data.coordinator)
        for description in DESCRIPTIONS
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
