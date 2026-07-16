"""Tool layer between the Claude agent and the store. All validation lives
here — the store is dumb, the model is untrusted with raw writes.

Dispatch is synchronous by design (pure store/file I/O); anything that needs
the event loop (running a scrape pass) goes through injected callbacks.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Callable

from finder.store import Store

MIN_CADENCE_MINUTES = 30
MAX_CADENCE_MINUTES = 7 * 24 * 60
DEFAULT_CADENCE_MINUTES = 240

TOOL_DEFS: list[dict] = [
    {
        "name": "get_config",
        "description": "Current search location, radius, cadence, and all queries.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_location",
        "description": "Set the search area: US postal code, craigslist site slug (e.g. 'sfbay'), radius in miles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "postal": {"type": "string"},
                "craigslist_site": {"type": "string"},
                "radius_miles": {"type": "number"},
                "latitude": {"type": "number",
                             "description": "optional; sharpens Facebook Marketplace radius search"},
                "longitude": {"type": "number", "description": "optional; pairs with latitude"},
            },
            "required": ["postal", "radius_miles"],
        },
    },
    {
        "name": "set_cadence",
        "description": "How often to scrape, in minutes (30–10080).",
        "input_schema": {
            "type": "object",
            "properties": {"minutes": {"type": "integer"}},
            "required": ["minutes"],
        },
    },
    {
        "name": "upsert_query",
        "description": "Create or update a named search (e.g. 'mcm-couch'). Omitted fields keep their old values on update.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case identifier"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "max_price": {"type": "number"},
                "aesthetic_description": {"type": "string"},
                "clip_threshold": {"type": "number"},
                "judge_top_k": {"type": "integer"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "pause_query",
        "description": "Pause or resume a query.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "paused": {"type": "boolean"}},
            "required": ["name", "paused"],
        },
    },
    {
        "name": "delete_query",
        "description": "Delete a query permanently.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "add_reference_images",
        "description": "Attach the images from the user's current message to a query's aesthetic reference set.",
        "input_schema": {
            "type": "object",
            "properties": {"query_name": {"type": "string"}},
            "required": ["query_name"],
        },
    },
    {
        "name": "run_now",
        "description": "Trigger a scrape pass immediately.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reevaluate_query",
        "description": "Force every already-seen listing for a query to be "
                       "re-judged on the next pass. Editing a query's criteria "
                       "already does this automatically; use this only after a "
                       "code/heuristic change the criteria can't see.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "status",
        "description": "Bot health: last/next run, listing counts, source state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_recent",
        "description": "Recent listings, optionally only judged matches. Includes near-misses when matched_only is false.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "matched_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_listing",
        "description": "Full detail for one listing by its #ref number.",
        "input_schema": {
            "type": "object",
            "properties": {"short_ref": {"type": "integer"}},
            "required": ["short_ref"],
        },
    },
    {
        "name": "mark_feedback",
        "description": "Record the user's verdict on a listing ('up' = more like this, 'down' = not this).",
        "input_schema": {
            "type": "object",
            "properties": {
                "short_ref": {"type": "integer"},
                "feedback": {"type": "string", "enum": ["up", "down"]},
            },
            "required": ["short_ref", "feedback"],
        },
    },
]


class Tools:
    def __init__(
        self,
        store: Store,
        references_dir: str | pathlib.Path = "references",
        trigger_run: Callable[[], str] | None = None,
        get_status: Callable[[], dict] | None = None,
        on_cadence_change: Callable[[], None] | None = None,
    ):
        self.store = store
        self.references_dir = pathlib.Path(references_dir)
        self._trigger_run = trigger_run
        self._get_status = get_status
        self._on_cadence_change = on_cadence_change
        # Set by the agent per-message: decoded image bytes from attachments.
        self.pending_images: list[tuple[bytes, str]] = []  # (data, ext)

    def dispatch(self, name: str, tool_input: dict) -> str:
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                return _err(f"unknown tool {name!r}")
            return json.dumps(handler(**tool_input))
        except TypeError as e:
            return _err(f"bad arguments: {e}")
        except ValueError as e:
            return _err(str(e))

    # -- config -------------------------------------------------------------

    def _t_get_config(self) -> dict:
        return {
            "location": self.store.get_setting("location"),
            "cadence_minutes": self.store.get_setting("cadence_minutes", DEFAULT_CADENCE_MINUTES),
            "queries": self.store.list_queries(),
        }

    def _t_set_location(self, postal: str, radius_miles: float, craigslist_site: str = "",
                        latitude: float | None = None, longitude: float | None = None) -> dict:
        if not re.fullmatch(r"\d{5}", postal):
            raise ValueError("postal must be a 5-digit US zip")
        if not 1 <= radius_miles <= 100:
            raise ValueError("radius_miles must be between 1 and 100")
        loc = self.store.get_setting("location") or {}
        loc.update({"postal": postal, "radius_miles": radius_miles})
        if craigslist_site:
            if not re.fullmatch(r"[a-z]+", craigslist_site):
                raise ValueError("craigslist_site must be a lowercase slug like 'sfbay'")
            loc["craigslist_site"] = craigslist_site
        if latitude is not None or longitude is not None:
            if latitude is None or longitude is None:
                raise ValueError("latitude and longitude must be set together")
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("latitude/longitude out of range")
            loc["lat"], loc["lon"] = latitude, longitude
        self.store.set_setting("location", loc)
        return {"ok": True, "location": loc}

    def _t_set_cadence(self, minutes: int) -> dict:
        if not MIN_CADENCE_MINUTES <= minutes <= MAX_CADENCE_MINUTES:
            raise ValueError(
                f"cadence must be {MIN_CADENCE_MINUTES}–{MAX_CADENCE_MINUTES} minutes"
            )
        self.store.set_setting("cadence_minutes", minutes)
        if self._on_cadence_change:
            self._on_cadence_change()
        return {"ok": True, "cadence_minutes": minutes, "note": "takes effect immediately"}

    # -- queries -------------------------------------------------------------

    def _t_upsert_query(self, name: str, **fields: Any) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", name):
            raise ValueError("name must be kebab-case, e.g. 'mcm-couch'")
        if "max_price" in fields and fields["max_price"] <= 0:
            raise ValueError("max_price must be positive")
        if "clip_threshold" in fields and not 0 <= fields["clip_threshold"] <= 1:
            raise ValueError("clip_threshold must be in [0, 1]")
        if "judge_top_k" in fields and not 1 <= fields["judge_top_k"] <= 25:
            raise ValueError("judge_top_k must be 1–25")
        if "keywords" in fields and not fields["keywords"]:
            raise ValueError("keywords must be non-empty")
        existing = self.store.get_query(name)
        spec = (existing["spec"] if existing else {
            "keywords": [], "max_price": None,
            "aesthetic_description": "", "clip_threshold": 0.24, "judge_top_k": 8,
        })
        spec.update({k: v for k, v in fields.items() if v is not None})
        if not spec["keywords"]:
            raise ValueError("a new query needs keywords")
        self.store.upsert_query(name, spec)
        return {"ok": True, "name": name, "spec": spec, "created": existing is None}

    def _t_pause_query(self, name: str, paused: bool) -> dict:
        if not self.store.set_query_paused(name, paused):
            raise ValueError(f"no query named {name!r}")
        return {"ok": True, "name": name, "paused": paused}

    def _t_delete_query(self, name: str) -> dict:
        if not self.store.delete_query(name):
            raise ValueError(f"no query named {name!r}")
        return {"ok": True, "deleted": name}

    def _t_add_reference_images(self, query_name: str) -> dict:
        if not self.store.get_query(query_name):
            raise ValueError(f"no query named {query_name!r}")
        if not self.pending_images:
            raise ValueError("no images attached to this message")
        qdir = self.references_dir / query_name
        qdir.mkdir(parents=True, exist_ok=True)
        start = len(list(qdir.iterdir()))
        saved = []
        for i, (data, ext) in enumerate(self.pending_images):
            p = qdir / f"ref-{start + i:04d}.{ext}"
            p.write_bytes(data)
            saved.append(p.name)
        self.pending_images = []
        return {"ok": True, "saved": saved, "total_refs": start + len(saved)}

    # -- ops ---------------------------------------------------------------------

    def _t_run_now(self) -> dict:
        if self._trigger_run is None:
            raise ValueError("scrape runner not wired up yet")
        return {"ok": True, "result": self._trigger_run()}

    def _t_reevaluate_query(self, name: str) -> dict:
        if not self.store.get_query(name):
            raise ValueError(f"no query named {name!r}")
        cleared = self.store.clear_criteria_hashes(name)
        result = self._trigger_run() if self._trigger_run else "run not wired up"
        return {"ok": True, "name": name, "listings_to_reevaluate": cleared,
                "result": result}

    def _t_status(self) -> dict:
        base = {
            "last_run": self.store.get_setting("last_run"),
            "queries": {q["name"]: ("paused" if q["paused"] else "active")
                        for q in self.store.list_queries()},
            "listings_seen": len(self.store.recent_listings(limit=100000)),
        }
        if self._get_status:
            base.update(self._get_status())
        return base

    # -- listings --------------------------------------------------------------

    def _t_list_recent(self, limit: int = 10, matched_only: bool = False) -> dict:
        limit = max(1, min(limit, 50))
        out = [
            {k: l[k] for k in
             ("short_ref", "title", "price", "url", "source", "clip_score",
              "judge_verdict", "judge_reason", "first_seen_at")}
            for l in self.store.recent_listings(limit=limit, matched_only=matched_only)
        ]
        return {"listings": out}

    def _t_get_listing(self, short_ref: int) -> dict:
        listing = self.store.get_listing_by_ref(short_ref)
        if not listing:
            raise ValueError(f"no listing #{short_ref}")
        return listing

    def _t_mark_feedback(self, short_ref: int, feedback: str) -> dict:
        listing = self.store.get_listing_by_ref(short_ref)
        if not listing:
            raise ValueError(f"no listing #{short_ref}")
        self.store.update_listing(listing["id"], feedback=feedback)
        return {"ok": True, "short_ref": short_ref, "feedback": feedback}


def _err(msg: str) -> str:
    return json.dumps({"error": msg})
