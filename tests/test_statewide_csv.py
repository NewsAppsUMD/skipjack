"""The wide per-contest CSVs of the statewide races (jurisdiction, a column per ballot
candidate, others, total)."""

from __future__ import annotations

import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.web import results, statewide_csv
from skipjack.web.results_unofficial import is_unofficial
from test_results_web import add


def cand(name, total, write_in=0, party="DEM", ballot=None):
    c = {"name": name, "party": party, "write_in": write_in, "total": total}
    if ballot is not None:
        c["ballot"] = ballot
    return c


def test_a_column_is_the_lower_case_last_name_of_the_first_person_on_the_line():
    assert statewide_csv.column_name("Kamala D. Harris and Tim Walz") == "harris"
    assert statewide_csv.column_name("Robert F. Kennedy, Jr. and Nicole Shanahan") == "kennedy"
    assert statewide_csv.column_name("Chase Oliver and Mike ter Maat") == "oliver"
    assert statewide_csv.column_name("Andrè Ramon McNeil, Sr.") == "mcneil"
    assert statewide_csv.column_name("Lester H. Waller, Jr.") == "waller"
    assert statewide_csv.column_name("Wes Moore") == "moore"


def test_candidates_sharing_a_last_name_get_first_and_last():
    names = statewide_csv.column_names(
        [cand("Wes Moore", 9), cand("Sharon E Harris", 5), cand("Kamala D. Harris", 3)]
    )
    assert names == ["moore", "sharon_harris", "kamala_harris"]


def test_a_column_never_collides_with_the_fixed_columns_or_itself():
    names = statewide_csv.column_names([cand("Tom Total", 2), cand("Ann Others", 1)])
    assert names == ["total_2", "others_2"]
    same = statewide_csv.column_names([cand("Wes Moore", 2), cand("Wes Moore", 1, party="REP")])
    assert same == ["wes_moore", "wes_moore_2"]


def contest(cells_by_county, candidates):
    rows = []
    for county, cells in cells_by_county.items():
        rows.append(
            {"county": county, "cells": cells, "total": sum(c for c in cells if c is not None)}
        )
    return {"candidates": candidates, "county_rows": rows, "has_against": False}


def test_the_csv_has_a_row_per_county_with_write_ins_summed_as_others():
    candidates = [
        cand("Kamala D. Harris and Tim Walz", 300),
        cand("Donald J. Trump and JD Vance", 200, party="REP"),
        cand("Other Write-Ins", 10, write_in=1, party=None),
        cand("Peter Sonski", 2, write_in=1, party="UNA"),
    ]
    text = statewide_csv.contest_csv(
        contest({"Allegany": [100, 150, 7, 2], "St. Mary's": [200, 50, 3, 0]}, candidates)
    )
    assert text == (
        "jurisdiction,harris,trump,others,total\n"
        "Allegany,100,150,9,259\n"
        "Saint Mary's,200,50,3,253\n"
    )


def test_columns_follow_the_ballot_order_not_the_vote_order():
    """The page lists candidates by votes; the file keeps the State Board's order, as the
    reference file does (harris, trump, oliver, stein, kennedy)."""
    candidates = [
        cand("Kamala D. Harris", 300, ballot=0),
        cand("Donald J. Trump", 200, party="REP", ballot=1),
        cand("Jill Ellen Stein", 30, party="GRN", ballot=3),
        cand("Chase Oliver", 20, party="LIB", ballot=2),
        cand("Other Write-Ins", 1, write_in=1, party=None, ballot=4),
    ]
    text = statewide_csv.contest_csv(contest({"Kent": [300, 200, 30, 20, 1]}, candidates))
    assert text == "jurisdiction,harris,trump,oliver,stein,others,total\nKent,300,200,20,30,1,551\n"


def test_a_contest_with_no_write_ins_still_has_an_others_column_of_zero():
    text = statewide_csv.contest_csv(
        contest({"Kent": [5, 4]}, [cand("Wes Moore", 5), cand("Eric Felber", 4)])
    )
    assert text == "jurisdiction,moore,felber,others,total\nKent,5,4,0,9\n"


def test_a_count_not_yet_reported_is_blank_not_zero():
    candidates = [
        cand("Wes Moore", 5),
        cand("Dan Cox", 4, party="REP"),
        cand("Other Write-Ins", 0, 1, None),
    ]
    text = statewide_csv.contest_csv(
        contest({"Kent": [None, None, None], "Talbot": [3, 2, None]}, candidates)
    )
    assert text == ("jurisdiction,moore,cox,others,total\nKent,,,,0\nTalbot,3,2,,5\n")


def test_a_retention_question_has_no_csv():
    c = contest({"Kent": [5]}, [cand("A Judge", 5)])
    c["has_against"] = True
    assert statewide_csv.contest_csv(c) is None


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(results, "N_COUNTIES", 2)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    for county, moore, cox in (("Allegany", 100, 40), ("Garrett", 20, 10)):
        add(c, "GP26", county, "dem", "Governor / Lt. Governor", "",
            [("Wes Moore", "DEM", True, {"early": moore})])  # fmt: skip
        add(c, "GP26", county, "rep", "Governor / Lt. Governor", "",
            [("Kelly Schulz", "REP", False, {"early": 1}), ("Dan Cox", "REP", True, {"early": cox})])  # fmt: skip
        add(
            c, "GP26", county, "dem", "State Senator", "2", [("Sen Two", "DEM", True, {"early": 7})]
        )
        # a statewide retention question: no CSV
        add(c, "GP26", county, "all", "Appeals Judge MD at Lrg", "",
            [("A Judge", None, True, {"early": 3}, 1)])  # fmt: skip
    yield c
    c.close()


def test_the_downloads_are_one_file_per_statewide_contest_in_hub_order(conn):
    found = statewide_csv.downloads(conn)
    assert [(d["file"], d["code"], d["title"]) for d in found] == [
        ("GP26-governor-dem.csv", "GP26", "Governor / Lt. Governor (Democratic)"),
        ("GP26-governor-rep.csv", "GP26", "Governor / Lt. Governor (Republican)"),
    ]
    assert found[0]["slug"] == "governor-lt-governor-dem"
    assert found[0]["unofficial"] is is_unofficial("GP26")


def test_a_file_is_built_from_the_contest_it_names(conn):
    assert statewide_csv.csv_for(conn, "GP26-governor-rep.csv") == (
        "jurisdiction,schulz,cox,others,total\nAllegany,1,40,0,41\nGarrett,1,10,0,11\n"
    )
    # Schulz is first in the SBE's files though Cox has more votes
    assert statewide_csv.csv_for(conn, "GP26-governor-rep.csv").startswith("jurisdiction,schulz")
    assert statewide_csv.csv_for(conn, "GP26-sheriff.csv") is None
    assert statewide_csv.csv_for(conn, "../etc/passwd") is None


def test_file_for_names_a_statewide_contest_page_and_nothing_else():
    assert (
        statewide_csv.file_for("PG24", "President - Vice Pres", "all", "statewide")
        == "PG24-president.csv"
    )
    assert (
        statewide_csv.file_for("GP26", "U.S. Senator", "dem", "statewide")
        == "GP26-us-senate-dem.csv"
    )
    assert statewide_csv.file_for("PG24", "Comptroller", "all", "local") is None
    assert statewide_csv.file_for("GG22", "Appeals Judge MD at Lrg", "all", "statewide") is None
