"""Shared fetcher plumbing: polite pacing and a common listing shape.

A fetcher produces dicts matching the listings schema in store.py:
  id ("<source>:<native_id>"), source, title, description, price, currency,
  url, image_urls[], location_text, lat, lon, posted_at
Search returns summary rows (cheap, one page); detail() enriches one row
(one request per unseen listing only — dedup happens between the two).
"""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
_DELAY_RANGE_S = (2.0, 6.0)


async def polite_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """Jittered delay before every request; one retry on transient failure."""
    await asyncio.sleep(random.uniform(*_DELAY_RANGE_S))
    try:
        r = await client.get(url, **kwargs)
        r.raise_for_status()
        return r
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        log.warning("retrying %s after %s", url, e)
        await asyncio.sleep(random.uniform(*_DELAY_RANGE_S))
        r = await client.get(url, **kwargs)
        r.raise_for_status()
        return r


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30,
    )
