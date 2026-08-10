"""Update coordinator for 9Space Hub."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_UPDATE_INTERVAL
from .hub_api import HubApiClient, HubApiError, HubSite

_LOGGER = logging.getLogger(__name__)


class HubCoordinator(DataUpdateCoordinator[dict[str, HubSite]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HubApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, HubSite]:
        try:
            return await self.client.async_get_sites()
        except HubApiError as err:
            raise UpdateFailed(str(err)) from err
