"""Strict validation for sanitized Center telemetry batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import math
import re
from typing import Any

MAX_BODY_BYTES = 512 * 1024
MAX_BATCH_EVENTS = 500
MAX_METRICS_PER_EVENT = 32
MAX_DISPLAY_NAME_CHARS = 100
MAX_STRING_METRIC_CHARS = 256

_SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_EVENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION_RE = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}(?:[-+][A-Za-z0-9.-]{1,32})?$")
_FULL_URL_RE = re.compile(r"(?:https?|rtsp)://", re.IGNORECASE)
_AUTH_RE = re.compile(r"(?:authorization|basic\s+|digest\s+)", re.IGNORECASE)
_CREDENTIAL_WORD_RE = re.compile(
    r"(?:password|passwd|secret|credential|authorization|token|api[-_ ]?key)",
    re.IGNORECASE,
)

ALLOWED_KINDS = frozenset(
    {
        "ha.fastdotcom",
        "ha.ping",
        "ha.rpi_power",
        "ha.system",
        "nvr.live",
        "nvr.probe",
        "nvr.recording",
        "nvr.snapshot",
        "producer.health",
    }
)
_DATA_IMAGE_RE = re.compile(r"data:image/", re.IGNORECASE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{80,}={0,2}$")
_IPV4_CANDIDATE_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
)

# Additive, deliberately explicit allowlist. New producer metrics require a
# reviewed schema addition instead of accepting arbitrary payloads.
ALLOWED_METRIC_KEYS = frozenset(
    {
        "available",
        "cache_hit",
        "camera_rtsp_alive",
        "camera_rtsp_ms",
        "channel_count",
        "checked_at",
        "describe_status",
        "disconnect_count_24h",
        "dropped_events",
        "error_code",
        "file_count_24h",
        "first_rtp_packet_ms",
        "gap_count_24h",
        "gap_total_seconds_24h",
        "integration_version",
        "invalid_file_count_24h",
        "largest_gap_seconds_24h",
        "last_boot",
        "last_recording",
        "last_recording_age_hours",
        "live_observed_hours_24h",
        "live_online_rate_24h",
        "live_sample_count_24h",
        "live_video",
        "load_15m",
        "load_1m",
        "load_5m",
        "memory_free_mb",
        "memory_used_percent",
        "page_count",
        "packet_loss_percent",
        "play_status",
        "processor_use_percent",
        "probe_duration_ms",
        "query_duration_ms",
        "recording_coverage_24h_pct",
        "recording_query_ok",
        "recording_recent",
        "result_code",
        "rpi_throttled",
        "rtp_packets",
        "rtp_timestamps",
        "rtt_ms",
        "setup_status",
        "snapshot_latency_ms",
        "snapshot_max_concurrency",
        "source_version",
        "telemetry_queue_capacity",
        "telemetry_queue_depth",
        "producer_state",
        "center_reachable",
        "state",
        "storage_free_gb",
        "storage_used_percent",
        "temperature_c",
        "truncated",
        "unit",
        "uptime_seconds",
        "upload_mbps",
        "download_mbps",
        "valid_file_count_24h",
        "voltage_v",
    }
)

_BOOL_METRICS = {
    "available",
    "cache_hit",
    "camera_rtsp_alive",
    "live_video",
    "recording_query_ok",
    "recording_recent",
    "rpi_throttled",
    "truncated",
    "center_reachable",
}
_INT_RANGES = {
    "channel_count": (0, 4096),
    "snapshot_max_concurrency": (1, 8),
    "telemetry_queue_capacity": (1, 100),
    "telemetry_queue_depth": (0, 100),
    "disconnect_count_24h": (0, 1_000_000),
    "dropped_events": (0, 10**12),
    "file_count_24h": (0, 10_000_000),
    "gap_count_24h": (0, 10_000_000),
    "invalid_file_count_24h": (0, 10_000_000),
    "live_sample_count_24h": (0, 10_000_000),
    "page_count": (0, 1_000_000),
    "rtp_packets": (0, 1_000_000),
    "rtp_timestamps": (0, 1_000_000),
    "valid_file_count_24h": (0, 10_000_000),
}
_NUMBER_RANGES = {
    "camera_rtsp_ms": (0.0, 3_600_000.0),
    "download_mbps": (0.0, 1_000_000.0),
    "first_rtp_packet_ms": (0.0, 3_600_000.0),
    "gap_total_seconds_24h": (0.0, 86_400.0),
    "largest_gap_seconds_24h": (0.0, 86_400.0),
    "last_recording_age_hours": (0.0, 100_000.0),
    "live_observed_hours_24h": (0.0, 24.0),
    "live_online_rate_24h": (0.0, 100.0),
    "load_15m": (0.0, 100_000.0),
    "load_1m": (0.0, 100_000.0),
    "load_5m": (0.0, 100_000.0),
    "memory_free_mb": (0.0, 10**9),
    "memory_used_percent": (0.0, 100.0),
    "packet_loss_percent": (0.0, 100.0),
    "processor_use_percent": (0.0, 100.0),
    "probe_duration_ms": (0.0, 3_600_000.0),
    "query_duration_ms": (0.0, 3_600_000.0),
    "recording_coverage_24h_pct": (0.0, 100.0),
    "rtt_ms": (0.0, 3_600_000.0),
    "snapshot_latency_ms": (0.0, 3_600_000.0),
    "storage_free_gb": (0.0, 10**9),
    "storage_used_percent": (0.0, 100.0),
    "temperature_c": (-100.0, 300.0),
    "upload_mbps": (0.0, 1_000_000.0),
    "uptime_seconds": (0.0, 1_000_000_000.0),
    "voltage_v": (0.0, 1000.0),
}
_STATUS_METRICS = {"describe_status", "play_status", "setup_status"}
_TIMESTAMP_METRICS = {"checked_at", "last_boot", "last_recording"}
_CODE_METRICS = {"error_code", "result_code"}
_VERSION_METRICS = {"source_version", "integration_version"}
_STATE_VALUES = frozenset(
    {
        "abnormal",
        "active",
        "connected",
        "disconnected",
        "false",
        "idle",
        "normal",
        "off",
        "ok",
        "on",
        "problem",
        "safe",
        "true",
        "unavailable",
        "unknown",
    }
)
_PRODUCER_STATE_VALUES = frozenset({"running", "stopped"})
_UNIT_VALUES = frozenset(
    {"%", "C", "GB", "MB", "Mbps", "V", "W", "events", "files", "gaps", "hours", "ms", "s"}
)
_NULLABLE_METRICS = _BOOL_METRICS | _TIMESTAMP_METRICS | _CODE_METRICS


class TelemetryValidationError(ValueError):
    """The producer supplied data outside the telemetry contract."""


@dataclass(frozen=True, slots=True)
class ValidatedEvent:
    event_id: str
    timestamp_ms: int
    kind: str
    channel_id: int | None
    metrics: dict[str, bool | int | float | str | None]


@dataclass(frozen=True, slots=True)
class ValidatedBatch:
    site_id: str
    display_name: str
    source: str
    events: tuple[ValidatedEvent, ...]


def _parse_timestamp(value: Any, field: str) -> int:
    if not isinstance(value, str) or len(value) > 64:
        raise TelemetryValidationError(f"invalid_{field}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise TelemetryValidationError(f"invalid_{field}") from None
    if parsed.tzinfo is None:
        raise TelemetryValidationError(f"invalid_{field}")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _looks_like_forbidden_string(value: str) -> bool:
    compact = value.strip()
    if (
        _FULL_URL_RE.search(compact)
        or _AUTH_RE.search(compact)
        or _CREDENTIAL_WORD_RE.search(compact)
        or _DATA_IMAGE_RE.search(compact)
    ):
        return True
    # Reject substantial base64-like blobs. Short identifiers and ordinary
    # state strings remain valid; JPEG/base64 payloads do not.
    if _BASE64_RE.fullmatch(compact):
        return True
    for candidate in _IPV4_CANDIDATE_RE.findall(compact):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return True
    for candidate in _IPV6_CANDIDATE_RE.findall(compact):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return True
    return False


def validate_display_name(value: Any) -> str:
    """Validate Center site display metadata for every persistence path."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_DISPLAY_NAME_CHARS
        or any(ord(char) < 32 for char in value)
        or _looks_like_forbidden_string(value)
    ):
        raise TelemetryValidationError("invalid_display_name")
    return value.strip()


def validate_error_code(value: Any) -> str:
    """Validate sanitized, programmatic error codes outside telemetry ingest."""
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value) or _looks_like_forbidden_string(value):
        raise TelemetryValidationError("invalid_error_code")
    return value


def _validate_metric(key: Any, value: Any) -> bool | int | float | str | None:
    if not isinstance(key, str) or key not in ALLOWED_METRIC_KEYS:
        raise TelemetryValidationError("metric_not_allowlisted")
    if value is None:
        if key in _NULLABLE_METRICS:
            return None
        raise TelemetryValidationError("metric_null_not_allowed")
    if key in _BOOL_METRICS:
        if type(value) is not bool:
            raise TelemetryValidationError("invalid_boolean_metric")
        return value
    if key in _INT_RANGES or key in _STATUS_METRICS:
        if type(value) is not int:
            raise TelemetryValidationError("invalid_integer_metric")
        lower, upper = _INT_RANGES.get(key, (100, 599))
        if not lower <= value <= upper:
            raise TelemetryValidationError("metric_number_out_of_range")
        return value
    if key in _NUMBER_RANGES:
        if type(value) not in {int, float} or not math.isfinite(float(value)):
            raise TelemetryValidationError("invalid_number_metric")
        lower, upper = _NUMBER_RANGES[key]
        if not lower <= float(value) <= upper:
            raise TelemetryValidationError("metric_number_out_of_range")
        return value
    if not isinstance(value, str) or len(value) > MAX_STRING_METRIC_CHARS:
        raise TelemetryValidationError("metric_must_match_schema")
    if _looks_like_forbidden_string(value):
        raise TelemetryValidationError("forbidden_metric_value")
    if key in _TIMESTAMP_METRICS:
        _parse_timestamp(value, key)
    elif key in _CODE_METRICS:
        if not _CODE_RE.fullmatch(value):
            raise TelemetryValidationError("invalid_metric_code")
    elif key in _VERSION_METRICS:
        if not _VERSION_RE.fullmatch(value):
            raise TelemetryValidationError("invalid_version")
    elif key == "state":
        if value not in _STATE_VALUES:
            raise TelemetryValidationError("invalid_state")
    elif key == "producer_state":
        if value not in _PRODUCER_STATE_VALUES:
            raise TelemetryValidationError("invalid_producer_state")
    elif key == "unit":
        if value not in _UNIT_VALUES:
            raise TelemetryValidationError("invalid_unit")
    else:
        raise TelemetryValidationError("metric_schema_missing")
    return value


def validate_batch(payload: Any) -> ValidatedBatch:
    """Validate a parsed JSON batch and return its normalized representation."""
    if not isinstance(payload, dict) or set(payload) != {
        "site_id",
        "display_name",
        "source",
        "events",
    }:
        raise TelemetryValidationError("invalid_batch_contract")
    site_id = payload["site_id"]
    if (
        not isinstance(site_id, str)
        or not _SITE_ID_RE.fullmatch(site_id)
        or _looks_like_forbidden_string(site_id)
    ):
        raise TelemetryValidationError("invalid_site_id")
    display_name = validate_display_name(payload["display_name"])
    source = payload["source"]
    if source not in {"addon", "integration"}:
        raise TelemetryValidationError("invalid_source")
    raw_events = payload["events"]
    if not isinstance(raw_events, list) or not raw_events:
        raise TelemetryValidationError("events_must_be_nonempty_list")
    if len(raw_events) > MAX_BATCH_EVENTS:
        raise TelemetryValidationError("batch_too_large")

    events: list[ValidatedEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict) or set(raw) != {
            "event_id",
            "timestamp",
            "kind",
            "channel_id",
            "metrics",
        }:
            raise TelemetryValidationError("invalid_event_contract")
        event_id = raw["event_id"]
        if (
            not isinstance(event_id, str)
            or not _EVENT_ID_RE.fullmatch(event_id)
            or _looks_like_forbidden_string(event_id)
        ):
            raise TelemetryValidationError("invalid_event_id")
        kind = raw["kind"]
        if kind not in ALLOWED_KINDS:
            raise TelemetryValidationError("invalid_kind")
        channel_id = raw["channel_id"]
        if channel_id is not None and (
            type(channel_id) is not int or not 1 <= channel_id <= 4096
        ):
            raise TelemetryValidationError("invalid_channel_id")
        raw_metrics = raw["metrics"]
        if not isinstance(raw_metrics, dict) or len(raw_metrics) > MAX_METRICS_PER_EVENT:
            raise TelemetryValidationError("invalid_metrics")
        metrics = {
            key: _validate_metric(key, value) for key, value in raw_metrics.items()
        }
        events.append(
            ValidatedEvent(
                event_id=event_id,
                timestamp_ms=_parse_timestamp(raw["timestamp"], "timestamp"),
                kind=kind,
                channel_id=channel_id,
                metrics=metrics,
            )
        )

    return ValidatedBatch(site_id, display_name, source, tuple(events))


def validate_site_id(site_id: str) -> str:
    """Validate a site path/query identifier using the ingest slug rules."""
    if not _SITE_ID_RE.fullmatch(site_id) or _looks_like_forbidden_string(site_id):
        raise TelemetryValidationError("invalid_site_id")
    return site_id


def canonical_metrics_json(metrics: dict[str, Any]) -> str:
    """Encode metrics deterministically for storage and quota accounting."""
    return json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
