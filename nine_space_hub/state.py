"""Bounded in-memory current state for 9Space Hub.

Home Assistant Recorder, not this app, owns status history. The only
persistent Hub payload is one atomically replaced last-good JPEG per camera.
"""

from __future__ import annotations

from dataclasses import asdict
import threading
import time
from typing import TYPE_CHECKING, Any, Iterable

from .validation import ValidatedBatch

if TYPE_CHECKING:
    from .scheduler import SnapshotSite

MAX_SITES = 32


class CurrentState:
    """Keep only the newest event and newest snapshot attempt in RAM."""

    def __init__(self, sites: Iterable[SnapshotSite]) -> None:
        self._lock = threading.RLock()
        self._sites = {site.site_id: site for site in sites}
        self._events: dict[tuple[str, str, str, int | None], dict[str, Any]] = {}
        self._attempts: dict[tuple[str, int], dict[str, Any]] = {}

    def ingest(self, batch: ValidatedBatch) -> int:
        if batch.site_id not in self._sites:
            raise KeyError("unknown_site")
        accepted = 0
        with self._lock:
            for event in batch.events:
                key = (batch.site_id, batch.source, event.kind, event.channel_id)
                current = self._events.get(key)
                if current is not None and current["timestamp_ms"] > event.timestamp_ms:
                    continue
                self._events[key] = {
                    **asdict(event),
                    "site_id": batch.site_id,
                    "display_name": batch.display_name,
                    "source": batch.source,
                }
                accepted += 1
        return accepted

    def register(self, site: SnapshotSite) -> bool:
        """Replace one runtime registration; no registry is persisted."""
        with self._lock:
            if site.site_id not in self._sites and len(self._sites) >= MAX_SITES:
                return False
            self._sites[site.site_id] = site
            return True

    def has_camera(self, site_id: str, camera_id: int) -> bool:
        with self._lock:
            site = self._sites.get(site_id)
            return site is not None and camera_id in site.channels

    def record_snapshot_attempt(
        self,
        site_id: str,
        camera_id: int,
        *,
        success: bool,
        timestamp_ms: int,
        latency_ms: float,
        error_code: str | None,
    ) -> None:
        if site_id not in self._sites:
            raise KeyError("unknown_site")
        with self._lock:
            self._attempts[(site_id, camera_id)] = {
                "success": success,
                "timestamp": timestamp_ms,
                "latency_ms": round(latency_ms, 3),
                "error_code": error_code,
            }

    def sites(self, snapshot_store: Any, *, max_stale_seconds: int) -> list[dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        result: list[dict[str, Any]] = []
        with self._lock:
            for site in self._sites.values():
                events = [
                    dict(event)
                    for key, event in self._events.items()
                    if key[0] == site.site_id
                ]
                events.sort(key=lambda item: (item["kind"], item["channel_id"] or 0, item["source"]))
                cameras = []
                for camera_id in site.channels:
                    by_kind = {
                        event["kind"]: event
                        for event in events
                        if event["channel_id"] == camera_id
                    }
                    live = by_kind.get("nvr.live", {}).get("metrics", {})
                    recording = by_kind.get("nvr.recording", {}).get("metrics", {})
                    age = snapshot_store.last_good_age_seconds(
                        site.site_id, camera_id, now_ms=now_ms
                    )
                    cameras.append(
                        {
                            "camera_id": camera_id,
                            "label": f"Camera {camera_id:02d}",
                            "snapshot_available": age is not None and age <= max_stale_seconds,
                            "last_good_age_seconds": age,
                            "latest_attempt": self._attempts.get((site.site_id, camera_id)),
                            "live_video": live.get("live_video"),
                            "live_checked_at": live.get("checked_at"),
                            "recording_query_ok": recording.get("recording_query_ok"),
                            "recording_recent": recording.get("recording_recent"),
                            "last_recording": recording.get("last_recording"),
                            "recording_checked_at": recording.get("checked_at"),
                            "recording_files_24h": recording.get("file_count_24h"),
                            "recording_coverage_24h": recording.get("recording_coverage_24h_pct"),
                            "recording_error": recording.get("error_code"),
                        }
                    )
                updated_at = max((event["timestamp_ms"] for event in events), default=None)
                result.append(
                    {
                        "site_id": site.site_id,
                        "display_name": site.display_name,
                        "updated_at": updated_at,
                        "cameras": cameras,
                        "latest_telemetry": events,
                    }
                )
        return result
