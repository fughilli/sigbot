"""Facebook fetcher tests: URL building, embedded-JSON parsing, checkpoint
detection, circuit breaker. No Playwright/browser — the network-facing methods
are exercised in production only; these cover the pure logic that decides what
gets parsed and when the source disables itself.
"""

import datetime
import json
import sys

import pytest

from finder.fetchers import facebook as fb
from finder.store import Store


def test_build_search_url_geo_and_price():
    url = fb.build_search_url(
        {"radius_miles": 20, "lat": 37.7573, "lon": -122.4906},
        {"keywords": ["mid century", "couch"], "max_price": 500},
    )
    assert url.startswith("https://www.facebook.com/marketplace/search?")
    assert "query=mid+century+couch" in url
    assert "maxPrice=500" in url
    assert "latitude=37.7573" in url and "longitude=-122.4906" in url
    assert "radius=32" in url  # 20mi -> 32km


def test_build_search_url_city_slug_no_geo():
    url = fb.build_search_url(
        {"radius_miles": 10, "facebook_city": "sanfrancisco"},
        {"keywords": ["desk"]},
    )
    assert "/marketplace/sanfrancisco/search?" in url
    assert "latitude" not in url


def _embedded_page(nodes):
    """Wrap listing nodes in the <script type=application/json> envelope FB uses,
    nested to prove the recursive walk finds them anywhere."""
    payload = {"require": [["ScheduledServerJS", "handle", None,
                            [{"__bbox": {"result": {"data": {"marketplace_search": {
                                "feed_units": {"edges": [{"node": {"listing": n}} for n in nodes]}
                            }}}}}]]]}
    return ('<html><body>'
            f'<script type="application/json" data-sjs>{json.dumps(payload)}</script>'
            '<script type="application/json">{"unrelated": true}</script>'
            '</body></html>')


def _node(nid, title, amount, offset=False):
    price = {"amount_with_offset": str(amount * 100)} if offset else {"amount": str(amount)}
    return {
        "id": nid,
        "marketplace_listing_title": title,
        "listing_price": price,
        "__typename": "MarketplaceListing",
        "primary_listing_photo": {"image": {"uri": f"https://scontent/{nid}.jpg"}},
        "location": {"reverse_geocode": {"city": "Oakland"}},
        "redacted_description": {"text": f"desc for {title}"},
    }


def test_parse_search_payload_extracts_and_dedups():
    html = _embedded_page([
        _node("101", "MCM Couch", 300),
        _node("102", "Teak credenza", 120, offset=True),  # amount_with_offset (cents) -> /100
        _node("101", "MCM Couch dup", 300),  # same id, ignored
    ])
    rows = fb.parse_search_payload(html)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    a = by_id["facebook:101"]
    assert a["title"] == "MCM Couch" and a["price"] == 300.0
    assert a["url"] == "https://www.facebook.com/marketplace/item/101/"
    assert a["image_urls"] == ["https://scontent/101.jpg"]
    assert a["location_text"] == "Oakland"
    assert by_id["facebook:102"]["price"] == 120.0  # offset divided


def test_parse_search_payload_tolerates_junk():
    assert fb.parse_search_payload("<html>no json here</html>") == []
    assert fb.parse_search_payload(
        '<script type="application/json">{bad json</script>') == []


def test_checkpoint_detection():
    assert fb._looks_like_checkpoint("https://www.facebook.com/checkpoint/?next", "")
    assert fb._looks_like_checkpoint("https://www.facebook.com/login/", "")
    assert fb._looks_like_checkpoint(
        "https://www.facebook.com/marketplace/search",
        "Please log in to Facebook to continue browsing")
    assert not fb._looks_like_checkpoint(
        "https://www.facebook.com/marketplace/search?query=couch",
        "<div>Marketplace results for couch</div>")


def test_circuit_breaker_lifecycle(tmp_path):
    store = Store(tmp_path / "c.db")
    circuit = fb.Circuit(store)

    open_, reason = circuit.is_open()
    assert not open_ and reason is None

    circuit.trip("login wall at /checkpoint")
    open_, reason = circuit.is_open()
    assert open_ and "checkpoint" in reason

    circuit.reset()
    assert circuit.is_open() == (False, None)
    store.close()


def test_circuit_breaker_expires(tmp_path):
    store = Store(tmp_path / "c.db")
    circuit = fb.Circuit(store)
    # Manually plant an already-expired cooldown.
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=1)).isoformat(timespec="seconds")
    store.set_setting("facebook_circuit", {"open_until": past, "reason": "old"})
    open_, _ = circuit.is_open()
    assert not open_
    store.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
