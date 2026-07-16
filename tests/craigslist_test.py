import pathlib
import sys

import pytest

from finder.fetchers.craigslist import parse_detail, parse_search, search_url

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_search_url():
    url = search_url(
        {"postal": "94103", "radius_miles": 15, "craigslist_site": "sfbay"},
        {"keywords": ["couch", "sofa"], "max_price": 600},
    )
    assert url.startswith("https://sfbay.craigslist.org/search/fua?")
    assert "query=couch%7Csofa" in url and "max_price=600" in url
    assert "postal=94103" in url and "search_distance=15" in url


def test_parse_search_fixture():
    rows = parse_search((FIXTURES / "cl_search.html").read_text())
    assert len(rows) > 100  # fixture captured 223 results
    first = rows[0]
    assert first["id"].startswith("craigslist:")
    assert first["url"].startswith("https://")
    assert first["title"] == "Sofa bed / couch"
    assert first["price"] == 40.0
    assert first["location_text"] == "San Francisco"
    # ids unique
    assert len({r["id"] for r in rows}) == len(rows)


def test_parse_detail_fixture():
    row = {"id": "craigslist:sLFvxJ5Pq6iQpYzaFjHCi5", "source": "craigslist",
           "url": "https://x", "title": "Sofa bed / couch", "price": 40.0}
    listing = parse_detail((FIXTURES / "cl_detail.html").read_text(), row)
    assert "QR Code" not in listing["description"]
    assert len(listing["description"]) > 20
    assert len(listing["image_urls"]) == 7
    assert all(u.startswith("https://images.craigslist.org/") for u in listing["image_urls"])
    assert listing["lat"] == pytest.approx(37.7573)
    assert listing["lon"] == pytest.approx(-122.4906)
    assert listing["posted_at"].startswith("2026-07-09")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
