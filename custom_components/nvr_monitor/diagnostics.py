"""Diagnostics support for Nine Space NVR Monitor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NvrMonitorConfigEntry

TO_REDACT = {"camera_ip", "ip", "nvr_host", "password", "username"}


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
        "network": async_redact_data(
            entry.runtime_data.network.data, TO_REDACT
        ),
        "network_history_24h": async_redact_data(
            entry.runtime_data.network.diagnostic_history(), TO_REDACT
        ),
        "services": async_redact_data(
            entry.runtime_data.service.data, TO_REDACT
        ),
        "service_history_24h": async_redact_data(
            entry.runtime_data.service.diagnostic_history(), TO_REDACT
        ),
        "recordings": async_redact_data(
            entry.runtime_data.recording.data, TO_REDACT
        ),
        "dahua_events": async_redact_data(
            entry.runtime_data.events.events, TO_REDACT
        ),
    }
