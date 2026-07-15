"""Thin async client for bbernhard/signal-cli-rest-api (json-rpc mode)."""

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

    # -- groups ------------------------------------------------------------------

    async def list_groups(self) -> list[dict]:
        r = await self._http.get(f"/v1/groups/{self.bot_number}")
        r.raise_for_status()
        return r.json()

    async def create_group(self, name: str, members: list[str]) -> dict:
        r = await self._http.post(
            f"/v1/groups/{self.bot_number}",
            json={"name": name, "members": members},
        )
        r.raise_for_status()
        return r.json()  # {"id": "<internal_id>"}

    # -- attachments ---------------------------------------------------------------

    async def fetch_attachment(self, attachment_id: str) -> bytes:
        r = await self._http.get(f"/v1/attachments/{attachment_id}")
        r.raise_for_status()
        return r.content

    # -- receive (websocket, json-rpc mode) ------------------------------------------

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
