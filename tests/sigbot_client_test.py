"""The wheel's client is stdlib-urllib; test it by faking urlopen."""

import io
import json
import sys
import urllib.error

import pytest

from sigbot_client import ServiceClient, SigbotApiError


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _Resp(json.dumps(
            {"messages": [{"id": 7, "text": "hi"}], "name": "ops", "sent": True}
        ).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_request_shape(calls):
    bot = ServiceClient("http://host:8100/", api_key="sb_abc")
    bot.send("hello", prefix=False)
    req = calls[0]
    assert req.full_url == "http://host:8100/api/v1/messages"
    assert req.get_method() == "POST"
    assert req.headers["Authorization"] == "Bearer sb_abc"
    assert json.loads(req.data) == {"text": "hello", "prefix": False}


def test_messages_query(calls):
    bot = ServiceClient("http://host:8100", api_key="sb_abc")
    msgs = bot.messages(limit=10, after_id=7)
    assert msgs == [{"id": 7, "text": "hi"}]
    assert calls[0].full_url == "http://host:8100/api/v1/messages?limit=10&after_id=7"
    assert calls[0].get_method() == "GET" and calls[0].data is None


def test_send_attachments_body(calls):
    bot = ServiceClient("http://host:8100", api_key="sb_abc")
    bot.send("pic", attachments_b64=["data:image/jpeg;base64,AA"])
    assert json.loads(calls[0].data)["attachments_b64"] == ["data:image/jpeg;base64,AA"]


def test_fetch_attachment_raw(calls):
    bot = ServiceClient("http://host:8100", api_key="sb_abc")
    data = bot.fetch_attachment("att/1")  # id gets percent-encoded
    assert isinstance(data, bytes)
    assert calls[0].full_url == "http://host:8100/api/v1/attachments/att%2F1"


def test_service_info(calls):
    assert ServiceClient("http://host:8100", "sb_abc").service()["name"] == "ops"


def test_http_error_surfaces(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"error": "invalid, revoked, or disabled API key"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SigbotApiError) as ei:
        ServiceClient("http://host:8100", "sb_dead").service()
    assert ei.value.status == 401
    assert "revoked" in ei.value.message


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
