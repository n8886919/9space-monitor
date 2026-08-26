"""Bounded in-memory current state for 9Space Hub.

Home Assistant Recorder, not this app, owns status history. The only
persistent Hub payload is one atomically replaced last-good JPEG per camera.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .scheduler import SnapshotSite

MAX_SITES = 32


class CurrentState:
    """Keep current snapshot attempts and bounded counters in RAM."""

    def __init__(self, sites: Iterable[SnapshotSite]) -> None:
        self._lock = threading.RLock()
        self._sites = {site.site_id: site for site in sites}
        self._attempts: dict[tuple[str, int], dict[str, Any]] = {}
        self._counts: dict[tuple[str, int], dict[str, int]] = {}
        self._disabled: set[tuple[str, int]] = set()

    def register(self, site: SnapshotSite) -> bool:
        """Replace one runtime registration; no registry is persisted."""
        with self._lock:
            if site.site_id not in self._sites and len(self._sites) >= MAX_SITES:
                return False
            self._sites[site.site_id] = site
            self._disabled = {
                key for key in self._disabled
                if key[0] != site.site_id or key[1] in site.channels
            }
            return True

    def has_camera(self, site_id: str, camera_id: int) -> bool:
        with self._lock:
            site = self._sites.get(site_id)
            return site is not None and camera_id in site.channels

    def is_camera_enabled(self, site_id: str, camera_id: int) -> bool:
        """Return whether a registered channel is enabled for snapshot refresh."""
        with self._lock:
            return self.has_camera(site_id, camera_id) and (site_id, camera_id) not in self._disabled

    def set_camera_enabled(self, site_id: str, camera_id: int, enabled: bool) -> bool:
        """Set one registered channel's bounded, in-memory enabled state."""
        with self._lock:
            if not self.has_camera(site_id, camera_id):
                return False
            key = (site_id, camera_id)
            if enabled:
                self._disabled.discard(key)
            else:
                self._disabled.add(key)
            return True

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
            counts = self._counts.setdefault(
                (site_id, camera_id), {"success": 0, "failure": 0, "consecutive_failures": 0}
            )
            counts["success" if success else "failure"] += 1
            counts["consecutive_failures"] = 0 if success else counts["consecutive_failures"] + 1
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
                cameras = []
                for camera_id in site.channels:
                    counts = self._counts.get(
                        (site.site_id, camera_id),
                        {"success": 0, "failure": 0, "consecutive_failures": 0},
                    )
                    total = counts["success"] + counts["failure"]
                    age = snapshot_store.last_good_age_seconds(
                        site.site_id, camera_id, now_ms=now_ms
                    )
                    cameras.append(
                        {
                            "camera_id": camera_id,
                            "label": f"CH {camera_id:02d}",
                            "enabled": (site.site_id, camera_id) not in self._disabled,
                            "snapshot_available": age is not None and age <= max_stale_seconds,
                            "last_good_age_seconds": age,
                            "latest_attempt": self._attempts.get((site.site_id, camera_id)),
                            "snapshot_success_count": counts["success"],
                            "snapshot_failure_count": counts["failure"],
                            "snapshot_consecutive_failures": counts["consecutive_failures"],
                            "snapshot_success_rate": round(counts["success"] * 100 / total, 2) if total else None,
                        }
                    )
                updated_at = max(
                    (attempt["timestamp"] for key, attempt in self._attempts.items() if key[0] == site.site_id),
                    default=None,
                )
                result.append(
                    {
                        "site_id": site.site_id,
                        "display_name": site.display_name,
                        "updated_at": updated_at,
                        "cameras": cameras,
                    }
                )
        return result
