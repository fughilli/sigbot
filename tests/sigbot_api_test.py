import sys

import pytest
from aiohttp.test_utils import AioHTTPTestCase

from sigbot import auth
from sigbot.api import build_app
from sigbot.store import Store

GROUPS = [
    {"id": "group.g1", "internal_id": "g1", "name": "Bound Group", "members": ["a", "b"]},
    {"id": "group.g2", "internal_id": "g2", "name": "Free Group", "members": ["a"]},
]


class FakeSignal:
    def __init__(self):
        self.sent = []

    async def send(self, recipient, message, attachments_b64=None):
        self.sent.append((recipient, message))

    async def list_groups(self):
        return GROUPS


class ApiTest(AioHTTPTestCase):
    async def get_application(self):
        self.store = Store(":memory:")
        self.signal = FakeSignal()
        self.store.upsert_admin("k", auth.hash_password("hunter22"))
        self.service = self.store.create_service(
            name="ops", group_id="g1", group_send_id="group.g1",
            group_name="Bound Group", label="Opsy", system_prompt="You are Opsy.")
        self.key, key_hash = auth.new_api_key()
        self.store.add_api_key(self.service["id"], key_hash, label="test")
        return build_app(self.store, self.signal)

    def _auth(self, key=None):
        return {"Authorization": f"Bearer {key or self.key}"}

    async def _login(self):
        r = await self.client.post("/auth/login",
                                   json={"username": "k", "password": "hunter22"})
        assert r.status == 200

    # -- service API -----------------------------------------------------------

    async def test_service_endpoint_requires_key(self):
        assert (await self.client.get("/api/v1/service")).status == 401
        assert (await self.client.get(
            "/api/v1/service", headers=self._auth("sb_bogus"))).status == 401
        r = await self.client.get("/api/v1/service", headers=self._auth())
        assert r.status == 200
        body = await r.json()
        assert body["label"] == "Opsy"
        assert "system_prompt" not in body  # privileged field stays private

    async def test_send_prefixes_and_logs(self):
        r = await self.client.post("/api/v1/messages", headers=self._auth(),
                                   json={"text": "deploy done"})
        assert r.status == 200
        assert self.signal.sent == [("group.g1", "[Opsy] deploy done")]

        r = await self.client.post("/api/v1/messages", headers=self._auth(),
                                   json={"text": "raw", "prefix": False})
        assert r.status == 200
        assert self.signal.sent[-1] == ("group.g1", "raw")

        r = await self.client.get("/api/v1/messages", headers=self._auth())
        texts = [m["text"] for m in (await r.json())["messages"]]
        assert texts == ["deploy done", "raw"]  # unprefixed text is what's logged

        assert (await self.client.post("/api/v1/messages", headers=self._auth(),
                                       json={"text": "  "})).status == 400

    async def test_messages_cursor(self):
        first = self.store.append_message(self.service["id"], "in", "signal", "hi",
                                          sender="u1", sender_name="Kay")
        self.store.append_message(self.service["id"], "in", "signal", "again",
                                  sender="u1", sender_name="Kay")
        r = await self.client.get(f"/api/v1/messages?after_id={first['id']}",
                                  headers=self._auth())
        assert [m["text"] for m in (await r.json())["messages"]] == ["again"]

    # -- admin surface ---------------------------------------------------------

    async def test_admin_requires_session(self):
        for method, path in [("get", "/admin/api/services"),
                             ("post", "/admin/api/services"),
                             ("get", "/admin/api/groups"),
                             ("post", f"/admin/api/services/{self.service['id']}/keys")]:
            r = await getattr(self.client, method)(path, json={})
            assert r.status == 401, path
        # an API key is NOT admin credentials
        r = await self.client.get("/admin/api/services", headers=self._auth())
        assert r.status == 401

    async def test_login_rejects_bad_credentials(self):
        r = await self.client.post("/auth/login",
                                   json={"username": "k", "password": "nope"})
        assert r.status == 401
        r = await self.client.post("/auth/login",
                                   json={"username": "ghost", "password": "hunter22"})
        assert r.status == 401

    async def test_full_admin_flow(self):
        await self._login()

        r = await self.client.get("/admin/api/groups")
        assert {g["internal_id"] for g in (await r.json())["groups"]} == {"g1", "g2"}

        # bind the free group to a new service
        r = await self.client.post("/admin/api/services", json={
            "group_id": "g2", "name": "fun", "label": "Jester",
            "system_prompt": "You joke.", "respond_to": "mention"})
        assert r.status == 201
        svc = (await r.json())["service"]
        assert svc["group_send_id"] == "group.g2" and svc["group_name"] == "Free Group"

        # double-bind and dup name are rejected
        assert (await self.client.post("/admin/api/services", json={
            "group_id": "g2", "name": "x", "label": "L", "system_prompt": "p"})).status == 409
        assert (await self.client.post("/admin/api/services", json={
            "group_id": "bogus", "name": "y", "label": "L", "system_prompt": "p"})).status == 400

        # persona edit
        r = await self.client.patch(f"/admin/api/services/{svc['id']}",
                                    json={"label": "Court Jester", "prefix_label": False})
        assert (await r.json())["service"]["label"] == "Court Jester"
        assert (await self.client.patch(f"/admin/api/services/{svc['id']}",
                                        json={"respond_to": "sometimes"})).status == 400

        # mint a key, then use it against the service API
        r = await self.client.post(f"/admin/api/services/{svc['id']}/keys",
                                   json={"label": "ci"})
        assert r.status == 201
        minted = await r.json()
        r = await self.client.get("/api/v1/service", headers=self._auth(minted["key"]))
        assert (await r.json())["name"] == "fun"

        # revoke it and the key stops working
        r = await self.client.delete(f"/admin/api/keys/{minted['key_id']}")
        assert r.status == 200
        r = await self.client.get("/api/v1/service", headers=self._auth(minted["key"]))
        assert r.status == 401

        # logout kills the session
        await self.client.post("/auth/logout")
        assert (await self.client.get("/admin/api/services")).status == 401


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
