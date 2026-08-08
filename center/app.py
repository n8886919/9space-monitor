"""FastAPI entry point for the 9Space Center telemetry service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .snapshots import DEFAULT_STORE_LIMIT_BYTES, SnapshotStore, validate_camera_id
from .scheduler import SnapshotScheduler, load_sites
from .storage import CapacityExceeded, InvalidEventTimestamp, TelemetryStorage
from .validation import (
    MAX_BODY_BYTES,
    TelemetryValidationError,
    validate_batch,
    validate_site_id,
)

DATABASE_PATH = os.environ.get("CENTER_DATABASE_PATH", "/data/telemetry.sqlite3")
SNAPSHOT_ROOT = os.environ.get("CENTER_SNAPSHOT_ROOT", "/data/snapshots")
MAX_STALE_SECONDS = int(os.environ.get("CENTER_MAX_STALE_SECONDS", "120"))
SNAPSHOT_STORE_LIMIT_BYTES = int(
    os.environ.get("CENTER_SNAPSHOT_STORE_LIMIT_BYTES", str(DEFAULT_STORE_LIMIT_BYTES))
)
SNAPSHOT_SITES_PATH = os.environ.get("CENTER_SNAPSHOT_SITES_PATH", "/data/snapshot-sites.json")
STATIC_ROOT = Path(__file__).with_name("static")
if MAX_STALE_SECONDS < 0:
    raise ValueError("CENTER_MAX_STALE_SECONDS_must_be_nonnegative")
RETENTION_PRUNE_INTERVAL_SECONDS = 3600


async def _retention_worker(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(RETENTION_PRUNE_INTERVAL_SECONDS)
        storage: TelemetryStorage = app.state.storage
        await asyncio.to_thread(storage.prune)


def create_app(
    storage: TelemetryStorage | None = None,
    snapshots: SnapshotStore | None = None,
    run_sync: Callable[..., Awaitable[Any]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.storage is None:
            app.state.storage = TelemetryStorage(DATABASE_PATH)
        await asyncio.to_thread(app.state.storage.prune)
        sites = await asyncio.to_thread(load_sites, SNAPSHOT_SITES_PATH)
        scheduler = SnapshotScheduler(
            sites, app.state.storage,
            app.state.snapshots or SnapshotStore(SNAPSHOT_ROOT, store_limit_bytes=SNAPSHOT_STORE_LIMIT_BYTES),
            run_sync=app.state.run_sync,
        )
        app.state.snapshots = scheduler.snapshots
        app.state.scheduler = scheduler
        await scheduler.start()
        task = asyncio.create_task(_retention_worker(app))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await scheduler.stop()

    app = FastAPI(title="9Space Center", version="0.2.0", lifespan=lifespan)
    app.state.storage = storage
    app.state.snapshots = snapshots
    app.state.scheduler = None
    # Production defaults to a worker thread so SQLite never blocks the
    # event loop. Tests may inject an immediate async runner to avoid the
    # host sandbox's known executor-shutdown hang.
    app.state.run_sync = run_sync or asyncio.to_thread
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def get_storage(request: Request) -> TelemetryStorage:
        result = request.app.state.storage
        if result is None:
            # Primarily useful for ASGI clients that deliberately skip
            # lifespan. Production uvicorn always runs lifespan startup.
            result = TelemetryStorage(DATABASE_PATH)
            request.app.state.storage = result
        return result

    def get_snapshots(request: Request) -> SnapshotStore:
        result = request.app.state.snapshots
        if result is None:
            result = SnapshotStore(SNAPSHOT_ROOT, store_limit_bytes=SNAPSHOT_STORE_LIMIT_BYTES)
            request.app.state.snapshots = result
        return result

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
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request_body_too_large")
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_content_length") from None
        body = bytearray()
        async for chunk in request.stream():
            # Check before copying. A chunked request without Content-Length
            # must not make our accumulator allocate beyond MAX_BODY_BYTES.
            remaining = MAX_BODY_BYTES - len(body)
            if len(chunk) > remaining:
                raise HTTPException(status_code=413, detail="request_body_too_large")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid_json") from None
        try:
            batch = validate_batch(payload)
        except TelemetryValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        try:
            result = await call_sync(request, get_storage(request).ingest, batch)
        except CapacityExceeded:
            raise HTTPException(status_code=507, detail="capacity_limit_exceeded") from None
        except InvalidEventTimestamp:
            raise HTTPException(status_code=422, detail="event_timestamp_too_far_in_future") from None
        return JSONResponse(
            {
                "inserted": result.inserted,
                "duplicates": result.duplicates,
                "expired": result.expired,
                "capacity_pruned": result.capacity_pruned,
            }
        )

    @app.get("/api/v1/sites")
    async def sites(request: Request) -> dict:
        return await call_sync(request, get_storage(request).usage)

    @app.get("/api/v1/sites/{site_id}/events")
    async def events(
        request: Request,
        site_id: str,
        after_cursor: int = Query(default=0, ge=0),
        kind: str | None = None,
        channel_id: int | None = Query(default=None, ge=1, le=4096),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> dict:
        try:
            validate_site_id(site_id)
        except TelemetryValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        rows = await call_sync(
            request,
            get_storage(request).query,
            site_id,
            after_cursor=after_cursor,
            kind=kind,
            channel_id=channel_id,
            limit=limit,
        )
        return {
            "events": rows,
            "next_cursor": rows[-1]["cursor"] if rows else after_cursor,
        }

    @app.get("/api/v1/sites/{site_id}/latest")
    async def latest(request: Request, site_id: str) -> dict:
        try:
            validate_site_id(site_id)
        except TelemetryValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        return {
            "site_id": site_id,
            "events": await call_sync(request, get_storage(request).latest, site_id),
        }

    @app.get("/api/v1/dashboard/summary")
    async def dashboard_summary(request: Request) -> JSONResponse:
        """Aggregate already-sanitized metadata for the local static UI."""
        now_ms = int(time.time() * 1000)
        storage = get_storage(request)
        snapshot_store = get_snapshots(request)
        usage = await call_sync(request, storage.usage)
        snapshot_usage = await call_sync(request, snapshot_store.usage)
        sites: list[dict[str, Any]] = []
        for usage_site in usage["sites"]:
            site_id = usage_site["site_id"]
            cameras = await call_sync(request, storage.snapshot_cameras, site_id)
            camera_summaries: list[dict[str, Any]] = []
            for camera in cameras:
                camera_id = camera["camera_id"]
                camera_summaries.append(
                    {
                        "camera_id": camera_id,
                        "last_good_age_seconds": await call_sync(
                            request, snapshot_store.last_good_age_seconds,
                            site_id, camera_id, now_ms=now_ms,
                        ),
                        "latest_attempt": await call_sync(
                            request, storage.latest_snapshot_attempt, site_id, camera_id
                        ),
                        "statistics": await call_sync(
                            request, storage.snapshot_statistics, site_id, camera_id, now_ms=now_ms
                        ),
                    }
                )
            latest_telemetry = await call_sync(request, storage.latest, site_id, now_ms=now_ms)
            sites.append(
                {
                    **usage_site,
                    "latest_telemetry": latest_telemetry,
                    "producer_health": [
                        event for event in latest_telemetry
                        if event["kind"] == "producer.health"
                    ],
                    "statistics": await call_sync(request, storage.snapshot_statistics, site_id, now_ms=now_ms),
                    "cameras": camera_summaries,
                }
            )
        scheduler = request.app.state.scheduler
        response = JSONResponse(
            {
                "capacity": {
                    "telemetry": {key: usage[key] for key in usage if key != "sites"},
                    "snapshots": snapshot_usage,
                },
                "scheduler": {"metadata_dropped": 0 if scheduler is None else scheduler.metadata_dropped},
                "sites": sites,
            }
        )
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
        if not await call_sync(request, get_storage(request).snapshot_camera_exists, site_id, camera_id):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        now_ms = int(time.time() * 1000)
        snapshot_store = get_snapshots(request)
        path = await call_sync(
            request, snapshot_store.get,
            site_id, camera_id, now_ms=now_ms, max_stale_seconds=MAX_STALE_SECONDS,
        )
        if path is None:
            exists = await call_sync(request, snapshot_store.has_last_good, site_id, camera_id)
            code = "snapshot_stale" if exists else "snapshot_unavailable"
            return JSONResponse(status_code=503, content={"error_code": code})
        return Response(
            content=await call_sync(request, path.read_bytes), media_type="image/jpeg"
        )

    @app.get(
        "/api/v1/sites/{site_id}/cameras/{camera_id}/last-good-snapshot",
        include_in_schema=False,
    )
    async def ui_last_good_snapshot(request: Request, site_id: str, camera_id: int):
        """UI-only image route: last-good may be stale, but remains single-image only."""
        try:
            validate_site_id(site_id)
            validate_camera_id(camera_id)
        except (TelemetryValidationError, ValueError):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        if not await call_sync(request, get_storage(request).snapshot_camera_exists, site_id, camera_id):
            return JSONResponse(status_code=404, content={"error_code": "snapshot_not_found"})
        content = await call_sync(
            request, get_snapshots(request).read_last_good, site_id, camera_id
        )
        if content is None:
            return JSONResponse(status_code=503, content={"error_code": "snapshot_unavailable"})
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/sites/{site_id}/export.json")
    async def export_json(
        request: Request,
        site_id: str,
        after_cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> JSONResponse:
        try:
            validate_site_id(site_id)
        except TelemetryValidationError as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        rows = await call_sync(
            request,
            get_storage(request).query,
            site_id,
            after_cursor=after_cursor,
            limit=limit,
        )
        response = JSONResponse(
            {
                "site_id": site_id,
                "events": rows,
                "next_cursor": rows[-1]["cursor"] if rows else after_cursor,
                "truncated": len(rows) == limit,
            }
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{site_id}-telemetry.json"'
        )
        return response

    return app


app = create_app()
