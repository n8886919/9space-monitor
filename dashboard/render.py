"""Render a private site mapping to Lovelace YAML or M5C telemetry JSON."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


_ENTITY = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
_SITE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_BAD = re.compile(r"(?:https?|rtsp)://|(?:password|passwd|secret|credential|authorization|token|api[-_ ]?key)|(?:image|jpeg|jpg|snapshot)|\.storage", re.I)
_IP = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_AUTH = re.compile(r"(?:basic\s+|digest\s+)", re.I)
_BASE64 = re.compile(r"^[A-Za-z0-9+/]{80,}={0,2}$")
_NVR_LABELS = {"live_video": "Live video", "recording_recent": "Recording", "last_recording": "Last recording"}
_METRIC_LABELS = {"available": "Reachable", "rtt_ms": "RTT", "packet_loss_percent": "Loss", "processor_use_percent": "CPU", "memory_used_percent": "Memory", "memory_free_mb": "Memory free", "storage_free_gb": "Storage free", "storage_used_percent": "Storage", "load_1m": "Load 1m", "load_5m": "Load 5m", "load_15m": "Load 15m", "temperature_c": "Temperature", "last_boot": "Last boot", "uptime_seconds": "Uptime", "rpi_throttled": "RPi throttled", "voltage_v": "Voltage", "download_mbps": "Download", "upload_mbps": "Upload", "state": "Status"}


def _invalid() -> ValueError:
    return ValueError("invalid mapping")


def _m5c_parse(items: list[dict[str, Any]]) -> bool:
    path = Path(__file__).parents[1] / "custom_components/nvr_monitor/ha_telemetry.py"
    spec = importlib.util.spec_from_file_location("_m5c_mapping", path)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.parse_mapping(items) is not None


def _label(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise _invalid()
    value = value.strip()
    if (_BAD.search(value) or _IP.search(value) or _IPV6.search(value)
            or _AUTH.search(value) or _BASE64.fullmatch(value)
            or any(ord(char) < 32 for char in value)):
        raise _invalid()
    return value


def _entity(value: object) -> str:
    if not isinstance(value, str) or not _ENTITY.fullmatch(value) or _BAD.search(value):
        raise _invalid()
    return value


def validate_mapping(raw: object) -> dict[str, Any]:
    """Return normalized mapping, rejecting unknown keys and unsafe input."""
    if not isinstance(raw, dict) or set(raw) != {"site_id", "display_name", "channels", "diagnostics"}:
        raise _invalid()
    site_id = raw["site_id"]
    if not isinstance(site_id, str) or not _SITE.fullmatch(site_id) or _BAD.search(site_id):
        raise _invalid()
    display_name = _label(raw["display_name"])
    channels, diagnostics = raw["channels"], raw["diagnostics"]
    if not isinstance(channels, list) or not channels or not isinstance(diagnostics, list):
        raise _invalid()
    seen_channel: set[int] = set()
    seen_entity: set[str] = set()
    telemetry: list[dict[str, Any]] = []
    normalized_channels: list[dict[str, Any]] = []
    for channel in channels:
        if not isinstance(channel, dict) or set(channel) != {"channel_id", "label", "nvr_entities", "ping"}:
            raise _invalid()
        channel_id = channel["channel_id"]
        if type(channel_id) is not int or not 1 <= channel_id <= 4096 or channel_id in seen_channel:
            raise _invalid()
        seen_channel.add(channel_id)
        nvr = channel["nvr_entities"]
        if not isinstance(nvr, dict) or set(nvr) != {"live_video", "recording_recent", "last_recording"}:
            raise _invalid()
        nvr = {key: _entity(value) for key, value in nvr.items()}
        if len(set(nvr.values())) != len(nvr) or any(value in seen_entity for value in nvr.values()):
            raise _invalid()
        seen_entity.update(nvr.values())
        ping = channel["ping"]
        if not isinstance(ping, list) or not ping:
            raise _invalid()
        for item in ping:
            if not isinstance(item, dict) or set(item) != {"entity_id", "kind", "metric", "unit", "channel_id"}:
                raise _invalid()
            if item.get("kind") != "ha.ping" or item.get("channel_id") != channel_id:
                raise _invalid()
            entity_id = _entity(item["entity_id"])
            if entity_id in seen_entity:
                raise _invalid()
            seen_entity.add(entity_id)
            telemetry.append(dict(item))
        normalized_channels.append({"channel_id": channel_id, "label": _label(channel["label"]), "nvr_entities": nvr, "ping": [dict(item) for item in ping]})
    normalized_diagnostics: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict) or set(item) != {"entity_id", "kind", "metric", "unit", "channel_id"}:
            raise _invalid()
        if item.get("kind") == "ha.ping" or item.get("channel_id") is not None:
            raise _invalid()
        entity_id = _entity(item["entity_id"])
        if entity_id in seen_entity:
            raise _invalid()
        seen_entity.add(entity_id)
        telemetry.append(dict(item))
        normalized_diagnostics.append(dict(item))
    if not _m5c_parse(telemetry):
        raise _invalid()
    telemetry.sort(key=lambda item: (item["kind"], item["metric"], item["channel_id"] or 0))
    normalized_diagnostics.sort(key=lambda item: (item["kind"], item["metric"], item["channel_id"] or 0))
    return {"site_id": site_id, "display_name": display_name, "channels": sorted(normalized_channels, key=lambda item: item["channel_id"]), "diagnostics": normalized_diagnostics, "telemetry": telemetry}


def telemetry_json(raw: object) -> str:
    return json.dumps(validate_mapping(raw)["telemetry"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def lovelace_yaml(raw: object) -> str:
    mapping = validate_mapping(raw)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ["title: " + quote(mapping["display_name"]), "views:", "  - title: " + quote(mapping["display_name"]), "    path: " + quote(mapping["site_id"]), "    cards:"]
    nvr_rows = [(f'{channel["label"]} - {_NVR_LABELS[key]}', channel["nvr_entities"][key]) for channel in mapping["channels"] for key in ("live_video", "recording_recent", "last_recording")]
    ping_rows = [(f'{channel["label"]} - {_METRIC_LABELS[item["metric"]]}', item["entity_id"]) for channel in mapping["channels"] for item in sorted(channel["ping"], key=lambda item: (item["kind"], item["metric"], item["channel_id"]))]
    diagnostic_rows = [(_METRIC_LABELS[item["metric"]], item["entity_id"]) for item in mapping["diagnostics"]]
    groups = [("NVR / Recording", nvr_rows), ("Ping / Network", ping_rows), ("Diagnostics", diagnostic_rows)]
    for title, entities in groups:
        lines += ["      - type: entities", "        title: " + quote(title), "        entities:"]
        for label, entity_id in entities:
            lines += ["          - entity: " + quote(entity_id), "            name: " + quote(label or entity_id)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a private 9Space site mapping")
    parser.add_argument("mapping", type=Path)
    parser.add_argument("--format", choices=("lovelace", "telemetry"), default="lovelace")
    args = parser.parse_args()
    try:
        raw = json.loads(args.mapping.read_text(encoding="utf-8"))
        sys.stdout.write(telemetry_json(raw) if args.format == "telemetry" else lovelace_yaml(raw))
    except (OSError, ValueError, json.JSONDecodeError):
        print("invalid mapping", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
