"""The NVR Monitor integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .addon_api import AddonApiClient
from .api import CameraProbeClient
from .const import CONF_ADDON_BASE_URL, DOMAIN, PLATFORMS
from .coordinator import AddonCoordinator, CameraServiceCoordinator
from .events import CameraEventTracker
from .models import cameras_from_entry


@dataclass(slots=True)
class NvrMonitorRuntimeData:
    """Runtime data shared by entity platforms."""

    addon: AddonCoordinator
    service: CameraServiceCoordinator
    events: CameraEventTracker


NvrMonitorConfigEntry: TypeAlias = ConfigEntry[NvrMonitorRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Set up NVR Monitor without blocking on add-on availability."""
    base_url = entry.data.get(CONF_ADDON_BASE_URL)
    if not isinstance(base_url, str) or not base_url:
        # M3 deliberately has no credential migration or direct-NVR fallback.
        return False

    cameras = cameras_from_entry(entry)
    addon_client = AddonApiClient(base_url, async_get_clientsession(hass))
    addon = AddonCoordinator(hass, entry, addon_client, cameras)
    service = CameraServiceCoordinator(hass, entry, CameraProbeClient(), cameras)
    events = CameraEventTracker(hass, entry, cameras)
    await events.async_setup()
    entry.runtime_data = NvrMonitorRuntimeData(
        addon=addon,
        service=service,
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
        hass, addon.async_refresh(), "Initial add-on channel refresh"
    )
    entry.async_create_background_task(
        hass, service.async_refresh(), "Initial camera service probe"
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Reload when the add-on URL or camera subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.events.async_save()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Remove retained data after a config entry is deleted."""
    # Keep cleanup for histories created by pre-M3 versions.
    for suffix in ("network_history", "service_history", "dahua_events"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{suffix}").async_remove()
