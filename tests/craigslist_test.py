import pathlib
import sys

import pytest

import asyncio

from finder.fetchers.craigslist import (
    CraigslistFetcher,
    parse_detail,
    parse_search,
    search_url,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_search_url_is_single_unquoted_keyword():
    # One keyword per search — NOT joined with '|' (which CL treats as a
    # word-level OR, not a phrase OR, collapsing coverage).
    url = search_url(
        {"postal": "94103", "radius_miles": 15, "craigslist_site": "sfbay"},
        "mid century dining chairs", max_price=600,
    )
    assert url.startswith("https://sfbay.craigslist.org/search/fua?")
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(url).query)
    assert qs["query"][0] == "mid century dining chairs"
    assert "|" not in qs["query"][0]
    assert qs["max_price"][0] == "600"
    assert qs["postal"][0] == "94103" and qs["search_distance"][0] == "15"


def test_search_runs_one_query_per_keyword_and_merges(tmp_path):
    # A fake HTTP client returning a distinct listing per keyword; the fetcher
    # should run one search each and union the results (dedup by id).
    pages = {
        "couch": '<li class="cl-static-search-result" title="A">'
                 '<a href="https://x/d/a/aaaaaaaaaaaaaaaa"><div class="title">A</div></a></li>'
                 '<li class="cl-static-search-result" title="Shared">'
                 '<a href="https://x/d/s/ssssssssssssssss"><div class="title">S</div></a></li>',
        "sofa": '<li class="cl-static-search-result" title="B">'
                '<a href="https://x/d/b/bbbbbbbbbbbbbbbb"><div class="title">B</div></a></li>'
                '<li class="cl-static-search-result" title="Shared">'
                '<a href="https://x/d/s/ssssssssssssssss"><div class="title">S</div></a></li>',
    }
    searched = []

    class FakeResp:
        def __init__(self, text): self.text = text

    class FakeClient:
        async def get(self, url, **kw):
            from urllib.parse import parse_qs, urlparse
            kwd = parse_qs(urlparse(url).query)["query"][0]
            searched.append(kwd)
            return FakeResp(pages[kwd])
        def raise_for_status(self): pass

    # patch polite_get to skip delays and call our fake client
    import finder.fetchers.base as base
    orig = base.polite_get

    async def fake_polite_get(client, url, **kw):
        return await client.get(url)

    base.polite_get = fake_polite_get
    try:
        f = CraigslistFetcher(client=FakeClient())
        rows = asyncio.run(f.search({"postal": "94103", "radius_miles": 15},
                                    {"keywords": ["couch", "sofa"]}))
    finally:
        base.polite_get = orig

    assert searched == ["couch", "sofa"]  # one search per keyword
    ids = {r["id"] for r in rows}
    assert len(ids) == 3  # A, B, and the shared S deduped to one
    assert "craigslist:aaaaaaaaaaaaaaaa" in ids


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
