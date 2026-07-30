"""Track useful Dahua events for configured cameras."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import CameraConfig

EVENT_DAHUA = "dahua_event_received"
EVENT_RETENTION_SECONDS = 7 * 86400
MAX_EVENTS = 10000
TRACKED_CODES = {
    "VideoLoss",
    "VideoMotion",
    "VideoBlind",
    "VideoAbnormalDetection",
    "VideoUnFocus",
    "StorageNotExist",
    "StorageFailure",
    "StorageLowSpace",
    "AlarmLocal",
}


class CameraEventTracker:
    """Keep a bounded event history and current per-channel state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cameras: list[CameraConfig],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.cameras = cameras
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Callable[[], None]] = []
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.dahua_events"
        )

    async def async_setup(self) -> None:
        """Load history and subscribe to the Dahua integration event bus."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("events"), list):
            self.events = stored["events"]
        self._prune()
        self.entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_DAHUA, self._handle_event)
        )

    async def async_save(self) -> None:
        """Save immediately during integration unload."""
        await self._store.async_save({"events": self.events})

    def _prune(self) -> None:
        cutoff = time.time() - EVENT_RETENTION_SECONDS
        channels = {camera.channel for camera in self.cameras}
        self.events = [
            event
            for event in self.events
            if float(event.get("ts", 0)) >= cutoff
            and (
                event.get("channel") is None
                or int(event["channel"]) in channels
            )
        ][-MAX_EVENTS:]

    @callback
    def _handle_event(self, event: Event) -> None:
        data = event.data
        code = str(data.get("Code", ""))
        if code not in TRACKED_CODES:
            return
        try:
            channel = int(data.get("index")) + 1
        except (TypeError, ValueError):
            channel = None
        configured_channels = {camera.channel for camera in self.cameras}
        if channel is not None and channel not in configured_channels:
            return

        self.events.append(
            {
                "ts": time.time(),
                "channel": channel,
                "code": code,
                "action": str(data.get("action", "")),
                "name": str(data.get("name", "")),
                "device_name": str(data.get("DeviceName", "")),
            }
        )
        self._prune()
        self._store.async_delay_save(lambda: {"events": self.events}, 60)
        for listener in tuple(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe an entity to event updates."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    def channel_events(
        self, channel: int, *, hours: int | None = None
    ) -> list[dict[str, Any]]:
        """Return events for one NVR channel."""
        cutoff = time.time() - hours * 3600 if hours is not None else 0
        return [
            event
            for event in self.events
            if event.get("channel") == channel
            and float(event.get("ts", 0)) >= cutoff
        ]

    def last_event(
        self, channel: int, code: str | None = None
    ) -> dict[str, Any] | None:
        """Return the newest matching event."""
        for event in reversed(self.events):
            if event.get("channel") == channel and (
                code is None or event.get("code") == code
            ):
                return event
        return None

    def is_active(self, channel: int, code: str) -> bool:
        """Return active state based on the latest Start/Stop event."""
        event = self.last_event(channel, code)
        return bool(event and event.get("action") == "Start")

    def count_starts_24h(self, channel: int, code: str) -> int:
        """Count event activations during the last 24 hours."""
        return sum(
            1
            for event in self.channel_events(channel, hours=24)
            if event.get("code") == code and event.get("action") == "Start"
        )
