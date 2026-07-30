"""Data models for Nine Space NVR Monitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_CAMERA_IP,
    CONF_CAMERA_NAME,
    CONF_CAMERA_ONVIF_PORT,
    CONF_CAMERA_RTSP_PORT,
    CONF_ENABLED,
    CONF_GROUP,
    CONF_MODEL,
    CONF_NVR_CHANNEL,
    DEFAULT_CAMERA_ONVIF_PORT,
    DEFAULT_CAMERA_RTSP_PORT,
)


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Configuration for one monitored camera."""

    subentry_id: str
    ip: str
    channel: int
    name: str
    model: str
    group: str
    rtsp_port: int
    onvif_port: int

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> CameraConfig:
        """Create a camera configuration from a config subentry."""
        data = subentry.data
        channel = int(data[CONF_NVR_CHANNEL])
        return cls(
            subentry_id=subentry.subentry_id,
            ip=str(data[CONF_CAMERA_IP]),
            channel=channel,
            name=str(data.get(CONF_CAMERA_NAME) or subentry.title or f"CH{channel:02d}"),
            model=str(data.get(CONF_MODEL, "")),
            group=str(data.get(CONF_GROUP, "")),
            rtsp_port=int(data.get(CONF_CAMERA_RTSP_PORT, DEFAULT_CAMERA_RTSP_PORT)),
            onvif_port=int(
                data.get(CONF_CAMERA_ONVIF_PORT, DEFAULT_CAMERA_ONVIF_PORT)
            ),
        )


def cameras_from_entry(entry: ConfigEntry) -> list[CameraConfig]:
    """Return only enabled camera subentries, sorted by NVR channel."""
    cameras = [
        CameraConfig.from_subentry(subentry)
        for subentry in entry.subentries.values()
        if subentry.data.get(CONF_ENABLED, True)
    ]
    return sorted(cameras, key=lambda camera: camera.channel)


ProbeResult: TypeAlias = dict[str, Any]
ProbeResults: TypeAlias = dict[str, ProbeResult]
