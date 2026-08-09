"""Constants for 9Space Monitor Hub."""

from datetime import timedelta

DOMAIN = "nine_space_monitor_hub"
CONF_HUB_BASE_URL = "hub_base_url"
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=30)
PLATFORMS = ["camera", "binary_sensor", "sensor"]
