"""Update coordinators for 9Space NVR Monitor."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .addon_api import AddonApiClient, AddonApiError
from .api import CameraProbeClient
from .const import ADDON_UPDATE_INTERVAL, SERVICE_UPDATE_INTERVAL
from .live_history import LiveHistoryStore
from .models import CameraConfig, ProbeResults

_LOGGER = logging.getLogger(__name__)


class AddonCoordinator(DataUpdateCoordinator[ProbeResults]):
    """Poll all app channels in one async request."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AddonApiClient,
        cameras: list[CameraConfig],
        live_history: LiveHistoryStore | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} app",
            update_interval=ADDON_UPDATE_INTERVAL,
        )
        self.client = client
        self.cameras = cameras
        self.live_history = live_history or LiveHistoryStore()

    async def _async_update_data(self) -> ProbeResults:
        try:
            channels = await self.client.async_get_channels()
        except AddonApiError as err:
            raise UpdateFailed(str(err)) from err
        by_channel = {channel.channel_id: channel for channel in channels}
        # Both CameraConfig.channel and API channel_id are one-based. No offset is
        # applied here so subentry nvr_channel=1 maps directly to channel_id=1.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        results: ProbeResults = {}
        for camera in self.cameras:
            if camera.channel not in by_channel:
                continue
            channel = by_channel[camera.channel]
            result = channel.as_dict()
            if channel.live_checked_at is not None:
                checked_at_ms = int(
                    datetime.fromisoformat(channel.live_checked_at).timestamp() * 1000
                )
                result.update(
                    self.live_history.observe(
                        camera.subentry_id,
                        checked_at_ms=checked_at_ms,
                        live_video=channel.live_video,
                        now_ms=now_ms,
                    )
                )
            results[camera.subentry_id] = result
        return results


class CameraServiceCoordinator(DataUpdateCoordinator[ProbeResults]):
    """Probe only each camera's own LAN services at low frequency."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CameraProbeClient,
        cameras: list[CameraConfig],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} camera services",
            update_interval=SERVICE_UPDATE_INTERVAL,
        )
        self.client = client
        self.cameras = cameras

    async def _async_update_data(self) -> ProbeResults:
        try:
            results = await self.hass.async_add_executor_job(
                self.client.probe_services, self.cameras
            )
        except Exception as err:
            raise UpdateFailed("camera_service_probe_failed") from err
        checked_at = datetime.now(timezone.utc).isoformat()
        for result in results.values():
            result["checked_at"] = checked_at
        return results
