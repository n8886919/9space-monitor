"""Memory-only, best-effort NVR telemetry delivery for M5B.

The add-on retains no telemetry history. Outbound batches only live in a
bounded asyncio queue, so a Center outage loses diagnostic metadata without
delaying NVR work or API shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_QUEUE_MAX_BATCHES = 100
MAX_QUEUE_MAX_BATCHES = 100
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_SHUTDOWN_WAIT_SECONDS = 0.1

_SAFE_ERROR_CODES = frozenset(
    {
        "authentication_failed",
        "internal_error",
        "no_video",
        "nvr_unreachable",
        "recording_query_failed",
        "rtsp_timeout",
    }
)
_SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:https?|rtsp)://|(?:password|passwd|secret|credential|authorization|token|api[-_ ]?key)",
    re.IGNORECASE,
)
_FORBIDDEN_WORD_RE = re.compile(
    r"(?:password|passwd|secret|credential|authorization|token|api[-_ ]?key)",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(r"(?:basic\s+|digest\s+)", re.IGNORECASE)
_IPV4_CANDIDATE_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{80,}={0,2}$")
_MAX_CHANNEL_ID = 4096
_RECORDING_INT_METRICS = frozenset(
    {"file_count_24h", "valid_file_count_24h", "invalid_file_count_24h", "gap_count_24h", "page_count"}
)
_RECORDING_NUMBER_METRICS = frozenset(
    {"recording_coverage_24h_pct", "gap_total_seconds_24h", "largest_gap_seconds_24h", "last_recording_age_hours", "query_duration_ms"}
)
_RECORDING_BOOL_METRICS = frozenset({"truncated"})
_HEALTH_INT_LIMITS = {
    "snapshot_max_concurrency": (1, 8),
    "telemetry_queue_depth": (0, MAX_QUEUE_MAX_BATCHES),
    "telemetry_queue_capacity": (1, MAX_QUEUE_MAX_BATCHES),
}
_HEALTH_STATES = frozenset({"running", "stopped"})
_VERSION_RE = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}(?:[-+][A-Za-z0-9.-]{1,32})?$")


class CenterClient(Protocol):
    async def post(self, url: str, payload: dict[str, Any], timeout_seconds: float) -> None: ...


class UrllibCenterClient:
    """Small dependency-free client; errors intentionally carry no payload log."""

    async def post(self, url: str, payload: dict[str, Any], timeout_seconds: float) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        def _send() -> None:
            request = Request(url, data=body, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URL is local add-on config
                if not 200 <= response.status < 300:
                    raise OSError("center_rejected_batch")

        await asyncio.to_thread(_send)


def _timestamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _safe_error(value: object) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_ERROR_CODES else None


def safe_site_metadata(site_id: object, display_name: object) -> tuple[str, str] | None:
    """Fail closed before enqueueing metadata that Center would reject."""
    if (
        not isinstance(site_id, str)
        or not _SITE_ID_RE.fullmatch(site_id)
        or _FORBIDDEN_TEXT_RE.search(site_id)
        or _AUTH_RE.search(site_id)
    ):
        return None
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
        return None
    if (
        _FORBIDDEN_TEXT_RE.search(display_name)
        or _AUTH_RE.search(display_name)
        or _BASE64_RE.fullmatch(display_name.strip())
        or any(ord(char) < 32 for char in display_name)
    ):
        return None
    for word in _IPV4_CANDIDATE_RE.findall(display_name) + _IPV6_CANDIDATE_RE.findall(display_name):
        try:
            ipaddress.ip_address(word)
        except ValueError:
            continue
        return None
    return site_id, display_name.strip()


def safe_center_url(value: object) -> str | None:
    """Allow only the fixed ingest destination; the host may be a tailnet IP."""
    if not isinstance(value, str) or len(value) > 2048 or _FORBIDDEN_WORD_RE.search(value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/api/v1/telemetry"
        or port is not None and not 1 <= port <= 65535
    ):
        return None
    return value


def safe_snapshot_base_url(value: object) -> str | None:
    """Validate the Tailscale origin advertised to Hub without logging it."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname or ""
    try:
        address = ipaddress.ip_address(hostname)
        tailnet_host = address in ipaddress.ip_network("100.64.0.0/10") or address in ipaddress.ip_network(
            "fd7a:115c:a1e0::/48"
        )
    except ValueError:
        tailnet_host = hostname.lower().endswith(".ts.net")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not tailnet_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not 1 <= port <= 65535
    ):
        return None
    return value.rstrip("/")


def telemetry_channel_ids(channel_count: object) -> range:
    """Keep telemetry within Center's channel-id contract without changing local API."""
    try:
        count = int(channel_count)
    except (TypeError, ValueError):
        return range(0)
    if count < 1:
        return range(0)
    return range(1, min(count, _MAX_CHANNEL_ID) + 1)


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64 or _FORBIDDEN_TEXT_RE.search(value):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        if datetime.fromisoformat(normalized).tzinfo is None:
            return None
    except ValueError:
        return None
    return value


def _safe_recording_metrics(value: object) -> dict[str, int | float | bool]:
    """Filter query-derived aggregates before they can reach the queue."""
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, int | float | bool] = {}
    for key, metric in value.items():
        if key in _RECORDING_INT_METRICS and type(metric) is int and 0 <= metric <= 10_000_000:
            safe[key] = metric
        elif key in _RECORDING_NUMBER_METRICS and type(metric) in {int, float}:
            numeric = float(metric)
            upper = 100.0 if key == "recording_coverage_24h_pct" else (86_400.0 if "gap" in key else 3_600_000.0 if key == "query_duration_ms" else 100_000.0)
            if 0.0 <= numeric <= upper:
                safe[key] = metric
        elif key in _RECORDING_BOOL_METRICS and type(metric) is bool:
            safe[key] = metric
    return safe


def _safe_producer_health(value: object) -> dict[str, str | int | bool | None]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, str | int | bool | None] = {}
    version = value.get("source_version")
    if isinstance(version, str) and _VERSION_RE.fullmatch(version):
        safe["source_version"] = version
    for key, (lower, upper) in _HEALTH_INT_LIMITS.items():
        metric = value.get(key)
        if type(metric) is int and lower <= metric <= upper:
            safe[key] = metric
    state = value.get("producer_state")
    if state in _HEALTH_STATES:
        safe["producer_state"] = state
    reachable = value.get("center_reachable")
    if type(reachable) is bool or reachable is None:
        safe["center_reachable"] = reachable
    return safe


class NvrTelemetryModel:
    """Create strictly allowlisted current-state NVR events."""

    def __init__(self) -> None:
        self._sequence = 0

    def _event(
        self, site_id: str, kind: str, channel_id: int | None, now_ms: int, metrics: dict[str, Any]
    ) -> dict[str, Any]:
        self._sequence += 1
        token = f"addon|{site_id}|{kind}|{channel_id}|{now_ms}|{self._sequence}"
        return {
            "event_id": hashlib.sha256(token.encode("ascii")).hexdigest(),
            "timestamp": _timestamp(now_ms),
            "kind": kind,
            "channel_id": channel_id,
            "metrics": metrics,
        }

    def events(
        self,
        site_id: str,
        channel_states: Mapping[int, Mapping[str, Any]],
        *,
        now_ms: int,
        dropped_events: int,
        producer_health: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for channel_id in sorted(channel_states):
            state = channel_states[channel_id]
            live_state = state.get("live", state)
            recording_state = state.get("recording", state)
            live_video = live_state.get("live_video")
            live_metrics: dict[str, Any] = {
                "live_video": live_video if type(live_video) is bool else None,
                "error_code": _safe_error(live_state.get("error_code")),
            }
            events.append(self._event(site_id, "nvr.live", channel_id, now_ms, live_metrics))
            recent = recording_state.get("recording_recent")
            recording_metrics: dict[str, Any] = {
                "recording_query_ok": bool(recording_state.get("recording_query_ok")),
                "recording_recent": recent if type(recent) is bool else None,
                "last_recording": _safe_timestamp(recording_state.get("last_recording")),
                "error_code": _safe_error(recording_state.get("error_code")),
            }
            if recording_metrics["recording_query_ok"]:
                recording_metrics.update(_safe_recording_metrics(recording_state.get("metrics")))
            events.append(
                self._event(
                    site_id,
                    "nvr.recording",
                    channel_id,
                    now_ms,
                    recording_metrics,
                )
            )
        events.append(
            self._event(
                site_id,
                "producer.health",
                None,
                now_ms,
                {
                    "channel_count": len(channel_states),
                    "dropped_events": dropped_events,
                    **_safe_producer_health(producer_health),
                },
            )
        )
        return events


class TelemetryProducer:
    """Bounded best-effort sender that never backpressures its callers."""

    def __init__(
        self,
        *,
        center_url: str,
        site_id: str,
        display_name: str,
        client: CenterClient | None = None,
        queue_max_batches: int = DEFAULT_QUEUE_MAX_BATCHES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        shutdown_wait_seconds: float = DEFAULT_SHUTDOWN_WAIT_SECONDS,
        registration: Mapping[str, Any] | None = None,
    ) -> None:
        self._center_url = center_url
        self._site_id = site_id
        self._display_name = display_name
        self._client = client or UrllibCenterClient()
        self._queue: asyncio.Queue[Sequence[dict[str, Any]]] = asyncio.Queue(
            maxsize=min(MAX_QUEUE_MAX_BATCHES, max(1, queue_max_batches))
        )
        self._timeout_seconds = timeout_seconds
        self._shutdown_wait_seconds = shutdown_wait_seconds
        self._registration = dict(registration) if registration is not None else None
        self._task: asyncio.Task[None] | None = None
        self._stopping_tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self.dropped_events = 0
        self.center_reachable: bool | None = None

    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    def enqueue(self, events: Sequence[dict[str, Any]]) -> bool:
        """Try once only; full queues drop data rather than delaying NVR work."""
        if not events:
            return True
        try:
            self._queue.put_nowait(tuple(events))
        except asyncio.QueueFull:
            self.dropped_events += len(events)
            return False
        return True

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    @property
    def state(self) -> str:
        return "running" if self._task is not None and not self._task.done() else "stopped"

    async def _run(self) -> None:
        while not self._stopping:
            events = await self._queue.get()
            payload = {
                "site_id": self._site_id,
                "display_name": self._display_name,
                "source": "addon",
                "events": list(events),
            }
            if self._registration is not None:
                payload["snapshot_registration"] = self._registration
            try:
                await asyncio.wait_for(
                    self._client.post(self._center_url, payload, self._timeout_seconds),
                    timeout=self._timeout_seconds,
                )
                self.center_reachable = True
            except Exception:  # noqa: BLE001 - Center errors must stay isolated
                self.center_reachable = False
                self.dropped_events += len(events)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        """Cancellation is intentionally time-bounded; no Center I/O blocks shutdown."""
        task = self._task
        if task is None:
            return
        self._stopping = True
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._shutdown_wait_seconds)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if task.done():
                if self._task is task:
                    self._task = None
                return
            # A third-party client that ignores cancellation cannot be killed
            # by asyncio.  Keep its worker referenced until completion rather
            # than dropping it as an untracked orphan task.
            self._stopping_tasks.add(task)

            def _finished(completed: asyncio.Task[None]) -> None:
                self._stopping_tasks.discard(completed)
                if self._task is completed:
                    self._task = None

            task.add_done_callback(_finished)
        else:
            if self._task is task:
                self._task = None
