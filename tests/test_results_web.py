"""Tests for the web results module: queries over results_county / results_votes.

The first half runs on made-up rows in an in-memory database. The db-backed section (skipped
when skipjack.db has no results rows) checks the real data: the statewide totals the SBE's
files add up to, that every office is classified, and that contest slugs are unique.
"""

from __future__ import annotations

import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.web import results
from skipjack.web.db import DB_PATH


@pytest.fixture
def conn(monkeypatch):
    # the fixtures have two counties; a statewide office must be on every county's ballot
    monkeypatch.setattr(results, "N_COUNTIES", 2)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def add(conn, election, county, party_file, office, district, candidates, source_id=None):
    """Rows for one contest in one county. A candidate is (name, party, winner, votes) with
    votes a {vote type stem: n} dict, optionally followed by an `against` count and write_in."""
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
                write_in, sum(votes.values()), against,
            ),
        )  # fmt: skip
        for stem, n in votes.items():
            conn.execute(
                "INSERT INTO results_votes (result_id, vote_type, votes, votes_against)"
                " VALUES (?, ?, ?, NULL)",
                (cur.lastrowid, stem, n),
            )


def gov(conn, county, moore, felber, winner=True):
    add(
        conn, "GP26", county, "dem", "Governor / Lt. Governor", "",
        [
            ("Wes Moore", "DEM", winner, {"early": moore // 2, "election_night": moore - moore // 2}),
            ("Eric Felber", "DEM", False, {"early": felber, "election_night": 0}),
        ],
    )  # fmt: skip


@pytest.fixture
def primary(conn):
    """GP26: Governor (D and R) in Allegany and Garrett, a sheriff (local) in Garrett only,
    State Senate districts 2 and 10, in SBE order within each county file."""
    gov(conn, "Allegany", 100, 30)
    add(conn, "GP26", "Allegany", "rep", "Governor / Lt. Governor", "",
        [("Dan Cox", "REP", True, {"early": 40}), ("Kelly Schulz", "REP", False, {"early": 60})])  # fmt: skip
    add(conn, "GP26", "Allegany", "dem", "State Senator", "10",
        [("Sen Ten", "DEM", True, {"early": 5})])  # fmt: skip
    add(conn, "GP26", "Allegany", "dem", "State Senator", "2",
        [("Sen Two", "DEM", True, {"early": 7})])  # fmt: skip
    gov(conn, "Garrett", 20, 60)  # Felber wins Garrett, though the SBE flags Moore statewide
    add(conn, "GP26", "Garrett", "rep", "Governor / Lt. Governor", "",
        [("Dan Cox", "REP", True, {"early": 10}), ("Kelly Schulz", "REP", False, {"early": 5})])  # fmt: skip
    add(conn, "GP26", "Garrett", "dem", "Sheriff", "", [("Sam Sheriff", "DEM", True, {"early": 9})])
    add(conn, "GP26", "Garrett", "non", "Board of Education", "1",
        [("Bea Board", "NON", True, {"early": 3})])  # fmt: skip
    add(conn, "GP26", "Garrett", "dem", "State Senator", "2",
        [("Sen Two", "DEM", True, {"early": 4})])  # fmt: skip
    return conn


@pytest.fixture
def general(conn):
    add(conn, "PG24", "Allegany", "all", "President - Vice Pres", "",
        [("Trump", "REP", False, {"early": 70, "mail_in_1": 10}),
         ("Harris", "DEM", True, {"early": 30, "mail_in_1": 5}),
         ("Write-ins", "", False, {"early": 1}, None, 1)])  # fmt: skip
    add(conn, "PG24", "Garrett", "all", "President - Vice Pres", "",
        [("Trump", "REP", False, {"early": 50}), ("Harris", "DEM", True, {"early": 10})])  # fmt: skip
    # at-large retention: totals for and against
    for county in ("Allegany", "Garrett"):
        add(conn, "PG24", county, "all", "Appeals Judge MD at Lrg", "",
            [("Judge J", "JUD", True, {"early": 80}, 20)])  # fmt: skip
    add(conn, "PG24", "Allegany", "all", "U.S. Congress", "10", [("A", "DEM", True, {"early": 1})])
    add(conn, "PG24", "Allegany", "all", "U.S. Congress", "2", [("B", "REP", True, {"early": 1})])
    return conn


# --- elections ---------------------------------------------------------------------------


def test_elections_newest_first_general_before_primary(conn):
    for code in ("PP20", "PP24", "GP26", "PG24", "PG20", "GP22"):
        add(conn, code, "Allegany", "all", "Sheriff", "", [("X", "DEM", True, {"early": 1})])
    got = results.elections(conn)
    assert [e["code"] for e in got] == ["GP26", "PG24", "PP24", "GP22", "PG20", "PP20"]
    gp26 = got[0]
    assert gp26["slug"] == "gp26" and gp26["year"] == 2026 and gp26["kind"] == "primary"
    assert gp26["election_day"] == "2026-06-23" and gp26["published"] is True
    assert "primary" in gp26["name"]


def test_elections_empty_and_unknown_codes_skipped(conn):
    assert results.elections(conn) == []
    add(conn, "ZZ99", "Allegany", "all", "Sheriff", "", [("X", "DEM", True, {"early": 1})])
    assert results.elections(conn) == []


# --- scope and slugs -------------------------------------------------------------------


def test_contest_slug():
    assert results.contest_slug("Governor / Lt. Governor", "", "dem") == "governor-lt-governor-dem"
    assert results.contest_slug("U.S. Congress", "5", "all") == "u-s-congress-5-all"


@pytest.mark.parametrize(
    "office,n,scope",
    [
        ("Governor / Lt. Governor", 24, "statewide"),
        ("Comptroller", 24, "statewide"),
        ("Comptroller", 1, "local"),  # a city comptroller in a presidential year
        ("U.S. Senator", 24, "statewide"),
        ("Representative in Congress", 3, "congress"),
        ("State Senator", 3, "senate"),
        ("House of Delegates", 3, "delegates"),
        ("Sheriff", 1, "local"),
        ("Female Delegates", 24, "local"),  # convention delegates
        ("Something New", 24, "local"),
    ],
)
def test_scope(office, n, scope):
    assert results._scope(office, n) == scope


def test_every_office_is_classified_once():
    lists = [
        results.STATEWIDE_OFFICES,
        results.CONGRESS_OFFICES,
        results.SENATE_OFFICES,
        results.DELEGATES_OFFICES,
        results.LOCAL_OFFICES,
    ]
    everything = [o for offices in lists for o in offices]
    assert len(everything) == len(set(everything))


# --- hub ---------------------------------------------------------------------------------


def test_primary_hub_groups_by_scope_with_summed_totals(primary):
    hub = results.hub(primary, "GP26")
    assert hub["election"]["code"] == "GP26"
    assert [g["scope"] for g in hub["groups"]] == ["statewide", "senate"]
    statewide = hub["groups"][0]
    assert [c["slug"] for c in statewide["contests"]] == [
        "governor-lt-governor-dem",
        "governor-lt-governor-rep",
    ]
    dem = statewide["contests"][0]
    assert dem["title"] == "Governor / Lt. Governor (Democratic)"
    assert dem["party_label"] == "Democratic" and dem["counties_reporting"] == 2
    assert [(c["name"], c["total"]) for c in dem["candidates"]] == [
        ("Wes Moore", 120),
        ("Eric Felber", 90),
    ]
    assert [c["winner"] for c in dem["candidates"]] == [True, False]  # the SBE's flag
    assert dem["candidates"][0]["share"] == pytest.approx(100 * 120 / 210)
    rep = statewide["contests"][1]
    assert [c["name"] for c in rep["candidates"]] == ["Kelly Schulz", "Dan Cox"]
    assert [c["total"] for c in rep["candidates"]] == [65, 50] and rep["counties_reporting"] == 2


def test_hub_leaves_out_local_contests_and_orders_districts_numerically(primary):
    hub = results.hub(primary, "GP26")
    titles = [c["title"] for g in hub["groups"] for c in g["contests"]]
    assert not any("Sheriff" in t or "Board" in t for t in titles)
    senate = hub["groups"][1]["contests"]
    assert [c["district"] for c in senate] == ["2", "10"]
    assert senate[0]["title"] == "State Senator, District 2 (Democratic)"
    assert senate[0]["candidates"][0]["total"] == 11 and senate[0]["counties_reporting"] == 2


def test_general_hub_spans_parties_and_drops_demoted_statewide_offices(general):
    add(general, "PG24", "Allegany", "all", "Comptroller", "", [("C", "DEM", True, {"early": 1})])
    hub = results.hub(general, "PG24")
    assert [g["scope"] for g in hub["groups"]] == ["statewide", "congress"]
    statewide = hub["groups"][0]["contests"]
    assert [c["slug"] for c in statewide] == [
        "president-vice-pres-all",
        "appeals-judge-md-at-lrg-all",
    ]  # Comptroller was on one county's ballot only
    president = statewide[0]
    assert president["title"] == "President / Vice President"
    assert [(c["name"], c["party"]) for c in president["candidates"]] == [
        ("Trump", "REP"),
        ("Harris", "DEM"),
        ("Write-ins", ""),
    ]
    assert president["candidates"][2]["write_in"] is True
    assert [c["district"] for c in hub["groups"][1]["contests"]] == ["2", "10"]
    assert statewide[1]["has_against"] and not president["has_against"]


def test_statewide_office_not_on_every_county_ballot_is_local(general, monkeypatch):
    monkeypatch.setattr(results, "N_COUNTIES", 24)
    assert results.hub(general, "PG24")["groups"][0]["scope"] == "congress"
    assert results.contest(general, "PG24", "president-vice-pres-all") is None


def test_hub_unknown_or_empty_election(conn):
    assert results.hub(conn, "GP26") is None
    assert results.hub(conn, "XX00") is None


# --- contest -----------------------------------------------------------------------------


def test_contest_candidates_vote_types_and_county_table(primary):
    c = results.contest(primary, "GP26", "governor-lt-governor-dem")
    assert c["title"] == "Governor / Lt. Governor (Democratic)" and c["scope"] == "statewide"
    assert c["total"] == 210 and c["counties_reporting"] == 2 and not c["has_against"]
    assert [v["label"] for v in c["vote_types"]] == [
        "Early", "Election Night", "Mail-In Ballot 1", "Provisional", "Mail-In Ballot 2",
    ]  # fmt: skip
    moore = c["candidates"][0]
    assert moore["votes"]["early"] == 50 + 10 and moore["votes"]["election_night"] == 50 + 10
    assert moore["votes"]["provisional"] == 0
    assert [r["county"] for r in c["county_rows"]] == ["Allegany", "Garrett"]
    allegany, garrett = c["county_rows"]
    assert allegany["cells"] == [100, 30] and allegany["total"] == 130
    assert allegany["slug"] == "allegany" and allegany["leader"] == 0
    # the county's winner is whoever led there, whatever the SBE's flags say
    assert garrett["cells"] == [20, 60] and garrett["leader"] == 1
    assert "against" not in allegany
    assert c["source_ids"] == [
        "sbe-results-GP26-Allegany-dem",
        "sbe-results-GP26-Garrett-dem",
    ]


def test_contest_partial_county_has_none_cell(primary):
    add(primary, "GP26", "Garrett", "dem", "State Senator", "2",
        [("Other", "DEM", False, {"early": 1})])  # fmt: skip
    c = results.contest(primary, "GP26", "state-senator-2-dem")
    names = [x["name"] for x in c["candidates"]]
    allegany = c["county_rows"][0]
    assert allegany["cells"][names.index("Other")] is None and allegany["total"] == 7


def test_contest_none_for_unknown_and_local(primary):
    assert results.contest(primary, "GP26", "nope") is None
    assert results.contest(primary, "GP26", "sheriff-dem") is None
    assert results.contest(primary, "XX00", "governor-lt-governor-dem") is None
    assert results.contest(primary, "PG24", "governor-lt-governor-dem") is None


def test_contest_against_columns_only_when_present(general):
    c = results.contest(general, "PG24", "appeals-judge-md-at-lrg-all")
    assert c["has_against"] is True
    judge = c["candidates"][0]
    assert judge["total"] == 160 and judge["against"] == 40
    assert judge["share"] == pytest.approx(80.0)  # for / (for + against)
    assert c["county_rows"][0]["against"] == [20] and c["county_rows"][0]["leader"] is None
    plain = results.contest(general, "PG24", "president-vice-pres-all")
    assert all("against" not in r for r in plain["county_rows"])
    assert plain["candidates"][0]["against"] is None


# --- the presidential primary double count -----------------------------------------------


def test_split_by_district_rows_are_not_added_to_the_county_total(conn):
    """PP24 / PP20 President: each county once with a blank district (its total) and again
    split by congressional district. Only the total counts, in every query."""
    for county, total, split in (
        ("Allegany", 100, [(6, 100)]),
        ("Baltimore County", 300, [(2, 200), (7, 100)]),
    ):
        add(
            conn,
            "PP24",
            county,
            "dem",
            "President - Vice Pres",
            "",
            [("Biden", "DEM", True, {"early": total})],
        )
        for district, n in split:
            add(conn, "PP24", county, "dem", "President - Vice Pres", str(district),
                [("Biden", "DEM", True, {"early": n})])  # fmt: skip
    # a district-only contest in the same election is untouched
    add(conn, "PP24", "Allegany", "dem", "U.S. Congress", "6", [("Rep", "DEM", True, {"early": 7})])
    c = results.contest(conn, "PP24", "president-vice-pres-dem")
    assert c["candidates"][0]["total"] == 400 and c["candidates"][0]["votes"]["early"] == 400
    assert [r["cells"] for r in c["county_rows"]] == [[100], [300]]
    assert results.hub(conn, "PP24")["groups"][0]["contests"][0]["candidates"][0]["total"] == 400
    assert results.contest(conn, "PP24", "u-s-congress-6-dem")["candidates"][0]["total"] == 7
    page = results.county_page(conn, "PP24", "baltimore-county")
    assert [
        (x["title"], x["candidates"][0]["total"]) for g in page["groups"] for x in g["contests"]
    ] == [("President / Vice President (Democratic)", 300)]
    assert results.contest_slugs(conn, "PP24") == ["president-vice-pres-dem", "u-s-congress-6-dem"]


# --- counties ----------------------------------------------------------------------------


def test_county_list(primary):
    got = results.county_list(primary, "GP26")
    assert got["election"]["code"] == "GP26"
    assert [(c["county"], c["slug"], c["contests"], c["party_files"]) for c in got["counties"]] == [
        ("Allegany", "allegany", 4, ["dem", "rep"]),
        ("Garrett", "garrett", 5, ["dem", "rep", "non"]),
    ]
    assert results.county_list(primary, "PG24") is None
    assert results.county_slugs(primary, "GP26") == ["allegany", "garrett"]


def test_county_page_primary_groups_by_party_in_sbe_order(primary):
    page = results.county_page(primary, "GP26", "garrett")
    assert page["county"] == "Garrett" and page["slug"] == "garrett"
    assert [(g["party_file"], g["label"]) for g in page["groups"]] == [
        ("dem", "Democratic"),
        ("rep", "Republican"),
        ("non", "Nonpartisan"),
    ]
    dem = page["groups"][0]["contests"]
    assert [c["title"] for c in dem] == [
        "Governor / Lt. Governor (Democratic)",
        "Sheriff (Democratic)",
        "State Senator, District 2 (Democratic)",
    ]
    assert [c["page_slug"] for c in dem] == [
        "governor-lt-governor-dem",
        None,
        "state-senator-2-dem",
    ]
    assert [x["name"] for x in dem[0]["candidates"]] == ["Eric Felber", "Wes Moore"]
    assert dem[0]["candidates"][1]["votes"]["early"] == 10
    assert page["vote_types"][2]["label"] == "Mail-In Ballot 1"
    assert page["groups"][2]["contests"][0]["page_slug"] is None
    assert page["source_ids"] == [
        "sbe-results-GP26-Garrett-dem",
        "sbe-results-GP26-Garrett-non",
        "sbe-results-GP26-Garrett-rep",
    ]


def test_county_page_general_is_one_list_and_marks_against(general):
    page = results.county_page(general, "PG24", "allegany")
    assert [g["party_file"] for g in page["groups"]] == ["all"]
    contests = page["groups"][0]["contests"]
    assert [c["page_slug"] for c in contests][:2] == [
        "president-vice-pres-all",
        "appeals-judge-md-at-lrg-all",
    ]
    assert [c["has_against"] for c in contests][:2] == [False, True]
    assert contests[1]["candidates"][0]["against"] == 20


def test_county_page_none_cases(primary):
    assert results.county_page(primary, "GP26", "nowhere") is None
    assert results.county_page(primary, "GP26", "worcester") is None  # no rows
    assert results.county_page(primary, "PG24", "allegany") is None
    assert results.county_page(primary, "XX00", "allegany") is None


def test_slug_lists_match_the_pages(primary, general):
    for c, code in ((primary, "GP26"), (general, "PG24")):
        slugs = results.contest_slugs(c, code)
        assert slugs == [x["slug"] for g in results.hub(c, code)["groups"] for x in g["contests"]]
        assert all(results.contest(c, code, s) for s in slugs)


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


@pytest.mark.parametrize(
    "code,slug,name,total",
    [
        ("PP20", "president-vice-pres-dem", "Joe Biden", 879_753),
        ("PG20", "president-vice-pres-all", "Joe Biden", 1_985_023),
        ("GP26", "governor-lt-governor-dem", "Wes Moore and Aruna Miller", 558_768),
        ("PG24", "president-vice-pres-all", "Kamala D. Harris and Tim Walz", 1_902_577),
    ],
)
def test_real_statewide_totals(real, code, slug, name, total):
    c = results.contest(real, code, slug)
    top = c["candidates"][0]
    assert (top["name"], top["total"], top["winner"]) == (name, total, True)
    assert c["counties_reporting"] == 24 and len(c["county_rows"]) == 24
    assert sum(r["cells"][0] for r in c["county_rows"]) == total
    assert sum(top["votes"].values()) == total  # the vote types add up to the total


def test_real_every_office_is_classified(real):
    known = set(results._OFFICE_SCOPE)
    offices = {r[0] for r in real.execute("SELECT DISTINCT office FROM results_county")}
    assert offices <= known, f"unclassified offices: {sorted(offices - known)}"


def test_real_statewide_contests_are_on_every_counties_ballot(real):
    for e in results.elections(real):
        for c in results._contest_index(real, e["code"]):
            if c["scope"] == "statewide":
                assert c["counties"] == 24, (e["code"], c["title"])
        got = results.hub(real, e["code"])
        for group in got["groups"]:
            if group["scope"] == "statewide":
                assert all(c["counties_reporting"] == 24 for c in group["contests"])
        # a statewide office that is on fewer ballots was demoted, not dropped silently
    gp22 = {c["title"] for g in results.hub(real, "GP22")["groups"][:1] for c in g["contests"]}
    assert "Comptroller (Democratic)" in gp22
    pg24 = {c["title"] for g in results.hub(real, "PG24")["groups"][:1] for c in g["contests"]}
    assert "Comptroller" not in pg24 and "President / Vice President" in pg24


def test_real_contest_slugs_are_unique_per_election(real):
    for e in results.elections(real):
        pages = results.contest_slugs(real, e["code"])
        assert len(pages) == len(set(pages)), e["code"]
        # local contests are not routed; they only share anchors when two counties spell an
        # office in different cases (DEM Cent Committee / Dem Cent Committee), never in one county
        for county in results.county_slugs(real, e["code"]):
            page = results.county_page(real, e["code"], county)
            slugs = [(c["slug"]) for g in page["groups"] for c in g["contests"]]
            assert len(slugs) == len(set(slugs)), (e["code"], county)


def test_real_every_page_and_county_page_builds(real):
    for e in results.elections(real):
        code = e["code"]
        for slug in results.contest_slugs(real, code):
            assert results.contest(real, code, slug) is not None
        assert len(results.county_slugs(real, code)) == 24
        sample = results.county_page(real, code, "montgomery")
        assert sample and sample["groups"]
        for g in sample["groups"]:
            assert all(c["candidates"] for c in g["contests"])


def test_real_hub_and_county_page_shape(real):
    hub = results.hub(real, "PG24")
    assert [g["scope"] for g in hub["groups"]] == ["statewide", "congress"]
    assert len(hub["groups"][1]["contests"]) == 8
    page = results.county_page(real, "GP26", "montgomery")
    assert [g["party_file"] for g in page["groups"]] == ["dem", "rep", "non"]
    caroline = results.county_page(real, "GP26", "caroline")
    assert [g["party_file"] for g in caroline["groups"]] == ["dem", "rep"]
    assert [e["code"] for e in results.elections(real)][:2] == ["GP26", "PG24"]


def test_local_offices_mixing_blank_and_district_rows_are_shown_as_published(conn):
    """A blank at-large row and numbered districts are different contests, never summed."""
    add(
        conn,
        "GG22",
        "Howard",
        "all",
        "Board of Education",
        "",
        [("At Large", "NON", True, {"early": 5})],
    )
    add(
        conn,
        "GG22",
        "Howard",
        "all",
        "Board of Education",
        "1",
        [("One", "NON", True, {"early": 7})],
    )
    page = results.county_page(conn, "GG22", "howard")
    got = [(c["district"], c["candidates"][0]["total"]) for c in page["groups"][0]["contests"]]
    assert got == [("", 5), ("1", 7)]


# --- leaders, ties, parties and the county page's winner semantics -----------------------


def test_county_with_no_votes_has_no_leader(general):
    add(general, "PG24", "Allegany", "all", "U.S. Congress", "8",
        [("A", "REP", True, {"early": 0}), ("B", "REP", False, {"early": 0})])  # fmt: skip
    add(general, "PG24", "Garrett", "all", "U.S. Congress", "8",
        [("A", "REP", True, {"early": 4}), ("B", "REP", False, {"early": 2})])  # fmt: skip
    rows = {
        r["county"]: r
        for r in results.contest(general, "PG24", "u-s-congress-8-all")["county_rows"]
    }
    assert rows["Allegany"]["total"] == 0 and rows["Allegany"]["leader"] is None
    assert rows["Allegany"]["tied"] == []
    assert rows["Garrett"]["leader"] == 0


def test_county_leaders_level_on_votes_are_tied_not_led(primary):
    add(primary, "GP26", "Allegany", "dem", "U.S. Senator", "",
        [("A", "DEM", True, {"early": 5}), ("B", "DEM", False, {"early": 5}),
         ("C", "DEM", False, {"early": 1})])  # fmt: skip
    add(primary, "GP26", "Garrett", "dem", "U.S. Senator", "",
        [("A", "DEM", True, {"early": 5}), ("B", "DEM", False, {"early": 2}),
         ("C", "DEM", False, {"early": 1})])  # fmt: skip
    c = results.contest(primary, "GP26", "u-s-senator-dem")
    allegany, garrett = c["county_rows"]
    assert allegany["leader"] is None and allegany["tied"] == [0, 1]
    assert garrett["leader"] == 0 and garrett["tied"] == []
    page = results.county_page(primary, "GP26", "allegany")
    sen = next(x for x in page["groups"][0]["contests"] if x["office"] == "U.S. Senator")
    assert sen["leader"] is None and sen["tied"] == [0, 1]


def test_same_name_under_two_parties_is_two_candidates(general):
    for county, d, r in (("Allegany", 3, 4), ("Garrett", 5, 6)):
        add(general, "PG24", county, "all", "U.S. Congress", "1",
            [("Pat Lee", "DEM", False, {"early": d}), ("Pat Lee", "REP", True, {"early": r})])  # fmt: skip
    c = results.contest(general, "PG24", "u-s-congress-1-all")
    assert [(x["name"], x["party"], x["total"]) for x in c["candidates"]] == [
        ("Pat Lee", "REP", 10), ("Pat Lee", "DEM", 8),
    ]  # fmt: skip
    assert [r["cells"] for r in c["county_rows"]] == [[4, 3], [6, 5]]
    page = results.county_page(general, "PG24", "allegany")
    cong = next(x for x in page["groups"][0]["contests"] if x["district"] == "1")
    assert [(x["party"], x["total"]) for x in cong["candidates"]] == [("REP", 4), ("DEM", 3)]


def test_county_page_flag_is_contest_wide_and_leader_is_local(primary):
    # Garrett: Felber leads (60 to 20) though the SBE flags Moore, the statewide winner
    page = results.county_page(primary, "GP26", "garrett")
    gov = next(x for x in page["groups"][0]["contests"] if x["office"] == "Governor / Lt. Governor")
    assert gov["scope"] == "statewide" and gov["page_slug"] == "governor-lt-governor-dem"
    assert [c["name"] for c in gov["candidates"]] == ["Eric Felber", "Wes Moore"]
    assert gov["leader"] == 0
    assert [c["winner"] for c in gov["candidates"]] == [False, True]
    sheriff = next(x for x in page["groups"][0]["contests"] if x["office"] == "Sheriff")
    assert sheriff["scope"] == "local" and sheriff["page_slug"] is None


def test_county_page_anchors_are_unique_where_local_slugs_collide(conn):
    for office in ("DEM Cent Comm at Large", "Dem Cent Comm at Large"):
        add(conn, "GP26", "Allegany", "dem", office, "", [("X", "DEM", True, {"early": 1})])
    page = results.county_page(conn, "GP26", "allegany")
    anchors = [c["anchor"] for c in page["groups"][0]["contests"]]
    assert len(set(anchors)) == 2 and anchors[0] == "dem-cent-comm-at-large-dem"
