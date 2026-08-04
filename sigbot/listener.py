"""Websocket consumer: routes group messages to their registered service.

Security posture: the bot only acts on messages in groups that have a
registered, enabled service. DMs and unregistered groups get silence — the
privileged dashboard is the only way to enroll a group.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Callable

from sigbot.signal_client import SignalClient

log = logging.getLogger(__name__)

_RECONNECT_INITIAL_S = 1
_RECONNECT_MAX_S = 60


@dataclasses.dataclass
class Incoming:
    service: dict           # services row (see store.py)
    sender: str             # ACI uuid or E.164, whichever the envelope carries
    sender_name: str
    text: str
    attachments: list[dict]  # signal attachment descriptors (id, contentType, …)
    mentioned: bool          # bot was @-mentioned or named in the text
    signal_ts: int | None = None  # Signal's timestamp; the target id for a reaction


def parse_envelope(envelope: dict, bot_number: str, bot_name: str,
                   services_by_group: dict[str, dict]) -> Incoming | None:
    """Returns an Incoming for a message in a registered group, None for
    everything else (own echoes, receipts, DMs, unregistered groups)."""
    source = envelope.get("sourceNumber") or envelope.get("sourceUuid") or ""
    if envelope.get("sourceNumber") == bot_number:
        return None  # our own send, echoed back
    data = envelope.get("dataMessage")
    if not data:
        return None  # receipts, typing indicators, sync noise
    group_id = (data.get("groupInfo") or {}).get("groupId")
    if not group_id or group_id not in services_by_group:
        return None
    text = data.get("message") or ""
    attachments = data.get("attachments") or []
    if not text and not attachments:
        return None
    service = services_by_group[group_id]
    return Incoming(
        service=service,
        sender=source,
        sender_name=envelope.get("sourceName") or source,
        text=text,
        attachments=attachments,
        mentioned=_is_mentioned(data, text, bot_number, bot_name, service["label"]),
        # dataMessage.timestamp is the message's own id in Signal; the envelope
        # timestamp matches it for a normal send but is the delivery time for
        # sync/edit envelopes, so prefer the dataMessage one.
        signal_ts=data.get("timestamp") or envelope.get("timestamp"),
    )


def _is_mentioned(data: dict, text: str, bot_number: str, bot_name: str,
                  label: str) -> bool:
    for m in data.get("mentions") or []:
        if m.get("number") == bot_number:
            return True
    lowered = text.lower()
    return any(n and n.lower() in lowered for n in (label, bot_name))


async def run_listener(client: SignalClient, bot_name: str,
                       get_services: Callable[[], dict[str, dict]],
                       handler) -> None:
    """handler: async (Incoming) -> None. get_services is re-read per envelope
    so dashboard changes take effect live. Reconnects with backoff forever."""
    backoff = _RECONNECT_INITIAL_S
    while True:
        try:
            async for envelope in client.receive():
                backoff = _RECONNECT_INITIAL_S
                inc = parse_envelope(envelope, client.bot_number, bot_name, get_services())
                if inc is None:
                    continue
                try:
                    await handler(inc)
                except Exception:
                    log.exception("handler failed for message %.80r in %s",
                                  inc.text, inc.service["name"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("receive stream dropped (%s); reconnecting in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)
