import sys

import pytest

from finder.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_settings_roundtrip(store):
    assert store.get_setting("location") is None
    store.set_setting("location", {"postal": "94103", "radius_miles": 15})
    store.set_setting("location", {"postal": "94103", "radius_miles": 25})
    assert store.get_setting("location")["radius_miles"] == 25


def test_query_lifecycle(store):
    store.upsert_query("mcm-couch", {"keywords": ["couch"], "max_price": 600})
    store.upsert_query("mcm-couch", {"keywords": ["couch", "sofa"], "max_price": 500})
    q = store.get_query("mcm-couch")
    assert q["spec"]["max_price"] == 500 and not q["paused"]

    assert store.set_query_paused("mcm-couch", True)
    assert store.get_query("mcm-couch")["paused"]
    assert store.list_queries(include_paused=False) == []
    assert not store.set_query_paused("nope", True)


def test_listing_dedup_and_refs(store):
    ref = store.add_listing(
        {"id": "cl:123", "source": "craigslist", "title": "Couch",
         "price": 300, "repost_hash": "abc", "image_urls": ["http://x/1.jpg"]}
    )
    assert ref == 1
    assert store.seen("cl:123")
    assert store.seen("cl:999", repost_hash="abc")  # repost under a new id
    assert not store.seen("cl:999", repost_hash="def")

    ref2 = store.add_listing({"id": "cl:456", "source": "craigslist", "title": "Sofa"})
    assert ref2 == 2
    got = store.get_listing_by_ref(2)
    assert got["id"] == "cl:456" and got["image_urls"] == []

    store.update_listing("cl:456", judge_verdict="match", judge_reason="nice lines")
    store.mark_notified("cl:456")
    assert store.recent_listings(matched_only=True)[0]["id"] == "cl:456"
    assert store.get_listing_by_ref(2)["notified_at"]


def test_chat_history_order_and_limit(store):
    for i in range(5):
        store.append_chat("user" if i % 2 == 0 else "assistant", f"msg{i}")
    recent = store.recent_chat(limit=3)
    assert [m["content"] for m in recent] == ["msg2", "msg3", "msg4"]
    assert recent[0]["role"] == "user"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
