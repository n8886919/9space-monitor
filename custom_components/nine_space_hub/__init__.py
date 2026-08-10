"""9Space Hub integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HUB_BASE_URL, PLATFORMS
from .coordinator import HubCoordinator
from .hub_api import HubApiClient


@dataclass(slots=True)
class HubRuntimeData:
    client: HubApiClient
    coordinator: HubCoordinator


HubConfigEntry = ConfigEntry[HubRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    client = HubApiClient(
        entry.data[CONF_HUB_BASE_URL], async_get_clientsession(hass)
    )
    coordinator = HubCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = HubRuntimeData(client, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: HubConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
