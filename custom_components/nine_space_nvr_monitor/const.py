"""Constants for 9Space NVR Monitor."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "nine_space_nvr_monitor"

ADDON_BASE_URL = "http://afa94ae2-9space-snapshot:8000"
CONF_CAMERA_IP = "camera_ip"
CONF_NVR_CHANNEL = "nvr_channel"
CONF_CAMERA_NAME = "camera_name"
CONF_MODEL = "model"
CONF_GROUP = "group"
CONF_ENABLED = "enabled"
CONF_CAMERA_RTSP_PORT = "camera_rtsp_port"
CONF_CAMERA_ONVIF_PORT = "camera_onvif_port"

SUBENTRY_TYPE_CAMERA = "camera"

DEFAULT_CAMERA_RTSP_PORT = 554
DEFAULT_CAMERA_ONVIF_PORT = 2020

ADDON_UPDATE_INTERVAL = timedelta(minutes=5)
SERVICE_UPDATE_INTERVAL = timedelta(minutes=5)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
