"""SQLite persistence, retention and bounded-capacity policy for Center."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sqlite3
import threading
import time
from typing import Any

from .validation import ValidatedBatch, ValidatedEvent, canonical_metrics_json

RETENTION_SECONDS = 7 * 24 * 60 * 60
# Logical waterlines deliberately leave substantial room for SQLite pages,
# indexes and WAL. The filesystem hard guard remains the final 2 GiB bound.
DEFAULT_SITE_LIMIT_BYTES = 192 * 1024 * 1024
DEFAULT_GLOBAL_LIMIT_BYTES = 1536 * 1024 * 1024
DEFAULT_PHYSICAL_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_PHYSICAL_RESERVE_BYTES = 128 * 1024 * 1024
MAX_FUTURE_SKEW_SECONDS = 5 * 60


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
            "(SELECT 1 FROM events WHERE events.site_id = sites.site_id)"
        )
        return cursor.rowcount

    @staticmethod
    def _logical_usage(connection: sqlite3.Connection, site_id: str | None = None) -> int:
        if site_id is None:
            row = connection.execute(
                "SELECT COALESCE(SUM(logical_bytes), 0) AS value FROM events"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COALESCE(SUM(logical_bytes), 0) AS value "
                "FROM events WHERE site_id = ?",
                (site_id,),
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
            f"SELECT row_id, logical_bytes FROM events {where} "
            "ORDER BY timestamp_ms ASC, row_id ASC",
            params,
        ).fetchall()
        selected: list[int] = []
        reclaimed = 0
        for row in rows:
            selected.append(int(row["row_id"]))
            reclaimed += int(row["logical_bytes"])
            if reclaimed >= required_bytes:
                break
        if reclaimed < required_bytes:
            raise CapacityExceeded("capacity_limit_exceeded")
        connection.executemany(
            "DELETE FROM events WHERE row_id = ?", ((row_id,) for row_id in selected)
        )
        return len(selected)

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
                    "COALESCE(SUM(events.logical_bytes), 0) AS logical_bytes, "
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
