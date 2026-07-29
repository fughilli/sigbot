"""Message-log poller: consumes the sigbot service API instead of a Signal
websocket. The finder's API key already confines it to the finder group; on
top of that, ONLY messages whose sender is the configured user are processed —
anyone else in the group gets silence.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from finder.notify.sigbot_api import SigbotService
from finder.store import Store

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = 2.0
_BACKOFF_INITIAL_S = 1
_BACKOFF_MAX_S = 60
_CURSOR_KEY = "sigbot_cursor"


@dataclasses.dataclass
class Incoming:
    text: str
    attachments: list[dict]  # sigbot attachment descriptors (id, contentType)


def parse_row(row: dict, user_id: str) -> Incoming | None:
    """Returns an Incoming for a user message, None for everything else (other
    group members, the finder's own sends echoed back through the log)."""
    if row.get("direction") != "in" or row.get("sender") != user_id:
        return None
    text = row.get("text") or ""
    attachments = row.get("attachments") or []
    if not text and not attachments:
        return None
    return Incoming(text=text, attachments=attachments)


async def run_listener(svc: SigbotService, store: Store, user_id: str,
                       handler, poll_interval: float = _POLL_INTERVAL_S) -> None:
    """handler: async (Incoming) -> None. Polls forever with backoff on API
    errors. The cursor persists in the store, so restarts don't replay old
    messages; on very first run it fast-forwards past any pre-existing log."""
    cursor = store.get_setting(_CURSOR_KEY)
    while cursor is None:
        try:
            latest = await svc.messages(limit=1)
            cursor = latest[-1]["id"] if latest else 0
            store.set_setting(_CURSOR_KEY, cursor)
        except Exception as e:
            log.warning("sigbot API unreachable for cursor init (%s); retrying", e)
            await asyncio.sleep(_BACKOFF_MAX_S / 12)

    backoff = _BACKOFF_INITIAL_S
    while True:
        try:
            rows = await svc.messages(after_id=cursor, limit=100)
            backoff = _BACKOFF_INITIAL_S
        except Exception as e:
            log.warning("poll failed (%s); retrying in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_S)
            continue
        for row in rows:
            cursor = row["id"]
            store.set_setting(_CURSOR_KEY, cursor)
            inc = parse_row(row, user_id)
            if inc is None:
                continue
            try:
                await handler(inc)
            except Exception:
                log.exception("handler failed for message %.80r", inc.text)
                try:
                    await svc.send("⚠️ I hit an error handling that — try again?")
                except Exception:
                    log.warning("error notice send failed too", exc_info=True)
        await asyncio.sleep(poll_interval)
