"""Claude agent loop: user message in the finder group -> tool calls -> reply.

Only plain text turns are persisted to chat history (not tool exchanges), so
trimming history can never split a tool_use/tool_result pair.
"""

from __future__ import annotations

import logging

import anthropic

from finder.bot.listener import Incoming
from finder.bot.tools import TOOL_DEFS, Tools
from finder.config import Config
from finder.notify.sigbot_api import SigbotService
from finder.store import Store

log = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 8
_HISTORY_TURNS = 30

_SYSTEM = """You are a furniture-finding assistant living in a Signal group \
chat with your one user. You manage saved searches over local marketplace \
listings (Craigslist now; Facebook Marketplace later), scraped on a schedule \
and matched against the user's target aesthetic.

Use the tools to read or change configuration, inspect listings (the user \
refers to them as #N), record feedback, and trigger runs. Rules:
- This is a phone chat: reply in 1-3 short sentences, no markdown headers.
- Never invent listings or config values; everything comes from tools.
- When the user asks for a change, make it with a tool, then confirm what \
changed in plain words. If a tool returns an error, relay the constraint.
- If no search query exists yet, walk the user through creating one \
(location + radius first, then keywords/price/aesthetic).
- Price caps, radius, cadence etc. have validation limits; trust tool errors.
- When the user sends photos of furniture they like, ask which query to file \
them under (or use add_reference_images directly if it's obvious)."""

_IMAGE_MEDIA_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class Agent:
    def __init__(self, config: Config, store: Store, svc: SigbotService,
                 tools: Tools):
        self.config = config
        self.store = store
        self.svc = svc
        self.tools = tools
        self.anthropic = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    async def handle(self, inc: Incoming) -> None:
        content: list[dict] = []
        self.tools.pending_images = []
        for att in inc.attachments:
            media_type = att.get("contentType", "")
            ext = _IMAGE_MEDIA_TYPES.get(media_type)
            if not ext:
                continue
            data = await self.svc.fetch_attachment(att["id"])
            self.tools.pending_images.append((data, ext))
        if self.tools.pending_images:
            content.append(
                {"type": "text",
                 "text": f"[user attached {len(self.tools.pending_images)} image(s)]"}
            )
        if inc.text:
            content.append({"type": "text", "text": inc.text})

        messages = self.store.recent_chat(limit=_HISTORY_TURNS) + [
            {"role": "user", "content": content}
        ]
        reply = await self._run_loop(messages)

        self.store.append_chat("user", content)
        self.store.append_chat("assistant", reply)
        await self.svc.send(reply)

    async def _run_loop(self, messages: list[dict]) -> str:
        for _ in range(_MAX_TOOL_ROUNDS):
            resp = await self.anthropic.messages.create(
                model=self.config.agent_model,
                max_tokens=1024,
                system=_SYSTEM,
                tools=TOOL_DEFS,
                messages=messages,
            )
            if resp.stop_reason != "tool_use":
                return _text_of(resp)
            messages = messages + [{"role": "assistant", "content": resp.content}]
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    log.info("tool %s(%s)", block.name, block.input)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": self.tools.dispatch(block.name, block.input),
                    })
            messages = messages + [{"role": "user", "content": results}]
        return "I got stuck in a loop trying to do that — mind rephrasing?"


def _text_of(resp) -> str:
    parts = [b.text for b in resp.content if b.type == "text"]
    return "\n".join(parts).strip() or "(done)"
