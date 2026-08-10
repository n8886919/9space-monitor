"""Config flow for 9Space Hub."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_HUB_BASE_URL, DOMAIN
from .hub_api import HubApiClient, HubCannotConnect, HubInvalidResponse, normalize_base_url

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({
    vol.Required(CONF_HUB_BASE_URL): TextSelector(
        TextSelectorConfig(type=TextSelectorType.URL)
    )
})


class HubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                base_url = normalize_base_url(str(user_input[CONF_HUB_BASE_URL]))
            except ValueError:
                errors["base"] = "invalid_response"
            else:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                client = HubApiClient(base_url, async_get_clientsession(self.hass))
                try:
                    await client.async_get_sites()
                except HubCannotConnect:
                    errors["base"] = "cannot_connect"
                except HubInvalidResponse:
                    errors["base"] = "invalid_response"
                except Exception:
                    _LOGGER.exception("Unexpected Hub validation failure")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(
                        title="9Space Hub",
                        data={CONF_HUB_BASE_URL: base_url},
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
            errors=errors,
        )
