"""Facebook Marketplace fetcher (Playwright, persistent logged-in profile).

The risky source (PLAN §2, §4.3): no public API, aggressive anti-bot. Design
priorities, in order: (1) never take down the pass — every failure is caught
and reported, never raised; (2) stay polite — one search URL per query, first
two screens, human-ish pacing; (3) fail safe on a login wall via a circuit
breaker that disables the source for a cooldown and surfaces one message.

Data comes from the JSON Facebook embeds in the search page (the
`marketplace_search` Relay payload) rather than the DOM, which changes shape
constantly. The extraction is defensive: unknown shape -> zero results, not a
crash. Login is a one-time interactive step (scripts/fb_login.py) that
populates the profile dir; this fetcher only consumes that session.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import random
import re
import urllib.parse

from finder.store import Store

log = logging.getLogger(__name__)

SOURCE = "facebook"
_MAX_DETAIL_FETCHES = 20
_COOLDOWN_HOURS = 24
_CIRCUIT_KEY = "facebook_circuit"
_NAV_TIMEOUT_MS = 45_000

# Login-wall / checkpoint signals in the page URL or body.
_CHECKPOINT_URL_MARKERS = ("/checkpoint", "/login", "login_alert", "/recover",
                           "two_factor", "/confirmemail")
_CHECKPOINT_TEXT_MARKERS = (
    "you must log in to continue",
    "log in to facebook",
    "please enter your password",
    "we've detected unusual activity",
    "confirm it's you",
    "temporarily blocked",
)


class CheckpointError(Exception):
    """Facebook demanded interactive auth — trip the circuit breaker."""


def build_search_url(location: dict, query_spec: dict) -> str:
    lat = location.get("lat")
    lon = location.get("lon")
    radius_km = round(float(location.get("radius_miles", 15)) * 1.60934)
    query = " ".join(query_spec.get("keywords", [])) or "furniture"
    params = {"query": query, "exact": "false"}
    if query_spec.get("max_price"):
        params["maxPrice"] = int(query_spec["max_price"])
    if lat is not None and lon is not None:
        params["latitude"] = round(float(lat), 4)
        params["longitude"] = round(float(lon), 4)
        params["radius"] = radius_km
    # /marketplace/<city>/search when we only have a place slug; the geo params
    # above take precedence when present.
    city = location.get("facebook_city", "").strip("/")
    base = f"https://www.facebook.com/marketplace/{city}/search" if city \
        else "https://www.facebook.com/marketplace/search"
    return f"{base}?{urllib.parse.urlencode(params)}"


def _looks_like_checkpoint(url: str, body_text: str) -> bool:
    low_url = url.lower()
    if any(m in low_url for m in _CHECKPOINT_URL_MARKERS):
        return True
    low = body_text[:5000].lower()
    return any(m in low for m in _CHECKPOINT_TEXT_MARKERS)


def _iter_listing_nodes(obj):
    """Walk the embedded Relay JSON, yielding marketplace listing dicts.

    A listing node is identified structurally (has a marketplace_listing_id /
    listing id plus a title-ish field), so it survives Facebook renaming the
    surrounding envelope."""
    if isinstance(obj, dict):
        lid = obj.get("id") or obj.get("marketplace_listing_id")
        title = obj.get("marketplace_listing_title") or obj.get("custom_title")
        if lid and title and ("listing_price" in obj or "formatted_price" in obj
                              or obj.get("__typename") == "MarketplaceListing"):
            yield obj
        for v in obj.values():
            yield from _iter_listing_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_listing_nodes(v)


def _price_of(node: dict) -> float | None:
    price = node.get("listing_price") or {}
    amount = price.get("amount") or price.get("amount_with_offset")
    try:
        val = float(amount)
        if price.get("amount_with_offset") and not price.get("amount"):
            val /= 100.0
        return val
    except (TypeError, ValueError):
        return None


def _photo_urls(node: dict) -> list[str]:
    urls = []
    primary = (((node.get("primary_listing_photo") or {}).get("image")) or {}).get("uri")
    if primary:
        urls.append(primary)
    for photo in node.get("listing_photos") or []:
        uri = ((photo or {}).get("image") or {}).get("uri")
        if uri and uri not in urls:
            urls.append(uri)
    return urls


def parse_search_payload(html: str) -> list[dict]:
    """Extract listing summaries from the JSON blobs embedded in a search page."""
    nodes: dict[str, dict] = {}
    # Facebook inlines many <script type="application/json"> Relay payloads.
    for blob in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>',
                           html, re.DOTALL):
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _iter_listing_nodes(data):
            native_id = str(node.get("id") or node.get("marketplace_listing_id"))
            if native_id in nodes:
                continue
            title = (node.get("marketplace_listing_title")
                     or node.get("custom_title") or "").strip()
            loc = ((node.get("location") or {}).get("reverse_geocode") or {}).get("city")
            nodes[native_id] = {
                "id": f"{SOURCE}:{native_id}",
                "source": SOURCE,
                "url": f"https://www.facebook.com/marketplace/item/{native_id}/",
                "title": title,
                "price": _price_of(node),
                "location_text": loc,
                "image_urls": _photo_urls(node),
                "description": (node.get("redacted_description") or {}).get("text")
                or node.get("marketplace_listing_title"),
            }
    return list(nodes.values())


class Circuit:
    """Persisted circuit breaker keyed in the store settings, so a cooldown
    survives daemon restarts."""

    def __init__(self, store: Store):
        self.store = store

    def state(self) -> dict:
        return self.store.get_setting(_CIRCUIT_KEY) or {}

    def is_open(self) -> tuple[bool, str | None]:
        st = self.state()
        until = st.get("open_until")
        if not until:
            return False, None
        if _now() >= until:
            return False, None
        return True, st.get("reason")

    def trip(self, reason: str) -> None:
        until = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(hours=_COOLDOWN_HOURS)).isoformat(timespec="seconds")
        self.store.set_setting(_CIRCUIT_KEY, {"open_until": until, "reason": reason,
                                              "tripped_at": _now()})
        log.warning("facebook circuit tripped until %s: %s", until, reason)

    def reset(self) -> None:
        if self.state():
            self.store.set_setting(_CIRCUIT_KEY, {})


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class FacebookFetcher:
    name = SOURCE
    max_detail_fetches = _MAX_DETAIL_FETCHES

    def __init__(self, store: Store, profile_dir: str = ".fb-profile",
                 headless: bool = True):
        self.store = store
        self.profile_dir = profile_dir
        self.headless = headless
        self.circuit = Circuit(store)
        self._context = None
        self._pw = None
        # Filled by search(); detail()/fetch_image() reuse the same context.
        self._page = None

    async def _ensure_context(self):
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            self.profile_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        self._context.set_default_navigation_timeout(_NAV_TIMEOUT_MS)

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._context = self._pw = self._page = None

    async def _human_pause(self) -> None:
        await asyncio.sleep(random.uniform(2.5, 6.0))

    async def search(self, location: dict, query_spec: dict) -> list[dict]:
        """Returns summary rows. Raises CheckpointError to trip the breaker;
        returns [] on any other trouble (the pass logs and moves on)."""
        await self._ensure_context()
        self._page = await self._context.new_page()
        url = build_search_url(location, query_spec)
        log.info("facebook search: %s", url)
        await self._page.goto(url, wait_until="domcontentloaded")
        await self._human_pause()

        body = await self._page.content()
        if _looks_like_checkpoint(self._page.url, await self._page.inner_text("body")):
            raise CheckpointError(f"login/checkpoint wall at {self._page.url}")

        # Two gentle scrolls to trigger the first couple of result batches.
        for _ in range(2):
            await self._page.mouse.wheel(0, 2200)
            await self._human_pause()
        body = await self._page.content()
        return parse_search_payload(body)

    async def detail(self, row: dict) -> dict:
        """Marketplace search already carries price/photos/short description;
        the item page mostly adds nothing we can rely on and doubles request
        volume, so detail is a no-op enrichment for FBM."""
        return dict(row)

    async def fetch_image(self, url: str) -> bytes | None:
        try:
            await self._ensure_context()
            resp = await self._context.request.get(url, timeout=_NAV_TIMEOUT_MS)
            if resp.ok:
                return await resp.body()
        except Exception as e:
            log.warning("facebook image fetch failed %s: %s", url, e)
        return None
