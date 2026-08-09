"""Restore volatile live-video aggregates from Home Assistant Recorder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
import logging

from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN
from .live_history import (
    LIVE_WINDOW_MS,
    LiveHistoryStore,
    samples_from_recorder_states,
)
from .models import CameraConfig

_LOGGER = logging.getLogger(__name__)


async def async_restore_live_history(
    hass: HomeAssistant,
    entry: ConfigEntry,
    cameras: list[CameraConfig],
    store: LiveHistoryStore,
) -> int:
    """Restore each camera's rolling window from Recorder, when available."""
    if "recorder" not in hass.config.components:
        return 0

    registry = er.async_get(hass)
    camera_by_entity: dict[str, CameraConfig] = {}
    for camera in cameras:
        unique_id = f"{entry.entry_id}_{camera.subentry_id}_nvr_live_video"
        if entity_id := registry.async_get_entity_id(
            Platform.BINARY_SENSOR, DOMAIN, unique_id
        ):
            camera_by_entity[entity_id] = camera
    if not camera_by_entity:
        return 0

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(milliseconds=LIVE_WINDOW_MS)
    query = partial(
        history.get_significant_states,
        hass,
        start_time,
        end_time,
        entity_ids=list(camera_by_entity),
        include_start_time_state=True,
        significant_changes_only=True,
        no_attributes=True,
    )
    try:
        states_by_entity = await get_instance(hass).async_add_executor_job(query)
    except Exception as err:  # Recorder/database failures must not block NVR status.
        _LOGGER.warning(
            "Unable to restore NVR live history from Recorder (%s)",
            type(err).__name__,
        )
        return 0

    now_ms = int(end_time.timestamp() * 1000)
    restored_count = 0
    for entity_id, camera in camera_by_entity.items():
        samples = samples_from_recorder_states(states_by_entity.get(entity_id, ()))
        if not samples:
            continue
        store.restore(camera.subentry_id, samples, now_ms=now_ms)
        restored_count += len(samples)
    _LOGGER.debug(
        "Restored %d NVR live samples for %d cameras from Recorder",
        restored_count,
        len(camera_by_entity),
    )
    return restored_count
