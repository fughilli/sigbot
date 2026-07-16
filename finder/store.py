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
    feedback     TEXT,               -- 'up' | 'down' | NULL
    outcome      TEXT,               -- pending|rejected_filter|rejected_clip|rejected_judge|surfaced
    judgement    TEXT                -- JSON list: one entry per pipeline stage
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
            self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations for DBs created before the column existed."""
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(listings)")}
        for col, decl in (("outcome", "TEXT"), ("judgement", "TEXT"),
                          ("criteria_hash", "TEXT")):
            if col not in have:
                self._db.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")

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

    def record_judgement(self, listing_id: str, stage: str, outcome: str,
                         **detail: Any) -> None:
        """Append a stage entry to the listing's judgement trail and set its
        current outcome. Stage-specific convenience columns (clip_score,
        judge_verdict, judge_reason) are mirrored when present in detail."""
        row = self._db.execute(
            "SELECT judgement FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
        trail = json.loads(row["judgement"]) if row and row["judgement"] else []
        trail.append({"stage": stage, "outcome": outcome, "at": _now(), **detail})
        fields: dict[str, Any] = {"judgement": json.dumps(trail), "outcome": outcome}
        for col in ("clip_score", "judge_verdict", "judge_reason"):
            if col in detail:
                fields[col] = detail[col]
        self.update_listing(listing_id, **fields)

    def outcome_counts(self) -> dict:
        rows = self._db.execute(
            "SELECT COALESCE(outcome,'unknown') o, COUNT(*) n FROM listings GROUP BY o"
        ).fetchall()
        return {r["o"]: r["n"] for r in rows}

    def get_listing_by_ref(self, short_ref: int) -> dict | None:
        row = self._db.execute("SELECT * FROM listings WHERE short_ref=?", (short_ref,)).fetchone()
        return self._listing_dict(row) if row else None

    def listings_for_query(self, query_name: str, stale_criteria: str | None = None,
                           limit: int = 1000) -> list[dict]:
        """Cached listings for a query. If stale_criteria is given, only those
        whose criteria_hash differs from it (i.e. need re-evaluation under the
        current criteria) — newest first."""
        if stale_criteria is None:
            rows = self._db.execute(
                "SELECT * FROM listings WHERE query_name=? ORDER BY first_seen_at DESC LIMIT ?",
                (query_name, limit)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM listings WHERE query_name=? "
                "AND (criteria_hash IS NULL OR criteria_hash != ?) "
                "ORDER BY first_seen_at DESC LIMIT ?",
                (query_name, stale_criteria, limit)).fetchall()
        return [self._listing_dict(r) for r in rows]

    def clear_criteria_hashes(self, query_name: str) -> int:
        """Force full re-evaluation of a query's listings on the next pass
        (e.g. after a code/heuristic change the criteria hash can't see)."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE listings SET criteria_hash=NULL WHERE query_name=?", (query_name,))
        return cur.rowcount

    def recent_listings(self, limit: int = 20, matched_only: bool = False,
                        outcome: str | None = None, query_name: str | None = None) -> list[dict]:
        clauses, params = [], []
        if matched_only:
            clauses.append("judge_verdict='match'")
        if outcome:
            clauses.append("outcome=?")
            params.append(outcome)
        if query_name:
            clauses.append("query_name=?")
            params.append(query_name)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM listings {where} ORDER BY first_seen_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._listing_dict(r) for r in rows]

    @staticmethod
    def _listing_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["image_urls"] = json.loads(d["image_urls"] or "[]")
        d["judgement"] = json.loads(d["judgement"] or "[]")
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
