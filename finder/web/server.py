"""Dashboard: read-only web UI showing live pipeline activity (SSE) and past
listings with their full judgement trail. Binds localhost by default; it
exposes listing data and nothing mutable, but there's no auth — keep it off
public interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib

from aiohttp import web

from finder.events import EventBus
from finder.store import Store

log = logging.getLogger(__name__)

_INDEX = pathlib.Path(__file__).parent / "index.html"


def build_app(store: Store, bus: EventBus, scheduler=None) -> web.Application:
    app = web.Application()

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=_INDEX.read_text(), content_type="text/html")

    async def api_status(_request: web.Request) -> web.Response:
        status = {
            "last_run": store.get_setting("last_run"),
            "location": store.get_setting("location"),
            "cadence_minutes": store.get_setting("cadence_minutes", 240),
            "outcomes": store.outcome_counts(),
            "queries": store.list_queries(),
        }
        if scheduler:
            status.update(scheduler.status())
        return web.json_response(status)

    async def api_listings(request: web.Request) -> web.Response:
        q = request.query
        listings = store.recent_listings(
            limit=min(int(q.get("limit", "50")), 500),
            outcome=q.get("outcome") or None,
            query_name=q.get("query") or None,
        )
        return web.json_response({"listings": listings})

    async def events(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })
        await resp.prepare(request)
        queue = bus.subscribe()
        try:
            await resp.write(b': connected\n\n')
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    payload = json.dumps(event).encode()
                    await resp.write(b"data: " + payload + b"\n\n")
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")  # hold the connection open
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            bus.unsubscribe(queue)
        return resp

    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/listings", api_listings)
    app.router.add_get("/events", events)
    return app


async def start_dashboard(store: Store, bus: EventBus, scheduler=None,
                          host: str = "127.0.0.1", port: int = 8099) -> web.AppRunner:
    runner = web.AppRunner(build_app(store, bus, scheduler), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("dashboard: http://%s:%d", host, port)
    return runner
