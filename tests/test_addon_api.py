"""Fake-session tests for the async local add-on API client."""

from __future__ import annotations

import asyncio
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

    class ClientTimeout:
        def __init__(self, *, total):
            self.total = total

    aiohttp.ClientError = ClientError
    aiohttp.ClientConnectionError = ClientConnectionError
    aiohttp.ContentTypeError = ContentTypeError
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
    "checked_at": "2026-08-01T21:31:00+08:00",
    "error_code": None,
}


class FakeResponse:
    def __init__(self, status=200, *, payload=None, body=b"", content_type="application/json"):
        self.status = status
        self._payload = payload
        self._body = body
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def read(self):
        return self._body


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
    async def test_health_channels_and_one_based_snapshot(self):
        session = FakeSession([
            FakeResponse(payload={"status": "ok"}),
            FakeResponse(payload=[CHANNEL]),
            FakeResponse(body=b"jpeg", content_type="image/jpeg"),
        ])
        client = addon_api.AddonApiClient("http://addon:8000/", session)

        await client.async_get_health()
        channels = await client.async_get_channels()
        image = await client.async_get_snapshot(1)

        self.assertEqual("http://addon:8000", client.base_url)
        self.assertEqual(1, channels[0].channel_id)
        self.assertEqual(b"jpeg", image)
        self.assertTrue(session.urls[-1].endswith("/channels/1/snapshot"))

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

    async def test_snapshot_status_semantics(self):
        for status, error in (
            (404, addon_api.AddonChannelNotFound),
            (503, addon_api.AddonSnapshotUnavailable),
        ):
            with self.subTest(status=status):
                client = addon_api.AddonApiClient(
                    "http://addon:8000", FakeSession([FakeResponse(status=status)])
                )
                with self.assertRaises(error):
                    await client.async_get_snapshot(1)

    def test_rejects_credentials_and_non_http_urls(self):
        for value in ("ftp://addon", "http://user:pass@addon", "addon:8000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                addon_api.normalize_base_url(value)


if __name__ == "__main__":
    unittest.main()
