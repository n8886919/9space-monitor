"""Diagnostics support for NVR Monitor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NvrMonitorConfigEntry

TO_REDACT = {
    "addon_base_url",
    "telemetry_center_url",
    "telemetry_mapping",
    "camera_ip",
    "ip",
    "nvr_host",
    "password",
    "username",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NvrMonitorConfigEntry
) -> dict[str, Any]:
    """Return redacted configuration and latest probe data."""
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "cameras": {
            subentry_id: {
                "title": subentry.title,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
            }
            for subentry_id, subentry in entry.subentries.items()
        },
        "addon": async_redact_data(entry.runtime_data.addon.data, TO_REDACT),
        "services": async_redact_data(
            entry.runtime_data.service.data, TO_REDACT
        ),
    }
