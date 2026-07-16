import asyncio
import sys

import pytest

from finder import pipeline
from finder.config import Config, SignalConfig
from finder.store import Store


class FakeFetcher:
    name = "craigslist"
    max_detail_fetches = 25

    def __init__(self, rows):
        self.rows = rows
        self.detail_calls = 0

    async def search(self, location, spec):
        return [{k: r[k] for k in ("id", "source", "url", "title", "price")}
                for r in self.rows]

    async def detail(self, row):
        self.detail_calls += 1
        return next(dict(r) for r in self.rows if r["id"] == row["id"])

    async def fetch_image(self, url):
        return b"imgbytes"


class FakeSignal:
    def __init__(self):
        self.sent = []

    async def send(self, recipient, message, attachments_b64=None):
        self.sent.append((recipient, message, None))

    async def send_image(self, recipient, message, image, mime="image/jpeg"):
        self.sent.append((recipient, message, image))


LISTINGS = [
    {"id": "craigslist:good1", "source": "craigslist", "url": "http://x/1",
     "title": "Mid-century couch", "price": 300.0,
     "description": "walnut frame couch", "image_urls": ["http://img/1.jpg"]},
    {"id": "craigslist:pricey", "source": "craigslist", "url": "http://x/2",
     "title": "Designer sofa", "price": 900.0,
     "description": "fancy sofa", "image_urls": ["http://img/2.jpg"]},
    {"id": "craigslist:noimg", "source": "craigslist", "url": "http://x/3",
     "title": "couch, no pics", "price": 100.0,
     "description": "trust me", "image_urls": []},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    store.set_setting("location", {"postal": "94103", "radius_miles": 15})
    store.upsert_query("mcm-couch", {"keywords": ["couch", "sofa"], "max_price": 500})
    config = Config(
        signal=SignalConfig(api_url="http://x", bot_number="+1", user_id="u"),
        db_path=str(tmp_path / "t.db"),
        sources={"craigslist": {"enabled": True}},
    )
    fetcher = FakeFetcher(LISTINGS)
    monkeypatch.setattr(pipeline, "CraigslistFetcher", lambda: fetcher)
    monkeypatch.setattr(pipeline, "IMAGE_CACHE_DIR", tmp_path / "imgcache")
    signal = FakeSignal()
    group = {"send_id": "group.abc", "internal_id": "abc"}
    yield store, config, fetcher, signal, group
    store.close()


def run(config, store, signal, group):
    return asyncio.run(pipeline.run_pass(config, store, signal, group))


def test_pass_filters_and_notifies(env):
    store, config, fetcher, signal, group = env
    summary = run(config, store, signal, group)
    assert summary == {"fetched": 3, "new": 3, "reevaluated": 0, "rejected": 2,
                       "surfaced": 1, "errors": 0}

    # the good one was surfaced with its image, message references #ref and price
    assert len(signal.sent) == 1
    recipient, message, image = signal.sent[0]
    assert recipient == "group.abc" and image == b"imgbytes"
    assert "$300" in message and "Mid-century couch" in message

    good = store.get_listing_by_ref(1)
    assert good["outcome"] == "surfaced" and good["notified_at"]
    stages = [j["stage"] for j in good["judgement"]]
    # no aesthetic target on this query -> clip records an explicit skip
    assert stages == ["hard_filter", "clip", "notify"]
    assert "skipped" in good["judgement"][1]["reason"]

    # judgement explains each rejection precisely
    pricey = [l for l in store.recent_listings(limit=10) if l["id"] == "craigslist:pricey"][0]
    assert pricey["outcome"] == "rejected_filter"
    assert "over $500 cap" in pricey["judgement"][0]["checks"]["price"]

    noimg = [l for l in store.recent_listings(limit=10) if l["id"] == "craigslist:noimg"][0]
    assert "no photos" in noimg["judgement"][0]["checks"]["images"]


def test_second_pass_is_all_dedup(env):
    store, config, fetcher, signal, group = env
    run(config, store, signal, group)
    first_details = fetcher.detail_calls
    summary = run(config, store, signal, group)
    assert summary["new"] == 0 and summary["surfaced"] == 0
    assert fetcher.detail_calls == first_details  # nothing refetched
    assert len(signal.sent) == 1  # no duplicate ping


def test_hard_filter_phrase_keywords():
    spec = {"keywords": ["mid century dining chairs"], "max_price": 500}
    listing = {"title": "Dining chairs, mid-century walnut", "price": 200.0,
               "description": "set of 4", "image_urls": ["http://i/1.jpg"]}
    passed, checks = pipeline.hard_filter(listing, spec)
    assert passed, checks
    assert "matched" in checks["keywords"]

    listing["title"] = "Office desk"
    listing["description"] = "plain desk"
    passed, checks = pipeline.hard_filter(listing, spec)
    assert not passed and checks["keywords"].startswith("reject")


def test_pass_skips_without_location(tmp_path):
    store = Store(tmp_path / "e.db")
    config = Config(
        signal=SignalConfig(api_url="http://x", bot_number="+1", user_id="u"),
        db_path=str(tmp_path / "e.db"),
    )
    summary = asyncio.run(pipeline.run_pass(config, store, FakeSignal(), {"send_id": "g"}))
    assert "skipped" in summary
    store.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
