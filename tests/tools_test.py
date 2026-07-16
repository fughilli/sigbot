import json
import sys

import pytest

from finder.bot.tools import TOOL_DEFS, Tools
from finder.store import Store


@pytest.fixture
def tools(tmp_path):
    store = Store(tmp_path / "t.db")
    yield Tools(store, references_dir=tmp_path / "refs",
                trigger_run=lambda: "started", get_status=lambda: {"next_run": "soon"})
    store.close()


def call(tools, tool, **kwargs):
    return json.loads(tools.dispatch(tool, kwargs))


def test_tool_defs_have_schemas():
    for t in TOOL_DEFS:
        assert t["name"] and t["description"] and t["input_schema"]["type"] == "object"


def test_set_location_validation(tools):
    assert "error" in call(tools, "set_location", postal="9411", radius_miles=15)
    assert "error" in call(tools, "set_location", postal="94103", radius_miles=500)
    assert "error" in call(tools, "set_location", postal="94103", radius_miles=15,
                           craigslist_site="SF Bay")
    ok = call(tools, "set_location", postal="94103", radius_miles=15, craigslist_site="sfbay")
    assert ok["ok"] and ok["location"]["craigslist_site"] == "sfbay"
    # radius-only update keeps the site
    ok = call(tools, "set_location", postal="94103", radius_miles=25)
    assert ok["location"]["craigslist_site"] == "sfbay"


def test_set_cadence_bounds_and_callback(tools):
    fired = []
    tools._on_cadence_change = lambda: fired.append(1)
    assert "error" in call(tools, "set_cadence", minutes=5)
    assert "error" in call(tools, "set_cadence", minutes=999999)
    assert not fired
    assert call(tools, "set_cadence", minutes=120)["ok"]
    assert fired == [1]


def test_upsert_query_validation_and_merge(tools):
    assert "error" in call(tools, "upsert_query", name="Bad Name")
    assert "error" in call(tools, "upsert_query", name="mcm-couch")  # no keywords
    assert "error" in call(tools, "upsert_query", name="mcm-couch",
                           keywords=["couch"], max_price=-5)
    ok = call(tools, "upsert_query", name="mcm-couch", keywords=["couch", "sofa"],
              max_price=600, aesthetic_description="mid-century modern")
    assert ok["created"] and ok["spec"]["clip_threshold"] == 0.24
    # partial update keeps other fields
    ok = call(tools, "upsert_query", name="mcm-couch", max_price=500)
    assert not ok["created"]
    assert ok["spec"]["max_price"] == 500 and ok["spec"]["keywords"] == ["couch", "sofa"]


def test_pause_delete_unknown_query(tools):
    assert "error" in call(tools, "pause_query", name="nope", paused=True)
    assert "error" in call(tools, "delete_query", name="nope")


def test_reference_images(tools, tmp_path):
    call(tools, "upsert_query", name="mcm-couch", keywords=["couch"])
    assert "error" in call(tools, "add_reference_images", query_name="nope")
    assert "error" in call(tools, "add_reference_images", query_name="mcm-couch")  # none pending
    tools.pending_images = [(b"fakejpg", "jpg"), (b"fakepng", "png")]
    ok = call(tools, "add_reference_images", query_name="mcm-couch")
    assert ok["total_refs"] == 2 and (tmp_path / "refs/mcm-couch/ref-0000.jpg").read_bytes() == b"fakejpg"
    assert tools.pending_images == []


def test_listing_tools(tools):
    ref = tools.store.add_listing(
        {"id": "cl:1", "source": "craigslist", "title": "Couch", "price": 300}
    )
    assert call(tools, "get_listing", short_ref=ref)["title"] == "Couch"
    assert "error" in call(tools, "get_listing", short_ref=99)
    assert call(tools, "mark_feedback", short_ref=ref, feedback="up")["ok"]
    assert call(tools, "list_recent")["listings"][0]["short_ref"] == ref


def test_run_now_and_status(tools):
    assert call(tools, "run_now")["result"] == "started"
    s = call(tools, "status")
    assert s["next_run"] == "soon" and "queries" in s


def test_reevaluate_query(tools):
    assert "error" in call(tools, "reevaluate_query", name="nope")
    call(tools, "upsert_query", name="mcm-couch", keywords=["couch"])
    tools.store.add_listing({"id": "cl:1", "source": "craigslist", "title": "C",
                             "query_name": "mcm-couch", "criteria_hash": "abc"})
    out = call(tools, "reevaluate_query", name="mcm-couch")
    assert out["ok"] and out["listings_to_reevaluate"] == 1 and out["result"] == "started"
    assert tools.store.get_listing_by_ref(1)["criteria_hash"] is None


def test_set_location_lat_lon(tools):
    assert "error" in call(tools, "set_location", postal="94103", radius_miles=10,
                           latitude=37.7)  # lon missing
    ok = call(tools, "set_location", postal="94103", radius_miles=10,
              latitude=37.7573, longitude=-122.4906)
    assert ok["location"]["lat"] == 37.7573 and ok["location"]["lon"] == -122.4906


def test_unknown_tool_and_bad_args(tools):
    assert "error" in call(tools, "frobnicate")
    assert "error" in call(tools, "set_cadence")  # missing arg


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
