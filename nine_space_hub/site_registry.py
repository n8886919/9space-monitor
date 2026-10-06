"""Bounded, atomic persistence for validated Hub site registrations."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from .scheduler import SnapshotSite
from .snapshots import validate_camera_id
from .validation import RegistrationValidationError, validate_registration

MAX_REGISTERED_SITES = 32
MAX_REGISTRY_BYTES = 128 * 1024
REGISTRY_VERSION = 1
LOCAL_SNAPSHOT_HOSTNAME = "afa94ae2-9space-snapshot"
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _valid_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_site_registry")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        address = None
    local = (
        parsed.scheme == "http"
        and parsed.hostname == LOCAL_SNAPSHOT_HOSTNAME
        and parsed.port == 8000
    )
    remote = (
        parsed.scheme == "http"
        and address is not None
        and (address in TAILSCALE_V4 or address in TAILSCALE_V6)
        and parsed.port == 8222
    )
    if (
        not (local or remote)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_site_registry")
    return value.rstrip("/")


def _decode_site(raw: object, refresh_seconds: int) -> SnapshotSite:
    expected = {"site_id", "display_name", "base_url", "channels", "concurrency", "timeout_seconds"}
    if not isinstance(raw, dict) or set(raw) not in (expected, expected | {"disabled_channels"}):
        raise ValueError("invalid_site_registry")
    try:
        registration = validate_registration({
            "site_id": raw["site_id"],
            "display_name": raw["display_name"],
            "channels": raw["channels"],
            "concurrency": raw["concurrency"],
            "timeout_seconds": raw["timeout_seconds"],
            "site_ip": None,
        })
    except RegistrationValidationError as exc:
        raise ValueError("invalid_site_registry") from exc
    return SnapshotSite(
        registration.site_id,
        registration.display_name,
        _valid_base_url(raw["base_url"]),
        registration.channels,
        registration.concurrency,
        registration.timeout_seconds,
        refresh_seconds,
    )


def _decode_disabled(raw: dict[str, object], site: SnapshotSite) -> set[tuple[str, int]]:
    disabled = raw.get("disabled_channels", [])
    if (
        not isinstance(disabled, list)
        or len(disabled) > len(site.channels)
        or any(type(channel) is not int or channel not in site.channels for channel in disabled)
        or len(set(disabled)) != len(disabled)
    ):
        raise ValueError("invalid_site_registry")
    return {(site.site_id, channel) for channel in disabled}


def _encode_site(site: SnapshotSite, disabled: set[tuple[str, int]]) -> dict[str, object]:
    return {
        "site_id": site.site_id,
        "display_name": site.display_name,
        "base_url": _valid_base_url(site.base_url),
        "channels": list(site.channels),
        "concurrency": site.concurrency,
        "timeout_seconds": site.timeout_seconds,
        "disabled_channels": sorted(channel for key, channel in disabled if key == site.site_id),
    }


class SiteRegistry:
    """Persist only current site configuration; health and attempts stay in RAM."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._sites: dict[str, SnapshotSite] = {}
        self._disabled: set[tuple[str, int]] = set()

    def load(self, *, refresh_seconds: int) -> tuple[SnapshotSite, ...]:
        try:
            raw_bytes = self.path.read_bytes()
        except FileNotFoundError:
            self._sites = {}
            self._disabled = set()
            return ()
        if len(raw_bytes) > MAX_REGISTRY_BYTES:
            raise ValueError("site_registry_too_large")
        try:
            payload = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_site_registry") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "sites"}
            or payload["version"] != REGISTRY_VERSION
            or not isinstance(payload["sites"], list)
            or len(payload["sites"]) > MAX_REGISTERED_SITES
        ):
            raise ValueError("invalid_site_registry")
        sites = tuple(_decode_site(item, refresh_seconds) for item in payload["sites"])
        if len({site.site_id for site in sites}) != len(sites):
            raise ValueError("invalid_site_registry")
        disabled = set()
        for raw, site in zip(payload["sites"], sites):
            disabled.update(_decode_disabled(raw, site))
        self._sites = {site.site_id: site for site in sites}
        self._disabled = disabled
        return sites

    def disabled_cameras(self) -> set[tuple[str, int]]:
        with self._lock:
            return set(self._disabled)

    def set_camera_enabled(self, site_id: str, camera_id: int, enabled: bool) -> bool:
        """Persist a user choice before publishing it; heartbeat cannot overwrite it."""
        validate_camera_id(camera_id)
        if type(enabled) is not bool:
            raise ValueError("invalid_enabled")
        with self._lock:
            site = self._sites.get(site_id)
            if site is None or camera_id not in site.channels:
                return False
            updated = set(self._disabled)
            if enabled:
                updated.discard((site_id, camera_id))
            else:
                updated.add((site_id, camera_id))
            if updated != self._disabled:
                self._write(self._sites, updated)
                self._disabled = updated
            return True

    def upsert(self, site: SnapshotSite) -> bool:
        with self._lock:
            if site.site_id not in self._sites and len(self._sites) >= MAX_REGISTERED_SITES:
                return False
            updated = dict(self._sites)
            updated[site.site_id] = site
            disabled = {
                key for key in self._disabled
                if key[0] != site.site_id or key[1] in site.channels
            }
            self._write(updated, disabled)
            self._sites = updated
            self._disabled = disabled
            return True

    def _write(self, sites: dict[str, SnapshotSite], disabled: set[tuple[str, int]]) -> None:
        """Atomically replace bounded configuration, called with the registry lock held."""
        payload = {
            "version": REGISTRY_VERSION,
            "sites": [_encode_site(item, disabled) for item in sites.values()],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_REGISTRY_BYTES:
            raise ValueError("site_registry_too_large")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
            ) as handle:
                temporary_name = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
