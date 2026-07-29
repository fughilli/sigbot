"""Client for the sigbot service API.

One sigbot instance hosts many *services* — one per Signal group chat, each
with its own persona and its own API keys. A key scopes this client to that
one group chat.

    from sigbot_client import ServiceClient

    bot = ServiceClient("http://myhost:8100", api_key="sb_...")
    bot.send("deploy finished ✅")           # posts into the group
    for m in bot.messages(limit=20):         # reads the group's message log
        print(m["sender_name"], m["text"])

Stdlib-only (urllib), so the wheel installs anywhere with no dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["ServiceClient", "SigbotApiError"]
__version__ = "0.1.0"


class SigbotApiError(Exception):
    """Non-2xx response from the sigbot API."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class ServiceClient:
    """Talks to one sigbot service (= one group chat), authorized by its API key."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # -- API surface -----------------------------------------------------------

    def service(self) -> dict:
        """This key's service: name, persona label, group name, reply policy."""
        return self._request("GET", "/api/v1/service")

    def send(self, text: str, prefix: bool | None = None) -> dict:
        """Post a message into the service's group chat as the bot.

        prefix: override the service's default for the '[label] ' prefix.
        """
        body: dict = {"text": text}
        if prefix is not None:
            body["prefix"] = prefix
        return self._request("POST", "/api/v1/messages", body)

    def messages(self, limit: int = 50, after_id: int | None = None) -> list[dict]:
        """The group's message log, oldest first. Poll incrementally by passing
        the last seen message's id as after_id."""
        params: dict = {"limit": limit}
        if after_id is not None:
            params["after_id"] = after_id
        query = urllib.parse.urlencode(params)
        return self._request("GET", f"/api/v1/messages?{query}")["messages"]

    # -- plumbing --------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"sigbot-client/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                message = json.loads(raw).get("error", raw)
            except json.JSONDecodeError:
                message = raw or e.reason
            raise SigbotApiError(e.code, message) from None
