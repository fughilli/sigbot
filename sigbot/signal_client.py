"""Thin async client for bbernhard/signal-cli-rest-api (json-rpc mode).

Standalone copy of finder/notify/signal.py plus profile management, so sigbot
has no dependency on the finder package.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, AsyncIterator

import httpx
import websockets

log = logging.getLogger(__name__)


class SignalClient:
    def __init__(self, api_url: str, bot_number: str):
        self.api_url = api_url.rstrip("/")
        self.bot_number = bot_number
        self._http = httpx.AsyncClient(base_url=self.api_url, timeout=60)

    async def close(self) -> None:
        await self._http.aclose()

    # -- send ------------------------------------------------------------------

    async def send(
        self,
        recipient: str,
        message: str,
        attachments_b64: list[str] | None = None,
    ) -> None:
        """recipient is a phone number or a 'group.<id>' send-id."""
        payload: dict[str, Any] = {
            "number": self.bot_number,
            "recipients": [recipient],
            "message": message,
        }
        if attachments_b64:
            payload["base64_attachments"] = attachments_b64
        r = await self._http.post("/v2/send", json=payload)
        r.raise_for_status()

    async def send_image(self, recipient: str, message: str, image: bytes,
                         mime: str = "image/jpeg") -> None:
        b64 = base64.b64encode(image).decode()
        await self.send(recipient, message, [f"data:{mime};base64,{b64}"])

    # -- reactions -------------------------------------------------------------

    async def react(self, recipient: str, emoji: str, target_author: str,
                    timestamp: int, remove: bool = False) -> None:
        """React to one message. signal-cli identifies the target by
        (target_author, timestamp) — its own message id — not by anything sigbot
        assigns, which is why the timestamp has to be captured at receive time.

        remove=True retracts a reaction previously sent with the same emoji.
        """
        payload: dict[str, Any] = {
            "reaction": emoji,
            "recipient": recipient,
            "target_author": target_author,
            "timestamp": timestamp,
        }
        # .request() rather than .post()/.delete(): the DELETE carries a body,
        # which httpx's delete() shorthand has no json= kwarg for.
        r = await self._http.request(
            "DELETE" if remove else "POST",
            f"/v1/reactions/{self.bot_number}",
            json=payload,
        )
        r.raise_for_status()

    # -- profile ---------------------------------------------------------------

    async def set_profile_name(self, name: str) -> None:
        r = await self._http.put(f"/v1/profiles/{self.bot_number}", json={"name": name})
        r.raise_for_status()

    # -- groups ----------------------------------------------------------------

    async def list_groups(self) -> list[dict]:
        r = await self._http.get(f"/v1/groups/{self.bot_number}")
        r.raise_for_status()
        return r.json()

    # -- attachments -----------------------------------------------------------

    async def fetch_attachment(self, attachment_id: str) -> bytes:
        r = await self._http.get(f"/v1/attachments/{attachment_id}")
        r.raise_for_status()
        return r.content

    # -- receive (websocket, json-rpc mode) ------------------------------------

    async def receive(self) -> AsyncIterator[dict]:
        """Yields envelope dicts forever; reconnects are the caller's concern
        (an exception ends the iterator)."""
        ws_url = self.api_url.replace("http://", "ws://").replace("https://", "wss://")
        async with websockets.connect(
            f"{ws_url}/v1/receive/{self.bot_number}", ping_interval=30
        ) as ws:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("undecodable frame: %.200s", raw)
                    continue
                envelope = msg.get("envelope")
                if envelope:
                    yield envelope
