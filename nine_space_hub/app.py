"""FastAPI service for the 9Space Hub Home Assistant app."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .scheduler import SnapshotScheduler, SnapshotSite, load_options
from .snapshots import SnapshotStore, validate_camera_id
from .state import CurrentState
from .validation import MAX_BODY_BYTES, RegistrationValidationError, validate_registration, validate_site_id

OPTIONS_PATH = "/data/options.json"
SNAPSHOT_ROOT = (
    "/data/snapshots"
    if Path(OPTIONS_PATH).exists()
    else "/tmp/9space-hub-snapshots"
)
STATIC_ROOT = Path(__file__).with_name("static")
CONFIG_PATH = Path(__file__).with_name("config.yaml")
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
LOCAL_SNAPSHOT_HOSTNAME = "afa94ae2-9space-snapshot"


def _app_version() -> str:
    """Read the Supervisor app version without adding a YAML runtime dependency."""
    for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if key == "version" and separator:
            version = value.strip().strip('"')
            if version:
                return version
    raise RuntimeError("missing_app_version")


APP_VERSION = _app_version()


def _snapshot_base_from_request(
    request: Request, site_id: str, registered_site_ip: str | None
) -> str:
    """Build the fixed site API origin from a peer IP or Hub MagicDNS suffix."""
    peer = request.client.host if request.client is not None else ""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        address = None
    if address is not None and (address in TAILSCALE_V4 or address in TAILSCALE_V6):
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"http://{host}:8222"

    raw_host = request.headers.get("host", "")
    try:
        parsed = urlsplit(f"//{raw_host}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_snapshot_peer") from None
    labels = hostname.split(".")
    if (
        port != 8765
        or len(labels) < 4
        or labels[-2:] != ["ts", "net"]
        or not all(labels)
    ):
        raise HTTPException(status_code=422, detail="invalid_snapshot_peer")
    if site_id == labels[0]:
        return f"http://{LOCAL_SNAPSHOT_HOSTNAME}:8000"
    if registered_site_ip is None:
        raise HTTPException(status_code=422, detail="invalid_snapshot_peer")
    try:
        site_address = ipaddress.ip_address(registered_site_ip)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_snapshot_peer") from None
    if site_address not in TAILSCALE_V4 and site_address not in TAILSCALE_V6:
        raise HTTPException(status_code=422, detail="invalid_snapshot_peer")
    host = f"[{site_address}]" if site_address.version == 6 else str(site_address)
    return f"http://{host}:8222"


def create_app(
    *,
    sites: tuple[SnapshotSite, ...] | None = None,
    max_stale_seconds: int | None = None,
    snapshot_refresh_seconds: int | None = None,
    snapshots: SnapshotStore | None = None,
    state: CurrentState | None = None,
    run_sync: Callable[..., Awaitable[Any]] | None = None,
) -> FastAPI:
    configured_stale, store_limit, configured_refresh = (
        load_options(OPTIONS_PATH) if sites is None else (120, 1024 * 1024 * 1024, 30)
    )
    active_sites = () if sites is None else sites
    stale_seconds = configured_stale if max_stale_seconds is None else max_stale_seconds
    refresh_seconds = (
        configured_refresh if snapshot_refresh_seconds is None else snapshot_refresh_seconds
    )
    snapshot_store = snapshots or SnapshotStore(SNAPSHOT_ROOT, store_limit_bytes=store_limit)
    current_state = state or CurrentState(active_sites)

    scheduler = SnapshotScheduler(
        active_sites,
        current_state,
        snapshot_store,
        run_sync=run_sync or asyncio.to_thread,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    dashboard_html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8").replace(
        "__APP_VERSION__", APP_VERSION
    )
    app = FastAPI(title="9Space Hub", version=APP_VERSION, lifespan=lifespan)
    app.state.run_sync = run_sync or asyncio.to_thread
    app.state.snapshots = snapshot_store
    app.state.current = current_state
    app.state.scheduler = scheduler
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    async def call_sync(request: Request, function: Callable[..., Any], *args, **kwargs):
        return await request.app.state.run_sync(function, *args, **kwargs)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def dashboard_index() -> HTMLResponse:
        return HTMLResponse(
            dashboard_html,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'self'",
            },
        )

    @app.post("/api/v1/snapshot-sites/register")
    async def register_snapshot_site(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail="application_json_required")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request_body_too_large")
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_content_length") from None
        body = bytearray()
        async for chunk in request.stream():
            if len(chunk) > MAX_BODY_BYTES - len(body):
                raise HTTPException(status_code=413, detail="request_body_too_large")
            body.extend(chunk)
        try:
            registration = validate_registration(json.loads(body))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid_json") from None
        except RegistrationValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        site = SnapshotSite(
            registration.site_id,
            registration.display_name,
            _snapshot_base_from_request(request, registration.site_id, registration.site_ip),
            registration.channels,
            registration.concurrency,
            registration.timeout_seconds,
            refresh_seconds,
        )
        if not request.app.state.current.register(site):
            raise HTTPException(status_code=422, detail="site_limit_reached")
        await request.app.state.scheduler.upsert(site)
        return JSONResponse({"registered": True})

    @app.get("/api/v1/sites")
    async def sites_endpoint(request: Request) -> JSONResponse:
        payload = await call_sync(
            request,
            request.app.state.current.sites,
            request.app.state.snapshots,
            max_stale_seconds=stale_seconds,
        )
        response = JSONResponse({"sites": payload})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/dashboard/summary")
    async def dashboard_summary(request: Request) -> JSONResponse:
        payload = await call_sync(
            request,
            request.app.state.current.sites,
            request.app.state.snapshots,
            max_stale_seconds=stale_seconds,
        )
        usage = await call_sync(request, request.app.state.snapshots.usage)
        response = JSONResponse({"snapshot_store": usage, "sites": payload})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.put("/api/v1/sites/{site_id}/cameras/{camera_id}/enabled")
    async def set_camera_enabled(request: Request, site_id: str, camera_id: int) -> JSONResponse:
        try:
            validate_site_id(site_id)
            validate_camera_id(camera_id)
        except (RegistrationValidationError, ValueError):
            raise HTTPException(status_code=404, detail="camera_not_found") from None
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid_json") from None
        if not isinstance(body, dict) or set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise HTTPException(status_code=422, detail="invalid_enabled")
        changed = await call_sync(
            request,
            request.app.state.current.set_camera_enabled,
            site_id,
            camera_id,
            body["enabled"],
        )
        if not changed:
            raise HTTPException(status_code=404, detail="camera_not_found")
        return JSONResponse({"enabled": body["enabled"]}, headers={"Cache-Control": "no-store"})

    @app.get(
        "/api/v1/sites/{site_id}/cameras/{camera_id}/snapshot",
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def snapshot(request: Request, site_id: str, camera_id: int):
        try:
            validate_site_id(site_id)
            validate_camera_id(camera_id)
        except (RegistrationValidationError, ValueError):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        if not request.app.state.current.has_camera(site_id, camera_id):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        path = await call_sync(
            request,
            request.app.state.snapshots.get,
            site_id,
            camera_id,
            max_stale_seconds=stale_seconds,
        )
        if path is None:
            exists = await call_sync(request, request.app.state.snapshots.has_last_good, site_id, camera_id)
            code = "snapshot_stale" if exists else "snapshot_unavailable"
            return JSONResponse(status_code=503, content={"error_code": code})
        return Response(
            content=await call_sync(request, path.read_bytes),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/api/v1/sites/{site_id}/cameras/{camera_id}/last-good-snapshot",
        include_in_schema=False,
    )
    async def ui_last_good_snapshot(request: Request, site_id: str, camera_id: int):
        try:
            validate_site_id(site_id)
            validate_camera_id(camera_id)
        except (RegistrationValidationError, ValueError):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        content = await call_sync(request, request.app.state.snapshots.read_last_good, site_id, camera_id)
        if content is None:
            return JSONResponse(status_code=503, content={"error_code": "snapshot_unavailable"})
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
