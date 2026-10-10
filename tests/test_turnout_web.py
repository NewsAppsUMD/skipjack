"""Tests for the web turnout module: queries over the turnout tables.

The first half runs on made-up rows in an in-memory database. The db-backed section
(skipjack.db must be built) pins real numbers from the committed GG26 snapshots.
"""

from __future__ import annotations

import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.web import turnout
from skipjack.web.db import DB_PATH


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def add(conn, as_of, rows, source_id="s", family="sent-returned"):
    """Rows into the turnout tables; rows carry (geography, county, district, category,
    party, measure, value)."""
    for geography, county, district, category, party, measure, value in rows:
        conn.execute(
            """INSERT INTO turnout_sources
               (source_id, election, family, geography, as_of, source_url, source_name,
                document_title, fetch_date, sha256, extraction_method, checks_json, notes)
               VALUES (?, 'GG26', ?, ?, ?, 'https://elections.maryland.gov/x.xlsx',
                'Maryland State Board of Elections', 'test', '2026-10-10T17:00:00+00:00',
                '', 'xlsx (calamine)', '{}', '')
               ON CONFLICT(source_id) DO NOTHING""",
            (source_id, family, geography, as_of),
        )
        conn.execute(
            """INSERT INTO turnout_counts
               (source_id, as_of, election, family, geography, county, district, category,
                party, measure, value)
               VALUES (?, ?, 'GG26', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                as_of,
                family,
                geography,
                county,
                district,
                category,
                party,
                measure,
                value,
            ),
        )


# county rows: two counties, two parties + totals, three measures
ALLEGANY = [
    ("county", "Allegany", "", "ALL", p, m, v)
    for p, m, v in [
        ("DEM", "sent", 2054),
        ("REP", "sent", 1701),
        ("OTH", "sent", 711),
        ("ALL", "sent", 4466),
        ("DEM", "received", 686),
        ("REP", "received", 544),
        ("OTH", "received", 212),
        ("ALL", "received", 1442),
        ("ALL", "accepted", 0),
    ]
]
WORCESTER = [
    ("county", "Worcester", "", "ALL", p, m, v)
    for p, m, v in [
        ("DEM", "sent", 2602),
        ("REP", "sent", 1816),
        ("OTH", "sent", 955),
        ("ALL", "sent", 5373),
        ("DEM", "received", 1023),
        ("REP", "received", 641),
        ("OTH", "received", 293),
        ("ALL", "received", 1957),
        ("ALL", "accepted", 0),
    ]
]


def test_elections_lists_gg26_with_its_snapshots(conn):
    add(conn, "2026-10-09", ALLEGANY + WORCESTER)
    add(conn, "2026-10-10", ALLEGANY + WORCESTER, source_id="s2")
    elections = turnout.elections(conn)
    assert [e["code"] for e in elections] == ["GG26"]
    assert elections[0]["name"] == "2026 general election"
    assert elections[0]["election_day"] == "2026-11-03"
    assert elections[0]["snapshots"]["sent-returned"] == ["2026-10-09", "2026-10-10"]


def test_statewide_totals_per_family(conn):
    add(conn, "2026-10-09", ALLEGANY + WORCESTER)
    headline = turnout.headline(conn, "GG26")
    assert headline["latest"] == "2026-10-09"
    # both categories' rows add up, and the printed-total rows never appear in the figures
    assert headline["sent-returned"]["sent"] == 9839  # 4466 + 5373
    assert headline["sent-returned"]["received"] == 3399  # 1442 + 1957
    assert "previous" not in headline["sent-returned"]  # no "change" without a previous


def test_headline_change_from_the_previous_snapshot(conn):
    add(conn, "2026-10-09", ALLEGANY + WORCESTER)
    add(conn, "2026-10-10", ALLEGANY + WORCESTER, source_id="s2")
    headline = turnout.headline(conn, "GG26")
    assert headline["latest"] == "2026-10-10"
    assert headline["previous"] == "2026-10-09"
    assert "change" in headline["sent-returned"]
    assert headline["sent-returned"]["change"]["sent"] == 0


def test_series_for_the_trend_charts(conn):
    add(conn, "2026-10-09", ALLEGANY + WORCESTER)
    add(conn, "2026-10-10", ALLEGANY + WORCESTER, source_id="s2")
    series = turnout.series(conn, "GG26", "sent-returned")
    assert series["dates"] == ["2026-10-09", "2026-10-10"]
    assert series["series"]["sent"] == [9839, 9839]
    assert series["series"]["received"] == [3399, 3399]
    assert series["series"]["accepted"] == [0, 0]


def test_county_rows_join_requests_and_sent_returned(conn):
    add(conn, "2026-10-09", ALLEGANY + WORCESTER)
    methods = [
        ("county", county, "", "ALL", "ALL", method, value)
        for county in ("Allegany", "Worcester")
        for method, value in [("agent", 1), ("mailed", 285), ("total", 286)]
    ]
    add(conn, "2026-10-09", methods, family="requests")
    rows = turnout.county_rows(conn, "GG26", "2026-10-09")
    assert [r["county"] for r in rows] == ["Allegany", "Worcester"]
    for r in rows:
        assert r["sent"] == (4466 if r["county"] == "Allegany" else 5373)
        assert r["received"] == (1442 if r["county"] == "Allegany" else 1957)
        assert r["requests_total"] == 286
        assert r["return_rate"] == (1442 / 4466 if r["county"] == "Allegany" else 1957 / 5373)


def test_a_county_missing_from_a_snapshot_reads_as_unknown_not_zero(conn):
    first = ALLEGANY + WORCESTER
    second = WORCESTER  # Allegany dropped from the newer snapshot (a partial file)
    add(conn, "2026-10-09", first)
    add(conn, "2026-10-10", second, source_id="s2")
    rows = {r["county"]: r for r in turnout.county_rows(conn, "GG26", "2026-10-10")}
    assert set(rows) == {"Worcester"}  # absent counties are absent, not zeroed


def test_previous_as_of(conn):
    add(conn, "2026-10-09", ALLEGANY)
    assert turnout.previous_as_of(conn, "GG26", "sent-returned", "2026-10-09") is None
    add(conn, "2026-10-10", ALLEGANY, source_id="s2")
    assert turnout.previous_as_of(conn, "GG26", "sent-returned", "2026-10-10") == "2026-10-09"


# ---------------------------------------------------------------------------
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")


@needs_db
def test_real_gg26_headline_against_the_committed_data():
    from skipjack.web.db import get_db

    with get_db() as conn:
        headline = turnout.headline(conn, "GG26")
        assert headline["latest"] == "2026-10-09"
        assert headline["sent-returned"]["sent"] == 605_052
        assert headline["sent-returned"]["received"] == 132_362
        assert headline["sent-returned"]["accepted"] == 2
        assert headline["requests"]["requests_total"] == 617_496
        rows = {r["county"]: r for r in turnout.county_rows(conn, "GG26", "2026-10-09")}
        assert len(rows) == 24
        allegany = rows["Allegany"]
        assert allegany["sent"] == 4466 and allegany["received"] == 1442
        assert allegany["return_rate"] is not None
