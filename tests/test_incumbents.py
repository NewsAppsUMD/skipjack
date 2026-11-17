"""Tests for the 2026 incumbents (skipjack.web.incumbents) and the roster loader.

Made-up roster rows and GP26 results in an in-memory database, two counties; the db-backed test
at the end checks the real roster against the real GP26 results.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline import officials
from skipjack.pipeline.provenance import compute_sha256
from skipjack.web import incumbents as inc
from skipjack.web import results
from skipjack.web.app import create_app
from skipjack.web.db import DB_PATH
from skipjack.web.routes import results as results_route


def add(conn, election, county, party_file, office, district, candidates):
    """One contest in one county: candidates are (name, party, winner, votes)."""
    for name, party, winner, votes in candidates:
        cur = conn.execute(
            """INSERT INTO results_county (source_id, election, county, party_file, office,
               district, candidate, party, winner, write_in, total_votes, total_against)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)""",
            (f"sbe-results-{election}-{county}-{party_file}", election, county, party_file,
             office, district, name, party, int(winner), votes),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO results_votes (result_id, vote_type, votes) VALUES (?, 'early', ?)",
            (cur.lastrowid, votes),
        )


def official(conn, name_full, first, last, body, title, jurisdiction, **extra):
    row = dict.fromkeys(officials.COLUMNS, "")
    level = "state" if jurisdiction == "Maryland" else "county"
    row.update(
        person_id=f"person:{last.lower()}", name_full=name_full, name_first=first,
        name_last=last, body_name=body, office_title=title, jurisdiction=jurisdiction,
        jurisdiction_level=level, record_type="office", selection_method="elected",
        term_end_year="2026", is_vacancy="0", party="Republican",
    )  # fmt: skip
    row.update(extra)
    cols = ", ".join(officials.COLUMNS)
    conn.execute(
        f"INSERT INTO officials (source_id, {cols}) VALUES (?{', ?' * len(officials.COLUMNS)})",
        ("maryland-manual-officials-test", *(row[c] for c in officials.COLUMNS)),
    )


def member(conn, full, first, last, district, state="MD", kind="rep", **extra):
    row = dict.fromkeys(officials.CONGRESS_COLUMNS, "")
    row.update(full_name=full, first_name=first, last_name=last, district=district, state=state,
               type=kind, party="Republican", bioguide_id=f"B{last[:4].upper()}")  # fmt: skip
    row.update(extra)
    cols = ", ".join(officials.CONGRESS_COLUMNS)
    conn.execute(
        f"INSERT INTO congress_members (source_id, {cols})"
        f" VALUES (?{', ?' * len(officials.CONGRESS_COLUMNS)})",
        ("congress-legislators-current-test", *(row[c] for c in officials.CONGRESS_COLUMNS)),
    )


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(results, "N_COUNTIES", 2)
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    c.execute(
        "INSERT INTO sources (source_id, source_url, source_name, document_title, fetch_date)"
        " VALUES ('maryland-manual-officials-test', 'https://example.org/officials.csv',"
        " 'Maryland Manual', 'Maryland Manual officeholders (test)', '2026-11-16')"
    )
    # ballots: Allegany and Garrett share House of Delegates District 1A
    add(c, "GP26", "Allegany", "rep", "Governor / Lt. Governor", "",
        [("Dan Cox and Someone Else", "REP", True, 50)])  # fmt: skip
    add(c, "GP26", "Garrett", "rep", "Governor / Lt. Governor", "",
        [("Dan Cox and Someone Else", "REP", True, 20)])  # fmt: skip
    for county, votes in (("Allegany", (40, 60)), ("Garrett", (10, 5))):
        add(c, "GP26", county, "rep", "House of Delegates", "01A",
            [("Jim Hinebaugh, Jr.", "REP", False, votes[0]),
             ("Pat Challenger", "REP", True, votes[1])])  # fmt: skip
    add(c, "GP26", "Garrett", "dem", "Sheriff", "",
        [("Sam Smith", "DEM", True, 9), ("Other Person", "DEM", False, 4)])  # fmt: skip
    add(c, "GP26", "Allegany", "rep", "County Executive", "",
        [('Robert "Bob" Jones', "REP", False, 30), ("Exec Winner", "REP", True, 40)])  # fmt: skip
    add(c, "GP26", "Allegany", "dem", "Clerk Circuit Court", "",
        [("Mary Clerk", "DEM", True, 12)])  # fmt: skip

    official(c, "James C. Hinebaugh, Jr.", "James", "Hinebaugh", "House of Delegates",
             "Delegate", "Maryland", district="1A", counties="Allegany & Garrett Counties",
             term_end_year="2027", name_suffix="Jr.", nickname="Jim")  # fmt: skip
    official(c, "Samuel L. Smith", "Samuel", "Smith", "Sheriff", "Sheriff", "Garrett County",
             note="(appointed March 1, 2025)")  # fmt: skip
    official(c, "Robert Q. Jones", "Robert", "Jones", "County Council", "Member",
             "Allegany County")  # fmt: skip
    # a clerk in Garrett: the Allegany Mary Clerk is someone else
    official(c, "Mary A. Clerk", "Mary", "Clerk", "Clerk", "Clerk", "Garrett County")
    # not up in 2026
    official(c, "Late Term", "Late", "Term", "Sheriff", "Sheriff", "Allegany County",
             term_end_year="2028")  # fmt: skip
    yield c
    c.close()


# --- names ----------------------------------------------------------------------------------


def test_ballot_names():
    assert inc.ballot_name('Theodore "Ted" J. Elder') == (
        {"theodore", "ted", "j"}, "elder", ["theodore", "j", "elder"],
    )  # fmt: skip
    given, last, _ = inc.ballot_name("Wes Moore and Aruna Miller")
    assert (given, last) == ({"wes"}, "moore")
    assert inc.ballot_name("Frank M. Conaway, Jr.")[1] == "conaway"
    assert inc.ballot_name("Reuben B. Collins, II")[1] == "collins"


def test_roster_names():
    row = dict.fromkeys(officials.COLUMNS, "")
    row.update(name_first="H.", name_middle="Kevin", name_last="Anderson")
    assert inc.roster_name(row) == ({"h", "kevin"}, {"anderson"})
    row.update(name_first="Jessica", name_middle="", name_last="Boyle-Tsottles", nickname="")
    assert inc.roster_name(row)[1] == {"boyle-tsottles", "tsottles"}


@pytest.mark.parametrize(
    ("a", "b", "ok"),
    [("sam", "samuel", True), ("bill", "william", True), ("al", "alison", False),
     ("chris", "christopher", True), ("john", "james", False)],
)  # fmt: skip
def test_compatible_given_names(a, b, ok):
    assert inc._compatible(a, b) is ok


def test_legislative_district():
    assert inc._legislative_district("1A") == "01A"
    assert inc._legislative_district("10") == "10"


def test_roster_family_takes_elected_offices_only():
    row = dict.fromkeys(officials.COLUMNS, "")
    row.update(record_type="office", is_vacancy="0", name_last="X", jurisdiction_level="county")
    assert (
        inc.roster_family({**row, "body_name": "Sheriff", "office_title": "Sheriff"}) == "sheriff"
    )
    assert inc.roster_family({**row, "body_name": "Sheriff", "office_title": "Chief Deputy Sheriff"}) is None  # fmt: skip
    assert inc.roster_family({**row, "body_name": "Board of License Commissioners",
                              "office_title": "Member"}) is None  # fmt: skip
    assert inc.roster_family({**row, "body_name": "Orphans’ Court", "office_title": "Judge"}) == "orphans_court"  # fmt: skip
    assert inc.roster_family({**row, "body_name": "Sheriff", "office_title": "Sheriff",
                              "is_vacancy": "1"}) is None  # fmt: skip


# --- the summary ----------------------------------------------------------------------------


def test_summary_of_a_primary(conn):
    s = inc.summary(conn, "GP26")
    assert s["up"] == 4  # the 2028 term is not up
    assert [(e["name"], e["candidate"], e["place"]) for e in s["lost"]] == [
        ("James C. Hinebaugh, Jr.", "Jim Hinebaugh, Jr.", "District 1A"),
    ]
    lost = s["lost"][0]
    assert (lost["total"], lost["url"]) == (50, "/results/gp26/house-of-delegates-01a-rep/")
    # "Sam" on the ballot is the roster's Samuel: the looser match, in his own contest
    assert [(e["name"], e["candidate"], e["appointed"]) for e in s["held"]] == [
        ("Samuel L. Smith", "Sam Smith", True),
    ]
    # a council member who ran for county executive
    assert [(e["name"], e["ran_for"], e["won"]) for e in s["other"]] == [
        ("Robert Q. Jones", "County executive", False),
    ]
    # Garrett's clerk is not Allegany's candidate of the same name
    assert s["absent"] == 1
    families = {f["family"]: (f["up"], f["ran"], f["lost"]) for f in s["families"]}
    assert families == {
        "delegate": (1, 1, 1), "county_council": (1, 0, 0), "sheriff": (1, 1, 0),
        "clerk": (1, 0, 0),
    }  # fmt: skip


def test_house_members(conn):
    for county in ("Allegany", "Garrett"):
        add(conn, "GP26", county, "rep", "U.S. Congress", "6",
            [("Pat Q. Member", "REP", False, 10), ("Upstart Winner", "REP", True, 30)])  # fmt: skip
    # a namesake of the retiring member in a county contest is not him
    add(conn, "GP26", "Allegany", "rep", "Judge Orphans' Court", "",
        [("Lee Retiring", "REP", True, 9)])  # fmt: skip
    member(conn, "Patrick Q. Member", "Patrick", "Member", "6", nickname="Pat")
    member(conn, "Lee Retiring", "Lee", "Retiring", "1")
    member(conn, "Out Of State", "Out", "State", "6", state="PA")
    member(conn, "Some Senator", "Some", "Senator", "", kind="sen")
    s = inc.summary(conn, "GP26")
    families = {f["family"]: (f["up"], f["ran"], f["lost"]) for f in s["families"]}
    assert families["congress"] == (2, 1, 1)
    lost = next(e for e in s["lost"] if e["office"] == "U.S. House")
    assert (lost["name"], lost["candidate"], lost["place"]) == (
        "Patrick Q. Member", "Pat Q. Member", "District 6",
    )  # fmt: skip
    assert s["lost"][0] is lost  # the House sorts before the legislature
    assert all(e["name"] != "Lee Retiring" for e in s["other"])
    contest = results.contest(conn, "GP26", "u-s-congress-6-rep")
    assert inc.mark(conn, "GP26", [contest]) is True
    assert [c["name"] for c in contest["candidates"] if c["incumbent"]] == ["Pat Q. Member"]


def test_congress_district():
    assert [inc._congress_district(d) for d in ("District 4", "4", "04")] == ["4", "4", "4"]


def test_overrides_drop_a_match(conn):
    conn.execute(
        "INSERT INTO official_overrides VALUES ('person:jones', 'GP26', 'Robert \"Bob\" Jones',"
        " 'unconfirmed', 'test')"
    )
    s = inc.summary(conn, "GP26")
    assert s["other"] == []
    assert s["absent"] == 2


def test_only_the_2026_elections(conn):
    assert inc.summary(conn, "GP22") is None


def test_no_roster_no_section(conn):
    conn.execute("DELETE FROM officials")
    assert inc.summary(conn, "GP26") is None


def test_election_page_shows_the_section(conn, monkeypatch):
    @contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(results_route, "get_db", fake_get_db)
    page = TestClient(create_app()).get("/results/gp26/")
    assert page.status_code == 200
    assert 'id="incumbents"' in page.text
    assert "<strong>1 lost</strong>" in page.text
    assert "Jim Hinebaugh, Jr." in page.text
    assert "(appointed to the seat)" not in page.text  # the appointee won


def test_mark_flags_incumbents_in_their_own_contests(conn):
    contest = results.contest(conn, "GP26", "house-of-delegates-01a-rep")
    assert inc.mark(conn, "GP26", [contest]) is True
    assert {c["name"]: c["incumbent"] for c in contest["candidates"]} == {
        "Jim Hinebaugh, Jr.": True,
        "Pat Challenger": False,
    }
    page = results.county_page(conn, "GP26", "allegany")
    contests = [c for g in page["groups"] for c in g["contests"]]
    inc.mark(conn, "GP26", contests, "Allegany")
    marked = {(c["office"], x["name"]) for c in contests for x in c["candidates"] if x["incumbent"]}
    # the delegate (a district contest) is marked; the council member running for county
    # executive is not that office's incumbent
    assert marked == {("House of Delegates", "Jim Hinebaugh, Jr.")}
    garrett = results.county_page(conn, "GP26", "garrett")
    contests = [c for g in garrett["groups"] for c in g["contests"]]
    inc.mark(conn, "GP26", contests, "Garrett")
    assert {x["name"] for c in contests for x in c["candidates"] if x["incumbent"]} == {
        "Jim Hinebaugh, Jr.",
        "Sam Smith",
    }


def test_pages_show_the_marker(conn, monkeypatch):
    @contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(results_route, "get_db", fake_get_db)
    client = TestClient(create_app())
    marker = 'title="incumbent">(i)</span>'
    for url in (
        "/results/gp26/house-of-delegates-01a-rep/",
        "/results/gp26/county/garrett/",
    ):
        page = client.get(url)
        assert page.status_code == 200, url
        assert marker in page.text, url
        assert "marks an incumbent running for their own office" in page.text, url
    # no incumbent in the Republican governor's race: no marks, no legend; nor on the election
    # page, whose district list shows only the delegate race's winner, Pat Challenger
    for url in ("/results/gp26/governor-lt-governor-rep/", "/results/gp26/"):
        page = client.get(url)
        assert marker not in page.text, url
        assert "marks an incumbent" not in page.text, url


# --- the roster loader ----------------------------------------------------------------------


def _write_roster(tmp_path, rows, overrides=None):
    folder = tmp_path / "officials"
    folder.mkdir()
    roster = folder / officials.ROSTER_NAME
    with roster.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=officials.COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**dict.fromkeys(officials.COLUMNS, ""), **r})
    sidecar = {
        "source_id": "maryland-manual-officials-test", "source_url": "https://example.org",
        "source_name": "Maryland Manual", "document_title": "test", "fetch_date": "2026-11-16",
        "sha256_original": compute_sha256(roster),
    }  # fmt: skip
    roster.with_name(roster.stem + "_provenance.json").write_text(json.dumps(sidecar))
    if overrides is not None:
        (folder / officials.OVERRIDES_NAME).write_text(overrides)
    return roster


def test_load_officials(tmp_path):
    _write_roster(
        tmp_path, [{"id": "a", "name_full": "A B"}],
        "person_id,election,candidate,decision,note\np,GP26,A B,not_a_match,checked\n",
    )  # fmt: skip
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    assert officials.load_officials(c, tmp_path) == 1
    assert c.execute("SELECT name_full, source_id FROM officials").fetchall() == [
        ("A B", "maryland-manual-officials-test"),
    ]
    assert c.execute("SELECT decision FROM official_overrides").fetchall() == [("not_a_match",)]


def test_load_congress_members(tmp_path):
    _write_roster(tmp_path, [{"id": "a"}])
    path = tmp_path / "officials" / officials.CONGRESS_NAME
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=officials.CONGRESS_COLUMNS)
        w.writeheader()
        w.writerow({**dict.fromkeys(officials.CONGRESS_COLUMNS, ""), "full_name": "A B"})
    sidecar = {
        "source_id": "congress-legislators-current-test", "source_url": "https://example.org",
        "source_name": "congress-legislators", "document_title": "test",
        "fetch_date": "2026-11-16", "sha256_original": compute_sha256(path),
    }  # fmt: skip
    path.with_name(path.stem + "_provenance.json").write_text(json.dumps(sidecar))
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    officials.load_officials(c, tmp_path)
    assert c.execute("SELECT full_name, source_id FROM congress_members").fetchall() == [
        ("A B", "congress-legislators-current-test"),
    ]
    path.write_text(path.read_text() + "x\n")
    c.execute("DELETE FROM congress_members")
    with pytest.raises(officials.OfficialsLoadError, match="sha256"):
        officials.load_officials(c, tmp_path)


def test_load_officials_checks_the_file(tmp_path):
    roster = _write_roster(tmp_path, [{"id": "a"}])
    roster.write_text(roster.read_text() + "b" + "," * (len(officials.COLUMNS) - 1) + "\n")
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    with pytest.raises(officials.OfficialsLoadError, match="sha256"):
        officials.load_officials(c, tmp_path)


def test_load_officials_rejects_an_unknown_decision(tmp_path):
    _write_roster(
        tmp_path, [{"id": "a"}], "person_id,election,candidate,decision,note\np,GP26,A,maybe,x\n"
    )
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    with pytest.raises(officials.OfficialsLoadError, match="maybe"):
        officials.load_officials(c, tmp_path)


# --- the real data --------------------------------------------------------------------------


def _real_db():
    if not DB_PATH.exists():
        return None
    c = sqlite3.connect(DB_PATH)
    try:
        if not c.execute("SELECT 1 FROM officials LIMIT 1").fetchone():
            return None
    except sqlite3.OperationalError:
        return None
    return c


def test_real_roster_and_primary():
    c = _real_db()
    if c is None:
        pytest.skip("skipjack.db has no officials roster")
    s = inc.summary(c, "GP26")
    up = {f["family"]: f["up"] for f in s["families"]}
    # every legislator and statewide officer is up; one roster row per seat
    assert (up["state_senator"], up["delegate"], up["governor"]) == (47, 141, 1)
    lost = {e["name"] for e in s["lost"]}
    assert {"Nancy J. King", "Dalya Attar", "Scott D. Shellenberger, Esq."} <= lost
    assert "Samuel L. Cogen" in lost  # "Sam Cogen" on the ballot
    # Maryland's eight House members; Hoyer (5th) retired, and the other seven were renominated
    house = next(f for f in s["families"] if f["family"] == "congress")
    assert (house["up"], house["ran"], house["lost"]) == (8, 7, 0)
    held = {e["name"] for e in s["held"] if e["office"] == "U.S. House"}
    assert {"Johnny Olszewski, Jr.", "April McClain Delaney", "Glenn Ivey"} <= held
    # the unconfirmed Robertson match stays out
    assert all(e["name"] != "Craig A. Robertson" for e in s["other"])
    c.close()
