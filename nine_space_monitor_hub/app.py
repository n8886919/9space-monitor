"""FastAPI service for the 9Space Monitor Hub Home Assistant add-on."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .scheduler import SnapshotScheduler, SnapshotSite, load_options
from .snapshots import SnapshotStore, validate_camera_id
from .state import CurrentState
from .validation import MAX_BODY_BYTES, TelemetryValidationError, validate_batch, validate_site_id

OPTIONS_PATH = "/data/options.json"
SNAPSHOT_ROOT = (
    "/data/snapshots"
    if Path(OPTIONS_PATH).exists()
    else "/tmp/9space-monitor-hub-snapshots"
)
STATIC_ROOT = Path(__file__).with_name("static")


def create_app(
    *,
    sites: tuple[SnapshotSite, ...] | None = None,
    max_stale_seconds: int | None = None,
    snapshots: SnapshotStore | None = None,
    state: CurrentState | None = None,
    run_sync: Callable[..., Awaitable[Any]] | None = None,
) -> FastAPI:
    configured_sites, configured_stale, store_limit = (
        load_options(OPTIONS_PATH) if sites is None else (sites, 120, 1024 * 1024 * 1024)
    )
    active_sites = configured_sites if sites is None else sites
    stale_seconds = configured_stale if max_stale_seconds is None else max_stale_seconds
    snapshot_store = snapshots or SnapshotStore(SNAPSHOT_ROOT, store_limit_bytes=store_limit)
    current_state = state or CurrentState(active_sites)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler = SnapshotScheduler(
            active_sites,
            current_state,
            snapshot_store,
            run_sync=app.state.run_sync,
        )
        app.state.scheduler = scheduler
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="9Space Monitor Hub", version="0.1.0", lifespan=lifespan)
    app.state.run_sync = run_sync or asyncio.to_thread
    app.state.snapshots = snapshot_store
    app.state.current = current_state
    app.state.scheduler = None
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    async def call_sync(request: Request, function: Callable[..., Any], *args, **kwargs):
        return await request.app.state.run_sync(function, *args, **kwargs)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def dashboard_index() -> FileResponse:
        return FileResponse(
            STATIC_ROOT / "index.html",
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'self'",
            },
        )

    @app.post("/api/v1/telemetry")
    async def ingest(request: Request) -> JSONResponse:
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
            batch = validate_batch(json.loads(body))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid_json") from None
        except TelemetryValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        try:
            accepted = request.app.state.current.ingest(batch)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown_site") from None
        return JSONResponse({"accepted": accepted})

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

    @app.get(
        "/api/v1/sites/{site_id}/cameras/{camera_id}/snapshot",
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def snapshot(request: Request, site_id: str, camera_id: int):
        try:
            validate_site_id(site_id)
            validate_camera_id(camera_id)
        except (TelemetryValidationError, ValueError):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        configured = {site.site_id: site for site in active_sites}
        if site_id not in configured or camera_id not in configured[site_id].channels:
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
        except (TelemetryValidationError, ValueError):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        content = await call_sync(request, request.app.state.snapshots.read_last_good, site_id, camera_id)
        if content is None:
            return JSONResponse(status_code=503, content={"error_code": "snapshot_unavailable"})
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
