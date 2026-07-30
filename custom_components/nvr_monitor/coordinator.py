"""Update coordinators for NVR Monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any

from icmplib import async_ping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import CameraProbeClient, NvrConfig
from .const import (
    DOMAIN,
    HISTORY_HOURS,
    HISTORY_SAVE_INTERVAL_UPDATES,
    NETWORK_UPDATE_INTERVAL,
    RECORDING_UPDATE_INTERVAL,
    SERVICE_HISTORY_SAVE_INTERVAL_UPDATES,
    SERVICE_UPDATE_INTERVAL,
)
from .models import CameraConfig, ProbeResults
from .recording import DahuaRecordingClient

_LOGGER = logging.getLogger(__name__)
_HISTORY_SECONDS = HISTORY_HOURS * 3600


class CameraNetworkCoordinator(DataUpdateCoordinator[ProbeResults]):
    """Collect ICMP state and maintain compact rolling history."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cameras: list[CameraConfig],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} network",
            update_interval=NETWORK_UPDATE_INTERVAL,
        )
        self.cameras = cameras
        self._history: dict[str, list[list[float | int | None]]] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.network_history"
        )
        self._updates_since_save = 0

    async def async_load_history(self) -> None:
        """Load retained rolling history."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("history"), dict):
            self._history = stored["history"]
        self._prune(time.time())

    async def async_save_history(self) -> None:
        """Persist compact history without writing every 30 seconds."""
        await self._store.async_save({"history": self._history})
        self._updates_since_save = 0

    def diagnostic_history(self) -> dict[str, list[list[float | int | None]]]:
        """Return retained network samples for a diagnostics download."""
        return self._history

    def _prune(self, now: float) -> None:
        cutoff = now - _HISTORY_SECONDS
        active_ids = {camera.subentry_id for camera in self.cameras}
        self._history = {
            subentry_id: [
                sample for sample in samples if float(sample[0]) >= cutoff
            ]
            for subentry_id, samples in self._history.items()
            if subentry_id in active_ids
        }

    async def _ping_one(
        self, camera: CameraConfig, semaphore: asyncio.Semaphore
    ) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                host = await async_ping(
                    camera.ip,
                    count=5,
                    interval=0.2,
                    timeout=1,
                    privileged=None,
                )
            except Exception as err:
                return camera.subentry_id, {
                    "reachable": False,
                    "rtt_avg_ms": None,
                    "rtt_min_ms": None,
                    "rtt_max_ms": None,
                    "jitter_ms": None,
                    "packet_loss_pct": 100.0,
                    "error": str(err) or type(err).__name__.lower(),
                }
        return camera.subentry_id, {
            "reachable": host.is_alive,
            "rtt_avg_ms": round(host.avg_rtt, 2) if host.is_alive else None,
            "rtt_min_ms": round(host.min_rtt, 2) if host.is_alive else None,
            "rtt_max_ms": round(host.max_rtt, 2) if host.is_alive else None,
            "jitter_ms": round(host.jitter, 2) if host.is_alive else None,
            "packet_loss_pct": round(host.packet_loss * 100, 2),
            "error": "" if host.is_alive else "no_reply",
        }

    @staticmethod
    def _aggregate(
        samples: list[list[float | int | None]], now: float
    ) -> dict[str, float | int | None]:
        if not samples:
            return {
                "online_rate_24h": None,
                "offline_count_24h": 0,
                "rtt_avg_24h_ms": None,
                "jitter_avg_24h_ms": None,
                "packet_loss_avg_24h_pct": None,
                "history_samples": 0,
                "observed_hours": 0.0,
            }
        online = [bool(sample[1]) for sample in samples]
        offline_count = sum(
            1
            for index, state in enumerate(online)
            if not state and (index == 0 or online[index - 1])
        )

        def average(position: int) -> float | None:
            values = [
                float(sample[position])
                for sample in samples
                if sample[position] is not None
            ]
            return round(sum(values) / len(values), 2) if values else None

        observed_seconds = min(
            _HISTORY_SECONDS, max(0.0, now - float(samples[0][0]))
        )
        return {
            "online_rate_24h": round(
                sum(online) / len(online) * 100, 2
            ),
            "offline_count_24h": offline_count,
            "rtt_avg_24h_ms": average(2),
            "jitter_avg_24h_ms": average(3),
            "packet_loss_avg_24h_pct": average(4),
            "history_samples": len(samples),
            "observed_hours": round(observed_seconds / 3600, 2),
        }

    async def _async_update_data(self) -> ProbeResults:
        now = time.time()
        self._prune(now)
        semaphore = asyncio.Semaphore(4)
        results_list = await asyncio.gather(
            *(self._ping_one(camera, semaphore) for camera in self.cameras)
        )
        results: ProbeResults = {}
        for subentry_id, result in results_list:
            sample: list[float | int | None] = [
                now,
                int(bool(result["reachable"])),
                result["rtt_avg_ms"],
                result["jitter_ms"],
                result["packet_loss_pct"],
            ]
            samples = self._history.setdefault(subentry_id, [])
            samples.append(sample)
            result.update(self._aggregate(samples, now))
            results[subentry_id] = result

        self._updates_since_save += 1
        if self._updates_since_save >= HISTORY_SAVE_INTERVAL_UPDATES:
            await self.async_save_history()
        return results


class CameraServiceCoordinator(DataUpdateCoordinator[ProbeResults]):
    """Probe camera services and actual NVR RTP video at low frequency."""

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
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.service_history"
        )
        self._updates_since_save = 0

    async def async_load_history(self) -> None:
        """Load the retained service probe history."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("history"), dict):
            self._history = stored["history"]
        self._prune(time.time())

    async def async_save_history(self) -> None:
        """Persist retained service probe history."""
        await self._store.async_save({"history": self._history})
        self._updates_since_save = 0

    def diagnostic_history(self) -> dict[str, list[dict[str, Any]]]:
        """Return retained service samples for a diagnostics download."""
        return self._history

    def _prune(self, now: float) -> None:
        cutoff = now - _HISTORY_SECONDS
        active_ids = {camera.subentry_id for camera in self.cameras}
        self._history = {
            subentry_id: [
                sample
                for sample in samples
                if float(sample.get("ts", 0)) >= cutoff
            ]
            for subentry_id, samples in self._history.items()
            if subentry_id in active_ids
        }

    async def _async_update_data(self) -> ProbeResults:
        try:
            results = await self.hass.async_add_executor_job(
                self.client.probe_services, self.cameras
            )
        except Exception as err:
            raise UpdateFailed(
                f"Unexpected service probe failure: {err}"
            ) from err
        checked_at = datetime.now(timezone.utc).isoformat()
        now = time.time()
        self._prune(now)
        retained_fields = (
            "onvif_port",
            "rtsp_port",
            "camera_rtsp_alive",
            "camera_rtsp_status",
            "camera_rtsp_ms",
            "camera_rtsp_error",
            "nvr_describe_ok",
            "nvr_setup_ok",
            "nvr_play_ok",
            "nvr_live_video",
            "nvr_rtp_packets",
            "nvr_rtp_timestamps",
            "nvr_first_packet_ms",
            "nvr_probe_ms",
            "nvr_error",
        )
        for subentry_id, result in results.items():
            result["checked_at"] = checked_at
            self._history.setdefault(subentry_id, []).append(
                {
                    "ts": now,
                    **{
                        field: result.get(field)
                        for field in retained_fields
                    },
                }
            )
        self._updates_since_save += 1
        if (
            self._updates_since_save
            >= SERVICE_HISTORY_SAVE_INTERVAL_UPDATES
        ):
            await self.async_save_history()
        return results


class CameraRecordingCoordinator(DataUpdateCoordinator[ProbeResults]):
    """Query NVR recording files at a deliberately low frequency."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        nvr: NvrConfig,
        cameras: list[CameraConfig],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} recordings",
            update_interval=RECORDING_UPDATE_INTERVAL,
        )
        self.client = DahuaRecordingClient(nvr)
        self.cameras = cameras

    async def _async_update_data(self) -> ProbeResults:
        try:
            results = await self.hass.async_add_executor_job(
                self.client.probe_recordings, self.cameras
            )
        except Exception as err:
            raise UpdateFailed(
                f"Unexpected recording query failure: {err}"
            ) from err
        checked_at = datetime.now(timezone.utc).isoformat()
        for result in results.values():
            result["checked_at"] = checked_at
        return results
