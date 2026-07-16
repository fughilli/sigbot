"""One scrape pass: fetch -> dedup -> stage filters -> notify.

Every fetched listing is stored with a judgement trail — one entry per stage,
each with an outcome and the reason — so the dashboard can show WHY a listing
was surfaced or discarded, not just the survivors. Stages today:

  hard_filter  price cap / keyword sanity / has-image   (free)
  notify       posted to the Signal group               (terminal for M2)

M3 inserts `clip` and `judge` stages between them.
"""

from __future__ import annotations

import hashlib
import logging

from finder.config import Config
from finder.events import EventBus
from finder.fetchers.craigslist import CraigslistFetcher
from finder.notify.signal import SignalClient
from finder.store import Store

log = logging.getLogger(__name__)


def repost_hash(listing: dict) -> str:
    first_img = (listing.get("image_urls") or [""])[0]
    key = f"{listing.get('title', '')}|{listing.get('price')}|{first_img}"
    return hashlib.sha1(key.encode()).hexdigest()


def hard_filter(listing: dict, spec: dict) -> tuple[bool, dict]:
    """Returns (passed, checks). Each check records its own verdict so the
    judgement trail explains exactly which constraint failed."""
    checks: dict = {}

    cap = spec.get("max_price")
    price = listing.get("price")
    if cap and price is not None and price > cap:
        checks["price"] = f"reject: ${price:.0f} over ${cap:.0f} cap"
    elif cap and price is None:
        checks["price"] = "pass (unlisted price, cap not enforceable)"
    else:
        checks["price"] = "pass"

    keywords = [k.lower() for k in spec.get("keywords", [])]
    haystack = f"{listing.get('title', '')} {listing.get('description', '')}".lower()
    if keywords and not any(k in haystack for k in keywords):
        checks["keywords"] = f"reject: none of {keywords} in title/description"
    else:
        checks["keywords"] = "pass"

    if not listing.get("image_urls"):
        checks["images"] = "reject: no photos (nothing to judge aesthetics on)"
    else:
        checks["images"] = f"pass ({len(listing['image_urls'])} photos)"

    passed = not any(v.startswith("reject") for v in checks.values())
    return passed, checks


def format_notification(listing: dict, ref: int) -> str:
    price = f"${listing['price']:.0f}" if listing.get("price") is not None else "$?"
    loc = listing.get("location_text") or listing["source"]
    return (
        f"\U0001f6cb #{ref} · {price} — {listing.get('title', '(untitled)')}"
        f" ({listing['source']}, {loc})\n{listing.get('url', '')}"
    )


async def run_pass(config: Config, store: Store, client: SignalClient,
                   group: dict, bus: EventBus | None = None) -> dict:
    def emit(event_type: str, **data) -> None:
        if bus:
            bus.publish(event_type, **data)

    queries = store.list_queries(include_paused=False)
    location = store.get_setting("location")
    if not queries or not location:
        log.info("pass skipped: no active queries or no location set")
        emit("pass.skipped", reason="no active queries or location not set")
        return {"skipped": "no active queries or location not set"}

    fetchers = []
    if config.sources.get("craigslist", {}).get("enabled"):
        fetchers.append(CraigslistFetcher())

    summary = {"fetched": 0, "new": 0, "rejected": 0, "surfaced": 0, "errors": 0}
    emit("pass.start", queries=[q["name"] for q in queries],
         sources=[f.name for f in fetchers])

    for query in queries:
        spec = query["spec"]
        for fetcher in fetchers:
            try:
                rows = await fetcher.search(location, spec)
            except Exception as e:
                log.exception("search failed: %s/%s", fetcher.name, query["name"])
                emit("source.error", source=fetcher.name, query=query["name"], error=str(e))
                summary["errors"] += 1
                continue
            summary["fetched"] += len(rows)
            emit("search.done", source=fetcher.name, query=query["name"],
                 results=len(rows))

            detail_budget = fetcher.max_detail_fetches
            for row in rows:
                if store.seen(row["id"]):
                    continue
                if detail_budget <= 0:
                    log.info("detail budget exhausted for %s", fetcher.name)
                    emit("source.budget_exhausted", source=fetcher.name)
                    break
                detail_budget -= 1
                try:
                    listing = await fetcher.detail(row)
                except Exception as e:
                    log.warning("detail failed %s: %s", row["url"], e)
                    summary["errors"] += 1
                    continue

                rhash = repost_hash(listing)
                if store.seen(listing["id"], repost_hash=rhash):
                    emit("listing.repost", id=listing["id"], title=listing.get("title"))
                    continue
                listing["repost_hash"] = rhash
                listing["query_name"] = query["name"]
                listing["outcome"] = "pending"
                ref = store.add_listing(listing)
                summary["new"] += 1
                emit("listing.new", short_ref=ref, id=listing["id"],
                     title=listing.get("title"), price=listing.get("price"),
                     url=listing.get("url"),
                     image=(listing.get("image_urls") or [None])[0],
                     query=query["name"])

                passed, checks = hard_filter(listing, spec)
                store.record_judgement(
                    listing["id"], "hard_filter",
                    "pass" if passed else "rejected_filter", checks=checks,
                )
                emit("listing.judged", short_ref=ref, stage="hard_filter",
                     outcome="pass" if passed else "rejected_filter", checks=checks)
                if not passed:
                    summary["rejected"] += 1
                    continue

                # M3: clip + judge stages land here.

                try:
                    message = format_notification(listing, ref)
                    image = None
                    if listing.get("image_urls"):
                        image = await fetcher.fetch_image(listing["image_urls"][0])
                    if image:
                        await client.send_image(group["send_id"], message, image)
                    else:
                        await client.send(group["send_id"], message)
                    store.mark_notified(listing["id"])
                    store.record_judgement(listing["id"], "notify", "surfaced",
                                           reason="passed all active stages")
                    summary["surfaced"] += 1
                    emit("listing.surfaced", short_ref=ref, title=listing.get("title"))
                except Exception as e:
                    log.exception("notify failed for %s", listing["id"])
                    store.record_judgement(listing["id"], "notify", "pending",
                                           reason=f"send failed, will retry: {e}")
                    summary["errors"] += 1

    emit("pass.end", **summary)
    log.info("pass done: %s", summary)
    return summary
