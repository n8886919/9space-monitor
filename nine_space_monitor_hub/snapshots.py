"""Bounded last-good JPEG filesystem store; status history is not persisted."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time

from .validation import TelemetryValidationError, validate_site_id

DEFAULT_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
DEFAULT_STORE_LIMIT_BYTES = 1024 * 1024 * 1024


def validate_camera_id(camera_id: int) -> int:
    if type(camera_id) is not int or not 1 <= camera_id <= 4096:
        raise ValueError("invalid_camera_id")
    return camera_id


class SnapshotStore:
    """One atomically-replaced opaque JPEG file per registered site/camera."""

    def __init__(
        self, root: str | Path, *, max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        store_limit_bytes: int = DEFAULT_STORE_LIMIT_BYTES,
    ) -> None:
        if max_snapshot_bytes <= 0 or store_limit_bytes < max_snapshot_bytes:
            raise ValueError("invalid_snapshot_store_limits")
        self.root = Path(root)
        self.max_snapshot_bytes = max_snapshot_bytes
        self.store_limit_bytes = store_limit_bytes
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, site_id: str, camera_id: int) -> Path:
        validate_site_id(site_id)
        validate_camera_id(camera_id)
        return self.root / site_id / f"{camera_id}.jpg"

    def write(self, site_id: str, camera_id: int, jpeg: bytes, *, timestamp_ms: int | None = None) -> Path:
        """Durably replace one file; a failed write leaves the old file intact."""
        if not isinstance(jpeg, bytes) or not jpeg or len(jpeg) > self.max_snapshot_bytes:
            raise ValueError("invalid_snapshot_bytes")
        target = self._path(site_id, camera_id)
        with self._lock:
            current = sum(
                path.stat().st_size for path in self.root.rglob("*") if path.is_file()
            )
            previous_size = target.stat().st_size if target.is_file() else 0
            if current - previous_size + len(jpeg) > self.store_limit_bytes:
                raise ValueError("snapshot_store_capacity_exceeded")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=f".{camera_id}-", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(jpeg)
                    handle.flush()
                    os.fsync(handle.fileno())
                if timestamp_ms is not None:
                    timestamp_seconds = timestamp_ms / 1000
                    os.utime(temporary_name, (timestamp_seconds, timestamp_seconds))
                os.replace(temporary_name, target)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        return target

    def get(
        self, site_id: str, camera_id: int, *, now_ms: int | None = None, max_stale_seconds: int = 120
    ) -> Path | None:
        if max_stale_seconds < 0:
            raise ValueError("invalid_max_stale_seconds")
        target = self._path(site_id, camera_id)
        try:
            stat = target.stat()
        except FileNotFoundError:
            return None
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        age_ms = now_ms - int(stat.st_mtime * 1000)
        if age_ms < 0 or age_ms > max_stale_seconds * 1000:
            return None
        return target

    def usage(self) -> dict[str, int]:
        """Return bounded-store capacity metadata without exposing file paths."""
        with self._lock:
            files = [path for path in self.root.rglob("*") if path.is_file()]
            return {
                "bytes": sum(path.stat().st_size for path in files),
                "file_count": len(files),
                "limit_bytes": self.store_limit_bytes,
            }

    def last_good_age_seconds(
        self, site_id: str, camera_id: int, *, now_ms: int | None = None
    ) -> int | None:
        target = self._path(site_id, camera_id)
        with self._lock:
            try:
                modified_ms = int(target.stat().st_mtime * 1000)
            except FileNotFoundError:
                return None
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        return max(0, (now_ms - modified_ms) // 1000)

    def has_last_good(self, site_id: str, camera_id: int) -> bool:
        with self._lock:
            return self._path(site_id, camera_id).is_file()

    def read_last_good(self, site_id: str, camera_id: int) -> bytes | None:
        """Read one opaque last-good image, regardless of freshness."""
        target = self._path(site_id, camera_id)
        try:
            with self._lock:
                return target.read_bytes()
        except FileNotFoundError:
            return None
