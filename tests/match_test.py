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
    j.model = "claude-sonnet-4-6"

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


def run_aesthetic_pass(tmp_path, rows, scorer, judge, top_k=8, threshold=0.24):
    store = Store(tmp_path / "t.db")
    store.set_setting("location", {"postal": "94103", "radius_miles": 15})
    store.upsert_query("mcm", {"keywords": ["couch"], "max_price": 500,
                               "aesthetic_description": "mid-century modern",
                               "clip_threshold": threshold, "judge_top_k": top_k})
    config = Config(signal=SignalConfig(api_url="x", bot_number="+1", user_id="u"),
                    db_path=str(tmp_path / "t.db"),
                    sources={"craigslist": {"enabled": True}})
    fetcher = FakeFetcher(rows)
    import unittest.mock as mock
    signal = FakeSignal()
    with mock.patch.object(pipeline, "CraigslistFetcher", lambda: fetcher):
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

    assert summary == {"fetched": 2, "new": 2, "rejected": 1, "surfaced": 1, "errors": 0}
    assert judge.calls == ["craigslist:1"]  # low scorer never judged
    assert "tapered walnut legs" in signal.sent[0]

    good = store.get_listing_by_ref(1)
    assert [j["stage"] for j in good["judgement"]] == ["hard_filter", "clip", "judge", "notify"]
    assert good["clip_score"] == 0.31 and good["judge_verdict"] == "match"

    low = store.get_listing_by_ref(2)
    assert low["outcome"] == "rejected_clip"
    assert low["judgement"][-1]["threshold"] == 0.24


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

    # judged best-first, lowest scorer dropped by budget
    assert judge.calls == ["craigslist:2", "craigslist:3"]
    assert summary["surfaced"] == 1
    dropped = [l for l in store.recent_listings(limit=10)
               if l["id"] == "craigslist:1"][0]
    assert dropped["outcome"] == "rejected_judge"
    assert "top-2" in dropped["judgement"][-1]["reason"]
    no_match = [l for l in store.recent_listings(limit=10)
                if l["id"] == "craigslist:3"][0]
    assert no_match["judge_reason"] == "too plain"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
