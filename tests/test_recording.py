"""Recording-query error reporting tests."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "nvr_monitor_test"


@dataclass(frozen=True)
class CameraConfig:
    """Minimal camera config used by recording.py."""

    subentry_id: str = "camera-1"
    channel: int = 1


def _load_recording_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = []
    sys.modules[PACKAGE] = package

    api = types.ModuleType(f"{PACKAGE}.api")
    api.NvrConfig = object
    sys.modules[api.__name__] = api

    models = types.ModuleType(f"{PACKAGE}.models")
    models.CameraConfig = CameraConfig
    models.ProbeResults = dict
    sys.modules[models.__name__] = models

    path = ROOT / "custom_components" / "nvr_monitor" / "recording.py"
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.recording", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recording = _load_recording_module()


class RecordingErrorTests(unittest.TestCase):
    """Ensure HTTP failures retain the Dahua CGI stage."""

    def _probe_error(self, failing_action: str, status: int = 400) -> str:
        client = object.__new__(recording.DahuaRecordingClient)

        def fake_get(_path, params):
            action = dict(params)["action"]
            if action == failing_action:
                raise HTTPError(
                    url="http://nvr/cgi-bin/mediaFileFind.cgi",
                    code=status,
                    msg="error",
                    hdrs=None,
                    fp=None,
                )
            if action == "factory.create":
                return "result=123"
            if action == "findFile":
                return "OK"
            if action == "findNextFile":
                return "found=0"
            return "OK"

        client._get = fake_get
        _, result = client._probe_one(CameraConfig())
        return result["recording_error"]

    def test_factory_create_http_error_has_stage(self) -> None:
        self.assertEqual(
            "factory_create_http_400",
            self._probe_error("factory.create"),
        )

    def test_find_file_http_error_has_stage(self) -> None:
        self.assertEqual(
            "find_file_http_400",
            self._probe_error("findFile"),
        )

    def test_find_next_file_http_error_has_stage(self) -> None:
        self.assertEqual(
            "find_next_file_http_400",
            self._probe_error("findNextFile"),
        )

    def test_unauthorized_remains_invalid_auth(self) -> None:
        self.assertEqual(
            "invalid_auth",
            self._probe_error("findNextFile", status=401),
        )


if __name__ == "__main__":
    unittest.main()
