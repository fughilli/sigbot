"""In-process pub/sub: the pipeline publishes progress events; the dashboard's
SSE handler subscribes. Lossy by design — a slow subscriber drops old events
rather than stalling the pipeline."""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

_QUEUE_MAX = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event_type: str, **data: Any) -> None:
        event = {
            "type": event_type,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            **data,
        }
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # drop oldest, keep the stream moving
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass
