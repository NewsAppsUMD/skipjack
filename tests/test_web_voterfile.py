"""Smoke and logic tests for the voter file pages, against the built skipjack.db."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from skipjack.web.app import app
from skipjack.web.db import DB_PATH, get_db
from skipjack.web.routes.voterfile import (
    build_county_context,
    load_snapshot,
    pct,
    previous_general,
)


def _has_snapshot() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM voter_file_snapshots").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


pytestmark = pytest.mark.skipif(not _has_snapshot(), reason="no voter file snapshot in skipjack.db")
client = TestClient(app)


def embedded(html: str) -> dict:
    m = re.search(r'<script id="voterfile-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_pct_handles_suppressed_and_zero():
    assert pct(1, 4) == 25.0
    assert pct(None, 4) is None
    assert pct(3, None) is None
    assert pct(3, 0) is None


def test_statewide_page():
    r = client.get("/voters/file/")
    assert r.status_code == 200
    data = embedded(r.text)
    assert [s["party"] for s in data["charts"]["primary"]["series"]] == ["DEM", "REP", "UNA", "ALL"]
    assert len(data["charts"]["primary"]["labels"]) == 3
    assert len(data["charts"]["general"]["labels"]) == 2
    assert 'href="/voters/file/county/montgomery/"' in r.text
    assert "About these numbers" in r.text


def test_county_page_with_apostrophe_and_table_links():
    assert client.get("/voters/file/county/prince-georges/").status_code == 200
    r = client.get("/voters/file/")
    names = re.findall(r'href="/voters/file/county/([^/"]+)/"', r.text)
    assert (
        names[0] == "allegany" and "prince-georges" in names
    )  # alphabetical, sorted in the browser
    assert "data-sortable" in r.text


def test_unknown_county_district_and_snapshot():
    assert client.get("/voters/file/county/atlantis/").status_code == 404
    assert client.get("/voters/file/districts/bogus/").status_code == 404
    assert client.get("/voters/file/", params={"snapshot": "1999-01-01"}).status_code == 404


def test_old_query_string_links_redirect():
    r = client.get("/voters/file/county", params={"county": "Kent"}, follow_redirects=True)
    assert r.url.path == "/voters/file/county/kent/"
    r = client.get("/voters/file/districts", params={"type": "legislative"}, follow_redirects=True)
    assert r.url.path == "/voters/file/districts/legislative/"
    r = client.get("/voters/file/districts", params={"type": "bogus"}, follow_redirects=True)
    assert r.url.path == "/voters/file/districts/congressional/"


@pytest.mark.parametrize("kind", ["congressional", "legislative"])
def test_district_pages(kind):
    r = client.get(f"/voters/file/districts/{kind}/")
    assert r.status_code == 200
    assert kind.capitalize() in r.text
    assert "data-sortable" in r.text


def test_context_numbers_are_consistent():
    with get_db() as conn:
        snapshot, meta = load_snapshot(conn, None)
        ctx = build_county_context(conn, snapshot, meta, "Maryland")
    tiles = ctx["tiles"]
    assert tiles["registered"] == tiles["active"] + tiles["inactive"]
    # "All voters" participation is between the party extremes in a general election.
    general = next(r for r in ctx["turnout_rows"] if r["election"]["key"] == "2024-PG")
    dem, rep, una, everyone = (c["pct"] for c in general["cells"])
    assert min(dem, rep, una) <= everyone <= max(dem, rep, una)
    # Voter-type columns add to 100%.
    for col in range(4):
        assert sum(t["pcts"][col] for t in ctx["voter_types"]) == pytest.approx(100.0)
    assert ctx["new_voters"]["since"]["key"] == "2024-PG"
    assert ctx["new_voters"]["latest"]["key"] == "2026-GP"


def test_small_county_cells_may_be_suppressed_but_pages_render():
    with get_db() as conn:
        snapshot, meta = load_snapshot(conn, None)
        ctx = build_county_context(conn, snapshot, meta, "Kent")
    # Whatever is suppressed arrives as None and never breaks the page.
    assert ctx["tiles"]["registered"] > 10_000
    assert client.get("/voters/file/county/kent/").status_code == 200


def test_previous_general():
    elections = [
        {"key": "2022-GG", "kind": "general", "date": "2022-11-08"},
        {"key": "2024-PP", "kind": "primary", "date": "2024-05-14"},
        {"key": "2024-PG", "kind": "general", "date": "2024-11-05"},
        {"key": "2026-GP", "kind": "primary", "date": "2026-06-23"},
    ]
    assert previous_general(elections)["key"] == "2024-PG"
    assert previous_general(elections[:2]) is not None  # 2022 general precedes the 2024 primary
    assert previous_general([elections[0]]) is None


def test_navigation_links():
    assert 'href="/voters/file/"' in client.get("/").text
    assert 'href="/voters/file/"' in client.get("/voters/").text


@pytest.mark.parametrize(
    "url",
    ["/voters/file/", "/voters/file/county/kent/", "/voters/file/districts/legislative/"],
)
def test_every_page_says_participation_is_not_official_turnout(url):
    html = client.get(url).text
    assert "not official turnout" in html.lower()
    assert "Who votes" not in html  # the old, unqualified heading


def test_charts_are_titled_participation_among_current_voters():
    html = client.get("/voters/file/").text
    assert "<h2>Participation among current voters</h2>" in html
    assert "Participation among current voters: primary elections" in html
    assert "Participation among current voters: general elections" in html


def test_coverage_table_shows_how_much_of_each_electorate_is_missing():
    with get_db() as conn:
        snapshot, meta = load_snapshot(conn, None)
        ctx = build_county_context(conn, snapshot, meta, "Maryland")
        kent = build_county_context(conn, snapshot, meta, "Kent")
    by_key = {c["election"]["key"]: c for c in ctx["coverage_rows"]}
    # The file holds well under all of the 2022 electorate and nearly all of the 2026 one.
    assert by_key["2022-GG"]["pct"] < 90
    assert by_key["2026-GP"]["pct"] > 95
    assert by_key["2022-GG"]["pct"] < by_key["2024-PG"]["pct"] < by_key["2026-GP"]["pct"]
    assert len(kent["coverage_rows"]) == len(ctx["coverage_rows"]) == 5
    assert "Share still in file" in client.get("/voters/file/").text


def test_entry_points_do_not_oversell_the_numbers():
    assert "Not official turnout" in client.get("/").text
    assert "Participation among current voters" in client.get("/voters/").text
