"""SQLite persistence: dynamic settings, queries, listings, chat history.

Everything the bot can change conversationally lives here (not in config.yaml).
Validation happens in the tool layer (bot/tools.py); this module is dumb storage.
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
CREATE TABLE IF NOT EXISTS queries (
    name        TEXT PRIMARY KEY,
    spec        TEXT NOT NULL,       -- JSON: keywords, max_price, aesthetic, thresholds
    paused      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    id           TEXT PRIMARY KEY,   -- source:native_id
    short_ref    INTEGER UNIQUE,     -- "#14" in chat
    source       TEXT NOT NULL,
    query_name   TEXT,
    title        TEXT,
    description  TEXT,
    price        REAL,
    currency     TEXT DEFAULT 'USD',
    url          TEXT,
    image_urls   TEXT,               -- JSON list
    location_text TEXT,
    lat          REAL,
    lon          REAL,
    posted_at    TEXT,
    first_seen_at TEXT NOT NULL,
    repost_hash  TEXT,               -- title+price+first-image; catches reposts
    clip_score   REAL,
    judge_verdict TEXT,              -- 'match' | 'no_match' | NULL (not judged)
    judge_reason TEXT,
    notified_at  TEXT,
    feedback     TEXT                -- 'up' | 'down' | NULL
);
CREATE INDEX IF NOT EXISTS listings_repost ON listings(repost_hash);
CREATE INDEX IF NOT EXISTS listings_seen   ON listings(first_seen_at);
CREATE TABLE IF NOT EXISTS chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,        -- 'user' | 'assistant'
    content    TEXT NOT NULL,        -- JSON message content blocks
    created_at TEXT NOT NULL
);
"""


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
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- settings ------------------------------------------------------------

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

    # -- queries ---------------------------------------------------------------

    def upsert_query(self, name: str, spec: dict) -> None:
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO queries(name,spec,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec, updated_at=excluded.updated_at",
                (name, json.dumps(spec), now, now),
            )

    def get_query(self, name: str) -> dict | None:
        row = self._db.execute("SELECT * FROM queries WHERE name=?", (name,)).fetchone()
        return self._query_dict(row) if row else None

    def list_queries(self, include_paused: bool = True) -> list[dict]:
        rows = self._db.execute("SELECT * FROM queries ORDER BY name").fetchall()
        out = [self._query_dict(r) for r in rows]
        return out if include_paused else [q for q in out if not q["paused"]]

    def set_query_paused(self, name: str, paused: bool) -> bool:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE queries SET paused=?, updated_at=? WHERE name=?",
                (int(paused), _now(), name),
            )
        return cur.rowcount > 0

    def delete_query(self, name: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM queries WHERE name=?", (name,))
        return cur.rowcount > 0

    @staticmethod
    def _query_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["spec"] = json.loads(d["spec"])
        d["paused"] = bool(d["paused"])
        return d

    # -- listings ----------------------------------------------------------------

    def seen(self, listing_id: str, repost_hash: str | None = None) -> bool:
        """A listing counts as seen if its id OR its repost hash is known."""
        if self._db.execute("SELECT 1 FROM listings WHERE id=?", (listing_id,)).fetchone():
            return True
        if repost_hash and self._db.execute(
            "SELECT 1 FROM listings WHERE repost_hash=?", (repost_hash,)
        ).fetchone():
            return True
        return False

    def add_listing(self, listing: dict) -> int:
        """Insert a new listing, assigning the next short_ref. Returns short_ref."""
        with self._lock, self._db:
            row = self._db.execute("SELECT COALESCE(MAX(short_ref),0)+1 AS n FROM listings").fetchone()
            ref = row["n"]
            cols = dict(listing)
            cols["image_urls"] = json.dumps(cols.get("image_urls") or [])
            cols.setdefault("first_seen_at", _now())
            cols["short_ref"] = ref
            keys = ",".join(cols)
            self._db.execute(
                f"INSERT INTO listings({keys}) VALUES({','.join('?' * len(cols))})",
                tuple(cols.values()),
            )
        return ref

    def update_listing(self, listing_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE listings SET {sets} WHERE id=?", (*fields.values(), listing_id)
            )

    def mark_notified(self, listing_id: str) -> None:
        self.update_listing(listing_id, notified_at=_now())

    def get_listing_by_ref(self, short_ref: int) -> dict | None:
        row = self._db.execute("SELECT * FROM listings WHERE short_ref=?", (short_ref,)).fetchone()
        return self._listing_dict(row) if row else None

    def recent_listings(self, limit: int = 20, matched_only: bool = False) -> list[dict]:
        where = "WHERE judge_verdict='match'" if matched_only else ""
        rows = self._db.execute(
            f"SELECT * FROM listings {where} ORDER BY first_seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._listing_dict(r) for r in rows]

    @staticmethod
    def _listing_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["image_urls"] = json.loads(d["image_urls"] or "[]")
        return d

    # -- chat history ----------------------------------------------------------

    def append_chat(self, role: str, content: Any) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO chat_history(role,content,created_at) VALUES(?,?,?)",
                (role, json.dumps(content), _now()),
            )

    def recent_chat(self, limit: int = 40) -> list[dict]:
        """Most recent `limit` turns, oldest first (ready for the messages API)."""
        rows = self._db.execute(
            "SELECT role,content FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in reversed(rows)]
