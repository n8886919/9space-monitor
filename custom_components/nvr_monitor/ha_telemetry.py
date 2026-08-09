"""Memory-only, allowlisted Home Assistant telemetry producer for M5C."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlsplit


RING_WINDOW_MS = 24 * 60 * 60 * 1000
DEFAULT_QUEUE_MAX_BATCHES = 100
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_SHUTDOWN_WAIT_SECONDS = 0.1
MAX_MAPPING_ITEMS = 64
SAMPLE_INTERVAL_MS = 5 * 60 * 1000
RING_MAX_EVENTS = (RING_WINDOW_MS // SAMPLE_INTERVAL_MS + 1) * (MAX_MAPPING_ITEMS + 1)
_SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
_FORBIDDEN_RE = re.compile(r"(?:https?|rtsp)://|(?:password|passwd|secret|credential|authorization|token|api[-_ ]?key)", re.I)
_AUTH_RE = re.compile(r"(?:basic\s+|digest\s+)", re.I)
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_IPV6_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{80,}={0,2}$")

# Legacy local Ping entries remain parseable during an upgrade, but are
# intentionally filtered before a Center producer is built.
_LOCAL_ONLY_MAPPING_SCHEMA: dict[tuple[str, str], str | None] = {
    ("ha.ping", "available"): None,
    ("ha.ping", "state"): None,
    ("ha.ping", "rtt_ms"): "ms",
    ("ha.ping", "packet_loss_percent"): "%",
}

# (kind, metric): required unit, or None for boolean/state values.
_CENTER_MAPPING_SCHEMA: dict[tuple[str, str], str | None] = {
    ("ha.system", "storage_free_gb"): "GB",
    ("ha.system", "storage_used_percent"): "%",
    ("ha.system", "memory_free_mb"): "MB",
    ("ha.system", "memory_used_percent"): "%",
    ("ha.system", "load_1m"): None,
    ("ha.system", "load_5m"): None,
    ("ha.system", "load_15m"): None,
    ("ha.system", "temperature_c"): "C",
    ("ha.system", "processor_use_percent"): "%",
    ("ha.system", "last_boot"): None,
    ("ha.system", "uptime_seconds"): "s",
    ("ha.rpi_power", "rpi_throttled"): None,
    ("ha.rpi_power", "voltage_v"): "V",
    ("ha.rpi_power", "state"): None,
    ("ha.fastdotcom", "download_mbps"): "Mbps",
    ("ha.fastdotcom", "upload_mbps"): "Mbps",
}
_BOOL_METRICS = {"available", "rpi_throttled"}
_STATE_VALUES = {"abnormal", "active", "connected", "disconnected", "false", "idle", "normal", "off", "ok", "on", "problem", "safe", "true"}
_MISSING = object()
_NUMBER_RANGES = {
    "download_mbps": (0.0, 1_000_000.0), "load_15m": (0.0, 100_000.0),
    "load_1m": (0.0, 100_000.0), "load_5m": (0.0, 100_000.0),
    "memory_free_mb": (0.0, 1_000_000_000.0), "memory_used_percent": (0.0, 100.0),
    "processor_use_percent": (0.0, 100.0),
    "storage_free_gb": (0.0, 1_000_000_000.0),
    "storage_used_percent": (0.0, 100.0), "temperature_c": (-100.0, 300.0),
    "upload_mbps": (0.0, 1_000_000.0), "uptime_seconds": (0.0, 1_000_000_000.0),
    "voltage_v": (0.0, 1000.0),
}


class CenterClient(Protocol):
    async def post(self, url: str, payload: dict[str, Any], timeout_seconds: float) -> None: ...


class AiohttpCenterClient:
    """Use Home Assistant's shared aiohttp session without logging payloads."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def post(self, url: str, payload: dict[str, Any], timeout_seconds: float) -> None:
        async with self._session.post(url, json=payload, timeout=timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise OSError("center_rejected_batch")


@dataclass(frozen=True, slots=True)
class MappingItem:
    entity_id: str
    kind: str
    metric: str
    unit: str | None
    channel_id: int | None


@dataclass(frozen=True, slots=True)
class _RingEvent:
    timestamp_ms: int
    event: dict[str, Any]


def safe_site_metadata(site_id: object, display_name: object) -> tuple[str, str] | None:
    if not isinstance(site_id, str) or not _SITE_ID_RE.fullmatch(site_id):
        return None
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
        return None
    if (_FORBIDDEN_RE.search(site_id) or _FORBIDDEN_RE.search(display_name)
            or _AUTH_RE.search(site_id) or _AUTH_RE.search(display_name)
            or _BASE64_RE.fullmatch(display_name.strip())
            or any(ord(char) < 32 for char in display_name)):
        return None
    for candidate in _IPV4_RE.findall(display_name) + _IPV6_RE.findall(display_name):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return None
    return site_id, display_name.strip()


def safe_center_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or _FORBIDDEN_RE.search(value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path != "/api/v1/telemetry" or (port is not None and not 1 <= port <= 65535)):
        return None
    return value


def parse_mapping(value: object) -> tuple[MappingItem, ...] | None:
    """Validate explicit UI mapping; entity IDs never leave this runtime object."""
    if not isinstance(value, list) or not value or len(value) > MAX_MAPPING_ITEMS:
        return None
    items: list[MappingItem] = []
    seen: set[tuple[str, str, int | None]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"entity_id", "kind", "metric", "unit", "channel_id"}:
            return None
        entity_id, kind, metric, unit, channel_id = (raw.get(key) for key in ("entity_id", "kind", "metric", "unit", "channel_id"))
        if not isinstance(entity_id, str) or not _ENTITY_ID_RE.fullmatch(entity_id) or _FORBIDDEN_RE.search(entity_id):
            return None
        schema = (
            _LOCAL_ONLY_MAPPING_SCHEMA
            if kind == "ha.ping"
            else _CENTER_MAPPING_SCHEMA
        )
        required_unit = schema.get((kind, metric), _MISSING) if isinstance(kind, str) and isinstance(metric, str) else _MISSING
        if required_unit is _MISSING or unit != required_unit:
            return None
        if kind == "ha.ping":
            if type(channel_id) is not int or not 1 <= channel_id <= 4096:
                return None
        elif channel_id is not None:
            return None
        key = (kind, metric, channel_id)
        if key in seen:
            return None
        seen.add(key)
        if kind == "ha.ping":
            continue
        items.append(MappingItem(entity_id, kind, metric, unit, channel_id))
    return tuple(items)


def parse_mapping_json(value: object) -> tuple[MappingItem, ...] | None:
    if not isinstance(value, str) or len(value) > 16_384:
        return None
    try:
        return parse_mapping(json.loads(value))
    except (TypeError, json.JSONDecodeError):
        return None


def _timestamp(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat()


def _value(metric: str, raw: object) -> bool | float | str | None:
    if raw is None or str(raw).lower() in {"unknown", "unavailable", "none"}:
        return None
    if metric in _BOOL_METRICS:
        if type(raw) is bool:
            return raw
        text = str(raw).lower()
        return {"on": True, "true": True, "off": False, "false": False}.get(text)
    if metric == "state":
        text = str(raw).lower()
        return text if text in _STATE_VALUES else None
    if metric == "last_boot":
        if not isinstance(raw, str) or len(raw) > 64:
            return None
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            if datetime.fromisoformat(normalized).tzinfo is None:
                return None
        except ValueError:
            return None
        return raw
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    lower, upper = _NUMBER_RANGES[metric]
    return number if math.isfinite(number) and lower <= number <= upper else None


class HATelemetryProducer:
    """Bounded RAM producer; sampling never awaits Center network I/O."""

    def __init__(self, *, center_url: str, site_id: str, display_name: str, mapping: Sequence[MappingItem], client: CenterClient,
                 queue_max_batches: int = DEFAULT_QUEUE_MAX_BATCHES, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 shutdown_wait_seconds: float = DEFAULT_SHUTDOWN_WAIT_SECONDS) -> None:
        self._center_url, self._site_id, self._display_name = center_url, site_id, display_name
        self._mapping, self._client = tuple(mapping), client
        self._queue: asyncio.Queue[tuple[dict[str, Any], ...]] = asyncio.Queue(maxsize=max(1, min(100, queue_max_batches)))
        self._ring: deque[_RingEvent] = deque(maxlen=RING_MAX_EVENTS)
        self._timeout_seconds, self._shutdown_wait_seconds = timeout_seconds, shutdown_wait_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping_tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._sequence = 0
        self.dropped_events = 0

    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    def sample(self, state_getter: Callable[[str], object], *, now_ms: int) -> bool:
        events: list[dict[str, Any]] = []
        cutoff = now_ms - RING_WINDOW_MS
        while self._ring and self._ring[0].timestamp_ms < cutoff:
            self._ring.popleft()
        for index, item in enumerate(self._mapping):
            state = state_getter(item.entity_id)
            raw = getattr(state, "state", state)
            value = _value(item.metric, raw)
            if value is None:
                continue
            self._sequence += 1
            event = {"event_id": hashlib.sha256(f"ha|{self._site_id}|{item.kind}|{item.metric}|{now_ms}|{index}|{self._sequence}".encode()).hexdigest(),
                     "timestamp": _timestamp(now_ms), "kind": item.kind, "channel_id": item.channel_id,
                     "metrics": {item.metric: value, **({"unit": item.unit} if item.unit else {})}}
            self._ring.append(_RingEvent(now_ms, event))
            events.append(event)
        self._sequence += 1
        health = {
            "event_id": hashlib.sha256(f"ha|{self._site_id}|producer.health|{now_ms}|{self._sequence}".encode()).hexdigest(),
            "timestamp": _timestamp(now_ms), "kind": "producer.health", "channel_id": None,
            "metrics": {"dropped_events": self.dropped_events},
        }
        self._ring.append(_RingEvent(now_ms, health))
        events.append(health)
        try:
            self._queue.put_nowait(tuple(events))
        except asyncio.QueueFull:
            self.dropped_events += len(events)
            return False
        return True

    async def _run(self) -> None:
        while not self._stopping:
            events = await self._queue.get()
            try:
                await asyncio.wait_for(self._client.post(self._center_url, {"site_id": self._site_id, "display_name": self._display_name, "source": "integration", "events": list(events)}, self._timeout_seconds), self._timeout_seconds)
            except Exception:  # Center failure is intentionally isolated from HA lifecycle.
                self.dropped_events += len(events)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stopping = True
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), self._shutdown_wait_seconds)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if not task.done():
                self._stopping_tasks.add(task)

                def _finished(completed: asyncio.Task[None]) -> None:
                    self._stopping_tasks.discard(completed)
                    if self._task is completed:
                        self._task = None

                task.add_done_callback(_finished)
                return
        if task.done() and self._task is task:
            self._task = None


async def async_finalize_unload(
    producer: HATelemetryProducer | None,
    unsubscribe: Callable[[], None] | None,
    *,
    platforms_unloaded: bool,
) -> None:
    """Only stop telemetry after Home Assistant confirms entry unload."""
    if not platforms_unloaded:
        return
    if unsubscribe is not None:
        unsubscribe()
    if producer is not None:
        await producer.stop()


def build_producer(config: Mapping[str, object], client: CenterClient) -> HATelemetryProducer | None:
    metadata = safe_site_metadata(config.get("telemetry_site_id"), config.get("telemetry_display_name"))
    url = safe_center_url(config.get("telemetry_center_url"))
    mapping = parse_mapping_json(config.get("telemetry_mapping"))
    if metadata is None or url is None or not mapping:
        return None
    return HATelemetryProducer(center_url=url, site_id=metadata[0], display_name=metadata[1], mapping=mapping, client=client)
