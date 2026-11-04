"""Tests for the GG26 results pages while the count runs: the unofficial queries and pages.

The database is built in memory from a committed snapshot (2026-11-04T160222, refreshed
04:02:22 PM), so the pages are tested on the board's real figures.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import load_results_unofficial
from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.web import results as qr
from skipjack.web import results_unofficial as qu
from skipjack.web.app import create_app
from skipjack.web.routes import results as results_route

SNAP = "2026-11-04T160222"
REPO_DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    shutil.copytree(
        REPO_DATA / "results_unofficial" / "GG26" / SNAP,
        data / "results_unofficial" / "GG26" / SNAP,
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    load_results_unofficial(conn, data)
    yield conn
    conn.close()


@pytest.fixture
def client(db, monkeypatch):
    @contextmanager
    def fake_get_db():
        yield db

    monkeypatch.setattr(results_route, "get_db", fake_get_db)
    return TestClient(create_app())


# --- queries --------------------------------------------------------------------------------


def test_gg26_is_listed_as_unofficial(db):
    found = qu.site_elections(db)
    assert [(e["code"], e.get("unofficial")) for e in found] == [("GG26", True)]
    assert qr.elections(db) == []  # certified results only: the about page counts their files


def test_published_certified_results_end_the_unofficial_listing(db, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    assert qu.site_elections(db) == []
    assert qu.hub(db, "GG26") is None
    assert qu.contest_slugs(db, "GG26") == []
    assert qu.county_slugs(db, "GG26") == []


def test_hub_groups_and_statewide_totals(db):
    hub = qu.hub(db, "GG26")
    assert [(g["scope"], len(g["contests"])) for g in hub["groups"]] == [
        ("statewide", 4), ("congress", 8), ("senate", 47), ("delegates", 71),
    ]  # fmt: skip
    gov = hub["groups"][0]["contests"][0]
    assert gov["title"] == "Governor / Lt. Governor"
    moore = gov["candidates"][0]
    assert (moore["name"], moore["party"], moore["total"]) == (
        "Wes Moore and Aruna Miller", "DEM", 1164949
    )  # fmt: skip
    assert moore["votes"]["provisional"] is None  # not one county has counted provisionals
    assert moore["votes"]["mail_in"] == 147871  # 18 counties' first mail-in count
    assert not any(
        c["winner"] for g in hub["groups"] for x in g["contests"] for c in x["candidates"]
    )


def test_a_judges_continuance_is_one_candidate_with_against(db):
    hub = qu.hub(db, "GG26")
    judges = next(c for c in hub["groups"][0]["contests"] if c["has_against"])
    assert judges["title"] == "Appellate Court judges, at large"
    friedman = judges["candidates"][0]
    assert friedman["name"] == "Dan Friedman"
    assert (friedman["total"], friedman["against"]) == (1305855, 262490)
    assert judges["ahead"] == []


def test_multi_seat_districts_show_as_many_ahead_as_seats(db):
    hub = qu.hub(db, "GG26")
    delegates = {c["district"]: c for c in hub["groups"][3]["contests"]}
    d9a = delegates["District 9A"]
    assert len(d9a["ahead"]) == 2
    assert d9a["ahead"][0]["total"] >= d9a["ahead"][1]["total"]


def test_contest_page_counts_counties_and_marks_no_winner(db):
    c = qu.contest(db, "GG26", "governor-lt-governor-all")
    assert c["counties_reporting"] == 24
    assert [v["label"] for v in c["vote_types"]] == [
        "Early Voting", "Election Day", "Mail-In Ballot", "Provisional"
    ]  # fmt: skip
    allegany = c["county_rows"][0]
    assert allegany["county"] == "Allegany"
    assert allegany["total"] == sum(n for n in allegany["cells"] if n is not None)
    assert qu.contest(db, "GG26", "sheriff-all") is None  # local: county pages only


def test_county_page_keeps_not_counted_apart_from_zero(db):
    caroline = qu.county_page(db, "GG26", "caroline")
    gov = next(c for c in caroline["groups"][0]["contests"] if c["office"].startswith("Governor"))
    assert all(c["votes"]["mail_in"] is None for c in gov["candidates"])
    assert gov["page_slug"] == "governor-lt-governor-all"
    sheriff = next(c for c in caroline["groups"][0]["contests"] if c["office"] == "Sheriff")
    assert sheriff["page_slug"] is None
    assert not any(c["office"].startswith("Question") for c in caroline["groups"][0]["contests"])


def test_unofficial_context(db):
    u = qu.unofficial_context(db, "GG26")
    assert u["as_of"] == "4:02 p.m. on Nov. 4, 2026"
    assert [(c["key"], c["counties"]) for c in u["canvasses"]] == [
        ("EV", 24), ("EN", 24), ("MIB 1", 18), ("Prov", 0), ("MIB 2", 0)
    ]  # fmt: skip
    assert len(u["canvass"]) == 24
    assert u["board_url"].endswith("/2026/General_results/index.html")


@pytest.mark.parametrize(
    ("refreshed", "label"),
    [
        ("11/04/2026 04:02:22 PM", "4:02 p.m. on Nov. 4, 2026"),
        ("11/12/2026 09:05:00 AM", "9:05 a.m. on Nov. 12, 2026"),
        ("06/24/2026 12:30:00 PM", "12:30 p.m. on June 24, 2026"),
    ],
)
def test_as_of_label(refreshed, label):
    assert qu._as_of(refreshed) == label


# --- pages ----------------------------------------------------------------------------------


def test_index_tags_the_election_being_counted(client):
    html = client.get("/results/").text
    assert "unofficial, count in progress" in html


def test_hub_page(client):
    html = client.get("/results/gg26/").text
    assert "Unofficial and incomplete." in html
    assert "4:02 p.m. on Nov. 4, 2026" in html
    assert "provisional, 0 of 24 counties reporting" in html
    assert "Ahead so far, with votes" in html
    assert "&#10003;" not in html  # no winner marks
    assert 'id="canvass"' in html
    assert "unofficial results pages" in html  # the source strip


def test_contest_page(client):
    html = client.get("/results/gg26/governor-lt-governor-all/").text
    assert "Unofficial and incomplete." in html
    assert "not yet counted" in html
    assert "Total so far" in html
    assert "1,164,949" in html
    assert "results-margin" in html  # the D - R map
    assert "The map shows the votes counted as of 4:02 p.m. on Nov. 4, 2026." in html
    assert "&#10003;" not in html


def test_county_page(client):
    html = client.get("/results/gg26/county/caroline/").text
    assert "Caroline has reported" in html
    assert "mail-in, first count not yet reported" in html
    assert "not yet counted" in html
    assert "&#10003;" not in html
    assert "gen_results_2026_by_county_6.html" in html  # the board page behind it


def test_county_index_page(client):
    html = client.get("/results/gg26/county/").text
    assert "Unofficial and incomplete." in html
    assert 'id="canvass"' in html
    assert "/results/gg26/county/st-marys/" in html


def test_unknown_pages_are_404(client):
    assert client.get("/results/gg26/sheriff-all/").status_code == 404
    assert client.get("/results/gg26/county/narnia/").status_code == 404


def test_pages_disappear_once_certified(client, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    assert client.get("/results/gg26/").status_code == 404
    assert client.get("/results/gg26/governor-lt-governor-all/").status_code == 404
    assert "count in progress" not in client.get("/results/").text


# --- ballot questions -----------------------------------------------------------------------


def test_statewide_questions_on_the_hub(db):
    found = qu.hub(db, "GG26")["questions"]
    assert [q["slug"] for q in found] == ["question-1", "question-2", "question-3"]
    q1 = found[0]
    assert q1["title"] == "Question 1: Constitutional Amendment"
    assert q1["detail"].endswith("Arbitration Reform for State Employees Act of 2026")
    assert (q1["for"], q1["against"]) == (1304321, 438763)
    assert [c["label"] for c in q1["choices"]] == [
        "For the Constitutional Amendment", "Against the Constitutional Amendment"
    ]  # fmt: skip
    assert round(q1["share_for"], 1) == 74.8


def test_question_page_data(db):
    q = qu.question(db, "GG26", "question-1")
    assert q["counties_reporting"] == 24
    assert q["choices"][0]["votes"]["provisional"] is None
    caroline = next(r for r in q["county_rows"] if r["county"] == "Caroline")
    # Caroline's question table prints 0 for mail-in; its mail-in count has not reported
    assert caroline["not_counted"] == ["Mail-In Ballot", "Provisional"]
    assert caroline["total"] == caroline["for"] + caroline["against"]
    assert len(q["source_ids"]) == 24
    assert qu.question(db, "GG26", "question-9") is None
    assert "question-1" in qu.contest_slugs(db, "GG26")


def test_county_questions_put_statewide_first_and_keep_their_own(db):
    page = qu.county_page(db, "GG26", "baltimore-county")
    offices = [(q["office"], q["statewide"]) for q in page["questions"]]
    assert offices[:3] == [
        ("Question 1: Constitutional Amendment", True),
        ("Question 2: Constitutional Amendment", True),
        ("Question 3: Constitutional Amendment", True),
    ]
    lettered = [q for q in page["questions"] if not q["statewide"]]
    assert lettered and all(q["slug"] is None for q in lettered)
    assert lettered[0]["text_url"].startswith("https://elections.maryland.gov/")


def test_question_map_is_for_minus_against():
    from skipjack.web.maps import question_map

    rows = [
        {"county": "Allegany", "for": 75, "against": 25, "total": 100},
        {"county": "Garrett", "for": 40, "against": 60, "total": 100},
    ]
    (m,) = question_map({"county_rows": rows})
    labels = {c["name"]: c["label"] for c in m["counties"]}
    assert labels["Allegany"] == "Allegany: +50 points"
    assert labels["Garrett"] == "Garrett: -20 points"
    assert labels["Kent"] == "Kent: no votes counted"
    assert m["legend"][0]["text"].startswith("more against")
    assert question_map({"county_rows": []}) == []


def test_question_page(client):
    html = client.get("/results/gg26/question-1/").text
    assert "Unofficial and incomplete." in html
    assert "1,304,321" in html
    assert "gen_qtext_2026_C_1.html" in html
    assert "question-margin" in html
    assert "mail-in ballot, provisional" in html  # Caroline's row
    assert "/results/gg26/county/caroline/#question-1-constitutional-amendment" in html
    assert client.get("/results/gg26/question-9/").status_code == 404


def test_hub_and_county_pages_list_questions(client):
    hub = client.get("/results/gg26/").text
    assert 'id="questions"' in hub
    assert "/results/gg26/question-3/" in hub
    county = client.get("/results/gg26/county/baltimore-county/").text
    assert 'id="question-1-constitutional-amendment"' in county
    assert "county question" in county


def test_question_pages_disappear_once_certified(client, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    assert client.get("/results/gg26/question-1/").status_code == 404
