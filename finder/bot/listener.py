"""Websocket consumer: filters envelopes down to messages the bot should act on.

Security posture: ONLY messages whose source is the configured user number are
processed — anyone else messaging the bot gets silence. Conversation happens in
the finder group; a 1:1 DM from the user gets a pointer to the group.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from finder.notify.signal import SignalClient

log = logging.getLogger(__name__)

_RECONNECT_INITIAL_S = 1
_RECONNECT_MAX_S = 60


@dataclasses.dataclass
class Incoming:
    text: str
    attachments: list[dict]  # signal attachment descriptors (id, contentType, …)


def parse_envelope(envelope: dict, user_number: str, group_internal_id: str) -> Incoming | None:
    """Returns an Incoming for a user message in the finder group, 'dm' for a
    1:1 from the user, None for everything else (strangers, receipts, echoes)."""
    if envelope.get("sourceNumber") != user_number:
        return None
    data = envelope.get("dataMessage")
    if not data:
        return None  # receipts, typing indicators, sync noise
    text = data.get("message") or ""
    attachments = data.get("attachments") or []
    if not text and not attachments:
        return None
    group = (data.get("groupInfo") or {}).get("groupId")
    if group == group_internal_id:
        return Incoming(text=text, attachments=attachments)
    if group is None:
        return Incoming(text="__dm__", attachments=[])
    return None  # some other group the bot is in


async def run_listener(client: SignalClient, user_number: str, group: dict, handler) -> None:
    """handler: async (Incoming) -> None. Reconnects with backoff forever."""
    backoff = _RECONNECT_INITIAL_S
    while True:
        try:
            async for envelope in client.receive():
                backoff = _RECONNECT_INITIAL_S
                inc = parse_envelope(envelope, user_number, group["internal_id"])
                if inc is None:
                    continue
                if inc.text == "__dm__":
                    await client.send(
                        user_number,
                        f"I live in the group chat — message me there \U0001f6cb",
                    )
                    continue
                try:
                    await handler(inc)
                except Exception:
                    log.exception("handler failed for message %.80r", inc.text)
                    await client.send(group["send_id"], "⚠️ I hit an error handling that — try again?")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("receive stream dropped (%s); reconnecting in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)
