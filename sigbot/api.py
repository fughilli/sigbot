"""HTTP server: per-service API (bearer key auth) + privileged dashboard
(session-cookie login).

One aiohttp app on one port:
  /api/v1/*        service surface, authenticated by a minted API key; scoped
                   to that key's group chat (service).
  /auth/*, /admin/api/*, /, /login
                   privileged surface: session cookie from username/password
                   login. Registers services/personas, mints and revokes keys.

CSRF: the session cookie is SameSite=Lax, so cross-site fetch/form POSTs don't
carry it; all mutating admin routes are cookie-authed JSON endpoints.
"""

from __future__ import annotations

import json
import logging
import pathlib

from aiohttp import web

from sigbot import auth
from sigbot.store import Store

log = logging.getLogger(__name__)

_STATIC = pathlib.Path(__file__).parent / "static"
_SESSION_COOKIE = "sigbot_session"

_SERVICE_PUBLIC_FIELDS = ("name", "label", "group_name", "respond_to", "prefix_label")
# 'none' = transport-only: the persona never replies; the group is driven
# entirely through the API (e.g. an external bot process like the finder).
_RESPOND_POLICIES = ("all", "mention", "none")
_MAX_ATTACHMENTS = 4
# An emoji reaction is a grapheme cluster, not a character: a flag or a
# skin-toned emoji is several codepoints joined by ZWJ. Generous enough for any
# real emoji, tight enough that the field can't smuggle a message.
_MAX_EMOJI_LEN = 32


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(text='{"error": "invalid JSON body"}',
                                 content_type="application/json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text='{"error": "body must be a JSON object"}',
                                 content_type="application/json")
    return data


def build_app(store: Store, signal_client, default_model: str = "") -> web.Application:
    app = web.Application()
    app["store"] = store
    app["signal"] = signal_client

    # -- auth helpers ----------------------------------------------------------

    def require_service(request: web.Request) -> dict:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise web.HTTPUnauthorized(
                text='{"error": "missing Authorization: Bearer <api key>"}',
                content_type="application/json")
        service = store.service_for_key(auth.hash_api_key(header[len("Bearer "):].strip()))
        if not service:
            raise web.HTTPUnauthorized(
                text='{"error": "invalid, revoked, or disabled API key"}',
                content_type="application/json")
        return service

    def session_user(request: web.Request) -> str | None:
        token = request.cookies.get(_SESSION_COOKIE)
        if not token:
            return None
        return store.session_user(auth.hash_session_token(token))

    def require_admin(request: web.Request) -> str:
        user = session_user(request)
        if not user:
            raise web.HTTPUnauthorized(text='{"error": "login required"}',
                                       content_type="application/json")
        return user

    # -- service API (key auth) ------------------------------------------------

    async def api_service(request: web.Request) -> web.Response:
        service = require_service(request)
        return web.json_response({k: service[k] for k in _SERVICE_PUBLIC_FIELDS})

    async def api_send(request: web.Request) -> web.Response:
        service = require_service(request)
        data = await _body(request)
        text = (data.get("text") or "").strip()
        attachments_b64 = data.get("attachments_b64") or []
        if not isinstance(attachments_b64, list) or not all(
                isinstance(a, str) for a in attachments_b64):
            return _json_error(400, "attachments_b64 must be a list of base64/data-URI strings")
        if len(attachments_b64) > _MAX_ATTACHMENTS:
            return _json_error(400, f"too many attachments ({_MAX_ATTACHMENTS} max)")
        if not text and not attachments_b64:
            return _json_error(400, "text or attachments_b64 is required")
        if len(text) > 4000:
            return _json_error(400, "text too long (4000 chars max)")
        prefix = data.get("prefix", service["prefix_label"])
        outgoing = f"[{service['label']}] {text}" if prefix and text else text
        await app["signal"].send(service["group_send_id"], outgoing,
                                 attachments_b64=attachments_b64 or None)
        msg = store.append_message(service["id"], "out", "api", text,
                                   has_attachments=bool(attachments_b64))
        return web.json_response({"sent": True, "message": msg})

    async def api_react(request: web.Request) -> web.Response:
        """React to a message in this service's group, or retract with DELETE.

        Reactions are how a bot acknowledges without adding to the transcript —
        an API client can mark a message seen and later mark it done without
        posting two more messages into the group.
        """
        service = require_service(request)
        try:
            message_id = int(request.match_info["id"])
        except ValueError:
            return _json_error(400, "message id must be an integer")
        data = await _body(request)
        emoji = (data.get("emoji") or "").strip()
        if not emoji:
            return _json_error(400, "emoji is required")
        if len(emoji) > _MAX_EMOJI_LEN:
            return _json_error(400, f"emoji too long ({_MAX_EMOJI_LEN} chars max)")

        msg = store.message_for_service(service["id"], message_id)
        if msg is None:
            return _json_error(404, "no such message in this service")
        # Both are needed to address a reaction, and neither exists for a message
        # sigbot itself sent or for one received before signal_ts was recorded.
        if not msg.get("signal_ts") or not msg.get("sender"):
            return _json_error(
                409,
                "message cannot be reacted to: it has no Signal timestamp/author "
                "(outgoing messages, and messages received before sigbot recorded "
                "timestamps, are not reactable)")
        try:
            await app["signal"].react(
                service["group_send_id"], emoji, msg["sender"], int(msg["signal_ts"]),
                remove=request.method == "DELETE",
            )
        except Exception as e:
            return _json_error(502, f"signal API reaction failed: {e}")
        return web.json_response(
            {"reacted": request.method != "DELETE", "message_id": message_id, "emoji": emoji})

    async def api_attachment(request: web.Request) -> web.Response:
        service = require_service(request)
        attachment_id = request.match_info["id"]
        if not store.service_has_attachment(service["id"], attachment_id):
            return _json_error(404, "no such attachment in this service's messages")
        try:
            data = await app["signal"].fetch_attachment(attachment_id)
        except Exception as e:
            return _json_error(502, f"signal API attachment fetch failed: {e}")
        return web.Response(body=data, content_type="application/octet-stream")

    async def api_messages(request: web.Request) -> web.Response:
        service = require_service(request)
        q = request.query
        try:
            limit = min(int(q.get("limit", "50")), 500)
            after_id = int(q["after_id"]) if "after_id" in q else None
        except ValueError:
            return _json_error(400, "limit/after_id must be integers")
        msgs = store.recent_messages(service["id"], limit=limit, after_id=after_id)
        return web.json_response({"messages": msgs})

    # -- login / pages ---------------------------------------------------------

    async def page_index(request: web.Request) -> web.Response:
        if not session_user(request):
            raise web.HTTPFound("/login")
        return web.Response(text=(_STATIC / "dashboard.html").read_text(),
                            content_type="text/html")

    async def page_login(request: web.Request) -> web.Response:
        return web.Response(text=(_STATIC / "login.html").read_text(),
                            content_type="text/html")

    async def auth_login(request: web.Request) -> web.Response:
        data = await _body(request)
        username = (data.get("username") or "").strip()
        admin = store.get_admin(username)
        # verify against a dummy hash on unknown users to keep timing flat
        stored = admin["password_hash"] if admin else auth.hash_password("!")
        if not auth.verify_password(data.get("password") or "", stored) or not admin:
            return _json_error(401, "bad username or password")
        token, token_hash = auth.new_session_token()
        store.create_session(token_hash, username)
        resp = web.json_response({"ok": True, "username": username})
        resp.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="Lax", path="/")
        return resp

    async def auth_logout(request: web.Request) -> web.Response:
        token = request.cookies.get(_SESSION_COOKIE)
        if token:
            store.delete_session(auth.hash_session_token(token))
        resp = web.json_response({"ok": True})
        resp.del_cookie(_SESSION_COOKIE, path="/")
        return resp

    # -- admin API (session auth) ----------------------------------------------

    async def admin_groups(request: web.Request) -> web.Response:
        require_admin(request)
        try:
            groups = await app["signal"].list_groups()
        except Exception as e:
            return _json_error(502, f"signal API unreachable: {e}")
        return web.json_response({"groups": [
            {"internal_id": g.get("internal_id"), "send_id": g.get("id"),
             "name": g.get("name"), "members": len(g.get("members") or [])}
            for g in groups
        ]})

    async def admin_list_services(request: web.Request) -> web.Response:
        require_admin(request)
        services = store.list_services()
        for s in services:
            s["api_keys"] = store.list_api_keys(s["id"])
        return web.json_response({"services": services})

    def _validated_persona_fields(data: dict) -> dict:
        out = {}
        if "respond_to" in data:
            if data["respond_to"] not in _RESPOND_POLICIES:
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": f"respond_to must be one of {_RESPOND_POLICIES}"}),
                    content_type="application/json")
            out["respond_to"] = data["respond_to"]
        for key in ("label", "system_prompt", "name"):
            if key in data:
                value = (data[key] or "").strip()
                if not value:
                    raise web.HTTPBadRequest(
                        text=json.dumps({"error": f"{key} must be non-empty"}),
                        content_type="application/json")
                out[key] = value
        if "prefix_label" in data:
            out["prefix_label"] = bool(data["prefix_label"])
        if "enabled" in data:
            out["enabled"] = bool(data["enabled"])
        if "model" in data:
            out["model"] = (data["model"] or "").strip() or None
        return out

    async def admin_create_service(request: web.Request) -> web.Response:
        require_admin(request)
        data = await _body(request)
        for key in ("name", "group_id", "label", "system_prompt"):
            if not (data.get(key) or "").strip():
                return _json_error(400, f"{key} is required")
        fields = _validated_persona_fields(data)
        group_id = data["group_id"].strip()
        if store.get_service_by_group(group_id):
            return _json_error(409, "that group already has a service")
        if store.get_service_by_name(fields["name"]):
            return _json_error(409, "that service name is taken")
        # resolve send-id + display name canonically from the live group list
        send_id, group_name = data.get("group_send_id"), data.get("group_name")
        try:
            for g in await app["signal"].list_groups():
                if g.get("internal_id") == group_id:
                    send_id, group_name = g["id"], g.get("name")
                    break
        except Exception:
            log.warning("signal API unreachable during service create", exc_info=True)
        if not send_id:
            return _json_error(400, "group_id not found among the bot's groups")
        service = store.create_service(
            name=fields["name"], group_id=group_id, group_send_id=send_id,
            group_name=group_name, label=fields["label"],
            system_prompt=fields["system_prompt"],
            respond_to=fields.get("respond_to", "all"),
            prefix_label=fields.get("prefix_label", True),
            model=fields.get("model"),
        )
        return web.json_response({"service": service}, status=201)

    async def admin_update_service(request: web.Request) -> web.Response:
        require_admin(request)
        service_id = int(request.match_info["id"])
        if not store.get_service(service_id):
            return _json_error(404, "no such service")
        data = await _body(request)
        fields = _validated_persona_fields(data)
        if "name" in fields:
            existing = store.get_service_by_name(fields["name"])
            if existing and existing["id"] != service_id:
                return _json_error(409, "that service name is taken")
        service = store.update_service(service_id, **fields)
        return web.json_response({"service": service})

    async def admin_delete_service(request: web.Request) -> web.Response:
        require_admin(request)
        if not store.delete_service(int(request.match_info["id"])):
            return _json_error(404, "no such service")
        return web.json_response({"ok": True})

    async def admin_mint_key(request: web.Request) -> web.Response:
        require_admin(request)
        service_id = int(request.match_info["id"])
        if not store.get_service(service_id):
            return _json_error(404, "no such service")
        data = await _body(request) if request.can_read_body else {}
        key, key_hash = auth.new_api_key()
        row = store.add_api_key(service_id, key_hash, label=(data.get("label") or "").strip() or None)
        # plaintext key is returned exactly once, never stored
        return web.json_response({"key": key, "key_id": row["id"],
                                  "created_at": row["created_at"]}, status=201)

    async def admin_revoke_key(request: web.Request) -> web.Response:
        require_admin(request)
        if not store.revoke_api_key(int(request.match_info["key_id"])):
            return _json_error(404, "no such active key")
        return web.json_response({"ok": True})

    async def admin_messages(request: web.Request) -> web.Response:
        require_admin(request)
        service_id = int(request.match_info["id"])
        if not store.get_service(service_id):
            return _json_error(404, "no such service")
        limit = min(int(request.query.get("limit", "100")), 1000)
        return web.json_response(
            {"messages": store.recent_messages(service_id, limit=limit)})

    app.router.add_get("/", page_index)
    app.router.add_get("/login", page_login)
    app.router.add_post("/auth/login", auth_login)
    app.router.add_post("/auth/logout", auth_logout)

    app.router.add_get("/api/v1/service", api_service)
    app.router.add_post("/api/v1/messages", api_send)
    app.router.add_get("/api/v1/messages", api_messages)
    app.router.add_post(r"/api/v1/messages/{id:\d+}/reactions", api_react)
    app.router.add_delete(r"/api/v1/messages/{id:\d+}/reactions", api_react)
    app.router.add_get("/api/v1/attachments/{id}", api_attachment)

    app.router.add_get("/admin/api/groups", admin_groups)
    app.router.add_get("/admin/api/services", admin_list_services)
    app.router.add_post("/admin/api/services", admin_create_service)
    app.router.add_patch(r"/admin/api/services/{id:\d+}", admin_update_service)
    app.router.add_delete(r"/admin/api/services/{id:\d+}", admin_delete_service)
    app.router.add_post(r"/admin/api/services/{id:\d+}/keys", admin_mint_key)
    app.router.add_delete(r"/admin/api/keys/{key_id:\d+}", admin_revoke_key)
    app.router.add_get(r"/admin/api/services/{id:\d+}/messages", admin_messages)
    return app


async def start_server(store: Store, signal_client, host: str, port: int,
                       default_model: str = "") -> web.AppRunner:
    runner = web.AppRunner(build_app(store, signal_client, default_model), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("sigbot API + dashboard: http://%s:%d", host, port)
    return runner
