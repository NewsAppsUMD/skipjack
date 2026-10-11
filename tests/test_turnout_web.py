"""Tests for the web turnout module: queries over the turnout tables.

The first half runs on made-up rows in an in-memory database. The db-backed section
(skipjack.db must be built) checks the real GG26 data by consistency — the pages and
headlines are read against whatever the committed rows sum to, since a certified
election's figures are fixed but an in-flight election's move with every snapshot.
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


# --- the pages, against the real built db -----------------------------------------------------
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")


@needs_db
def test_turnout_pages_render_the_real_election():
    """The db-backed render check on the real data: the pages show what the committed
    rows sum to, whatever day that is — the numbers' pins live in the parse's printed
    reconciliation, not in tests (a pinned day went red on the next snapshot)."""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app
    from skipjack.web.dates import day_label
    from skipjack.web.db import get_db

    client = TestClient(create_app())
    r = client.get("/turnout/")
    assert r.status_code == 200
    assert "2026 General Election" in r.text
    r = client.get("/turnout/gg26/")
    assert r.status_code == 200
    with get_db() as conn:
        headline = turnout.headline(conn, "GG26")
    requests_total = headline["requests"]["requests_total"]
    sent_returned = headline["sent-returned"]
    for value in (requests_total, sent_returned["sent"], sent_returned["received"]):
        assert f"{value:,}" in r.text
    assert day_label(sent_returned["as_of"]) in r.text  # the page says which day it shows
    # the parenthesized party figures inside the sent and received cells are labelled
    # where the table names them: the board's DEM / REP / OTH columns
    assert r.text.count("(Dem / Rep / other)") == 2
    assert "November 3, 2026" in r.text
    assert "data-sortable" in r.text
    assert 'id="turnout-data"' in r.text
    assert 'src="' + "/static/turnout.js" in r.text
    assert client.get("/turnout/nope/").status_code == 404


@needs_db
def test_turnout_nav_reaches_the_nav():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    assert client.get("/").text.find("/turnout/") != -1
    r = client.get("/turnout/gg26/")
    # the page links its downloads
    assert "/downloads/" in r.text


@needs_db
def test_real_gg26_headline_against_the_committed_data():
    """The headline reads each family at its own latest snapshot. The day and its
    figures are read from the db (they move as snapshots land), and the check sums the
    committed county rows independently — schema or query drift fails, a new snapshot
    does not."""
    from skipjack.web.db import get_db

    with get_db() as conn:
        headline = turnout.headline(conn, "GG26")
        latest = {
            family: conn.execute(
                "SELECT MAX(as_of) FROM turnout_counts WHERE election = 'GG26' AND family = ?",
                (family,),
            ).fetchone()[0]
            for family in ("requests", "sent-returned")
        }

        def county_sum(family, measure, as_of):
            return conn.execute(
                """SELECT SUM(value) FROM turnout_counts
                   WHERE election = 'GG26' AND family = ? AND measure = ? AND as_of = ?
                     AND geography = 'county' AND district = '' AND category = 'ALL'
                     AND party = 'ALL' AND county != ''""",
                (family, measure, as_of),
            ).fetchone()[0]

        sent_returned = headline["sent-returned"]
        assert headline["latest"] == max(latest.values())
        assert sent_returned["as_of"] == latest["sent-returned"]
        assert headline["requests"]["as_of"] == latest["requests"]
        assert sent_returned["sent"] == county_sum("sent-returned", "sent", latest["sent-returned"])
        assert sent_returned["received"] == county_sum(
            "sent-returned", "received", latest["sent-returned"]
        )
        assert sent_returned["accepted"] == county_sum(
            "sent-returned", "accepted", latest["sent-returned"]
        )
        assert headline["requests"]["requests_total"] == county_sum(
            "requests", "total", latest["requests"]
        )
        rows = {r["county"]: r for r in turnout.county_rows(conn, "GG26", latest["sent-returned"])}
        assert len(rows) == 24  # every county has published mail figures
        assert rows["Allegany"]["return_rate"] is not None


@needs_db
def test_data_page_lists_the_turnout_snapshots():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    html = TestClient(create_app()).get("/data/").text
    assert "/downloads/turnout/GG26/requests/county/2026-10-09.csv" in html
    assert "/downloads/turnout/GG26/sent-returned/legislative/2026-10-09_provenance.json" in html


@needs_db
def test_the_data_page_does_not_list_a_certified_election_s_unofficial_tallies():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    html = TestClient(create_app()).get("/data/").text
    assert "/downloads/turnout/GP26/early-voting/" not in html
    assert "/downloads/turnout/GP26/same-day/" not in html
    assert "/downloads/turnout/GP26/requests/" in html


@needs_db
def test_about_page_lists_the_turnout_sources():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    html = TestClient(create_app()).get("/about").text
    assert "sbe-turnout-gg26-requests-county-" in html
    assert "sbe-turnout-gg26-sent-returned-legislative-" in html


@needs_db
def test_the_turnout_csv_download_route_serves_files():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/downloads/turnout/GG26/requests/county/2026-10-09.csv")
    assert r.status_code == 200
    assert "Allegany" in r.text


@needs_db
def test_the_live_app_does_not_serve_a_certified_election_s_unofficial_tallies():
    """Files under data/ must not leak through a static mount: the unofficial tallies
    of a certified election 404 at direct download addresses too."""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/downloads/turnout/GP26/requests/county/2026-06-19.csv")
    assert r.status_code == 200  # a publishable family still serves
    for forbidden in (
        "/downloads/turnout/GP26/early-voting/county/2026-06-23.csv",
        "/downloads/turnout/GP26/same-day/county/2026-06-23.csv",
        "/downloads/turnout/GP26/../../skipjack.db",
    ):
        assert client.get(forbidden).status_code == 404


def test_zero_sent_counts_have_no_return_rate(conn):
    rows = [
        ("county", "Kent", "", "ALL", "ALL", "sent", 0),
        ("county", "Kent", "", "ALL", "ALL", "received", 0),
    ]
    add(conn, "2026-10-09", rows)
    [row] = turnout.county_rows(conn, "GG26", "2026-10-09")
    assert row["sent"] == 0 and row["return_rate"] is None


def test_chart_data_on_one_axis_with_a_missing_family(conn):
    # requests reported on a day the mail-in figures did not refresh: one axis,
    # None where a family has nothing
    add(
        conn, "2026-10-09", [("county", "Kent", "", "ALL", "ALL", "total", 1000)], family="requests"
    )
    add(conn, "2026-10-10", ALLEGANY, source_id="s2")
    chart = turnout.chart_data(conn, "GG26")
    assert chart["dates"] == ["2026-10-09", "2026-10-10"]
    requests = next(s for s in chart["series"] if s["name"] == "Requests")
    sent = next(s for s in chart["series"] if s["name"] == "Sent")
    assert requests["values"] == [1000, None]
    assert sent["values"] == [None, 4466]


def test_chart_data_drops_an_all_zero_series(conn):
    # accepted counts are zero until the canvass: that line waits until it moves
    add(conn, "2026-10-09", ALLEGANY)
    chart = turnout.chart_data(conn, "GG26")
    assert [s["name"] for s in chart["series"]] == ["Sent", "Received"]


def _page_db(tmp_path, snapshots):
    """A db file with GG26 snapshots: (as_of, rows) pairs, plus the registration
    table's minimum so the app boots."""
    path = tmp_path / "skipjack.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS voter_registration (
            id INTEGER PRIMARY KEY, source_id TEXT, report_date TEXT, county TEXT,
            party TEXT, party_name TEXT, active_voters INTEGER)"""
    )
    for i, snapshot in enumerate(snapshots):
        as_of, rows = snapshot[:2]
        family = snapshot[2] if len(snapshot) > 2 else "sent-returned"
        add(conn, as_of, rows, source_id=f"s{i}", family=family)
    conn.commit()
    conn.close()
    return path


@needs_db
def test_the_page_renders_a_change_from_a_second_snapshot(tmp_path, monkeypatch):
    """The daily case: the two files refresh on their own days, so the page reads each
    family at its own snapshot, and a second snapshot's tile shows the real change."""
    from fastapi.testclient import TestClient

    import skipjack.web.db as web_db
    from skipjack.web.app import create_app

    grew = [row if row[5] != "sent" or row[4] != "ALL" else row[:6] + (6373,) for row in WORCESTER]
    requested_only = [("county", "Kent", "", "ALL", "ALL", "total", 900)]
    db_path = _page_db(
        tmp_path,
        [
            ("2026-10-08", ALLEGANY + WORCESTER),
            ("2026-10-10", requested_only, "requests"),  # only the requests file refreshed
            ("2026-10-12", ALLEGANY + grew),
        ],
    )
    monkeypatch.setattr(web_db, "DB_PATH", db_path)
    html = TestClient(create_app()).get("/turnout/gg26/").text

    # the sent tile shows the real +1,000 change: the change is keyed by the measure
    # ("total"/"sent"), whatever the requests family's key would have been
    assert "+1,000" in html and "delta-up" in html
    # the county joining reads each family at its own snapshot: Kent is requests-only
    assert 'data-sort=""' in html  # absent values sort last, not as -1
    assert "<td>Kent</td>" in html
    assert "10,839" in html  # the whole state's ballots sent (4,466 + 6,373)
    assert "1,442" in html  # Allegany's received back, from the latest snapshot
    # the header says when each family was last updated
    assert "October 12, 2026" in html and "October 10, 2026" in html
    # a differing second date is said once, not once per tile
    assert html.count("(ballot figures as of") == 1


@needs_db
def test_the_as_of_sentence_says_a_shared_date_once(tmp_path, monkeypatch):
    """When every tile's figures come from the same report (the common daily case), the
    sentence says the date once: the published page repeated it once per tile."""
    from fastapi.testclient import TestClient

    import skipjack.web.db as web_db
    from skipjack.web.app import create_app

    requested_only = [("county", "Kent", "", "ALL", "ALL", "total", 900)]
    db_path = _page_db(
        tmp_path,
        [
            ("2026-10-09", ALLEGANY + WORCESTER),
            ("2026-10-09", requested_only, "requests"),
        ],
    )
    monkeypatch.setattr(web_db, "DB_PATH", db_path)
    html = TestClient(create_app()).get("/turnout/gg26/").text

    assert "as of October 9, 2026." in html
    assert "(ballot figures as of" not in html


# --- the certified (final) election page: GP26 --------------------------------------------------


def test_certified_headline_and_mode_shares(conn):
    add(
        conn,
        "2026-07-23",
        [
            ("county", "Allegany", "", "ALL", "ALL", "election-day", 6478),
            ("county", "Allegany", "", "ALL", "ALL", "early-voting", 1401),
            ("county", "Allegany", "", "ALL", "ALL", "vote-by-mail", 2452),
            ("county", "Allegany", "", "ALL", "ALL", "provisional", 284),
            ("county", "Allegany", "", "ALL", "ALL", "total", 10615),
            ("county", "Worcester", "", "ALL", "ALL", "election-day", 5125),
            ("county", "Worcester", "", "ALL", "ALL", "early-voting", 1566),
            ("county", "Worcester", "", "ALL", "ALL", "vote-by-mail", 2573),
            ("county", "Worcester", "", "ALL", "ALL", "provisional", 260),
            ("county", "Worcester", "", "ALL", "ALL", "total", 9524),
        ],
        family="county-canvass",
    )
    cert = turnout.certified_headline(conn, "GG26")
    assert cert["cast"] == 20139  # 10615 + 9524
    assert cert["by_mode"]["election-day"]["share"] == pytest.approx((6478 + 5125) / 20139)


def test_certified_county_rows_join_the_families(conn):
    add(
        conn,
        "2026-07-23",
        [
            ("county", "Allegany", "", "ALL", "ALL", m, v)
            for m, v in [("total", 10615), ("election-day", 6478)]
        ],
        family="county-canvass",
    )
    add(
        conn,
        "2026-07-23",
        [
            ("county", "Allegany", "", "ALL", "ALL", m, v)
            for m, v in [("sent", 4027), ("received", 2456), ("accepted", 2423), ("rejected", 33)]
        ],
        family="mail-voting",
    )
    # the unofficial tallies are in the data for the joining counties: a certified
    # election's rows must not surface them
    add(
        conn,
        "2026-07-23",
        [("county", "Allegany", "", "ALL", "ALL", "total", 1308)],
        family="early-voting",
    )
    add(
        conn,
        "2026-07-23",
        [
            ("county", "Allegany", "", "ALL", "ALL", m, v)
            for m, v in [("new-total", 11), ("addrchg-total", 5), ("total", 16)]
        ],
        family="same-day",
    )
    rows = turnout.certified_county_rows(
        conn,
        "GG26",
        {
            "county-canvass": "2026-07-23",
            "votes-accepted": "2026-07-23",
            "mail-voting": "2026-07-23",
            "requests": "2026-07-23",
        },
    )
    [row] = rows
    assert row["county"] == "Allegany"
    assert row["cast"] == 10615 and row["counted"] is None
    assert row["mail_voting"]["sent"] == 4027 and row["mail_voting"]["rejected"] == 33
    assert "early_voting_total" not in row
    assert "same_day" not in row


@needs_db
def test_real_pg24_page_renders_the_certified_data():
    """The 2024 general's page of record, pinned to figures parsed from the committed
    files (the April 2026 corrected re-posts superseded the December originals, so the
    canvass figures read at their own latest snapshot); nothing comes from the board's
    unofficial eight-day tallies."""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/turnout/")
    assert "2024 General Election" in r.text
    r = client.get("/turnout/pg24/")
    assert r.status_code == 200
    assert "3,067,931" in r.text  # ballots cast (the corrected canvass)
    assert "3,062,158" in r.text  # votes counted
    assert "72.4%" in r.text  # the board's own printed percentage (active registration)
    assert "994,654" in r.text  # early voting
    assert "1,158,770" in r.text  # election day
    assert "765,695" in r.text  # vote by mail
    assert "148,812" in r.text  # provisional
    assert "766,522" in r.text  # mail-in received back
    assert "762,971" in r.text  # mail-in accepted
    assert "3,551" in r.text  # mail-in rejected
    assert "882,938" in r.text  # mail-in ballot requests
    # the 2024-era denominator is printed where the percentage is explained
    assert "active registration alone" in r.text
    # figures of record only: no unofficial tallies, no links to them
    for forbidden in (
        "(the EV tally)",
        ">Same-day</th>",
        "/downloads/turnout/PG24/early-voting",
        "/downloads/turnout/PG24/same-day",
    ):
        assert forbidden not in r.text


# ---------------------------------------------------------------------------
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")


def test_certified_headlines_read_the_total_column_not_the_party_rows(conn):
    """The eligible workbooks publish one row per party plus each county's Total
    column; the state's count is the Total column's sum, not every party cell's — the
    turnout percentage hangs on the difference."""
    for party, value in (("DEM", 3068), ("REP", 1133), ("ALL", 4202)):
        add(
            conn,
            "2024-10-20",
            [("county", "Allegany", "", "ALL", party, "active", value)],
            family="eligible-active",
        )
        add(
            conn,
            "2024-10-20",
            [("county", "Allegany", "", "ALL", party, "inactive", 500 if party == "ALL" else 0)],
            family="eligible-inactive",
        )
    cert = turnout.certified_headline(conn, "GG26")
    assert cert["eligible"]["active"] == 4202  # not 4202 + 3068 + 1133
    assert cert["eligible"]["inactive"] == 500
    assert (
        turnout._family_county(conn, "GG26", "eligible-active", "Allegany", "2024-10-20")["active"]
        == 4202
    )


def test_the_board_s_printed_percentage_comes_from_its_own_sheet(conn):
    """The certified turnout percentage is the board's print: its official turnout
    sheet's ballots cast over the eligible voters the sheet itself prints (each summed
    from the county rows the parse checked). The site's guide-formula is the fallback
    when the board's sheet has not republished."""
    rows = [
        ("county", "Allegany", "", "ALL", "ALL", measure, value)
        for measure, value in [
            ("election-day", 1145134),
            ("early-voting", 984945),
            ("vote-by-mail", 765783),
            ("provisional", 149993),
            ("eligible", 4204572),
        ]
    ]
    add(conn, "2024-12-11", rows, family="official-turnout")
    cert = turnout.certified_headline(conn, "GG26")
    assert cert["turnout"]["value"] == pytest.approx(3045855 / 4204572)
    assert cert["turnout"]["printed"]  # the board's own sheet computed this


def test_the_guide_s_percentage_is_the_fallback_without_the_sheet(conn):
    """No official-turnout sheet published, but the two eligible workbooks are: the
    percentage computes per the guide — ballots cast over eligible voters (active and
    inactive together)."""
    add(
        conn,
        "2024-12-11",
        [("county", "Allegany", "", "ALL", "ALL", "total", 6048)],
        family="county-canvass",
    )
    add(
        conn,
        "2024-10-20",
        [("county", "Allegany", "", "ALL", "ALL", "active", 8042)],
        family="eligible-active",
    )
    add(
        conn,
        "2024-10-20",
        [("county", "Allegany", "", "ALL", "ALL", "inactive", 700)],
        family="eligible-inactive",
    )
    cert = turnout.certified_headline(conn, "GG26")
    assert cert["turnout"]["value"] == pytest.approx(6048 / (8042 + 700))
    assert not cert["turnout"]["printed"]


@needs_db
def test_real_gp26_page_renders_the_certified_data():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/turnout/")
    assert r.status_code == 200
    assert "2026 Primary Election" in r.text and "2026 General Election" in r.text
    r = client.get("/turnout/gp26/")
    assert r.status_code == 200
    assert "902,818" in r.text  # ballots cast, statewide
    assert "891,567" in r.text  # votes counted
    assert "41.3%" in r.text  # election-day share of the cast ballots
    assert "3,281" in r.text  # mail-in ballots rejected
    # the mail-in envelope tile carries the board's word for a canvass acceptance —
    # "accepted", not "counted" (that word belongs to the counted-votes report) — and the
    # page says the two mail-in figures measure different records
    assert "295,400 accepted" in r.text and "295,400 counted" not in r.text
    assert "envelopes returned" in r.text
    # a certified page carries only its figures of record: nothing from the board's
    # unofficial eight-day turnout files, not even their download links
    for forbidden in (
        "185,031",  # the unofficial early-voting tally
        "2,179",  # the unofficial same-day total
        "Early voting (the EV tally)",
        ">Same-day</th>",
        "/downloads/turnout/GP26/early-voting",
        "/downloads/turnout/GP26/same-day",
    ):
        assert forbidden not in r.text
    # the denominator files had not re-published when this snapshot was taken: the page
    # says so instead of claiming a turnout percentage
    assert "turnout percentage" in r.text
