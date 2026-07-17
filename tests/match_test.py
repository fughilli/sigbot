"""Judge + pipeline aesthetic-stage tests with fakes (no torch, no network).

The real CLIP model is exercised only in production — these tests cover the
orchestration: threshold gating, top-K budget, judgement trails, reasons in
notifications.
"""

import asyncio
import io
import json
import sys

import pytest

from finder import pipeline
from finder.config import Config, SignalConfig
from finder.match import judge as judge_mod
from finder.store import Store


# -- judge unit: content assembly + verdict parsing ---------------------------

class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs

        class Block:
            type = "text"
            text = json.dumps(self.payload)

        class Resp:
            content = [Block()]

        return Resp()


def make_judge(payload):
    j = judge_mod.Judge.__new__(judge_mod.Judge)
    j.model = "claude-haiku-4-5"

    class C:
        pass

    j.client = C()
    j.client.messages = FakeMessages(payload)
    return j


def _png_bytes():
    from PIL import Image

    img = Image.new("RGB", (2000, 1200), (200, 150, 100))  # over the 1568 edge cap
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_judge_builds_request_and_parses_verdict():
    j = make_judge({"match": True, "confidence": 1.7, "reason": "walnut frame"})
    listing = {"title": "Couch", "price": 300, "description": "d" * 3000}
    verdict = asyncio.run(j.judge(listing, [_png_bytes()],
                                  {"aesthetic_description": "mcm"}, [_png_bytes()]))
    assert verdict["match"] is True
    assert verdict["confidence"] == 1.0  # clamped
    assert verdict["reason"] == "walnut frame"

    kwargs = j.client.messages.last_kwargs
    assert kwargs["model"] == "claude-haiku-4-5"  # judge uses the configured model
    assert kwargs["output_config"]["format"]["schema"] == judge_mod.VERDICT_SCHEMA
    blocks = kwargs["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2  # 1 ref + 1 listing
    assert all(b["source"]["media_type"] == "image/jpeg" for b in images)
    # description truncated in prose block
    prose = next(b["text"] for b in blocks if b["type"] == "text" and "Title:" in b["text"])
    assert len(prose) < 2000


def test_resize_caps_long_edge():
    from PIL import Image

    resized = judge_mod._resize_jpeg(_png_bytes())
    img = Image.open(io.BytesIO(resized))
    assert max(img.size) <= 1568 and img.format == "JPEG"


# -- pipeline: clip gate + top-K judge budget ---------------------------------

class FakeFetcher:
    name = "craigslist"
    max_detail_fetches = 25

    def __init__(self, rows):
        self.rows = rows

    async def search(self, location, spec):
        return [{k: r[k] for k in ("id", "source", "url", "title", "price")}
                for r in self.rows]

    async def detail(self, row):
        return next(dict(r) for r in self.rows if r["id"] == row["id"])

    async def fetch_image(self, url):
        return b"img:" + url.encode()


class FakeSignal:
    def __init__(self):
        self.sent = []

    async def send(self, recipient, message, attachments_b64=None):
        self.sent.append(message)

    async def send_image(self, recipient, message, image, mime="image/jpeg"):
        self.sent.append(message)


class FakeScorer:
    """Scores by listing id via a canned table."""

    def __init__(self, table):
        self.table = table

    def score(self, images, spec, ref_dir):
        key = images[0].decode().split(":", 1)[1] if images else ""
        return self.table[key]


class FakeJudge:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    async def judge(self, listing, images, spec, ref_images):
        self.calls.append(listing["id"])
        return self.verdicts[listing["id"]]


def listing(n, price=300.0):
    return {"id": f"craigslist:{n}", "source": "craigslist", "url": f"http://x/{n}",
            "title": f"couch {n}", "price": price, "description": "a couch",
            "image_urls": [f"http://img/{n}.jpg"]}


def run_aesthetic_pass(tmp_path, rows, scorer, judge, top_k=8, threshold=0.24,
                       store=None, upsert=True, spec_overrides=None):
    import unittest.mock as mock

    if store is None:
        store = Store(tmp_path / "t.db")
        store.set_setting("location", {"postal": "94103", "radius_miles": 15})
    if upsert:
        spec = {"keywords": ["couch"], "max_price": 500,
                "aesthetic_description": "mid-century modern",
                "clip_threshold": threshold, "judge_top_k": top_k}
        spec.update(spec_overrides or {})
        store.upsert_query("mcm", spec)
    config = Config(signal=SignalConfig(api_url="x", bot_number="+1", user_id="u"),
                    db_path=str(tmp_path / "t.db"),
                    sources={"craigslist": {"enabled": True}})
    fetcher = FakeFetcher(rows)
    signal = FakeSignal()
    with mock.patch.object(pipeline, "CraigslistFetcher", lambda: fetcher), \
         mock.patch.object(pipeline, "IMAGE_CACHE_DIR", tmp_path / "imgcache"):
        summary = asyncio.run(pipeline.run_pass(
            config, store, signal, {"send_id": "g"}, scorer=scorer, judge=judge))
    return store, signal, summary


def test_clip_threshold_gates_and_judge_reason_in_ping(tmp_path):
    rows = [listing(1), listing(2)]
    scorer = FakeScorer({
        "http://img/1.jpg": {"score": 0.31, "visual": 0.3, "text": 0.35, "refs": 2},
        "http://img/2.jpg": {"score": 0.10, "visual": 0.1, "text": 0.1, "refs": 2},
    })
    judge = FakeJudge({"craigslist:1": {"match": True, "confidence": 0.9,
                                        "reason": "tapered walnut legs"}})
    store, signal, summary = run_aesthetic_pass(tmp_path, rows, scorer, judge)

    assert summary == {"fetched": 2, "new": 2, "reevaluated": 0, "rejected": 1,
                       "surfaced": 1, "errors": 0}
    assert judge.calls == ["craigslist:1"]  # low scorer never judged
    assert "tapered walnut legs" in signal.sent[0]

    good = store.get_listing_by_ref(1)
    assert [j["stage"] for j in good["judgement"]] == ["hard_filter", "clip", "judge", "notify"]
    assert good["clip_score"] == 0.31 and good["judge_verdict"] == "match"
    assert good["criteria_hash"]  # finalized

    low = store.get_listing_by_ref(2)
    assert low["outcome"] == "rejected_clip"
    assert low["judgement"][-1]["threshold"] == 0.24
    assert low["criteria_hash"]  # finalized


def test_criteria_change_reevaluates_without_refetch(tmp_path):
    rows = [listing(1)]
    # first pass: threshold too high, listing rejected at clip
    scorer = FakeScorer({"http://img/1.jpg": {"score": 0.30, "visual": 0.3,
                                              "text": 0.3, "refs": 2}})
    judge = FakeJudge({"craigslist:1": {"match": True, "confidence": 0.9,
                                        "reason": "walnut, great"}})
    store, signal, s1 = run_aesthetic_pass(tmp_path, rows, scorer, judge,
                                           spec_overrides={"clip_threshold": 0.5})
    assert s1["surfaced"] == 0
    assert store.get_listing_by_ref(1)["outcome"] == "rejected_clip"

    # lower the threshold -> criteria hash changes -> re-evaluated, no re-fetch,
    # and now it clips-through and gets judged + surfaced
    fetcher_calls_before = None  # FakeFetcher.detail not called for cached listing
    store, signal2, s2 = run_aesthetic_pass(
        tmp_path, rows=[], scorer=scorer, judge=judge, store=store,
        spec_overrides={"clip_threshold": 0.2})
    assert s2["new"] == 0 and s2["reevaluated"] == 1
    assert s2["surfaced"] == 1
    good = store.get_listing_by_ref(1)
    assert good["outcome"] == "surfaced" and good["judge_verdict"] == "match"
    assert "walnut, great" in signal2.sent[0]


def test_reports_near_misses_when_nothing_matches_on_manual_run(tmp_path):
    rows = [listing(1), listing(2)]
    scorer = FakeScorer({
        "http://img/1.jpg": {"score": 0.35, "visual": 0.35, "text": 0.3, "refs": 1},
        "http://img/2.jpg": {"score": 0.28, "visual": 0.28, "text": 0.2, "refs": 1},
    })
    # judge rejects both
    judge = FakeJudge({
        "craigslist:1": {"match": False, "confidence": 0.6, "reason": "no woven seat"},
        "craigslist:2": {"match": False, "confidence": 0.5, "reason": "wrong era"},
    })
    store = Store(tmp_path / "t.db")
    store.set_setting("location", {"postal": "94103", "radius_miles": 15})
    store.set_setting("report_empty_next", True)  # simulates run_now / reevaluate
    store2, signal, summary = run_aesthetic_pass(tmp_path, rows, scorer, judge, store=store)

    assert summary["surfaced"] == 0
    assert len(signal.sent) == 1  # a near-miss report, not silence
    report = signal.sent[0]
    assert "none matched" in report and "Closest" in report
    assert "#1" in report and "no woven seat" in report  # highest clip first
    # flag consumed
    assert store.get_setting("report_empty_next") is False


def test_no_near_miss_report_on_scheduled_pass(tmp_path):
    rows = [listing(1)]
    scorer = FakeScorer({"http://img/1.jpg": {"score": 0.35, "visual": 0.35,
                                             "text": 0.3, "refs": 1}})
    judge = FakeJudge({"craigslist:1": {"match": False, "confidence": 0.6, "reason": "nope"}})
    # report_empty_next NOT set -> scheduled pass stays quiet
    store, signal, summary = run_aesthetic_pass(tmp_path, rows, scorer, judge)
    assert summary["surfaced"] == 0 and signal.sent == []


def test_unchanged_criteria_second_pass_is_noop(tmp_path):
    rows = [listing(1)]
    scorer = FakeScorer({"http://img/1.jpg": {"score": 0.3, "visual": 0.3,
                                             "text": 0.3, "refs": 2}})
    judge = FakeJudge({"craigslist:1": {"match": True, "confidence": 0.9, "reason": "yep"}})
    store, signal, s1 = run_aesthetic_pass(tmp_path, rows, scorer, judge)
    assert s1["surfaced"] == 1

    # same criteria, no new rows: nothing re-evaluated, no duplicate ping
    store, signal2, s2 = run_aesthetic_pass(
        tmp_path, rows=[], scorer=scorer, judge=judge, store=store, upsert=False)
    assert s2 == {"fetched": 0, "new": 0, "reevaluated": 0, "rejected": 0,
                  "surfaced": 0, "errors": 0}
    assert signal2.sent == []


def test_progressive_judge_coverage_over_passes(tmp_path):
    # 3 clip-passers, judge budget 1 -> one judged per pass, rest deferred and
    # carried forward (criteria_hash stays unset until judged)
    rows = [listing(1), listing(2), listing(3)]
    scorer = FakeScorer({
        "http://img/1.jpg": {"score": 0.30, "visual": 0.3, "text": None, "refs": 1},
        "http://img/2.jpg": {"score": 0.40, "visual": 0.4, "text": None, "refs": 1},
        "http://img/3.jpg": {"score": 0.35, "visual": 0.35, "text": None, "refs": 1},
    })
    judge = FakeJudge({f"craigslist:{n}": {"match": True, "confidence": 0.8,
                                           "reason": f"m{n}"} for n in (1, 2, 3)})
    store, signal, s1 = run_aesthetic_pass(tmp_path, rows, scorer, judge,
                                           spec_overrides={"judge_top_k": 1})
    assert judge.calls == ["craigslist:2"]  # highest clip score judged first
    assert s1["surfaced"] == 1

    # pass 2: no new rows; the two deferred ones are still stale -> re-clipped,
    # next highest judged
    store, signal2, s2 = run_aesthetic_pass(
        tmp_path, rows=[], scorer=scorer, judge=judge, store=store, upsert=False)
    assert s2["reevaluated"] == 2 and s2["surfaced"] == 1
    assert judge.calls == ["craigslist:2", "craigslist:3"]  # cumulative


def test_judge_top_k_budget_by_clip_rank(tmp_path):
    rows = [listing(1), listing(2), listing(3)]
    scorer = FakeScorer({
        "http://img/1.jpg": {"score": 0.30, "visual": 0.3, "text": None, "refs": 1},
        "http://img/2.jpg": {"score": 0.40, "visual": 0.4, "text": None, "refs": 1},
        "http://img/3.jpg": {"score": 0.35, "visual": 0.35, "text": None, "refs": 1},
    })
    judge = FakeJudge({
        "craigslist:2": {"match": True, "confidence": 0.8, "reason": "great"},
        "craigslist:3": {"match": False, "confidence": 0.7, "reason": "too plain"},
    })
    store, signal, summary = run_aesthetic_pass(tmp_path, rows, scorer, judge, top_k=2)

    # judged best-first, lowest scorer deferred (not rejected) by budget
    assert judge.calls == ["craigslist:2", "craigslist:3"]
    assert summary["surfaced"] == 1
    deferred = [l for l in store.recent_listings(limit=10)
                if l["id"] == "craigslist:1"][0]
    assert deferred["outcome"] == "deferred"
    assert deferred["criteria_hash"] is None  # carried forward, not finalized
    assert "top-2" in deferred["judgement"][-1]["reason"]
    no_match = [l for l in store.recent_listings(limit=10)
                if l["id"] == "craigslist:3"][0]
    assert no_match["judge_reason"] == "too plain" and no_match["criteria_hash"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
