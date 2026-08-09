"""Fake-session tests for the async local add-on API client."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

try:
    import aiohttp
except ModuleNotFoundError:
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientConnectionError(ClientError):
        pass

    class ContentTypeError(ClientError):
        pass

    class ClientPayloadError(ClientError):
        pass

    class ClientTimeout:
        def __init__(self, *, total):
            self.total = total

    aiohttp.ClientError = ClientError
    aiohttp.ClientConnectionError = ClientConnectionError
    aiohttp.ContentTypeError = ContentTypeError
    aiohttp.ClientPayloadError = ClientPayloadError
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ClientSession = object
    sys.modules["aiohttp"] = aiohttp


PATH = Path(__file__).resolve().parents[1] / "custom_components/nvr_monitor/addon_api.py"
SPEC = importlib.util.spec_from_file_location("nvr_monitor_addon_api_test", PATH)
assert SPEC and SPEC.loader
addon_api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = addon_api
SPEC.loader.exec_module(addon_api)


CHANNEL = {
    "channel_id": 1,
    "live_video": True,
    "snapshot_available": True,
    "recording_query_ok": True,
    "recording_recent": True,
    "last_recording": "2026-08-01T21:30:00+08:00",
    "recording_files_24h": 42,
    "recording_coverage_24h": 97.5,
    "daily_online_rate": 92.5,
    "nvr_live_video_disconnect_count_24h": 3,
    "checked_at": "2026-08-01T21:31:00+08:00",
    "error_code": None,
}


class FakeResponse:
    def __init__(
        self,
        status=200,
        *,
        payload=None,
        body=b"",
        content_type="application/json",
        enter_raises: Exception | None = None,
        exit_raises: Exception | None = None,
    ):
        self.status = status
        self._payload = payload
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._enter_raises = enter_raises
        self._exit_raises = exit_raises

    async def __aenter__(self):
        if self._enter_raises is not None:
            raise self._enter_raises
        return self

    async def __aexit__(self, *_args):
        if self._exit_raises is not None:
            raise self._exit_raises
        return None

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def read(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def make_content_type_error() -> Exception:
    try:
        return aiohttp.ContentTypeError(None, ())
    except TypeError:
        return aiohttp.ContentTypeError()


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    async def get(self, url, **_kwargs):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AddonApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_and_channels(self):
        session = FakeSession([
            FakeResponse(payload={"status": "ok"}),
            FakeResponse(payload=[CHANNEL]),
        ])
        client = addon_api.AddonApiClient("http://addon:8000/", session)

        await client.async_get_health()
        channels = await client.async_get_channels()

        self.assertEqual("http://addon:8000", client.base_url)
        self.assertEqual(1, channels[0].channel_id)
        self.assertEqual(42, channels[0].recording_files_24h)
        self.assertEqual(97.5, channels[0].recording_coverage_24h)
        self.assertEqual(92.5, channels[0].daily_online_rate)
        self.assertEqual(3, channels[0].nvr_live_video_disconnect_count_24h)
        self.assertEqual(42, channels[0].as_dict()["recording_files_24h"])

    async def test_optional_aggregate_fields_support_older_addon(self):
        older = {
            key: value
            for key, value in CHANNEL.items()
            if key
            not in {
                "recording_files_24h",
                "recording_coverage_24h",
                "daily_online_rate",
                "nvr_live_video_disconnect_count_24h",
            }
        }
        client = addon_api.AddonApiClient(
            "http://addon:8000", FakeSession([FakeResponse(payload=[older])])
        )
        channel = (await client.async_get_channels())[0]
        self.assertIsNone(channel.recording_files_24h)
        self.assertIsNone(channel.recording_coverage_24h)
        self.assertIsNone(channel.daily_online_rate)
        self.assertIsNone(channel.nvr_live_video_disconnect_count_24h)

    async def test_invalid_aggregate_fields_are_rejected(self):
        invalid_values = (
            ("recording_files_24h", -1),
            ("recording_files_24h", True),
            ("recording_coverage_24h", 101),
            ("daily_online_rate", -0.1),
            ("nvr_live_video_disconnect_count_24h", 1.5),
        )
        for key, value in invalid_values:
            with self.subTest(key=key, value=value):
                client = addon_api.AddonApiClient(
                    "http://addon:8000",
                    FakeSession([FakeResponse(payload=[{**CHANNEL, key: value}])]),
                )
                with self.assertRaises(addon_api.AddonInvalidResponse):
                    await client.async_get_channels()

    async def test_invalid_contract_is_safe_error(self):
        client = addon_api.AddonApiClient(
            "http://addon:8000", FakeSession([FakeResponse(payload=[{"channel_id": 1}])])
        )
        with self.assertRaises(addon_api.AddonInvalidResponse):
            await client.async_get_channels()

    async def test_naive_timestamp_is_invalid(self):
        invalid = {**CHANNEL, "checked_at": "2026-08-01T21:31:00"}
        client = addon_api.AddonApiClient(
            "http://addon:8000", FakeSession([FakeResponse(payload=[invalid])])
        )
        with self.assertRaises(addon_api.AddonInvalidResponse):
            await client.async_get_channels()

    async def test_connection_error_is_safe_error(self):
        client = addon_api.AddonApiClient(
            "http://addon:8000", FakeSession([aiohttp.ClientConnectionError("secret URL")])
        )
        with self.assertRaisesRegex(addon_api.AddonCannotConnect, "addon_unavailable"):
            await client.async_get_health()

    async def test_response_context_enter_client_error_maps_to_cannot_connect(self):
        secret = "http://user:pass@fake-secret-addon/path"
        for api_call, response in (
            (
                lambda client: client.async_get_health(),
                FakeResponse(enter_raises=aiohttp.ClientConnectionError(secret)),
            ),
            (
                lambda client: client.async_get_channels(),
                FakeResponse(enter_raises=aiohttp.ClientConnectionError(secret)),
            ),
        ):
            with self.subTest(api_call=api_call):
                client = addon_api.AddonApiClient("http://addon:8000", FakeSession([response]))
                with self.assertRaises(addon_api.AddonCannotConnect) as ctx:
                    await api_call(client)
                self.assertNotIn(secret, str(ctx.exception))

    async def test_response_context_exit_client_error_maps_to_cannot_connect(self):
        secret = "credential=demo:pass"
        client = addon_api.AddonApiClient(
            "http://addon:8000",
            FakeSession([
                FakeResponse(
                    payload={"status": "ok"},
                    exit_raises=aiohttp.ClientConnectionError(secret),
                )
            ]),
        )
        with self.assertRaises(addon_api.AddonCannotConnect) as ctx:
            await client.async_get_health()
        self.assertNotIn(secret, str(ctx.exception))

    async def test_json_client_error_maps_to_cannot_connect(self):
        secret = "http://fake-secret-addon/healthz"
        for api_call, payload_error in (
            (lambda client: client.async_get_health(), aiohttp.ClientPayloadError(secret)),
            (lambda client: client.async_get_channels(), aiohttp.ClientPayloadError(secret)),
        ):
            with self.subTest(api_call=api_call):
                client = addon_api.AddonApiClient(
                    "http://addon:8000", FakeSession([FakeResponse(payload=payload_error)])
                )
                with self.assertRaises(addon_api.AddonCannotConnect) as ctx:
                    await api_call(client)
                self.assertNotIn(secret, str(ctx.exception))

    async def test_json_invalid_payload_maps_to_invalid_json(self):
        for api_call, payload_error in (
            (lambda client: client.async_get_health(), ValueError("bad json")),
            (lambda client: client.async_get_channels(), ValueError("bad json")),
            (lambda client: client.async_get_health(), make_content_type_error()),
            (lambda client: client.async_get_channels(), make_content_type_error()),
        ):
            with self.subTest(api_call=api_call, payload_error=type(payload_error).__name__):
                client = addon_api.AddonApiClient(
                    "http://addon:8000", FakeSession([FakeResponse(payload=payload_error)])
                )
                with self.assertRaisesRegex(addon_api.AddonInvalidResponse, "invalid_json"):
                    await api_call(client)

    async def test_json_timeout_maps_to_cannot_connect(self):
        for api_call in (
            lambda client: client.async_get_health(),
            lambda client: client.async_get_channels(),
        ):
            with self.subTest(api_call=api_call):
                client = addon_api.AddonApiClient(
                    "http://addon:8000", FakeSession([FakeResponse(payload=TimeoutError("slow"))])
                )
                with self.assertRaisesRegex(addon_api.AddonCannotConnect, "addon_unavailable"):
                    await api_call(client)

    def test_rejects_credentials_and_non_http_urls(self):
        for value in ("ftp://addon", "http://user:pass@addon", "addon:8000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                addon_api.normalize_base_url(value)


if __name__ == "__main__":
    unittest.main()
