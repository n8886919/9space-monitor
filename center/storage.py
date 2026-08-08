"""SQLite persistence, retention and bounded-capacity policy for Center."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import sqlite3
import threading
import time
from typing import Any

from .validation import (
    TelemetryValidationError,
    ValidatedBatch,
    ValidatedEvent,
    canonical_metrics_json,
    validate_display_name,
    validate_error_code,
    validate_site_id,
)
from .snapshots import validate_camera_id

RETENTION_SECONDS = 7 * 24 * 60 * 60
# Logical waterlines deliberately leave substantial room for SQLite pages,
# indexes and WAL. The filesystem hard guard remains the final 2 GiB bound.
DEFAULT_SITE_LIMIT_BYTES = 192 * 1024 * 1024
DEFAULT_GLOBAL_LIMIT_BYTES = 1536 * 1024 * 1024
DEFAULT_PHYSICAL_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_PHYSICAL_RESERVE_BYTES = 128 * 1024 * 1024
MAX_FUTURE_SKEW_SECONDS = 5 * 60
MAX_SNAPSHOT_LATENCY_MS = 3_600_000.0
PING_AGGREGATE_METRICS = ("rtt_ms", "packet_loss_percent")
PING_AGGREGATE_WINDOWS_MS = {
    "1h": 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
}


class CapacityExceeded(RuntimeError):
    """A single atomic batch cannot fit within the configured limits."""


class InvalidEventTimestamp(ValueError):
    """An event timestamp is too far in the future to retain safely."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    inserted: int
    duplicates: int
    expired: int
    capacity_pruned: int


def _iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


class TelemetryStorage:
    """Small synchronous SQLite store; callers run methods off the event loop."""

    def __init__(
        self,
        path: str,
        *,
        retention_seconds: int = RETENTION_SECONDS,
        site_limit_bytes: int = DEFAULT_SITE_LIMIT_BYTES,
        global_limit_bytes: int = DEFAULT_GLOBAL_LIMIT_BYTES,
        physical_limit_bytes: int = DEFAULT_PHYSICAL_LIMIT_BYTES,
        physical_reserve_bytes: int = DEFAULT_PHYSICAL_RESERVE_BYTES,
        future_skew_seconds: int = MAX_FUTURE_SKEW_SECONDS,
    ) -> None:
        if (
            retention_seconds <= 0
            or site_limit_bytes <= 0
            or global_limit_bytes <= 0
            or physical_limit_bytes <= 0
            or physical_reserve_bytes < 0
            or future_skew_seconds < 0
        ):
            raise ValueError("storage_limits_must_be_positive")
        if site_limit_bytes > global_limit_bytes:
            raise ValueError("site_limit_exceeds_global_limit")
        if global_limit_bytes >= physical_limit_bytes:
            raise ValueError("logical_limit_must_leave_physical_headroom")
        if physical_reserve_bytes >= physical_limit_bytes:
            raise ValueError("physical_reserve_exceeds_limit")
        self.path = path
        self.retention_seconds = retention_seconds
        self.site_limit_bytes = site_limit_bytes
        self.global_limit_bytes = global_limit_bytes
        self.physical_limit_bytes = physical_limit_bytes
        self.physical_reserve_bytes = physical_reserve_bytes
        self.future_skew_seconds = future_skew_seconds
        self._lock = threading.RLock()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    site_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    last_seen_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('addon', 'integration')),
                    event_id TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    ingested_at_ms INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    channel_id INTEGER,
                    metrics_json TEXT NOT NULL,
                    logical_bytes INTEGER NOT NULL CHECK(logical_bytes > 0),
                    UNIQUE(source, site_id, event_id),
                    FOREIGN KEY(site_id) REFERENCES sites(site_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS events_site_time
                    ON events(site_id, timestamp_ms, row_id);
                CREATE INDEX IF NOT EXISTS events_time
                    ON events(timestamp_ms, row_id);
                CREATE INDEX IF NOT EXISTS events_site_kind_channel_time
                    ON events(site_id, kind, channel_id, timestamp_ms);
                CREATE TABLE IF NOT EXISTS snapshot_cameras (
                    site_id TEXT NOT NULL,
                    camera_id INTEGER NOT NULL CHECK(camera_id BETWEEN 1 AND 4096),
                    PRIMARY KEY(site_id, camera_id),
                    FOREIGN KEY(site_id) REFERENCES sites(site_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS snapshot_attempts (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id TEXT NOT NULL,
                    camera_id INTEGER NOT NULL CHECK(camera_id BETWEEN 1 AND 4096),
                    timestamp_ms INTEGER NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0, 1)),
                    latency_ms REAL NOT NULL CHECK(latency_ms >= 0),
                    error_code TEXT,
                    logical_bytes INTEGER NOT NULL CHECK(logical_bytes > 0),
                    FOREIGN KEY(site_id, camera_id) REFERENCES snapshot_cameras(site_id, camera_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS snapshot_attempts_site_camera_time
                    ON snapshot_attempts(site_id, camera_id, timestamp_ms, row_id);
                """
            )

    def physical_usage(self) -> int:
        """Return bytes consumed by the SQLite database, WAL and SHM files."""
        return sum(
            os.path.getsize(path)
            for path in (self.path, f"{self.path}-wal", f"{self.path}-shm")
            if os.path.isfile(path)
        )

    def _check_physical_preflight(self, incoming_logical_bytes: int) -> None:
        safe_waterline = self.physical_limit_bytes - self.physical_reserve_bytes
        current = self.physical_usage()
        # SQLite/index growth varies by page layout. Four times the encoded
        # logical payload plus a minimum page allowance is intentionally
        # conservative, and the post-write check below remains authoritative.
        estimated_growth = max(64 * 1024, incoming_logical_bytes * 4)
        if current > safe_waterline or current + estimated_growth > safe_waterline:
            raise CapacityExceeded("physical_capacity_preflight_failed")

    def _check_physical_postwrite(self) -> None:
        if self.physical_usage() > self.physical_limit_bytes:
            raise CapacityExceeded("physical_capacity_limit_exceeded")

    @staticmethod
    def _checkpoint_wal(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    @staticmethod
    def _event_storage(event: ValidatedEvent, site_id: str, source: str) -> tuple[str, int]:
        metrics_json = canonical_metrics_json(event.metrics)
        # Logical quota includes the variable fields plus a conservative row
        # overhead allowance. This is deterministic across SQLite versions.
        logical_bytes = (
            len(site_id.encode())
            + len(source.encode())
            + len(event.event_id.encode())
            + len(event.kind.encode())
            + len(metrics_json.encode("utf-8"))
            + 96
        )
        return metrics_json, logical_bytes

    def _prune_expired(self, connection: sqlite3.Connection, cutoff_ms: int) -> int:
        cursor = connection.execute(
            "DELETE FROM events WHERE timestamp_ms < ?", (cutoff_ms,)
        )
        connection.execute(
            "DELETE FROM sites WHERE NOT EXISTS "
            "(SELECT 1 FROM events WHERE events.site_id = sites.site_id) "
            "AND NOT EXISTS (SELECT 1 FROM snapshot_cameras "
            "WHERE snapshot_cameras.site_id = sites.site_id)"
        )
        connection.execute("DELETE FROM snapshot_attempts WHERE timestamp_ms < ?", (cutoff_ms,))
        return cursor.rowcount

    @staticmethod
    def _logical_usage(connection: sqlite3.Connection, site_id: str | None = None) -> int:
        where = "" if site_id is None else " WHERE site_id = ?"
        params: tuple[Any, ...] = () if site_id is None else (site_id,)
        row = connection.execute(
            "SELECT COALESCE(SUM(logical_bytes), 0) AS value FROM ("
            "SELECT logical_bytes FROM events" + where + " UNION ALL "
            "SELECT logical_bytes FROM snapshot_attempts" + where + ")",
            params + params,
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _delete_oldest(
        connection: sqlite3.Connection, required_bytes: int, *, site_id: str | None = None
    ) -> int:
        if required_bytes <= 0:
            return 0
        where = "WHERE site_id = ?" if site_id is not None else ""
        params: tuple[Any, ...] = (site_id,) if site_id is not None else ()
        rows = connection.execute(
            "SELECT table_name, row_id, logical_bytes FROM ("
            f"SELECT 'events' AS table_name, row_id, timestamp_ms, logical_bytes FROM events {where} "
            "UNION ALL "
            f"SELECT 'snapshot_attempts' AS table_name, row_id, timestamp_ms, logical_bytes FROM snapshot_attempts {where}"
            ") ORDER BY timestamp_ms ASC, row_id ASC",
            params + params,
        ).fetchall()
        selected: list[tuple[str, int]] = []
        reclaimed = 0
        for row in rows:
            selected.append((str(row["table_name"]), int(row["row_id"])))
            reclaimed += int(row["logical_bytes"])
            if reclaimed >= required_bytes:
                break
        if reclaimed < required_bytes:
            raise CapacityExceeded("capacity_limit_exceeded")
        for table_name, row_id in selected:
            connection.execute(f"DELETE FROM {table_name} WHERE row_id = ?", (row_id,))
        return len(selected)

    def register_snapshot_camera(
        self, site_id: str, camera_id: int, display_name: str, *, now_ms: int | None = None
    ) -> None:
        """Explicit, metadata-only registry used to distinguish 404 from 503."""
        validate_site_id(site_id)
        validate_camera_id(camera_id)
        display_name = validate_display_name(display_name)
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO sites(site_id, display_name, last_seen_ms) VALUES(?, ?, ?) "
                    "ON CONFLICT(site_id) DO UPDATE SET display_name=excluded.display_name",
                    (site_id, display_name, now_ms),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO snapshot_cameras(site_id, camera_id) VALUES(?, ?)",
                    (site_id, camera_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def snapshot_camera_exists(self, site_id: str, camera_id: int) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM snapshot_cameras WHERE site_id = ? AND camera_id = ?",
                (site_id, camera_id),
            ).fetchone()
        return row is not None

    def snapshot_cameras(self, site_id: str) -> list[dict[str, Any]]:
        """Return only registered camera metadata, never snapshot storage details."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT camera_id FROM snapshot_cameras WHERE site_id = ? ORDER BY camera_id",
                (site_id,),
            ).fetchall()
            return [{"camera_id": int(row["camera_id"])} for row in rows]

    def latest_snapshot_attempt(
        self, site_id: str, camera_id: int
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT timestamp_ms, success, latency_ms, error_code FROM snapshot_attempts "
                "WHERE site_id = ? AND camera_id = ? "
                "ORDER BY timestamp_ms DESC, row_id DESC LIMIT 1",
                (site_id, camera_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "timestamp": _iso(int(row["timestamp_ms"])),
            "status": "success" if bool(row["success"]) else "failure",
            "latency_ms": float(row["latency_ms"]),
            "error_code": row["error_code"],
        }

    def record_snapshot_attempt(
        self, site_id: str, camera_id: int, *, success: bool, timestamp_ms: int,
        latency_ms: float, error_code: str | None = None, now_ms: int | None = None,
    ) -> None:
        validate_site_id(site_id)
        validate_camera_id(camera_id)
        if (
            type(success) is not bool
            or type(timestamp_ms) is not int
            or type(latency_ms) not in {int, float}
            or not math.isfinite(float(latency_ms))
            or not 0 <= latency_ms <= MAX_SNAPSHOT_LATENCY_MS
        ):
            raise ValueError("invalid_snapshot_attempt")
        if success:
            if error_code is not None:
                raise ValueError("successful_attempt_has_error")
        else:
            validate_error_code(error_code)
        logical_bytes = 96 + len(site_id.encode()) + len(error_code or "")
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if timestamp_ms > now_ms + self.future_skew_seconds * 1000:
            raise InvalidEventTimestamp("snapshot_attempt_too_far_in_future")
        cutoff_ms = now_ms - self.retention_seconds * 1000
        if timestamp_ms < cutoff_ms:
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune_expired(connection, cutoff_ms)
                registered = connection.execute(
                    "SELECT 1 FROM snapshot_cameras WHERE site_id = ? AND camera_id = ?",
                    (site_id, camera_id),
                ).fetchone()
                if registered is None:
                    raise KeyError("unknown_snapshot_camera")
                if logical_bytes > self.site_limit_bytes or logical_bytes > self.global_limit_bytes:
                    raise CapacityExceeded("snapshot_attempt_exceeds_capacity_limit")
                self._check_physical_preflight(logical_bytes)
                self._delete_oldest(
                    connection, self._logical_usage(connection, site_id) + logical_bytes - self.site_limit_bytes,
                    site_id=site_id,
                )
                if self._logical_usage(connection) + logical_bytes > self.global_limit_bytes:
                    raise CapacityExceeded("global_logical_capacity_limit_exceeded")
                connection.execute(
                    "INSERT INTO snapshot_attempts(site_id, camera_id, timestamp_ms, success, latency_ms, error_code, logical_bytes) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (site_id, camera_id, timestamp_ms, int(success), float(latency_ms), error_code, logical_bytes),
                )
                self._check_physical_postwrite()
                connection.commit()
            except Exception:
                connection.rollback()
                self._checkpoint_wal(connection)
                raise

    def snapshot_statistics(self, site_id: str, camera_id: int | None = None, *, now_ms: int | None = None) -> dict[str, dict[str, float | int | None]]:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        windows = {"1h": 60 * 60 * 1000, "24h": 24 * 60 * 60 * 1000, "7d": 7 * 24 * 60 * 60 * 1000}
        result: dict[str, dict[str, float | int | None]] = {}
        with self._lock, self._connect() as connection:
            for label, window_ms in windows.items():
                clauses = ["site_id = ?", "timestamp_ms >= ?"]
                params: list[Any] = [site_id, now_ms - window_ms]
                if camera_id is not None:
                    clauses.append("camera_id = ?")
                    params.append(camera_id)
                row = connection.execute(
                    "SELECT COUNT(*) AS attempts, COALESCE(SUM(success), 0) AS successes, "
                    "AVG(latency_ms) AS mean, AVG(latency_ms * latency_ms) AS mean_square "
                    "FROM snapshot_attempts WHERE " + " AND ".join(clauses), params,
                ).fetchone()
                attempts = int(row["attempts"])
                mean = None if attempts == 0 else float(row["mean"])
                variance = 0.0 if mean is None else max(0.0, float(row["mean_square"]) - mean * mean)
                result[label] = {"attempts": attempts, "success_rate": None if not attempts else int(row["successes"]) / attempts, "latency_mean_ms": mean, "latency_population_stddev_ms": variance ** 0.5 if mean is not None else None}
        return result

    def ingest(self, batch: ValidatedBatch, *, now_ms: int | None = None) -> IngestResult:
        """Atomically deduplicate, prune and insert one validated batch."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        cutoff_ms = now_ms - self.retention_seconds * 1000
        expired = 0
        duplicates = 0
        prepared: list[tuple[ValidatedEvent, str, int]] = []
        seen: set[str] = set()
        for event in batch.events:
            if event.event_id in seen:
                duplicates += 1
                continue
            seen.add(event.event_id)
            if event.timestamp_ms < cutoff_ms:
                expired += 1
                continue
            if event.timestamp_ms > now_ms + self.future_skew_seconds * 1000:
                raise InvalidEventTimestamp("event_timestamp_too_far_in_future")
            metrics_json, logical_bytes = self._event_storage(
                event, batch.site_id, batch.source
            )
            prepared.append((event, metrics_json, logical_bytes))

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune_expired(connection, cutoff_ms)
                new_rows: list[tuple[ValidatedEvent, str, int]] = []
                for item in prepared:
                    event = item[0]
                    exists = connection.execute(
                        "SELECT 1 FROM events WHERE source = ? AND site_id = ? "
                        "AND event_id = ?",
                        (batch.source, batch.site_id, event.event_id),
                    ).fetchone()
                    if exists:
                        duplicates += 1
                    else:
                        new_rows.append(item)
                batch_bytes = sum(item[2] for item in new_rows)
                if batch_bytes > self.site_limit_bytes or batch_bytes > self.global_limit_bytes:
                    raise CapacityExceeded("batch_exceeds_capacity_limit")

                self._check_physical_preflight(batch_bytes)

                capacity_pruned = self._delete_oldest(
                    connection,
                    self._logical_usage(connection, batch.site_id)
                    + batch_bytes
                    - self.site_limit_bytes,
                    site_id=batch.site_id,
                )
                # Site quota is strictly site-local. Global pressure never
                # deletes another site's in-retention data; this batch fails
                # closed and the transaction restores any site-local prune.
                if self._logical_usage(connection) + batch_bytes > self.global_limit_bytes:
                    raise CapacityExceeded("global_logical_capacity_limit_exceeded")

                connection.execute(
                    "INSERT INTO sites(site_id, display_name, last_seen_ms) VALUES(?, ?, ?) "
                    "ON CONFLICT(site_id) DO UPDATE SET "
                    "display_name=excluded.display_name, last_seen_ms=excluded.last_seen_ms",
                    (batch.site_id, batch.display_name, now_ms),
                )
                connection.executemany(
                    "INSERT INTO events(site_id, source, event_id, timestamp_ms, "
                    "ingested_at_ms, kind, channel_id, metrics_json, logical_bytes) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            batch.site_id,
                            batch.source,
                            event.event_id,
                            event.timestamp_ms,
                            now_ms,
                            event.kind,
                            event.channel_id,
                            metrics_json,
                            logical_bytes,
                        )
                        for event, metrics_json, logical_bytes in new_rows
                    ),
                )
                self._check_physical_postwrite()
                connection.commit()
            except Exception:
                connection.rollback()
                # An uncommitted write can still leave allocated WAL pages.
                # Truncate them after rollback so a rejected batch does not
                # consume the physical reserve indefinitely.
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                raise
        return IngestResult(len(new_rows), duplicates, expired, capacity_pruned)

    def prune(self, *, now_ms: int | None = None) -> int:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock, self._connect() as connection:
            return self._prune_expired(
                connection, now_ms - self.retention_seconds * 1000
            )

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": int(row["row_id"]),
            "site_id": str(row["site_id"]),
            "source": str(row["source"]),
            "event_id": str(row["event_id"]),
            "timestamp": _iso(int(row["timestamp_ms"])),
            "kind": str(row["kind"]),
            "channel_id": row["channel_id"],
            "metrics": json.loads(row["metrics_json"]),
        }

    def query(
        self,
        site_id: str,
        *,
        after_cursor: int = 0,
        kind: str | None = None,
        channel_id: int | None = None,
        limit: int = 1000,
        now_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000 or after_cursor < 0:
            raise ValueError("invalid_query_bounds")
        self.prune(now_ms=now_ms)
        clauses = ["site_id = ?", "row_id > ?"]
        params: list[Any] = [site_id, after_cursor]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT row_id, site_id, source, event_id, timestamp_ms, kind, "
                "channel_id, metrics_json FROM events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY row_id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def latest(self, site_id: str, *, now_ms: int | None = None) -> list[dict[str, Any]]:
        self.prune(now_ms=now_ms)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT row_id, site_id, source, event_id, timestamp_ms, kind, "
                "channel_id, metrics_json FROM events WHERE site_id = ? "
                "ORDER BY timestamp_ms DESC, row_id DESC",
                (site_id,),
            ).fetchall()
        latest: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int | None]] = set()
        for row in rows:
            key = (str(row["source"]), str(row["kind"]), row["channel_id"])
            if key not in seen:
                seen.add(key)
                latest.append(self._decode_row(row))
        return latest

    def ping_summary(self, site_id: str, *, now_ms: int | None = None) -> list[dict[str, Any]]:
        """Return bounded, sanitized Ping rollups without relying on ``latest``.

        A Ping producer can emit availability and numeric samples in separate
        events.  Reading the raw retained rows here prevents the generic
        latest-event deduplication view from hiding the current availability.
        """
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        self.prune(now_ms=now_ms)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT row_id, timestamp_ms, channel_id, metrics_json FROM events "
                "WHERE site_id = ? AND kind = 'ha.ping' AND channel_id IS NOT NULL "
                "AND timestamp_ms <= ? ORDER BY timestamp_ms DESC, row_id DESC",
                (site_id, now_ms),
            ).fetchall()

        channels: dict[int, dict[str, Any]] = {}
        for row in rows:
            channel_id = int(row["channel_id"])
            item = channels.setdefault(
                channel_id,
                {
                    "current": {
                        "available": None,
                        "state": None,
                        "rtt_ms": None,
                        "packet_loss_percent": None,
                    },
                    "windows": {
                        label: {
                            metric: {"mean": None, "count": 0, "_sum": 0.0}
                            for metric in PING_AGGREGATE_METRICS
                        }
                        for label in PING_AGGREGATE_WINDOWS_MS
                    },
                },
            )
            metrics = json.loads(row["metrics_json"])
            # Rows are newest-first.  Retain every current Ping field
            # independently, so a later rtt/loss event cannot hide a prior
            # reachable/state event in the generic latest-event view.
            current = item["current"]
            if current["available"] is None and type(metrics.get("available")) is bool:
                current["available"] = metrics["available"]
            if current["state"] is None and isinstance(metrics.get("state"), str):
                current["state"] = metrics["state"]
            for metric in PING_AGGREGATE_METRICS:
                value = metrics.get(metric)
                if current[metric] is None and type(value) in {int, float}:
                    current[metric] = value
            timestamp_ms = int(row["timestamp_ms"])
            for label, window_ms in PING_AGGREGATE_WINDOWS_MS.items():
                if timestamp_ms < now_ms - window_ms:
                    continue
                for metric in PING_AGGREGATE_METRICS:
                    value = metrics.get(metric)
                    # Validation already enforces the schema; keep this narrow
                    # check so booleans/non-numeric data can never aggregate.
                    if type(value) not in {int, float}:
                        continue
                    aggregate = item["windows"][label][metric]
                    aggregate["_sum"] += float(value)
                    aggregate["count"] += 1

        result: list[dict[str, Any]] = []
        for channel_id in sorted(channels):
            item = channels[channel_id]
            windows: dict[str, dict[str, dict[str, float | int | None]]] = {}
            for label, metrics in item["windows"].items():
                windows[label] = {}
                for metric, aggregate in metrics.items():
                    count = aggregate["count"]
                    windows[label][metric] = {
                        "mean": None if count == 0 else aggregate["_sum"] / count,
                        "count": count,
                    }
            result.append(
                {
                    "channel_id": channel_id,
                    "current": item["current"],
                    "windows": windows,
                }
            )
        return result

    def usage(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            global_bytes = self._logical_usage(connection)
            sites = [
                {
                    "site_id": str(row["site_id"]),
                    "display_name": str(row["display_name"]),
                    "logical_bytes": int(row["logical_bytes"]),
                    "event_count": int(row["event_count"]),
                    "last_seen": _iso(int(row["last_seen_ms"])),
                }
                for row in connection.execute(
                    "SELECT sites.site_id, sites.display_name, sites.last_seen_ms, "
                    "COALESCE((SELECT SUM(logical_bytes) FROM events "
                    "WHERE events.site_id = sites.site_id), 0) + "
                    "COALESCE((SELECT SUM(logical_bytes) FROM snapshot_attempts "
                    "WHERE snapshot_attempts.site_id = sites.site_id), 0) AS logical_bytes, "
                    "COUNT(events.row_id) AS event_count FROM sites "
                    "LEFT JOIN events ON events.site_id = sites.site_id "
                    "GROUP BY sites.site_id ORDER BY sites.site_id"
                ).fetchall()
            ]
        return {
            "logical_bytes": global_bytes,
            "physical_bytes": self.physical_usage(),
            "logical_limit_bytes": self.global_limit_bytes,
            "physical_limit_bytes": self.physical_limit_bytes,
            "sites": sites,
        }
