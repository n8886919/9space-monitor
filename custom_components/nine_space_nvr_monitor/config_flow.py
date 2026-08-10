"""Config flow for 9Space NVR Monitor."""

from __future__ import annotations

from ipaddress import ip_address
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .addon_api import (
    AddonApiClient,
    AddonCannotConnect,
    AddonInvalidResponse,
)
from .const import (
    ADDON_BASE_URL,
    CONF_CAMERA_IP,
    CONF_NVR_CHANNEL,
    DOMAIN,
    SUBENTRY_TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)

ADDON_SCHEMA = vol.Schema({})

CAMERA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CAMERA_IP): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_NVR_CHANNEL): NumberSelector(
            NumberSelectorConfig(
                min=1, max=64, step=1, mode=NumberSelectorMode.BOX
            )
        ),
    }
)


class NvrMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the integration config flow."""

    VERSION = 2

    async def _async_validate_addon(self) -> None:
        """Validate process health and the channel response contract."""
        client = AddonApiClient(ADDON_BASE_URL, async_get_clientsession(self.hass))
        await client.async_get_health()
        await client.async_get_channels()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported subentry types."""
        return {SUBENTRY_TYPE_CAMERA: CameraSubentryFlowHandler}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the local app API."""
        errors: dict[str, str] = {}
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        try:
            await self._async_validate_addon()
        except AddonCannotConnect:
            errors["base"] = "cannot_connect"
        except AddonInvalidResponse:
            errors["base"] = "invalid_response"
        except Exception:
            _LOGGER.error("Unexpected app validation error")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title="9Space NVR Monitor",
                data={},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                ADDON_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the local app API."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_validate_addon()
            except AddonCannotConnect:
                errors["base"] = "cannot_connect"
            except AddonInvalidResponse:
                errors["base"] = "invalid_response"
            except Exception:
                _LOGGER.error("Unexpected app validation error")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title="9Space NVR Monitor",
                    data={},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                ADDON_SCHEMA, user_input
            ),
            errors=errors,
        )


class CameraSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit a monitored camera."""

    def _validate(
        self,
        user_input: dict[str, Any],
        *,
        ignored_subentry_id: str | None = None,
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        try:
            ip = str(ip_address(str(user_input[CONF_CAMERA_IP]).strip()))
        except ValueError:
            errors[CONF_CAMERA_IP] = "invalid_ip"
            return errors

        channel = int(user_input[CONF_NVR_CHANNEL])
        for subentry in self._get_entry().subentries.values():
            if subentry.subentry_id == ignored_subentry_id:
                continue
            if subentry.data.get(CONF_CAMERA_IP) == ip:
                errors[CONF_CAMERA_IP] = "duplicate_ip"
            if int(subentry.data.get(CONF_NVR_CHANNEL, -1)) == channel:
                errors[CONF_NVR_CHANNEL] = "duplicate_channel"
        user_input[CONF_CAMERA_IP] = ip
        return errors

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a camera."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                channel = int(user_input[CONF_NVR_CHANNEL])
                return self.async_create_entry(
                    title=f"{channel:02d}",
                    data=user_input,
                    unique_id=f"channel_{channel}",
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CAMERA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a camera."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(
                user_input, ignored_subentry_id=subentry.subentry_id
            )
            if not errors:
                channel = int(user_input[CONF_NVR_CHANNEL])
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=f"{channel:02d}",
                    unique_id=f"channel_{channel}",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                CAMERA_SCHEMA, user_input or dict(subentry.data)
            ),
            errors=errors,
        )
