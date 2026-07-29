"""Async client for the sigbot service API — the finder's only transport.

The finder no longer talks to signal-cli-rest-api; sigbot owns the Signal
account. The finder's API key scopes every call here to the furniture-finder
group's service (registered in the sigbot dashboard with respond_to='none',
so sigbot's persona stays silent and the finder drives the conversation).
"""

from __future__ import annotations

import base64
import logging
import urllib.parse

import httpx

log = logging.getLogger(__name__)


class SigbotService:
    def __init__(self, api_url: str, api_key: str):
        self._http = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def service_info(self) -> dict:
        r = await self._http.get("/api/v1/service")
        r.raise_for_status()
        return r.json()

    async def send(self, message: str, attachments_b64: list[str] | None = None) -> None:
        payload: dict = {"text": message}
        if attachments_b64:
            payload["attachments_b64"] = attachments_b64
        r = await self._http.post("/api/v1/messages", json=payload)
        r.raise_for_status()

    async def send_image(self, message: str, image: bytes,
                         mime: str = "image/jpeg") -> None:
        b64 = base64.b64encode(image).decode()
        await self.send(message, [f"data:{mime};base64,{b64}"])

    async def messages(self, after_id: int | None = None, limit: int = 100) -> list[dict]:
        """Service message log, oldest first; after_id is the poll cursor."""
        params: dict = {"limit": limit}
        if after_id is not None:
            params["after_id"] = after_id
        r = await self._http.get("/api/v1/messages", params=params)
        r.raise_for_status()
        return r.json()["messages"]

    async def fetch_attachment(self, attachment_id: str) -> bytes:
        quoted = urllib.parse.quote(attachment_id, safe="")
        r = await self._http.get(f"/api/v1/attachments/{quoted}")
        r.raise_for_status()
        return r.content
