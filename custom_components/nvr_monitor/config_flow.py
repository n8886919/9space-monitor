"""Config flow for Nine Space NVR Monitor."""

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
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CameraProbeClient, NvrConfig
from .const import (
    CONF_CAMERA_IP,
    CONF_CAMERA_NAME,
    CONF_CAMERA_ONVIF_PORT,
    CONF_CAMERA_RTSP_PORT,
    CONF_ENABLED,
    CONF_GROUP,
    CONF_MODEL,
    CONF_NVR_CHANNEL,
    CONF_NVR_HOST,
    CONF_NVR_HTTP_PORT,
    CONF_NVR_RTSP_PORT,
    DEFAULT_CAMERA_ONVIF_PORT,
    DEFAULT_CAMERA_RTSP_PORT,
    DEFAULT_NVR_RTSP_PORT,
    DEFAULT_NVR_HTTP_PORT,
    DOMAIN,
    SUBENTRY_TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)


def _port_selector(default: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=1,
            max=65535,
            step=1,
            mode=NumberSelectorMode.BOX,
        )
    )


NVR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NVR_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(
            CONF_NVR_RTSP_PORT, default=DEFAULT_NVR_RTSP_PORT
        ): _port_selector(DEFAULT_NVR_RTSP_PORT),
        vol.Required(
            CONF_NVR_HTTP_PORT, default=DEFAULT_NVR_HTTP_PORT
        ): _port_selector(DEFAULT_NVR_HTTP_PORT),
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT, autocomplete="username"
            )
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)

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
        vol.Optional(CONF_CAMERA_NAME): str,
        vol.Optional(CONF_MODEL): str,
        vol.Optional(CONF_GROUP): str,
        vol.Required(CONF_ENABLED, default=True): bool,
        vol.Required(
            CONF_CAMERA_RTSP_PORT, default=DEFAULT_CAMERA_RTSP_PORT
        ): _port_selector(DEFAULT_CAMERA_RTSP_PORT),
        vol.Required(
            CONF_CAMERA_ONVIF_PORT, default=DEFAULT_CAMERA_ONVIF_PORT
        ): _port_selector(DEFAULT_CAMERA_ONVIF_PORT),
    }
)


def _validate_nvr(user_input: dict[str, Any]) -> None:
    CameraProbeClient(
        NvrConfig(
            host=str(user_input[CONF_NVR_HOST]).strip(),
            http_port=int(user_input[CONF_NVR_HTTP_PORT]),
            port=int(user_input[CONF_NVR_RTSP_PORT]),
            username=str(user_input[CONF_USERNAME]),
            password=str(user_input[CONF_PASSWORD]),
        )
    ).validate_nvr()


class NineSpaceNvrMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the integration config flow."""

    VERSION = 1

    def _host_is_configured(
        self, host: str, *, ignored_entry_id: str | None = None
    ) -> bool:
        """Return whether another entry already uses the NVR host."""
        return any(
            entry.entry_id != ignored_entry_id
            and str(entry.data.get(CONF_NVR_HOST, "")).strip() == host
            for entry in self._async_current_entries()
        )

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
        """Configure the NVR."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_NVR_HOST]).strip()
            if self._host_is_configured(host):
                return self.async_abort(reason="already_configured")
            else:
                try:
                    await self.hass.async_add_executor_job(
                        _validate_nvr, user_input
                    )
                except PermissionError:
                    errors["base"] = "invalid_auth"
                except (OSError, TimeoutError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected NVR validation error")
                    errors["base"] = "unknown"
                else:
                    data = dict(user_input)
                    data[CONF_NVR_HOST] = host
                    return self.async_create_entry(
                        title=f"Nine Space NVR ({host})", data=data
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                NVR_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the NVR."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_NVR_HOST]).strip()
            if self._host_is_configured(
                host, ignored_entry_id=entry.entry_id
            ):
                errors["base"] = "already_configured"
            else:
                try:
                    await self.hass.async_add_executor_job(
                        _validate_nvr, user_input
                    )
                except PermissionError:
                    errors["base"] = "invalid_auth"
                except (OSError, TimeoutError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected NVR validation error")
                    errors["base"] = "unknown"
                else:
                    data = dict(user_input)
                    data[CONF_NVR_HOST] = host
                    return self.async_update_reload_and_abort(
                        entry,
                        title=f"Nine Space NVR ({host})",
                        data=data,
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                NVR_SCHEMA, user_input or dict(entry.data)
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
                title = (
                    str(user_input.get(CONF_CAMERA_NAME, "")).strip()
                    or f"CH{channel:02d}"
                )
                return self.async_create_entry(
                    title=title,
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
                title = (
                    str(user_input.get(CONF_CAMERA_NAME, "")).strip()
                    or f"CH{channel:02d}"
                )
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=title,
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
