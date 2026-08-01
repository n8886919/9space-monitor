"""Regression tests for coordinator aggregation helpers."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest


class _DummyCoordinator:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __class_getitem__(cls, _item):
        return cls


class _DummyUpdateFailed(Exception):
    pass


class _DummyStore:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def async_load(self):
        return None

    async def async_save(self, _data):
        return None


# Stub Home Assistant dependencies so the coordinator module can be imported.
homeassistant = types.ModuleType("homeassistant")
sys.modules["homeassistant"] = homeassistant

config_entries = types.ModuleType("homeassistant.config_entries")
config_entries.ConfigEntry = object
config_entries.ConfigSubentry = object
sys.modules["homeassistant.config_entries"] = config_entries

core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
core.Event = object
core.callback = lambda func: func
sys.modules["homeassistant.core"] = core

helpers = types.ModuleType("homeassistant.helpers")
sys.modules["homeassistant.helpers"] = helpers

storage = types.ModuleType("homeassistant.helpers.storage")
storage.Store = _DummyStore
sys.modules["homeassistant.helpers.storage"] = storage

update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
update_coordinator.DataUpdateCoordinator = _DummyCoordinator
update_coordinator.UpdateFailed = _DummyUpdateFailed
sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator

const = types.ModuleType("homeassistant.const")
const.PERCENTAGE = "%"
const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")
const.UnitOfTime = types.SimpleNamespace(HOURS="h")
const.Platform = types.SimpleNamespace(BINARY_SENSOR="binary_sensor", SENSOR="sensor")
const.CONF_PASSWORD = "password"
const.CONF_USERNAME = "username"
sys.modules["homeassistant.const"] = const

helpers_mod = types.ModuleType("homeassistant.helpers")
helpers_mod.device_registry = types.SimpleNamespace(async_get=lambda hass: None)
sys.modules["homeassistant.helpers"] = helpers_mod

# Stub icmplib used by coordinator imports.
icmplib = types.ModuleType("icmplib")
icmplib.async_ping = None
sys.modules["icmplib"] = icmplib

ROOT = "/home/nolanasd123/9space-monitor"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

custom_components_pkg = types.ModuleType("custom_components")
custom_components_pkg.__path__ = []
sys.modules["custom_components"] = custom_components_pkg

nvr_pkg = types.ModuleType("custom_components.nvr_monitor")
nvr_pkg.__path__ = [f"{ROOT}/custom_components/nvr_monitor"]
sys.modules["custom_components.nvr_monitor"] = nvr_pkg

# Load the coordinator module directly to avoid executing the package __init__.
coordinator_path = f"{ROOT}/custom_components/nvr_monitor/coordinator.py"
spec = importlib.util.spec_from_file_location(
    "custom_components.nvr_monitor.coordinator",
    coordinator_path,
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class AggregationTests(unittest.TestCase):
    def test_network_aggregation_uses_one_hour_window(self) -> None:
        now = 1_700_000_000
        samples = [
            [now - 3700, 1, 120.0, 30.0, 20.0],
            [now - 1800, 1, 80.0, 20.0, 5.0],
            [now - 100, 0, None, None, None],
        ]

        result = module.CameraNetworkCoordinator._aggregate(samples, now)

        self.assertEqual(66.67, result["online_rate_24h"])
        self.assertEqual(1, result["offline_count_24h"])
        self.assertEqual(80.0, result["rtt_avg_1h_ms"])
        self.assertEqual(5.0, result["packet_loss_avg_1h_pct"])
        self.assertEqual(100.0, result["rtt_avg_24h_ms"])
        self.assertEqual(12.5, result["packet_loss_avg_24h_pct"])

    def test_service_live_metrics_count_disconnect_transitions(self) -> None:
        now = 1_700_000_000
        samples = [
            {"ts": now - 3600, "nvr_live_video": True},
            {"ts": now - 1800, "nvr_live_video": False},
            {"ts": now - 100, "nvr_live_video": True},
        ]

        result = module.CameraServiceCoordinator._aggregate_live_metrics(
            samples,
            now,
        )

        self.assertEqual(66.7, result["live_online_rate_24h"])
        self.assertEqual(
            1, result["nvr_live_video_disconnect_count_24h"]
        )


if __name__ == "__main__":
    unittest.main()
