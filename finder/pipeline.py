"""One scrape pass: fetch -> dedup -> stage filters -> clip -> judge -> notify.

Every fetched listing is stored with a judgement trail — one entry per stage,
each with an outcome and the reason — so the dashboard can show WHY a listing
was surfaced or discarded, not just the survivors. Stages:

  hard_filter  price cap / keyword sanity / has-image      (free)
  clip         embedding similarity vs reference board     (local CPU)
  judge        Claude vision verdict on top-K clip scorers (metered)
  notify       posted to the Signal group

Queries with no aesthetic target (no reference images, no description) skip
clip+judge and surface straight from the hard filter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import re

from finder.config import Config
from finder.events import EventBus
from finder.fetchers.craigslist import CraigslistFetcher
from finder.match import judge as judge_mod
from finder.notify.signal import SignalClient
from finder.store import Store

log = logging.getLogger(__name__)

REFERENCES_DIR = pathlib.Path("references")
IMAGE_CACHE_DIR = pathlib.Path("cache/images")
# Per-pass ceiling on NEW detail-page fetches per source (politeness bound —
# each is a throttled HTTP request). Re-evaluating already-cached listings is
# free of this budget, so criteria changes reconsider the whole backlog.
DEFAULT_MAX_NEW_PER_PASS = 40
_scorer_singleton = None


def _default_scorer():
    global _scorer_singleton
    if _scorer_singleton is None:
        from finder.match.clip_scorer import ClipScorer  # defers torch import
        _scorer_singleton = ClipScorer()
    return _scorer_singleton


def repost_hash(listing: dict) -> str:
    first_img = (listing.get("image_urls") or [""])[0]
    key = f"{listing.get('title', '')}|{listing.get('price')}|{first_img}"
    return hashlib.sha1(key.encode()).hexdigest()


def criteria_fingerprint(spec: dict, ref_dir: pathlib.Path) -> str:
    """A stable hash of everything that affects a listing's verdict: the
    matching-relevant spec fields plus the reference-image set. When any of
    these change, a listing's stored hash no longer matches and it gets
    re-evaluated on the next pass."""
    parts = {k: spec.get(k) for k in
             ("keywords", "max_price", "aesthetic_description",
              "clip_threshold", "judge_top_k")}
    refs = []
    if ref_dir.is_dir():
        for p in sorted(ref_dir.iterdir()):
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                refs.append(f"{p.name}:{p.stat().st_mtime_ns}")
    blob = json.dumps({"spec": parts, "refs": refs}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


async def cached_image(fetcher, url: str) -> bytes | None:
    """Fetch an image, memoizing bytes to disk so re-evaluation under changed
    criteria doesn't re-download. fetcher may be None (re-eval with the source
    disabled) — then only the cache is consulted."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = IMAGE_CACHE_DIR / f"{key}.img"
    if path.exists():
        return path.read_bytes()
    if fetcher is None:
        return None
    data = await fetcher.fetch_image(url)
    if data:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            log.warning("image cache write failed (%s); continuing", e)
    return data


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

    # A keyword "phrase" matches if all its words appear somewhere in the
    # title+description (any order) — verbatim substring would wrongly reject
    # e.g. "mid century dining chairs" against "dining chairs, mid-century".
    keywords = [k.lower() for k in spec.get("keywords", [])]
    haystack = re.sub(r"[^a-z0-9 ]", " ",
                      f"{listing.get('title', '')} {listing.get('description', '')}".lower())
    words = set(haystack.split())
    def phrase_hits(phrase: str) -> bool:
        return all(w in words for w in re.sub(r"[^a-z0-9 ]", " ", phrase).split())
    if keywords and not any(phrase_hits(k) for k in keywords):
        checks["keywords"] = f"reject: no keyword phrase fully present in title/description"
    else:
        matched = [k for k in keywords if phrase_hits(k)]
        checks["keywords"] = f"pass (matched: {', '.join(matched) or 'no keywords set'})"

    if not listing.get("image_urls"):
        checks["images"] = "reject: no photos (nothing to judge aesthetics on)"
    else:
        checks["images"] = f"pass ({len(listing['image_urls'])} photos)"

    passed = not any(v.startswith("reject") for v in checks.values())
    return passed, checks


def format_notification(listing: dict, ref: int, reason: str | None = None) -> str:
    price = f"${listing['price']:.0f}" if listing.get("price") is not None else "$?"
    loc = listing.get("location_text") or listing["source"]
    lines = [
        f"\U0001f6cb #{ref} · {price} — {listing.get('title', '(untitled)')}"
        f" ({listing['source']}, {loc})"
    ]
    if reason:
        lines.append(reason)
    lines.append(listing.get("url", ""))
    return "\n".join(lines)


async def run_pass(config: Config, store: Store, client: SignalClient,
                   group: dict, bus: EventBus | None = None,
                   scorer=None, judge=None) -> dict:
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
    fb = None
    fb_cfg = config.sources.get("facebook", {})
    if fb_cfg.get("enabled"):
        from finder.fetchers.facebook import Circuit, FacebookFetcher
        open_, reason = Circuit(store).is_open()
        if open_:
            log.info("facebook source in cooldown: %s", reason)
            emit("source.circuit_open", source="facebook", reason=reason)
        else:
            fb = FacebookFetcher(store, profile_dir=fb_cfg.get("profile_dir", ".fb-profile"))
            fetchers.append(fb)

    summary = {"fetched": 0, "new": 0, "reevaluated": 0, "rejected": 0,
               "surfaced": 0, "errors": 0}
    emit("pass.start", queries=[q["name"] for q in queries],
         sources=[f.name for f in fetchers])
    fetchers_by_source = {f.name: f for f in fetchers}

    def finalize(listing_id: str, chash: str) -> None:
        # Mark this listing as fully evaluated under the current criteria, so
        # the next pass skips it — UNLESS the criteria change again.
        store.update_listing(listing_id, criteria_hash=chash)

    async def notify(listing: dict, ref: int, source_fetcher, image: bytes | None,
                     reason: str | None = None) -> None:
        try:
            # Only ping once per listing. A re-evaluated listing that already
            # matched keeps its 'surfaced' outcome without a duplicate message.
            if not listing.get("notified_at"):
                message = format_notification(listing, ref, reason)
                if image is None and listing.get("image_urls"):
                    image = await cached_image(source_fetcher, listing["image_urls"][0])
                if image:
                    await client.send_image(group["send_id"], message, image)
                else:
                    await client.send(group["send_id"], message)
                store.mark_notified(listing["id"])
                summary["surfaced"] += 1
                emit("listing.surfaced", short_ref=ref, title=listing.get("title"))
            store.record_judgement(listing["id"], "notify", "surfaced",
                                   reason=reason or "passed all active stages")
        except Exception as e:
            log.exception("notify failed for %s", listing["id"])
            store.record_judgement(listing["id"], "notify", "pending",
                                   reason=f"send failed, will retry: {e}")
            summary["errors"] += 1

    for query in queries:
        spec = query["spec"]
        ref_dir = REFERENCES_DIR / query["name"]
        chash = criteria_fingerprint(spec, ref_dir)
        has_aesthetic = bool((spec.get("aesthetic_description") or "").strip()) or (
            ref_dir.is_dir() and any(ref_dir.iterdir())
        )
        budget = spec.get("max_new_per_pass", DEFAULT_MAX_NEW_PER_PASS)
        # (listing, ref, source_name, images, clip_score) awaiting the judge.
        judge_pool: list[tuple[dict, int, str, list[bytes], float]] = []

        async def evaluate(listing: dict, ref: int, source_fetcher) -> None:
            """Run hard_filter + clip for one listing; queue survivors for the
            batched judge. Finalizes the criteria hash at every terminal
            outcome (but NOT for clip-passers, which the judge finalizes)."""
            passed, checks = hard_filter(listing, spec)
            store.record_judgement(
                listing["id"], "hard_filter",
                "pass" if passed else "rejected_filter", checks=checks)
            emit("listing.judged", short_ref=ref, stage="hard_filter",
                 outcome="pass" if passed else "rejected_filter", checks=checks)
            if not passed:
                summary["rejected"] += 1
                finalize(listing["id"], chash)
                return

            if not has_aesthetic:
                store.record_judgement(listing["id"], "clip", "pass",
                                       reason="skipped: no aesthetic target configured")
                await notify(listing, ref, source_fetcher, None)
                finalize(listing["id"], chash)
                return

            images = []
            for url in (listing.get("image_urls") or [])[:4]:
                data = await cached_image(source_fetcher, url)
                if data:
                    images.append(data)
            active_scorer = scorer or _default_scorer()
            try:
                result = await asyncio.to_thread(active_scorer.score, images, spec, ref_dir)
            except Exception as e:
                log.exception("clip scoring failed for %s", listing["id"])
                store.record_judgement(listing["id"], "clip", "pending",
                                       reason=f"scorer error: {e}")
                summary["errors"] += 1
                return  # not finalized -> retried next pass

            if "skipped" in result:
                store.record_judgement(listing["id"], "clip", "pass",
                                       reason=f"skipped: {result['skipped']}")
                await notify(listing, ref, source_fetcher, images[0] if images else None)
                finalize(listing["id"], chash)
                return

            threshold = spec.get("clip_threshold", 0.24)
            clip_pass = result["score"] >= threshold
            store.record_judgement(
                listing["id"], "clip", "pass" if clip_pass else "rejected_clip",
                score=result["score"], threshold=threshold, clip_score=result["score"],
                reason=f"visual={result['visual']} text={result['text']} refs={result['refs']}")
            emit("listing.judged", short_ref=ref, stage="clip",
                 outcome="pass" if clip_pass else "rejected_clip",
                 score=result["score"], threshold=threshold)
            if not clip_pass:
                summary["rejected"] += 1
                finalize(listing["id"], chash)
                return
            judge_pool.append((listing, ref, source_fetcher, images, result["score"]))

        # -- 1. new listings from each source (fetch-budget limited) -----------
        for fetcher in fetchers:
            try:
                rows = await fetcher.search(location, spec)
            except Exception as e:
                # A Facebook login wall trips the circuit breaker (disable the
                # source for a cooldown) and tells the user once, rather than
                # retrying into a lockout. Detected by class name so the
                # pipeline needn't import playwright.
                if type(e).__name__ == "CheckpointError" and fb is not None:
                    fb.circuit.trip(str(e))
                    await client.send(
                        group["send_id"],
                        "⚠️ Facebook wants a manual login — Marketplace "
                        "paused for 24h. Run scripts/fb_login.py on the box when "
                        "convenient, then tell me to retry Facebook.")
                    emit("source.checkpoint", source="facebook", error=str(e))
                    break  # stop hitting FB entirely this pass
                log.exception("search failed: %s/%s", fetcher.name, query["name"])
                emit("source.error", source=fetcher.name, query=query["name"], error=str(e))
                summary["errors"] += 1
                continue
            summary["fetched"] += len(rows)
            emit("search.done", source=fetcher.name, query=query["name"], results=len(rows))

            detail_budget = min(budget, fetcher.max_detail_fetches)
            for row in rows:
                if store.seen(row["id"]):
                    continue
                if detail_budget <= 0:
                    log.info("new-fetch budget exhausted for %s", fetcher.name)
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
                     image=(listing.get("image_urls") or [None])[0], query=query["name"])
                await evaluate(listing, ref, fetcher)

        # -- 2. cached listings whose criteria changed (re-eval, no fetch) -----
        # New clip-passers from step 1 are queued but not yet finalized, so they
        # match the stale query too — exclude them to avoid double-processing.
        pool_ids = {item[0]["id"] for item in judge_pool}
        stale = [l for l in store.listings_for_query(query["name"], stale_criteria=chash)
                 if l["id"] not in pool_ids]
        if stale:
            log.info("re-evaluating %d cached listings for %s under new criteria",
                     len(stale), query["name"])
            emit("query.reevaluating", query=query["name"], count=len(stale))
        for listing in stale:
            summary["reevaluated"] += 1
            await evaluate(listing, listing["short_ref"],
                           fetchers_by_source.get(listing["source"]))

        # -- 3. judge stage: top-K by clip score, per query --------------------
        if judge_pool:
            top_k = int(spec.get("judge_top_k", 8))
            judge_pool.sort(key=lambda item: item[4], reverse=True)
            active_judge = judge or judge_mod.Judge(
                config.anthropic_api_key, config.agent_model)
            ref_images = judge_mod.load_reference_images(ref_dir)

            for i, (listing, ref, source_fetcher, images, clip_score) in enumerate(judge_pool):
                if i >= top_k:
                    # Not judged this pass — leave the criteria hash unset so the
                    # next pass reconsiders it (progressive coverage: K judged
                    # per pass until the clip-passing backlog drains).
                    store.record_judgement(
                        listing["id"], "judge", "deferred",
                        reason=f"clip-passed (score {clip_score}); below "
                               f"top-{top_k} judge budget this pass")
                    emit("listing.judged", short_ref=ref, stage="judge",
                         outcome="deferred", reason="below top-K budget")
                    continue
                try:
                    verdict = await active_judge.judge(listing, images, spec, ref_images)
                except Exception as e:
                    log.exception("judge failed for %s", listing["id"])
                    store.record_judgement(listing["id"], "judge", "pending",
                                           reason=f"judge error: {e}")
                    summary["errors"] += 1
                    continue  # not finalized -> retried next pass
                outcome = "pass" if verdict["match"] else "rejected_judge"
                store.record_judgement(
                    listing["id"], "judge", outcome,
                    judge_verdict="match" if verdict["match"] else "no_match",
                    judge_reason=verdict["reason"], reason=verdict["reason"],
                    confidence=verdict["confidence"])
                emit("listing.judged", short_ref=ref, stage="judge", outcome=outcome,
                     reason=verdict["reason"], confidence=verdict["confidence"])
                if verdict["match"]:
                    await notify(listing, ref, source_fetcher,
                                 images[0] if images else None, reason=verdict["reason"])
                else:
                    summary["rejected"] += 1
                finalize(listing["id"], chash)

    if fb is not None:
        await fb.close()

    emit("pass.end", **summary)
    log.info("pass done: %s", summary)
    return summary
