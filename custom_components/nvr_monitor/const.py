"""Constants for NVR Monitor."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "nvr_monitor"

CONF_NVR_HOST = "nvr_host"
CONF_NVR_HTTP_PORT = "nvr_http_port"
CONF_NVR_RTSP_PORT = "nvr_rtsp_port"
CONF_CAMERA_IP = "camera_ip"
CONF_NVR_CHANNEL = "nvr_channel"
CONF_CAMERA_NAME = "camera_name"
CONF_MODEL = "model"
CONF_GROUP = "group"
CONF_ENABLED = "enabled"
CONF_CAMERA_RTSP_PORT = "camera_rtsp_port"
CONF_CAMERA_ONVIF_PORT = "camera_onvif_port"

SUBENTRY_TYPE_CAMERA = "camera"

DEFAULT_NVR_RTSP_PORT = 554
DEFAULT_NVR_HTTP_PORT = 80
DEFAULT_CAMERA_RTSP_PORT = 554
DEFAULT_CAMERA_ONVIF_PORT = 2020

NETWORK_UPDATE_INTERVAL = timedelta(seconds=30)
SERVICE_UPDATE_INTERVAL = timedelta(minutes=5)
RECORDING_UPDATE_INTERVAL = timedelta(minutes=15)
HISTORY_HOURS = 24
HISTORY_SAVE_INTERVAL_UPDATES = 20
SERVICE_HISTORY_SAVE_INTERVAL_UPDATES = 3

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
