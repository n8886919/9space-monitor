"""FastAPI entry point for the 9Space Center telemetry service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .storage import CapacityExceeded, InvalidEventTimestamp, TelemetryStorage
from .validation import (
    MAX_BODY_BYTES,
    TelemetryValidationError,
    validate_batch,
    validate_site_id,
)

DATABASE_PATH = os.environ.get("CENTER_DATABASE_PATH", "/data/telemetry.sqlite3")
RETENTION_PRUNE_INTERVAL_SECONDS = 3600


async def _retention_worker(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(RETENTION_PRUNE_INTERVAL_SECONDS)
        storage: TelemetryStorage = app.state.storage
        await asyncio.to_thread(storage.prune)


def create_app(
    storage: TelemetryStorage | None = None,
    run_sync: Callable[..., Awaitable[Any]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.storage is None:
            app.state.storage = TelemetryStorage(DATABASE_PATH)
        await asyncio.to_thread(app.state.storage.prune)
        task = asyncio.create_task(_retention_worker(app))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="9Space Center", version="0.1.0", lifespan=lifespan)
    app.state.storage = storage
    # Production defaults to a worker thread so SQLite never blocks the
    # event loop. Tests may inject an immediate async runner to avoid the
    # host sandbox's known executor-shutdown hang.
    app.state.run_sync = run_sync or asyncio.to_thread

    def get_storage(request: Request) -> TelemetryStorage:
        result = request.app.state.storage
        if result is None:
            # Primarily useful for ASGI clients that deliberately skip
            # lifespan. Production uvicorn always runs lifespan startup.
            result = TelemetryStorage(DATABASE_PATH)
            request.app.state.storage = result
        return result

    async def call_sync(request: Request, function: Callable[..., Any], *args, **kwargs):
        return await request.app.state.run_sync(function, *args, **kwargs)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
