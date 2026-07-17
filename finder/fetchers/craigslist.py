"""Craigslist fetcher.

Parses the static no-JS markup craigslist embeds in search pages
(`li.cl-static-search-result` — verified live 2026-07, fixture in
tests/fixtures/cl_search.html) and classic detail pages (#postingbody,
.price, time.timeago, #map data-lat/lon, images.craigslist.org gallery).
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from finder.fetchers import base

log = logging.getLogger(__name__)

SOURCE = "craigslist"
_CATEGORY = "fua"  # furniture - all
_MAX_DETAIL_FETCHES = 60  # per-source politeness ceiling; pipeline budget caps lower
_IMG_RE = re.compile(r"https://images\.craigslist\.org/[^\"\s]+_600x450\.jpg")

# One search per keyword, merged. Craigslist's `|` is NOT an OR of phrases — it
# ORs the two adjacent WORDS while spaces AND, so joining multi-word keyword
# phrases with `|` collapses to a near-AND query that matches almost nothing
# (verified 2026-07: 8 joined phrases -> 14 hits; the same phrases searched
# separately and unioned -> 541). So each keyword is its own unquoted search
# (Craigslist's natural AND-of-words, broad) and the results are merged.
_MAX_KEYWORD_SEARCHES = 8  # bound request volume per query per pass
# Craigslist 404s on a `query` param longer than 180 chars; a single keyword is
# far shorter, but guard anyway.
_MAX_QUERY_LEN = 180


def search_url(location: dict, keyword: str, max_price: float | None = None) -> str:
    site = location.get("craigslist_site", "sfbay")
    params = [
        ("query", keyword[:_MAX_QUERY_LEN]),
        ("postal", location.get("postal", "")),
        ("search_distance", str(int(location.get("radius_miles", 15)))),
    ]
    if max_price:
        params.append(("max_price", str(int(max_price))))
    qs = str(httpx.QueryParams([(k, v) for k, v in params if v]))
    return f"https://{site}.craigslist.org/search/{_CATEGORY}?{qs}"


def parse_search(html: str) -> list[dict]:
    """Summary rows from a search page; native id is the URL's last path token."""
    out = []
    for li in HTMLParser(html).css("li.cl-static-search-result"):
        a = li.css_first("a")
        if not a or not a.attributes.get("href"):
            continue
        url = a.attributes["href"]
        native_id = url.rstrip("/").rsplit("/", 1)[-1]
        title_node = li.css_first(".title")
        price_node = li.css_first(".price")
        loc_node = li.css_first(".location")
        price = None
        if price_node:
            digits = re.sub(r"[^\d.]", "", price_node.text())
            price = float(digits) if digits else None
        out.append({
            "id": f"{SOURCE}:{native_id}",
            "source": SOURCE,
            "url": url,
            "title": (title_node.text().strip() if title_node
                      else li.attributes.get("title", "")),
            "price": price,
            "location_text": loc_node.text().strip() if loc_node else None,
        })
    return out


def parse_detail(html: str, row: dict) -> dict:
    """Enrich a summary row with description, images, geo, posted time."""
    tree = HTMLParser(html)
    listing = dict(row)

    body = tree.css_first("#postingbody")
    if body:
        text = body.text().replace("QR Code Link to This Post", "").strip()
        listing["description"] = re.sub(r"\n{3,}", "\n\n", text)

    time_node = tree.css_first("time.date.timeago")
    if time_node:
        listing["posted_at"] = time_node.attributes.get("datetime")

    map_node = tree.css_first("#map")
    if map_node:
        try:
            listing["lat"] = float(map_node.attributes.get("data-latitude", ""))
            listing["lon"] = float(map_node.attributes.get("data-longitude", ""))
        except ValueError:
            pass

    seen: list[str] = []
    for url in _IMG_RE.findall(html):
        if url not in seen:
            seen.append(url)
    listing["image_urls"] = seen

    if listing.get("price") is None:
        price_node = tree.css_first(".price")
        if price_node:
            digits = re.sub(r"[^\d.]", "", price_node.text())
            listing["price"] = float(digits) if digits else None

    return listing


class CraigslistFetcher:
    name = SOURCE

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or base.make_client()

    async def search(self, location: dict, query_spec: dict) -> list[dict]:
        """One search per keyword (up to _MAX_KEYWORD_SEARCHES), merged by id.
        A single keyword failing doesn't sink the rest."""
        keywords = query_spec.get("keywords", [])
        max_price = query_spec.get("max_price")
        if len(keywords) > _MAX_KEYWORD_SEARCHES:
            log.info("craigslist: %d keywords, searching first %d",
                     len(keywords), _MAX_KEYWORD_SEARCHES)
        merged: dict[str, dict] = {}
        for keyword in keywords[:_MAX_KEYWORD_SEARCHES]:
            url = search_url(location, keyword, max_price)
            log.info("craigslist search %r", keyword)
            try:
                r = await base.polite_get(self.client, url)
            except httpx.HTTPError as e:
                log.warning("craigslist search failed for %r: %s", keyword, e)
                continue
            for row in parse_search(r.text):
                merged.setdefault(row["id"], row)
        log.info("craigslist: %d unique listings across %d keyword searches",
                 len(merged), min(len(keywords), _MAX_KEYWORD_SEARCHES))
        return list(merged.values())

    async def detail(self, row: dict) -> dict:
        r = await base.polite_get(self.client, row["url"])
        return parse_detail(r.text, row)

    async def fetch_image(self, url: str) -> bytes | None:
        try:
            r = await base.polite_get(self.client, url)
            return r.content
        except httpx.HTTPError as e:
            log.warning("image fetch failed %s: %s", url, e)
            return None

    max_detail_fetches = _MAX_DETAIL_FETCHES
