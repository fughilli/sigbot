"""SQLite persistence: services (group personas), API keys, message log,
dashboard admins and their sessions.

A *service* is one group chat the bot lives in: internal/send group ids plus
the persona (member label + system prompt). API keys hang off a service; the
message log holds both directions so the persona has context and API clients
can read the conversation.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3
import threading
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL              -- JSON
);
CREATE TABLE IF NOT EXISTS admins (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,     -- slug, e.g. 'ops-alerts'
    group_id       TEXT NOT NULL UNIQUE,     -- signal internal id (envelope groupInfo.groupId)
    group_send_id  TEXT NOT NULL,            -- 'group.<b64>' send id
    group_name     TEXT,
    label          TEXT NOT NULL,            -- persona member label
    system_prompt  TEXT NOT NULL,
    respond_to     TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'mention'
    prefix_label   INTEGER NOT NULL DEFAULT 1,    -- prefix outgoing msgs with [label]
    model          TEXT,                     -- NULL -> config default
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    key_hash     TEXT NOT NULL UNIQUE,
    label        TEXT,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    direction   TEXT NOT NULL,       -- 'in' | 'out'
    via         TEXT NOT NULL,       -- 'signal' | 'agent' | 'api'
    sender      TEXT,                -- uuid/number for 'in'; NULL for 'out'
    sender_name TEXT,
    text        TEXT NOT NULL,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_service ON messages(service_id, id);
"""

_MUTABLE_SERVICE_FIELDS = {
    "name", "label", "system_prompt", "respond_to", "prefix_label",
    "model", "enabled", "group_name",
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | pathlib.Path):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- settings --------------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    # -- admins ----------------------------------------------------------------

    def upsert_admin(self, username: str, password_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO admins(username,password_hash,created_at) VALUES(?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash",
                (username, password_hash, _now()),
            )

    def get_admin(self, username: str) -> dict | None:
        row = self._db.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_admins(self) -> list[str]:
        return [r["username"] for r in
                self._db.execute("SELECT username FROM admins ORDER BY username")]

    def delete_admin(self, username: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM admins WHERE username=?", (username,))
        return cur.rowcount > 0

    def count_admins(self) -> int:
        return self._db.execute("SELECT COUNT(*) n FROM admins").fetchone()["n"]

    # -- sessions --------------------------------------------------------------

    def create_session(self, token_hash: str, username: str, ttl_hours: int = 12) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        expires = (now + datetime.timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE expires_at < ?",
                             (now.isoformat(timespec="seconds"),))
            self._db.execute(
                "INSERT INTO sessions(token_hash,username,created_at,expires_at) VALUES(?,?,?,?)",
                (token_hash, username, _now(), expires),
            )

    def session_user(self, token_hash: str) -> str | None:
        row = self._db.execute(
            "SELECT username FROM sessions WHERE token_hash=? AND expires_at >= ?",
            (token_hash, _now()),
        ).fetchone()
        return row["username"] if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    # -- services --------------------------------------------------------------

    def create_service(self, *, name: str, group_id: str, group_send_id: str,
                       group_name: str | None, label: str, system_prompt: str,
                       respond_to: str = "all", prefix_label: bool = True,
                       model: str | None = None) -> dict:
        now = _now()
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO services(name,group_id,group_send_id,group_name,label,"
                "system_prompt,respond_to,prefix_label,model,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name, group_id, group_send_id, group_name, label, system_prompt,
                 respond_to, int(prefix_label), model, now, now),
            )
        return self.get_service(cur.lastrowid)  # type: ignore[return-value]

    def update_service(self, service_id: int, **fields: Any) -> dict | None:
        unknown = set(fields) - _MUTABLE_SERVICE_FIELDS
        if unknown:
            raise ValueError(f"immutable or unknown fields: {sorted(unknown)}")
        if fields:
            if "prefix_label" in fields:
                fields["prefix_label"] = int(bool(fields["prefix_label"]))
            if "enabled" in fields:
                fields["enabled"] = int(bool(fields["enabled"]))
            sets = ",".join(f"{k}=?" for k in fields)
            with self._lock, self._db:
                self._db.execute(
                    f"UPDATE services SET {sets}, updated_at=? WHERE id=?",
                    (*fields.values(), _now(), service_id),
                )
        return self.get_service(service_id)

    def delete_service(self, service_id: int) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM services WHERE id=?", (service_id,))
        return cur.rowcount > 0

    def get_service(self, service_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        return self._service_dict(row) if row else None

    def get_service_by_name(self, name: str) -> dict | None:
        row = self._db.execute("SELECT * FROM services WHERE name=?", (name,)).fetchone()
        return self._service_dict(row) if row else None

    def get_service_by_group(self, group_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM services WHERE group_id=?", (group_id,)).fetchone()
        return self._service_dict(row) if row else None

    def list_services(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM services ORDER BY name").fetchall()
        return [self._service_dict(r) for r in rows]

    def services_by_group(self, enabled_only: bool = True) -> dict[str, dict]:
        out = {}
        for s in self.list_services():
            if enabled_only and not s["enabled"]:
                continue
            out[s["group_id"]] = s
        return out

    @staticmethod
    def _service_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["prefix_label"] = bool(d["prefix_label"])
        d["enabled"] = bool(d["enabled"])
        return d

    # -- api keys --------------------------------------------------------------

    def add_api_key(self, service_id: int, key_hash: str, label: str | None = None) -> dict:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO api_keys(service_id,key_hash,label,created_at) VALUES(?,?,?,?)",
                (service_id, key_hash, label, _now()),
            )
            row = self._db.execute("SELECT * FROM api_keys WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def service_for_key(self, key_hash: str) -> dict | None:
        """Resolve an API key to its (enabled) service; touches last_used_at."""
        row = self._db.execute(
            "SELECT s.*, k.id AS key_id FROM api_keys k JOIN services s ON s.id=k.service_id "
            "WHERE k.key_hash=? AND k.revoked_at IS NULL AND s.enabled=1",
            (key_hash,),
        ).fetchone()
        if not row:
            return None
        with self._lock, self._db:
            self._db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?",
                             (_now(), row["key_id"]))
        d = self._service_dict(row)
        d.pop("key_id", None)
        return d

    def list_api_keys(self, service_id: int) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,service_id,label,created_at,revoked_at,last_used_at "
            "FROM api_keys WHERE service_id=? ORDER BY id", (service_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: int) -> bool:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (_now(), key_id),
            )
        return cur.rowcount > 0

    # -- messages --------------------------------------------------------------

    def append_message(self, service_id: int, direction: str, via: str, text: str,
                       sender: str | None = None, sender_name: str | None = None,
                       has_attachments: bool = False) -> dict:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO messages(service_id,direction,via,sender,sender_name,"
                "text,has_attachments,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (service_id, direction, via, sender, sender_name, text,
                 int(has_attachments), _now()),
            )
            row = self._db.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._message_dict(row)

    def recent_messages(self, service_id: int, limit: int = 50,
                        after_id: int | None = None) -> list[dict]:
        """Oldest-first. after_id gives an incremental-poll cursor."""
        if after_id is not None:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE service_id=? AND id>? ORDER BY id LIMIT ?",
                (service_id, after_id, limit)).fetchall()
            return [self._message_dict(r) for r in rows]
        rows = self._db.execute(
            "SELECT * FROM messages WHERE service_id=? ORDER BY id DESC LIMIT ?",
            (service_id, limit)).fetchall()
        return [self._message_dict(r) for r in reversed(rows)]

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["has_attachments"] = bool(d["has_attachments"])
        return d
