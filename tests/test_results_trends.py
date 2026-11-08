"""Tests for the per-county results-over-time query layer (skipjack.web.results_trends).

The first part runs on made-up rows for Allegany and Garrett across GG14, PG16 and GG22
(generals), GP14 and GP22 (primaries) and a GG26 unofficial count. The db-backed tests at the
end (skipped when skipjack.db is absent) build every county from the real data.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.web import results, results_trends, results_unofficial
from skipjack.web.db import DB_PATH


@pytest.fixture
def conn(monkeypatch):
    # the fixtures have two counties; a statewide office must be on every county's ballot
    monkeypatch.setattr(results, "N_COUNTIES", 2)
    monkeypatch.setattr(results_unofficial, "N_COUNTIES", 2)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def add(conn, election, county, party_file, office, district, candidates, source_id=None):
    """Rows for one contest in one county. A candidate is (name, party, winner, votes) with
    votes an int, optionally followed by an `against` count and write_in."""
    for name, party, winner, votes, *extra in candidates:
        against = extra[0] if extra else None
        write_in = extra[1] if len(extra) > 1 else 0
        cur = conn.execute(
            """INSERT INTO results_county (source_id, election, county, party_file, office,
               district, candidate, party, winner, write_in, total_votes, total_against)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id or f"sbe-results-{election}-{county}-{party_file}",
                election, county, party_file, office, district, name, party, int(winner),
                write_in, votes, against,
            ),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO results_votes (result_id, vote_type, votes, votes_against)"
            " VALUES (?, ?, ?, NULL)",
            (cur.lastrowid, "early", votes),
        )


def add_unofficial(conn, county, office, district, candidates, vote_for=1):
    """GG26 rows from the board's unofficial pages. A candidate is (name, party, votes)."""
    for name, party, votes in candidates:
        cur = conn.execute(
            """INSERT INTO results_unofficial (source_id, election, snapshot, last_refreshed,
               county, kind, office, district, area, vote_for, seat_rule, candidate_heading,
               detail, text_url, precincts_reported, precincts_total, candidate, choice, party,
               write_in, total_votes)
               VALUES (?, 'GG26', '2026-11-04T160222', '11/04/2026 04:02:22 PM', ?, 'contest',
                       ?, ?, '', ?, '', '', '', '', 30, 30, ?, '', ?, 0, ?)""",
            (f"sbe-results-unofficial-GG26-{county}", county, office, district, vote_for,
             name, party, votes),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO results_unofficial_votes (result_id, vote_type, votes) VALUES (?, ?, ?)",
            (cur.lastrowid, "early", votes),
        )


COUNTIES = ("Allegany", "Garrett")


@pytest.fixture
def history(conn):
    """Two counties, three generals (GG14, PG16, GG22), two primaries (GP14, GP22)."""
    for county in COUNTIES:
        # --- GG14: Hogan carries both counties and the state
        add(conn, "GG14", county, "all", "Governor / Lt. Governor", "",
            [("Larry Hogan", "REP", True, 60 if county == "Allegany" else 70),
             ("Anthony Brown", "DEM", False, 40 if county == "Allegany" else 30)])  # fmt: skip
        add(conn, "GG14", county, "all", "U.S. Congress", "6",
            [("Dan Bongino", "REP", False, 55), ("John Delaney", "DEM", True, 45)])  # fmt: skip
        add(conn, "GG14", county, "all", "Appeals Judge MD at Lrg", "",
            [("Judge J", "JUD", True, 80, 20)])  # fmt: skip
        add(conn, "GG14", county, "all", "Sheriff", "",
            [("Rob Sheriff", "REP", True, 52), ("Dee Sheriff", "DEM", False, 48)])  # fmt: skip
        add(conn, "GG14", county, "all", "County Commissioner", "1",
            [("Jake Shade", "REP", True, 50), ("Dee One", "DEM", False, 30)])  # fmt: skip
        add(conn, "GG14", county, "all", "County Commissioner", "2",
            [("Bill Valentine", "REP", True, 1)])  # fmt: skip
        add(conn, "GG14", county, "all", "Judge Orphans Court", "",
            [("Ann A", "REP", True, 30), ("Bob B", "REP", True, 25), ("Cat C", "DEM", True, 20),
             ("Dan D", "DEM", False, 15)])  # fmt: skip
        add(conn, "GG14", county, "all", "Mayor of Cumberland", "", [("May Or", "", True, 9)])
        add(conn, "GG14", county, "all", "DEM Cent Comm", "", [("Party P", "DEM", True, 9)])
        # --- PG16: Trump carries both counties, Clinton carries the state
        add(conn, "PG16", county, "all", "President - Vice Pres", "",
            [("Donald Trump", "REP", False, 65), ("Hillary Clinton", "DEM", True, 35),
             ("Write-ins", "", False, 2, None, 1)])  # fmt: skip
        add(conn, "PG16", county, "all", "Rep in Congress", "6",
            [("Amie Hoeber", "REP", False, 52), ("John Delaney", "DEM", True, 48)])  # fmt: skip
        # --- GG22: Cox carries both counties, Moore carries the state
        add(conn, "GG22", county, "all", "Governor / Lt. Governor", "",
            [("Dan Cox", "REP", False, 60), ("Wes Moore", "DEM", True, 40)])  # fmt: skip
        add(conn, "GG22", county, "all", "U.S. Congress", "6",
            [("Neil Parrott", "REP", False, 55), ("David Trone", "DEM", True, 45)])  # fmt: skip
        add(conn, "GG22", county, "all", "Sheriff", "",
            [("Rob Sheriff", "REP", True, 55), ("Dee Sheriff", "DEM", False, 45)])  # fmt: skip
        add(conn, "GG22", county, "all", "County Commissioner", "1",
            [("Jake Shade", "REP", True, 12), ("Dee One", "DEM", False, 8)])  # fmt: skip
        add(conn, "GG22", county, "all", "County Commissioner", "2",
            [("Bill Valentine", "REP", True, 20)])  # fmt: skip
        add(conn, "GG22", county, "all", "County Commissioner", "3",
            [("Tre Three", "REP", True, 11), ("Dee Three", "DEM", False, 9)])  # fmt: skip
        add(conn, "GG22", county, "all", "Judge Orphans' Court", "",
            [("Ann A", "REP", True, 30), ("Bob B", "REP", True, 25), ("Cat C", "DEM", True, 20),
             ("Dan D", "DEM", False, 15)])  # fmt: skip
        # --- GP14 and GP22 primaries
        for code in ("GP14", "GP22"):
            allegany = county == "Allegany"
            add(conn, code, county, "dem", "Governor / Lt. Governor", "",
                [("Dem Nominee", "DEM", True, 60 if allegany else 40),
                 ("Dem Runner-up", "DEM", False, 40 if allegany else 60)])  # fmt: skip
            add(conn, code, county, "rep", "Governor / Lt. Governor", "",
                [("Rep Nominee", "REP", True, 70), ("Rep Runner-up", "REP", False, 30)])  # fmt: skip
            add(conn, code, county, "dem", "Sheriff", "", [("Dee Sheriff", "DEM", True, 33)])
            add(conn, code, county, "non", "Board of Education", "1",
                [("Bea Board", "NON", True, 20), ("Bo Board", "NON", False, 10)])  # fmt: skip
    # a city comptroller, on one county's ballot only, is not the state office
    add(conn, "PG16", "Allegany", "all", "Comptroller", "", [("City C", "DEM", True, 5)])
    return conn


@pytest.fixture
def allegany(history):
    return results_trends.county_trends(history, "allegany")


def _series(trends, level, series_id):
    lv = next(lv for lv in trends["levels"] if lv["level"] == level)
    return next(s for s in lv["offices"] if s["id"] == series_id)


# --- elections ---------------------------------------------------------------------------


def test_elections_oldest_first_and_split_by_kind(allegany):
    assert [e["code"] for e in allegany["elections"]] == ["GP14", "GG14", "PG16", "GP22", "GG22"]
    assert [e["code"] for e in allegany["generals"]] == ["GG14", "PG16", "GG22"]
    assert [e["code"] for e in allegany["primaries"]] == ["GP14", "GP22"]
    assert [e["cycle"] for e in allegany["generals"]] == [
        "gubernatorial", "presidential", "gubernatorial"
    ]  # fmt: skip
    assert all(e["unofficial"] is False for e in allegany["elections"])
    assert allegany["county"] == "Allegany" and allegany["slug"] == "allegany"
    assert [lv["level"] for lv in allegany["levels"]] == ["federal", "state", "county"]
    assert [lv["label"] for lv in allegany["levels"]] == ["Federal", "State", "County"]


def test_every_series_carries_its_level_and_no_scratch_keys(allegany):
    for lv in allegany["levels"]:
        for s in lv["offices"]:
            assert s["level"] == lv["level"], s["id"]
            assert not [k for k in s if k.startswith("_")], s["id"]
    assert _series(allegany, "county", "sheriff")["level"] == "county"


def test_unknown_county_is_none(history):
    assert results_trends.county_trends(history, "narnia") is None
    assert results_trends.county_trends(history, "worcester") is None  # no rows


def test_county_slugs_in_sbe_order(history):
    add(history, "GG22", "Anne Arundel", "all", "Sheriff", "", [("X", "DEM", True, 1)])
    assert results_trends.county_slugs(history) == ["allegany", "anne-arundel", "garrett"]


# --- general points ----------------------------------------------------------------------


def test_general_point_shares_margin_winners_leader_and_hrefs(allegany):
    gov = _series(allegany, "state", "governor")
    assert gov["title"] == "Governor" and gov["key"] == "governor"
    assert gov["district"] == "" and gov["districted"] is False and gov["breaks"] == []
    gg14, pg16, gg22 = gov["general"]
    assert pg16 is None  # no governor in a presidential year
    assert gg14["election"] == "GG14" and gg14["year"] == 2014 and gg14["unofficial"] is False
    assert gg14["total"] == 100 and gg14["seats"] == 1 and gg14["era"] is None
    assert gg14["dem"] == {"name": "Anthony Brown", "votes": 40, "share": 40.0}
    assert gg14["rep"] == {"name": "Larry Hogan", "votes": 60, "share": 60.0}
    assert gg14["other_share"] == 0.0 and gg14["contested"] is True
    assert gg14["margin"] == -20.0
    assert gg14["winners"] == [{"name": "Larry Hogan", "party": "REP"}]
    assert gg14["leader_party"] == "REP" and gg14["matched"] is True
    assert gg14["href"] == "/results/gg14/county/allegany/#governor-lt-governor-all"
    assert gg14["contest_href"] == "/results/gg14/governor-lt-governor-all/"
    assert gg22["margin"] == -20.0 and gg22["matched"] is False
    assert gg22["winners"] == [{"name": "Wes Moore", "party": "DEM"}]


def test_write_ins_count_in_the_total_but_not_as_other_candidates(allegany):
    president = _series(allegany, "federal", "president")
    _, pg16, _ = president["general"]
    assert pg16["total"] == 102
    assert pg16["rep"]["share"] == 63.7 and pg16["dem"]["share"] == 34.3
    assert pg16["other_share"] == 2.0
    assert pg16["winners"] == [{"name": "Hillary Clinton", "party": "DEM"}]
    assert pg16["leader_party"] == "REP" and pg16["matched"] is False


def test_uncontested_point_has_no_margin(allegany):
    commissioner = _series(allegany, "county", "county_commissioner-2")
    gg14 = commissioner["general"][0]
    assert gg14["contested"] is False and gg14["margin"] is None
    assert gg14["dem"] is None and gg14["rep"] == {
        "name": "Bill Valentine",
        "votes": 1,
        "share": 100.0,
    }
    assert gg14["other_share"] == 0.0
    assert gg14["matched"] is None  # a county office, the flag is the county's own
    assert gg14["contest_href"] is None


def test_multi_seat_sums_party_votes_and_lists_every_winner(allegany):
    court = _series(allegany, "county", "orphans_court")
    assert court["title"] == "Orphans' Court judge"
    gg14 = court["general"][0]
    assert gg14["seats"] == 3 and gg14["total"] == 90
    assert gg14["rep"] == {"name": "Ann A, Bob B", "votes": 55, "share": 61.1}
    assert gg14["dem"] == {"name": "Cat C, Dan D", "votes": 35, "share": 38.9}
    assert gg14["margin"] == pytest.approx(-22.2)
    assert [w["name"] for w in gg14["winners"]] == ["Ann A", "Bob B", "Cat C"]
    assert gg14["leader_party"] is None


def test_matched_follows_the_flag_and_is_none_without_one(history):
    add(history, "PG16", "Allegany", "all", "U.S. Senator", "",
        [("Chris Van Hollen", "DEM", True, 45), ("Kathy Szeliga", "REP", False, 55)])  # fmt: skip
    add(history, "PG16", "Garrett", "all", "U.S. Senator", "",
        [("Chris Van Hollen", "DEM", True, 45), ("Kathy Szeliga", "REP", False, 55)])  # fmt: skip
    t = results_trends.county_trends(history, "allegany")
    senate = _series(t, "federal", "us_senate")["general"][1]
    assert senate["matched"] is False
    house = _series(t, "federal", "us_house-6")["general"][1]
    assert house["matched"] is None  # not a statewide contest


# --- series ------------------------------------------------------------------------------


def test_office_key_consults_both_spellings_and_is_none_for_strangers():
    assert results_trends._office_key("Sheriff", "local") == "sheriff"
    assert results_trends._office_key("Clerk of the Circuit Court", "local") == "clerk"
    assert results_trends._office_key("Comptroller", "statewide") == "comptroller"
    assert results_trends._office_key("Comptroller", "local") == "city_comptroller"
    assert results_trends._office_key("Dog Catcher", "local") is None


def test_us_house_series_breaks_at_2022_with_an_era_per_point(allegany):
    house = _series(allegany, "federal", "us_house-6")
    assert house["title"] == "U.S. House, District 6" and house["districted"] is True
    assert house["district"] == "6" and house["breaks"] == [2022]
    assert [p["era"] for p in house["general"]] == [2012, 2012, 2022]
    assert [p["election"] for p in house["general"]] == ["GG14", "PG16", "GG22"]


def test_county_commissioner_breaks_when_its_district_set_changes(allegany):
    county = next(lv for lv in allegany["levels"] if lv["level"] == "county")
    ids = [s["id"] for s in county["offices"]]
    assert ids.index("county_commissioner-1") < ids.index("county_commissioner-2")
    assert ids.index("county_commissioner-2") < ids.index("county_commissioner-3")
    assert ids.index("sheriff") > ids.index("county_commissioner-3")
    for d in ("1", "2", "3"):
        assert _series(allegany, "county", f"county_commissioner-{d}")["breaks"] == [2022]
    three = _series(allegany, "county", "county_commissioner-3")
    assert three["title"] == "County Commissioner, District 3"
    assert three["general"][0] is None and three["general"][2] is not None
    assert _series(allegany, "county", "sheriff")["breaks"] == []


def test_a_staggered_board_is_not_a_break(history):
    """Seats elected in presidential years and in gubernatorial years are two classes of one
    board, compared within their cycle only."""
    for county in COUNTIES:
        for code, districts in (("GG14", "35"), ("PG16", "24"), ("GG22", "35")):
            for d in districts:
                add(history, code, county, "all", "Board of Education", d,
                    [("Bea Board", "NON", True, 5), ("Bo Board", "NON", False, 3)])  # fmt: skip
    t = results_trends.county_trends(history, "allegany")
    for d in ("2", "3", "4", "5"):
        assert _series(t, "county", f"board_of_education-{d}")["breaks"] == []


def test_judicial_municipal_party_and_retention_contests_are_left_out(allegany):
    ids = {s["id"] for lv in allegany["levels"] for s in lv["offices"]}
    assert "judge" not in ids and "municipal" not in ids and "party" not in ids
    assert "convention" not in ids
    assert ids == {
        "president", "us_house-6", "governor", "city_comptroller", "sheriff",
        "county_commissioner-1", "county_commissioner-2", "county_commissioner-3",
        "orphans_court", "board_of_education-1",
    }  # fmt: skip


def test_comptroller_in_a_presidential_year_is_a_county_series(history):
    allegany = results_trends.county_trends(history, "allegany")
    state = next(lv for lv in allegany["levels"] if lv["level"] == "state")
    assert [s["id"] for s in state["offices"]] == ["governor"]
    city = _series(allegany, "county", "city_comptroller")
    assert city["title"] == "City Comptroller"
    assert [p and p["total"] for p in city["general"]] == [None, 5, None]
    garrett = results_trends.county_trends(history, "garrett")
    assert "city_comptroller" not in {s["id"] for lv in garrett["levels"] for s in lv["offices"]}


# --- primaries ---------------------------------------------------------------------------


def test_primary_points(allegany):
    gov = _series(allegany, "state", "governor")
    assert set(gov["primary"]) == {"dem", "rep"}
    gp14, gp22 = gov["primary"]["dem"]
    assert gp14["election"] == "GP14" and gp14["year"] == 2014 and gp14["party_file"] == "dem"
    assert gp14["seats"] == 1 and gp14["candidates"] == 2 and gp14["contested"] is True
    assert gp14["total"] == 100
    assert gp14["winner"] == {"name": "Dem Nominee", "votes": 60, "share": 60.0}
    assert gp14["href"] == "/results/gp14/county/allegany/#governor-lt-governor-dem"
    assert gp22["election"] == "GP22"
    sheriff = _series(allegany, "county", "sheriff")
    assert list(sheriff["primary"]) == ["dem"]
    assert sheriff["primary"]["dem"][0]["contested"] is False
    assert sheriff["primary"]["dem"][0]["winner"]["name"] == "Dee Sheriff"
    board = _series(allegany, "county", "board_of_education-1")
    assert board["general"] == [None, None, None]
    assert list(board["primary"]) == ["non"] and board["primary"]["non"][1]["candidates"] == 2


def test_primary_winner_is_none_without_a_flag(history):
    add(history, "GP22", "Allegany", "rep", "Sheriff", "", [("Un Flagged", "REP", False, 7)])
    t = results_trends.county_trends(history, "allegany")
    sheriff = _series(t, "county", "sheriff")
    assert sheriff["primary"]["rep"] == [None, {
        "election": "GP22", "year": 2022, "unofficial": False, "party_file": "rep", "seats": 1,
        "candidates": 1, "contested": False, "total": 7, "winner": None,
        "href": "/results/gp22/county/allegany/#sheriff-rep",
    }]  # fmt: skip


def test_primary_electorate(allegany):
    gp14, gp22 = allegany["summary"]["primary_electorate"]
    assert gp14["dem"] == {"title": "Governor / Lt. Governor (Democratic)", "total": 100}
    assert gp14["rep"] == {"title": "Governor / Lt. Governor (Republican)", "total": 100}
    assert gp14["dem_share_of_two"] == 50.0
    assert gp22["dem_share_of_two"] == 50.0


# --- summary -----------------------------------------------------------------------------


def test_top_of_ticket_by_cycle_with_statewide_margin_and_lean(allegany):
    gg14, pg16, gg22 = allegany["summary"]["top"]
    assert (gg14["office"], gg14["title"]) == ("governor", "Governor")
    assert (pg16["office"], pg16["title"]) == ("president", "President")
    assert gg14["margin"] == -20.0
    # statewide: Hogan 130, Brown 70 -> 35 - 65
    assert gg14["statewide_margin"] == -30.0 and gg14["lean"] == 10.0
    assert pg16["statewide_margin"] == pytest.approx(-29.4) and pg16["lean"] == pytest.approx(0)
    assert gg22["statewide_margin"] == -20.0 and gg22["lean"] == 0.0
    assert gg14["href"] == "/results/gg14/county/allegany/#governor-lt-governor-all"


def test_top_is_none_when_the_ballot_does_not_open_with_the_ticket(history):
    add(history, "PG20", "Allegany", "all", "Sheriff", "", [("X", "DEM", True, 1)])
    add(history, "PG20", "Garrett", "all", "Sheriff", "", [("X", "DEM", True, 1)])
    t = results_trends.county_trends(history, "allegany")
    assert [e["code"] for e in t["generals"]] == ["GG14", "PG16", "PG20", "GG22"]
    assert t["summary"]["top"][2] is None and t["summary"]["ticket_gap"][2] is None


def test_ticket_gap_uses_countywide_contested_county_offices_only(allegany):
    gg14, pg16, gg22 = allegany["summary"]["ticket_gap"]
    assert pg16["offices"] == [] and pg16["mean_gap"] is None and pg16["compared"] == 0
    assert gg14["top_title"] == "Governor" and gg14["top_dem_share"] == 40.0
    # the commissioner districts cover part of the county, the orphans' court is a three-seat
    # contest with few votes per seat, Congress is not a county office
    assert gg14["offices"] == [{"id": "sheriff", "title": "Sheriff", "dem_share": 48.0, "gap": 8.0}]
    assert gg14["mean_gap"] == 8.0 and gg14["compared"] == 1
    assert gg22["offices"][0]["gap"] == 5.0


def test_disagreements_newest_first(history):
    garrett = results_trends.county_trends(history, "garrett")
    got = garrett["summary"]["disagreements"]
    assert [(d["election"], d["title"], d["party_label"]) for d in got] == [
        ("GG22", "Governor", None),
        ("GP22", "Governor", "Democratic primary"),
        ("PG16", "President", None),
        ("GP14", "Governor", "Democratic primary"),
    ]
    assert got[0]["county_leader"] == {"name": "Dan Cox", "party": "REP"}
    assert got[0]["statewide_winner"] == {"name": "Wes Moore", "party": "DEM"}
    assert got[0]["href"] == "/results/gg22/county/garrett/#governor-lt-governor-all"
    assert got[1]["county_leader"] == {"name": "Dem Runner-up", "party": "DEM"}
    assert got[1]["statewide_winner"] == {"name": "Dem Nominee", "party": "DEM"}
    assert got[1]["year"] == 2022
    assert [d["kind"] for d in got] == ["general", "primary", "general", "primary"]
    # Allegany picked the nominee in both primaries
    allegany = results_trends.county_trends(history, "allegany")
    assert [d["election"] for d in allegany["summary"]["disagreements"]] == ["GG22", "PG16"]


def test_winners_wall_collapses_runs(allegany):
    wall = {w["id"]: w for w in allegany["summary"]["winners_wall"]}
    assert wall["sheriff"]["title"] == "Sheriff" and wall["sheriff"]["level"] == "county"
    assert wall["sheriff"]["runs"] == [
        {"name": "Rob Sheriff", "party": "REP", "years": [2014, 2022]}
    ]
    assert wall["governor"]["runs"] == [
        {"name": "Larry Hogan", "party": "REP", "years": [2014]},
        {"name": "Wes Moore", "party": "DEM", "years": [2022]},
    ]
    # a multi-seat contest's winner set is one run
    assert wall["orphans_court"]["runs"] == [
        {"name": "Ann A, Bob B, Cat C", "party": "REP, REP, DEM", "years": [2014, 2022]}
    ]
    assert "board_of_education-1" not in wall  # no general winners
    assert [w["id"] for w in allegany["summary"]["winners_wall"]][:2] == ["president", "us_house-6"]


def test_same_winners_ignores_ballot_order():
    a = [{"name": "Ann A", "party": "REP"}, {"name": "Bob B", "party": "REP"}, {"name": "Cat C", "party": "DEM"}]  # fmt: skip
    b = [{"name": "Cat C", "party": "DEM"}, {"name": "Ann A", "party": "REP"}, {"name": "Bob B", "party": "REP"}]  # fmt: skip
    assert results_trends._same_winners(a, b)
    assert not results_trends._same_winners(a, b[:2])
    assert not results_trends._same_winners(a, [*b[:2], {"name": "Dan D", "party": "REP"}])


def test_winners_wall_merges_a_winner_whose_middle_name_or_suffix_drifts(conn):
    for code, name in (
        ("GG14", "John Delaney"),
        ("PG16", "John K. Delaney"),
        ("GG22", "John K. Delaney, Jr."),
    ):
        for county in COUNTIES:
            add(conn, code, county, "all", "Governor / Lt. Governor", "",
                [("Rep R", "REP", False, 40), ("Dem D", "DEM", True, 60)])  # fmt: skip
            add(conn, code, county, "all", "U.S. Congress", "6",
                [("Other O", "REP", False, 40), (name, "DEM", True, 60)])  # fmt: skip
    # a different person with the same first name, and the same name on another party line
    add(conn, "PG24", "Allegany", "all", "President - Vice Pres", "",
        [("Rep R", "REP", False, 40), ("Dem D", "DEM", True, 60)])  # fmt: skip
    add(conn, "PG24", "Allegany", "all", "U.S. Congress", "6",
        [("Other O", "REP", False, 40), ("John Smith", "DEM", True, 60)])  # fmt: skip
    add(conn, "PG24", "Garrett", "all", "President - Vice Pres", "",
        [("Rep R", "REP", False, 40), ("Dem D", "DEM", True, 60)])  # fmt: skip
    add(conn, "PG24", "Garrett", "all", "U.S. Congress", "6",
        [("Other O", "DEM", False, 40), ("John K. Delaney", "REP", True, 60)])  # fmt: skip
    wall = {
        w["id"]: w
        for w in results_trends.county_trends(conn, "allegany")["summary"]["winners_wall"]
    }
    assert wall["us_house-6"]["runs"] == [
        {"name": "John K. Delaney, Jr.", "party": "DEM", "years": [2014, 2016, 2022]},
        {"name": "John Smith", "party": "DEM", "years": [2024]},
    ]
    wall = {
        w["id"]: w for w in results_trends.county_trends(conn, "garrett")["summary"]["winners_wall"]
    }
    assert [r["years"] for r in wall["us_house-6"]["runs"]] == [[2014, 2016, 2022], [2024]]


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [("John Delaney", "John K. Delaney", True), ("John Delaney", "JOHN DELANEY, JR.", True),
     ("David Trone", "David J. Trone", True), ("Ann A", "Bob B", False),
     ("John Delaney", "John Smith", False), ("Delaney", "John Delaney", False)],
)  # fmt: skip
def test_same_person(a, b, same):
    assert results_trends._same_person(a, b) is same


def test_source_ids_in_first_seen_order(allegany):
    assert allegany["source_ids"][:3] == [
        "sbe-results-GP14-Allegany-dem",
        "sbe-results-GP14-Allegany-non",
        "sbe-results-GP14-Allegany-rep",
    ]
    assert len(allegany["source_ids"]) == len(set(allegany["source_ids"])) == 9


# --- the unofficial count ----------------------------------------------------------------


@pytest.fixture
def with_gg26(history):
    assert results_unofficial.is_unofficial("GG26")  # RESULTS_ELECTIONS["GG26"].published is False
    for county in COUNTIES:
        add_unofficial(history, county, "Governor / Lt. Governor", "",
                       [("Wes Moore and Aruna Miller", "Democratic", 30),
                        ("Dan Cox and Rob Krop", "Republican", 70)])  # fmt: skip
        add_unofficial(history, county, "Sheriff", "",
                       [("Rob Sheriff", "Republican", 58), ("Dee Sheriff", "Democratic", 42)])  # fmt: skip
        add_unofficial(history, county, "Clerk of the Circuit Court", "",
                       [("Clerk K", "Republican", 58)])  # fmt: skip
        add_unofficial(history, county, "Representative in Congress", "District 6",
                       [("April McClain Delaney", "Democratic", 44), ("Neil Parrott", "Republican", 56)])  # fmt: skip
    return history


@pytest.mark.parametrize(
    ("spelled", "district"),
    [("District 6", "6"), ("District 1A", "01A"), ("District 43A", "43A"), ("District A", "A"),
     ("6", "6"), ("01A", "01A"), ("A", "A"), ("Circuit 8", "Circuit 8"), ("", "")],
)  # fmt: skip
def test_unofficial_districts_take_the_certified_spelling(spelled, district):
    assert results_trends._district(spelled) == district


def test_gg26_is_a_flagged_last_point(with_gg26):
    t = results_trends.county_trends(with_gg26, "allegany")
    assert [e["code"] for e in t["generals"]] == ["GG14", "PG16", "GG22", "GG26"]
    assert t["generals"][-1]["unofficial"] is True and t["generals"][-1]["cycle"] == "gubernatorial"
    gov = _series(t, "state", "governor")
    gg26 = gov["general"][3]
    assert gg26["unofficial"] is True and gg26["election"] == "GG26"
    assert gg26["dem"] == {"name": "Wes Moore and Aruna Miller", "votes": 30, "share": 30.0}
    assert gg26["margin"] == -40.0 and gg26["winners"] == [] and gg26["matched"] is None
    assert gg26["leader_party"] == "REP"
    assert gg26["href"] == "/results/gg26/county/allegany/#governor-lt-governor-all"
    assert gg26["contest_href"] == "/results/gg26/governor-lt-governor-all/"
    top = t["summary"]["top"][3]
    assert top["office"] == "governor" and top["statewide_margin"] == -40.0 and top["lean"] == 0.0
    # the board's spellings map to the same offices
    assert _series(t, "county", "clerk")["general"][3]["total"] == 58
    sheriff = _series(t, "county", "sheriff")
    assert sheriff["general"][3]["rep"]["votes"] == 58
    house = _series(t, "federal", "us_house-6")  # "District 6" joins the certified series
    assert house["general"][3]["dem"]["votes"] == 44 and house["breaks"] == [2022]
    assert house["general"][3]["href"].endswith("#representative-in-congress-district-6-all")
    # no winner flag: the wall and the disagreements stop at the certified results
    wall = {w["id"]: w for w in t["summary"]["winners_wall"]}
    assert wall["sheriff"]["runs"][0]["years"] == [2014, 2022]
    assert all(d["election"] != "GG26" for d in t["summary"]["disagreements"])
    assert t["summary"]["ticket_gap"][3]["offices"][0]["gap"] == 12.0
    assert "sbe-results-unofficial-GG26-Allegany" in t["source_ids"]


def test_gg26_disappears_once_certified(with_gg26, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    t = results_trends.county_trends(with_gg26, "allegany")
    assert [e["code"] for e in t["generals"]] == ["GG14", "PG16", "GG22"]


# --- the index ---------------------------------------------------------------------------


def test_counties_index_rows(history):
    rows = results_trends.counties_index(history)
    assert [r["slug"] for r in rows] == ["allegany", "garrett"]
    allegany = rows[0]
    assert allegany["county"] == "Allegany"
    assert allegany["latest"]["election"] == "GG22" and allegany["first"]["election"] == "GG14"
    assert allegany["latest"]["lean"] == 0.0 and allegany["first"]["lean"] == 10.0
    assert allegany["shift"] == 0.0
    assert allegany["spark"] == [-20.0, pytest.approx(-29.4), -20.0]
    assert allegany["generals"] == 3 and allegany["primaries"] == 2
    # latest governor (GG22) and president (PG16), and the shift from the earlier to the later
    assert allegany["governor"]["election"] == "GG22"
    assert allegany["president"]["election"] == "PG16"
    assert allegany["cycle_shift"]["value"] == pytest.approx(9.4)
    assert (allegany["cycle_shift"]["from"], allegany["cycle_shift"]["to"]) == (2016, 2022)
    garrett = rows[1]
    assert garrett["first"]["margin"] == -40.0 and garrett["shift"] == 20.0


# --- the real data -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    if not DB_PATH.exists():
        pytest.skip("skipjack.db is not built")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        n = c.execute("SELECT COUNT(*) FROM results_county").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    if not n:
        c.close()
        pytest.skip("skipjack.db has no results rows")
    yield c
    c.close()


def test_real_every_county_builds(real):
    slugs = results_trends.county_slugs(real)
    assert len(slugs) == 24 and slugs[0] == "allegany" and slugs[-1] == "worcester"
    for slug in slugs:
        t = results_trends.county_trends(real, slug)
        assert [lv["level"] for lv in t["levels"]] == ["federal", "state", "county"]
        assert all(lv["offices"] for lv in t["levels"]), slug
        assert len(t["summary"]["top"]) == len(t["generals"])
        assert len(t["summary"]["ticket_gap"]) == len(t["generals"])
        assert len(t["summary"]["primary_electorate"]) == len(t["primaries"])
        ids = [s["id"] for lv in t["levels"] for s in lv["offices"]]
        # a "District A" / "District 6" from the unofficial pages joins its certified series
        assert len(ids) == len(set(ids)) and not any("district" in i for i in ids), slug
        for lv in t["levels"]:
            for s in lv["offices"]:
                assert s["level"] == lv["level"]
                assert len(s["general"]) == len(t["generals"]), (slug, s["id"])
                for pts in s["primary"].values():
                    assert len(pts) == len(t["primaries"])


def test_real_allegany_governor_spans_2010_to_2022(real):
    t = results_trends.county_trends(real, "allegany")
    gov = _series(t, "state", "governor")
    certified = [p for p in gov["general"] if p and not p["unofficial"]]
    assert [p["year"] for p in certified] == [2010, 2014, 2018, 2022]
    assert all(p["contested"] and p["margin"] is not None for p in certified)
    assert all(p["winners"] for p in certified)
    house = _series(t, "federal", "us_house-6")
    assert house["breaks"] == [2012, 2022]
    years = [e["year"] for e in t["generals"]]
    assert years[0] == 2010 and years == sorted(years)


def test_real_lettered_districts_join_their_series_and_keep_no_false_break(real):
    """Harford's council and board seats are "District A" .. "District F" on the unofficial
    pages and "A" .. "F" in the certified files: one series each, and no break in 2026. Where a
    county really gained a seat for 2026 (Baltimore County's eighth and ninth council districts,
    Wicomico's sixth and seventh) the break stays."""
    harford = results_trends.county_trends(real, "harford")
    for key in ("county_council", "board_of_education"):
        for letter in "ABCDEF":
            s = _series(harford, "county", f"{key}-{letter.lower()}")
            assert 2026 not in s["breaks"], s["id"]
            assert s["general"][-1] is not None and s["general"][-1]["unofficial"], s["id"]
    baltimore = results_trends.county_trends(real, "baltimore-county")
    assert _series(baltimore, "county", "county_council-9")["breaks"] == [2026]
    assert _series(baltimore, "county", "county_council-8")["general"][-2] is None
    wicomico = results_trends.county_trends(real, "wicomico")
    assert _series(wicomico, "county", "county_council-7")["breaks"] == [2026]


def test_real_every_unofficial_office_is_known(real):
    try:
        offices = {r[0] for r in real.execute(
            "SELECT DISTINCT office FROM results_unofficial WHERE kind <> 'question'"
        )}  # fmt: skip
    except sqlite3.OperationalError:
        pytest.skip("no unofficial rows")
    for office in offices:
        assert results_trends._office_key(office, "local") is not None, office


def test_real_counties_index_has_24_rows(real):
    t0 = time.perf_counter()
    rows = results_trends.counties_index(real)
    elapsed = time.perf_counter() - t0
    assert len(rows) == 24
    assert all(r["latest"] and r["first"] for r in rows)
    assert all(r["generals"] >= 8 for r in rows)
    assert elapsed < 10  # measured at ~1.5 s; a loose bound so a slow runner does not flake
