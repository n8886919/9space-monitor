"""The NVR Monitor integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .api import CameraProbeClient, NvrConfig
from .const import (
    CONF_NVR_HOST,
    CONF_NVR_HTTP_PORT,
    CONF_NVR_RTSP_PORT,
    DEFAULT_NVR_HTTP_PORT,
    DEFAULT_NVR_RTSP_PORT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import (
    CameraNetworkCoordinator,
    CameraRecordingCoordinator,
    CameraServiceCoordinator,
)
from .events import CameraEventTracker
from .models import cameras_from_entry


@dataclass(slots=True)
class NvrMonitorRuntimeData:
    """Runtime data shared by entity platforms."""

    network: CameraNetworkCoordinator
    service: CameraServiceCoordinator
    recording: CameraRecordingCoordinator
    events: CameraEventTracker


NvrMonitorConfigEntry: TypeAlias = ConfigEntry[NvrMonitorRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Set up NVR Monitor from a config entry."""
    nvr = NvrConfig(
        host=str(entry.data[CONF_NVR_HOST]),
        http_port=int(
            entry.data.get(CONF_NVR_HTTP_PORT, DEFAULT_NVR_HTTP_PORT)
        ),
        port=int(entry.data.get(CONF_NVR_RTSP_PORT, DEFAULT_NVR_RTSP_PORT)),
        username=str(entry.data[CONF_USERNAME]),
        password=str(entry.data[CONF_PASSWORD]),
    )
    client = CameraProbeClient(nvr)
    cameras = cameras_from_entry(entry)

    network = CameraNetworkCoordinator(hass, entry, cameras)
    service = CameraServiceCoordinator(hass, entry, client, cameras)
    recording = CameraRecordingCoordinator(hass, entry, nvr, cameras)
    events = CameraEventTracker(hass, entry, cameras)
    await network.async_load_history()
    await service.async_load_history()
    await events.async_setup()
    await network.async_config_entry_first_refresh()
    entry.runtime_data = NvrMonitorRuntimeData(
        network=network,
        service=service,
        recording=recording,
        events=events,
    )

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Dahua",
        model="NVR",
        name=entry.title,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass, service.async_refresh(), "Initial camera service probe"
    )
    entry.async_create_background_task(
        hass, recording.async_refresh(), "Initial recording query"
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Reload when the NVR or camera subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.network.async_save_history()
    await entry.runtime_data.service.async_save_history()
    await entry.runtime_data.events.async_save()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Remove retained data after a config entry is deleted."""
    for suffix in ("network_history", "service_history", "dahua_events"):
        await Store(
            hass,
            1,
            f"{DOMAIN}.{entry.entry_id}.{suffix}",
        ).async_remove()
