"""The 9Space NVR Monitor integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .addon_api import AddonApiClient
from .api import CameraProbeClient
from .const import ADDON_BASE_URL, DOMAIN, PLATFORMS
from .coordinator import AddonCoordinator, CameraServiceCoordinator
from .live_history import LiveHistoryStore
from .models import cameras_from_entry
from .recorder_history import async_restore_live_history


@dataclass(slots=True)
class NvrMonitorRuntimeData:
    """Runtime data shared by entity platforms."""

    addon: AddonCoordinator
    service: CameraServiceCoordinator


NvrMonitorConfigEntry: TypeAlias = ConfigEntry[NvrMonitorRuntimeData]


async def async_migrate_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Enable entities disabled by older integration defaults once."""
    if entry.version < 2:
        entity_registry = er.async_get(hass)
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                entity_registry.async_update_entity(
                    registry_entry.entity_id, disabled_by=None
                )
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Set up 9Space NVR Monitor without blocking on app availability."""
    cameras = cameras_from_entry(entry)
    addon_client = AddonApiClient(ADDON_BASE_URL, async_get_clientsession(hass))
    live_history = LiveHistoryStore()
    await async_restore_live_history(hass, entry, cameras, live_history)
    addon = AddonCoordinator(hass, entry, addon_client, cameras, live_history)
    service = CameraServiceCoordinator(hass, entry, CameraProbeClient(), cameras)
    entry.runtime_data = NvrMonitorRuntimeData(
        addon=addon,
        service=service,
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
        hass, addon.async_refresh(), "Initial app channel refresh"
    )
    entry.async_create_background_task(
        hass, service.async_refresh(), "Initial camera service probe"
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Reload when config entry data or camera subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unloaded


async def async_remove_entry(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> None:
    """Remove retained data after a config entry is deleted."""
    # Keep cleanup for histories created by pre-M3 versions.
    for suffix in ("network_history", "service_history", "dahua_events"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{suffix}").async_remove()
