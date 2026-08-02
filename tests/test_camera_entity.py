"""Regression tests for snapshot camera base-class initialization."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CAMERA_PATH = ROOT / "custom_components/nvr_monitor/camera.py"
PACKAGE = "nvr_monitor_camera_entity_test"


class FakeCamera:
    """Minimal non-cooperative Home Assistant Camera stub."""

    def __init__(self) -> None:
        self.camera_init_called = True
        self.access_tokens = ["camera-token"]
        self._webrtc_provider = None


class FakeCoordinatorEntity:
    """Minimal non-cooperative CoordinatorEntity stub."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator) -> None:
        self.coordinator_init_called = True
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class FakeAddonApiError(Exception):
    """Stand-in for the integration API error."""


class FakeSnapshotClient:
    def __init__(self, result: bytes = b"\xff\xd8jpeg") -> None:
        self.result = result
        self.channels: list[int] = []
        self.error: Exception | None = None

    async def async_get_snapshot(self, channel: int) -> bytes:
        self.channels.append(channel)
        if self.error is not None:
            raise self.error
        return self.result


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def _load_camera_module():
    package = _module(PACKAGE)
    package.__path__ = []

    homeassistant = _module("homeassistant")
    components = _module("homeassistant.components")
    camera_component = _module(
        "homeassistant.components.camera", Camera=FakeCamera
    )
    config_entries = _module(
        "homeassistant.config_entries", ConfigSubentry=object
    )
    core = _module("homeassistant.core", HomeAssistant=object)
    helpers = _module("homeassistant.helpers")
    entity_platform = _module(
        "homeassistant.helpers.entity_platform",
        AddConfigEntryEntitiesCallback=object,
    )
    update_coordinator = _module(
        "homeassistant.helpers.update_coordinator",
        CoordinatorEntity=FakeCoordinatorEntity,
    )

    local_modules = {
        PACKAGE: package,
        f"{PACKAGE}.addon_api": _module(
            f"{PACKAGE}.addon_api", AddonApiError=FakeAddonApiError
        ),
        f"{PACKAGE}.coordinator": _module(
            f"{PACKAGE}.coordinator", AddonCoordinator=object
        ),
        f"{PACKAGE}.entity": _module(
            f"{PACKAGE}.entity",
            camera_device_info=lambda entry, subentry, camera: "device-info",
        ),
        f"{PACKAGE}.models": _module(
            f"{PACKAGE}.models",
            CameraConfig=object,
            cameras_from_entry=lambda entry: [],
        ),
    }
    package.NvrMonitorConfigEntry = object

    stubs = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.camera": camera_component,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        **local_modules,
    }
    module_name = f"{PACKAGE}.camera"
    spec = importlib.util.spec_from_file_location(module_name, CAMERA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module


camera_module = _load_camera_module()


class SnapshotCameraEntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = FakeSnapshotClient()
        self.coordinator = types.SimpleNamespace(
            client=self.client,
            data={"camera-subentry": {"snapshot_available": True}},
            last_update_success=True,
        )
        self.entry = types.SimpleNamespace(
            entry_id="entry-id",
            runtime_data=types.SimpleNamespace(addon=self.coordinator),
        )
        self.subentry = types.SimpleNamespace(subentry_id="camera-subentry")
        self.camera_config = types.SimpleNamespace(
            subentry_id="camera-subentry", channel=1
        )

    def _entity(self):
        return camera_module.AddonSnapshotCamera(
            self.entry, self.subentry, self.camera_config
        )

    async def test_initializes_both_non_cooperative_bases(self):
        entity = self._entity()

        self.assertTrue(entity.camera_init_called)
        self.assertTrue(entity.coordinator_init_called)
        self.assertTrue(entity.access_tokens)
        self.assertTrue(hasattr(entity, "_webrtc_provider"))
        self.assertIs(self.coordinator, entity.coordinator)
        self.assertEqual(
            "entry-id_camera-subentry_snapshot", entity._attr_unique_id
        )

    async def test_fetches_one_based_snapshot_on_demand(self):
        entity = self._entity()

        image = await entity.async_camera_image()

        self.assertEqual(b"\xff\xd8jpeg", image)
        self.assertEqual([1], self.client.channels)

    async def test_addon_api_error_returns_none(self):
        entity = self._entity()
        self.client.error = FakeAddonApiError("snapshot_failed")

        self.assertIsNone(await entity.async_camera_image())
        self.assertEqual([1], self.client.channels)


if __name__ == "__main__":
    unittest.main()
