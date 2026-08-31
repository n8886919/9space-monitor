"""Internal Android notification and Pikmin invite APIs backed by SQLite."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit


LOGGER = logging.getLogger("notification_log")
MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_LIMIT = 500
TEXT_LIMITS = {
    "package_name": 512,
    "app_name": 512,
    "title": 4096,
    "text": 32768,
    "sub_text": 4096,
    "category": 256,
    "notification_key": 2048,
    "source_device": 512,
}
EVENT_TYPES = {"posted", "removed"}
PAYLOAD_FIELDS = {*TEXT_LIMITS, "event_type", "occurred_at", "extra"}
MAX_INVITER_LENGTH = 256


class ApiError(Exception):
    """An expected client-facing API error."""

    def __init__(self, status: int, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.error_code = error_code
        self.detail = detail


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_timestamp(value: Any, fallback: datetime) -> str:
    """Normalize ISO-8601 or Unix seconds/milliseconds to an UTC timestamp."""
    if value is None or value == "":
        return format_timestamp(fallback)

    parsed: datetime
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        epoch = float(value)
        if epoch > 100_000_000_000:
            epoch /= 1000
        try:
            parsed = datetime.fromtimestamp(epoch, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ApiError(422, "invalid_timestamp", "occurred_at is out of range") from exc
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return format_timestamp(fallback)
        try:
            if candidate.replace(".", "", 1).isdigit():
                return parse_timestamp(float(candidate), fallback)
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApiError(
                422,
                "invalid_timestamp",
                "occurred_at must be ISO-8601 or Unix seconds/milliseconds",
            ) from exc
        if parsed.tzinfo is None:
            raise ApiError(422, "invalid_timestamp", "ISO-8601 occurred_at needs a timezone")
    else:
        raise ApiError(422, "invalid_timestamp", "occurred_at has an invalid type")
    return format_timestamp(parsed)


def validated_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(422, "invalid_payload", f"{field} must be a string")
    if len(value) > TEXT_LIMITS[field]:
        raise ApiError(422, "field_too_long", f"{field} exceeds its length limit")
    return value


def validate_notification(payload: Any, now: datetime) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(422, "invalid_payload", "JSON body must be an object")
    unknown = sorted(set(payload) - PAYLOAD_FIELDS)
    if unknown:
        raise ApiError(422, "unknown_fields", f"unknown fields: {', '.join(unknown)}")

    result = {field: validated_text(payload, field) for field in TEXT_LIMITS}
    if not any(result[field] for field in ("package_name", "app_name", "title", "text")):
        raise ApiError(
            422,
            "empty_notification",
            "at least one of package_name, app_name, title, or text is required",
        )

    event_type = payload.get("event_type", "posted")
    if event_type not in EVENT_TYPES:
        raise ApiError(422, "invalid_event_type", "event_type must be posted or removed")
    result["event_type"] = event_type
    result["occurred_at"] = parse_timestamp(payload.get("occurred_at"), now)

    extra = payload.get("extra", {})
    if not isinstance(extra, dict):
        raise ApiError(422, "invalid_payload", "extra must be an object")
    extra_json = json.dumps(extra, ensure_ascii=False, separators=(",", ":"))
    if len(extra_json.encode("utf-8")) > 16 * 1024:
        raise ApiError(422, "field_too_long", "extra exceeds its length limit")
    result["extra_json"] = extra_json
    return result


def validate_invite(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ApiError(422, "invalid_payload", "JSON body must be an object")
    unknown = sorted(set(payload) - {"inviter"})
    if unknown:
        raise ApiError(422, "unknown_fields", f"unknown fields: {', '.join(unknown)}")
    inviter = payload.get("inviter")
    if not isinstance(inviter, str):
        raise ApiError(422, "invalid_inviter", "inviter must be a string")
    inviter = inviter.strip()
    if not inviter:
        raise ApiError(422, "invalid_inviter", "inviter must not be empty")
    if len(inviter) > MAX_INVITER_LENGTH:
        raise ApiError(422, "invalid_inviter", "inviter exceeds 256 characters")
    return inviter


class NotificationStore:
    """Small SQLite store; every operation uses its own connection."""

    def __init__(self, path: Path, retention_days: int, max_rows: int) -> None:
        if not 1 <= retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        if not 100 <= max_rows <= 1_000_000:
            raise ValueError("max_rows must be between 100 and 1000000")
        self.path = path
        self.retention_days = retention_days
        self.max_rows = max_rows
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sub_text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    notification_key TEXT NOT NULL,
                    source_device TEXT NOT NULL,
                    extra_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_occurred_at
                    ON notifications(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_notifications_package_name_id
                    ON notifications(package_name, id);
                CREATE TABLE IF NOT EXISTS mushroom_invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mushroom_invites_inviter
                    ON mushroom_invites(inviter);
                CREATE INDEX IF NOT EXISTS idx_mushroom_invites_received_at
                    ON mushroom_invites(received_at);
                """
            )

    def insert(self, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
        received_at = format_timestamp(now)
        columns = (
            "occurred_at",
            "event_type",
            "package_name",
            "app_name",
            "title",
            "text",
            "sub_text",
            "category",
            "notification_key",
            "source_device",
            "extra_json",
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO notifications (received_at, {', '.join(columns)}) "
                f"VALUES (?, {', '.join('?' for _ in columns)})",
                (received_at, *(payload[column] for column in columns)),
            )
            notification_id = int(cursor.lastrowid)
            self._prune(connection, now)
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("new notification was pruned unexpectedly")
        return self._serialize(row)

    def _prune(self, connection: sqlite3.Connection, now: datetime) -> None:
        cutoff = format_timestamp(now - timedelta(days=self.retention_days))
        connection.execute("DELETE FROM notifications WHERE received_at < ?", (cutoff,))
        connection.execute(
            """
            DELETE FROM notifications
            WHERE id < COALESCE(
                (SELECT id FROM notifications ORDER BY id DESC LIMIT 1 OFFSET ?),
                0
            )
            """,
            (self.max_rows - 1,),
        )

    def list(
        self,
        limit: int,
        before_id: int | None,
        package_name: str | None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if before_id is not None:
            clauses.append("id < ?")
            parameters.append(before_id)
        if package_name is not None:
            clauses.append("package_name = ?")
            parameters.append(package_name)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM notifications{where} ORDER BY id DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS row_count,
                       MIN(received_at) AS oldest_received_at,
                       MAX(received_at) AS newest_received_at
                FROM notifications
                """
            ).fetchone()
        return dict(row)

    def insert_invite(self, inviter: str, now: datetime) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO mushroom_invites (inviter, received_at) VALUES (?, ?)",
                (inviter, format_timestamp(now)),
            )
            return int(cursor.lastrowid)

    def list_invites(self, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, inviter, received_at
                FROM mushroom_invites
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def invite_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS total_invites,
                       COUNT(DISTINCT inviter) AS unique_inviters
                FROM mushroom_invites
                """
            ).fetchone()
            inviters = connection.execute(
                """
                SELECT
                    inviter,
                    COUNT(*) AS count,
                    MAX(received_at) AS last_invited_at
                FROM mushroom_invites
                GROUP BY inviter
                ORDER BY count DESC, last_invited_at DESC
                """
            ).fetchall()
        return {
            "total_invites": int(totals["total_invites"]),
            "unique_inviters": int(totals["unique_inviters"]),
            "inviters": [dict(row) for row in inviters],
        }

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["extra"] = json.loads(result.pop("extra_json"))
        return result


class NotificationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        store: NotificationStore,
        legacy_ignored_token: str | None = None,
    ) -> None:
        # Keep the optional argument for callers of 0.1.x; authentication is no
        # longer needed because the host port mapping is disabled in config.yaml.
        del legacy_ignored_token
        self.store = store
        super().__init__(address, NotificationHandler)


class NotificationHandler(BaseHTTPRequestHandler):
    server: NotificationServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path)
            if path.path == "/healthz":
                self.send_json(HTTPStatus.OK, {"status": "ok"})
                return
            if path.path == "/api/v1/notifications":
                query = parse_qs(path.query, keep_blank_values=True)
                allowed = {"limit", "before_id", "package_name"}
                if set(query) - allowed:
                    raise ApiError(400, "invalid_query", "unknown query parameter")
                limit = self.parse_positive_int(query, "limit", 100, MAX_QUERY_LIMIT)
                before_id = self.parse_optional_positive_int(query, "before_id")
                package_name = self.single_query_value(query, "package_name")
                if package_name is not None and len(package_name) > TEXT_LIMITS["package_name"]:
                    raise ApiError(400, "invalid_query", "package_name is too long")
                items = self.server.store.list(limit, before_id, package_name)
                next_before_id = items[-1]["id"] if len(items) == limit else None
                self.send_json(
                    HTTPStatus.OK,
                    {"items": items, "next_before_id": next_before_id},
                )
                return
            if path.path == "/api/v1/stats":
                self.send_json(HTTPStatus.OK, self.server.store.stats())
                return
            if path.path == "/api/v1/invites/stats":
                self.send_json(HTTPStatus.OK, self.server.store.invite_stats())
                return
            if path.path == "/api/v1/invites":
                query = parse_qs(path.query, keep_blank_values=True)
                if set(query) - {"limit"}:
                    raise ApiError(400, "invalid_query", "unknown query parameter")
                limit = self.parse_positive_int(query, "limit", 100, MAX_QUERY_LIMIT)
                self.send_json(
                    HTTPStatus.OK,
                    {"invites": self.server.store.list_invites(limit)},
                )
                return
            raise ApiError(404, "not_found", "endpoint not found")
        except ApiError as exc:
            self.send_api_error(exc)
        except Exception:
            LOGGER.exception("Unhandled GET failure")
            self.send_api_error(ApiError(500, "internal_error", "internal server error"))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path)
            if path.path not in {"/api/v1/notifications", "/api/v1/invites"}:
                raise ApiError(404, "not_found", "endpoint not found")
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json")
            raw = self.read_body()
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ApiError(400, "invalid_json", "request body is not valid JSON") from exc
            now = utc_now()
            if path.path == "/api/v1/invites":
                inviter = validate_invite(body)
                invite_id = self.server.store.insert_invite(inviter, now)
                self.send_json(HTTPStatus.CREATED, {"ok": True, "id": invite_id})
                return
            payload = validate_notification(body, now)
            item = self.server.store.insert(payload, now)
            self.send_json(HTTPStatus.CREATED, item)
        except ApiError as exc:
            self.send_api_error(exc)
        except Exception:
            LOGGER.exception("Unhandled POST failure")
            self.send_api_error(ApiError(500, "internal_error", "internal server error"))

    def read_body(self) -> bytes:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ApiError(411, "length_required", "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError(400, "invalid_content_length", "Content-Length is invalid") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large", "request body exceeds 65536 bytes")
        return self.rfile.read(length)

    @staticmethod
    def single_query_value(query: dict[str, list[str]], name: str) -> str | None:
        values = query.get(name)
        if values is None:
            return None
        if len(values) != 1:
            raise ApiError(400, "invalid_query", f"{name} must appear once")
        return values[0]

    def parse_positive_int(
        self,
        query: dict[str, list[str]],
        name: str,
        default: int,
        maximum: int,
    ) -> int:
        value = self.single_query_value(query, name)
        if value is None:
            return default
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ApiError(400, "invalid_query", f"{name} must be an integer") from exc
        if not 1 <= parsed <= maximum:
            raise ApiError(400, "invalid_query", f"{name} must be between 1 and {maximum}")
        return parsed

    def parse_optional_positive_int(
        self, query: dict[str, list[str]], name: str
    ) -> int | None:
        value = self.single_query_value(query, name)
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ApiError(400, "invalid_query", f"{name} must be an integer") from exc
        if parsed < 1:
            raise ApiError(400, "invalid_query", f"{name} must be positive")
        return parsed

    def send_api_error(self, error: ApiError) -> None:
        self.send_json(error.status, {"error_code": error.error_code, "detail": error.detail})

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # Deliberately omit query strings, headers, and notification content.
        LOGGER.info("%s %s -> %s", self.command, urlsplit(self.path).path, code)

    def log_message(self, format: str, *args: Any) -> None:
        return


def load_options(path: Path) -> dict[str, Any]:
    try:
        options = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"options file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("options file is not valid JSON") from exc
    if not isinstance(options, dict):
        raise RuntimeError("options must be a JSON object")
    return options


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = load_options(Path("/data/options.json"))
    try:
        retention_days = int(options.get("retention_days", 30))
        max_rows = int(options.get("max_rows", 100000))
        store = NotificationStore(
            Path("/data/notifications.sqlite3"),
            retention_days=retention_days,
            max_rows=max_rows,
        )
    except (TypeError, ValueError) as exc:
        LOGGER.error("Invalid add-on options: %s", exc)
        return 2

    server = NotificationServer(("0.0.0.0", 8099), store)
    LOGGER.info("Notification Log listening on port 8099")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
