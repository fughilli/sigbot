"""Persona agent: a group message arrives -> log it -> (maybe) reply in
character using the service's system prompt.

Every message in a registered group is logged for context, but a reply is only
generated per the service's respond_to policy ('all' or 'mention'). The LLM
conversation is reconstructed from the message log: incoming messages become
user turns prefixed with the sender's name; anything the bot said (agent reply
or API-injected message) becomes an assistant turn.
"""

from __future__ import annotations

import base64
import logging

import anthropic

from sigbot.config import Config
from sigbot.listener import Incoming
from sigbot.signal_client import SignalClient
from sigbot.store import Store

log = logging.getLogger(__name__)

_HISTORY_MESSAGES = 40
_MAX_TOKENS = 1024

_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_SYSTEM_TEMPLATE = """You are "{label}", a member of the Signal group chat \
"{group_name}". Incoming messages are shown as "Sender name: text"; reply with \
your message text only — no name prefix, no markdown headers. This is a phone \
chat, so keep replies short unless asked for detail.

Your persona:
{system_prompt}"""


def compose_system(service: dict) -> str:
    return _SYSTEM_TEMPLATE.format(
        label=service["label"],
        group_name=service.get("group_name") or "(unnamed group)",
        system_prompt=service["system_prompt"].strip(),
    )


def build_llm_messages(rows: list[dict], current_images: list[tuple[bytes, str]]) -> list[dict]:
    """Message-log rows (oldest first) -> Claude messages. Consecutive turns of
    the same role are merged; images (from the triggering message only) are
    attached to the final user turn."""
    merged: list[dict] = []
    for row in rows:
        role = "user" if row["direction"] == "in" else "assistant"
        text = row["text"]
        if role == "user":
            text = f"{row['sender_name'] or row['sender'] or 'Someone'}: {text}"
            if row["has_attachments"] and not row["text"]:
                text += "[sent an attachment]"
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"][0]["text"] += "\n" + text
        else:
            merged.append({"role": role, "content": [{"type": "text", "text": text}]})
    if not merged or merged[-1]["role"] != "user":
        return []  # nothing to respond to (shouldn't happen: caller just logged one)
    for data, media_type in current_images:
        merged[-1]["content"].append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.b64encode(data).decode()},
        })
    return merged


def should_respond(inc: Incoming) -> bool:
    policy = inc.service.get("respond_to", "all")
    return policy == "all" or (policy == "mention" and inc.mentioned)


class PersonaAgent:
    def __init__(self, config: Config, store: Store, client: SignalClient):
        self.config = config
        self.store = store
        self.client = client
        self.anthropic = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    async def handle(self, inc: Incoming) -> None:
        service = inc.service
        self.store.append_message(
            service["id"], "in", "signal", inc.text,
            sender=inc.sender, sender_name=inc.sender_name,
            has_attachments=bool(inc.attachments),
        )
        if not should_respond(inc):
            return

        images: list[tuple[bytes, str]] = []
        for att in inc.attachments:
            media_type = att.get("contentType", "")
            if media_type not in _IMAGE_MEDIA_TYPES:
                continue
            try:
                images.append((await self.client.fetch_attachment(att["id"]), media_type))
            except Exception:
                log.warning("attachment %s fetch failed", att.get("id"), exc_info=True)

        rows = self.store.recent_messages(service["id"], limit=_HISTORY_MESSAGES)
        messages = build_llm_messages(rows, images)
        if not messages:
            return

        resp = await self.anthropic.messages.create(
            model=service.get("model") or self.config.default_model,
            max_tokens=_MAX_TOKENS,
            system=compose_system(service),
            messages=messages,
        )
        reply = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if not reply:
            return
        outgoing = f"[{service['label']}] {reply}" if service["prefix_label"] else reply
        await self.client.send(service["group_send_id"], outgoing)
        self.store.append_message(service["id"], "out", "agent", reply)
