"""Constants for NVR Monitor."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "nvr_monitor"

CONF_ADDON_BASE_URL = "addon_base_url"
CONF_CAMERA_IP = "camera_ip"
CONF_NVR_CHANNEL = "nvr_channel"
CONF_CAMERA_NAME = "camera_name"
CONF_MODEL = "model"
CONF_GROUP = "group"
CONF_ENABLED = "enabled"
CONF_CAMERA_RTSP_PORT = "camera_rtsp_port"
CONF_CAMERA_ONVIF_PORT = "camera_onvif_port"
CONF_TELEMETRY_CENTER_URL = "telemetry_center_url"
CONF_TELEMETRY_DISPLAY_NAME = "telemetry_display_name"
CONF_TELEMETRY_MAPPING = "telemetry_mapping"
CONF_TELEMETRY_SITE_ID = "telemetry_site_id"

SUBENTRY_TYPE_CAMERA = "camera"

DEFAULT_CAMERA_RTSP_PORT = 554
DEFAULT_CAMERA_ONVIF_PORT = 2020

ADDON_UPDATE_INTERVAL = timedelta(minutes=5)
SERVICE_UPDATE_INTERVAL = timedelta(minutes=5)
HA_TELEMETRY_INTERVAL = timedelta(minutes=5)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
