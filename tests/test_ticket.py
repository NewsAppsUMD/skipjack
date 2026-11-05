"""Tests for skipjack.web.ticket: who outran the top of the ticket on a county page.

The unit tests build county-page dicts by hand. The db-backed test (skipped without real
results rows) checks the rules on known counties.
"""

from __future__ import annotations

import sqlite3

import pytest

from skipjack.web import results, ticket
from skipjack.web.db import DB_PATH


def cand(name, party, total, write_in=False):
    return {"name": name, "party": party, "total": total, "write_in": write_in}


def contest(office, candidates, district="", scope="local", seats=1, has_against=False):
    return {
        "office": office,
        "district": district,
        "scope": scope,
        "seats": seats,
        "has_against": has_against,
        "title": f"{office} {district}".strip(),
        "anchor": f"{office} {district}".strip().lower().replace(" ", "-"),
        "candidates": candidates,
    }


GOV = contest(
    "Governor / Lt. Governor",
    [cand("Moore", "DEM", 600), cand("Cox", "REP", 380), cand("Write-ins", "", 20, True)],
    scope="statewide",
)


def page(*contests, kind="general"):
    return {
        "election": {"kind": kind},
        "groups": [{"party_file": "all", "contests": [GOV, *contests]}],
    }


def names(out):
    return [r["name"] for r in out["rows"]]


def test_statewide_and_county_offices_compared_by_votes():
    out = ticket.outran(
        page(
            contest(
                "Comptroller",
                [cand("Lierman", "DEM", 610), cand("Glassman", "REP", 370)],
                scope="statewide",
            ),  # fmt: skip
            contest("Sheriff", [cand("Dee", "DEM", 500), cand("Arr", "REP", 450)]),
        )
    )
    assert names(out) == ["Arr", "Lierman"]  # biggest lead first
    assert out["rows"][0]["diff"] == 70 and out["rows"][0]["top_name"] == "Cox"
    assert out["rows"][0]["diff_pct"] == pytest.approx(100 * 70 / 380)
    assert out["compared"] == 4
    assert out["top_title"] == "Governor"
    assert [t["name"] for t in out["ticket"]] == ["Moore", "Cox"]


def test_a_legislative_district_counts_only_when_it_is_the_whole_county():
    one = page(contest("State Senator", [cand("S", "REP", 400), cand("T", "DEM", 500)],
                       district="5", scope="senate"))  # fmt: skip
    assert names(ticket.outran(one)) == ["S"]
    two = page(
        contest("State Senator", [cand("S", "REP", 300), cand("T", "DEM", 100)], "4", "senate"),
        contest("State Senator", [cand("U", "REP", 390), cand("V", "DEM", 200)], "5", "senate"),
    )
    assert ticket.outran(two)["compared"] == 0


def test_county_districts_that_split_the_county_are_left_out():
    out = ticket.outran(
        page(
            contest("County Council", [cand("A", "REP", 300), cand("B", "DEM", 200)], "1"),
            contest("County Council", [cand("C", "REP", 200), cand("D", "DEM", 300)], "2"),
        )
    )
    assert out["compared"] == 0


def test_residency_districts_elected_countywide_are_compared():
    out = ticket.outran(
        page(
            contest("County Commissioner", [cand("A", "REP", 400), cand("B", "DEM", 550)], "1"),
            contest("County Commissioner", [cand("C", "REP", 390), cand("D", "DEM", 560)], "2"),
        )
    )
    assert names(out) == ["A", "C"] and out["compared"] == 4


def test_a_large_district_is_caught_by_the_partition_sum():
    # like Dorchester's Orphans' Court: district 2 holds most of the county and two seats
    out = ticket.outran(
        page(
            contest(
                "Judge Orphans' Court",
                [cand("A", "REP", 400), cand("B", "DEM", 380), cand("C", "DEM", 200)],
                "2",
                seats=2,
            ),  # 0.49 of the top
            contest("Judge Orphans' Court", [cand("D", "REP", 150), cand("E", "DEM", 120)], "1"),
        )  # fmt: skip
    )
    assert out["compared"] == 0


def test_a_lone_county_district_with_few_votes_is_left_out():
    # PG20 Wicomico: only council district 2 on the ballot
    out = ticket.outran(
        page(contest("County Council", [cand("A", "REP", 150), cand("B", "DEM", 90)], "2"))
    )
    assert out["compared"] == 0


def test_unopposed_candidates_and_write_ins_are_left_out():
    out = ticket.outran(
        page(
            contest("State's Attorney", [cand("A", "REP", 700), cand("W", "", 9, True)]),
            contest("Sheriff", [cand("B", "REP", 500), cand("C", "REP", 300)]),  # same party
            contest(
                "Orphans",
                [cand("D", "DEM", 650), cand("E", "DEM", 640), cand("F", "REP", 400)],
                seats=3,
            ),  # fmt: skip
        )
    )
    assert out["compared"] == 0 and out["rows"] == []


def test_multi_seat_contest_with_more_candidates_than_seats_is_compared():
    out = ticket.outran(
        page(
            contest(
                "Council At Large",
                [
                    cand("A", "DEM", 620),
                    cand("B", "DEM", 590),
                    cand("C", "REP", 390),
                    cand("D", "REP", 370),
                ],
                seats=2,
            )
        )  # fmt: skip
    )
    assert names(out) == ["A", "C"]


def test_unaffiliated_and_retention_are_not_compared():
    out = ticket.outran(
        page(
            contest("Sheriff", [cand("U", "UNA", 900), cand("R", "REP", 100)]),
            contest("Judge", [cand("J", "", 800)], scope="statewide", has_against=True),
        )
    )
    assert out["compared"] == 1 and out["rows"] == []


def test_only_general_elections_with_a_top_of_ticket_contest():
    assert ticket.outran(page(kind="primary")) is None
    p = page()
    p["groups"][0]["contests"] = [contest("Sheriff", [cand("A", "REP", 1)])]
    assert ticket.outran(p) is None


# --- the real data ---------------------------------------------------------------------------


def _real_page(code, county):
    if not DB_PATH.exists():
        pytest.skip("no skipjack.db")
    conn = sqlite3.connect(DB_PATH)
    try:
        if not conn.execute(
            "SELECT 1 FROM results_county WHERE election = ? LIMIT 1", (code,)
        ).fetchone():
            pytest.skip(f"no {code} results in skipjack.db")
        return results.county_page(conn, code, county)
    finally:
        conn.close()


def test_real_gg22_frederick():
    p = _real_page("GG22", "frederick")
    contests = [c for g in p["groups"] for c in g["contests"]]
    top = sum(c["total"] for c in contests[0]["candidates"])
    whole = {
        c["title"] for c, f in zip(contests, ticket.countywide(contests, top), strict=True) if f
    }
    assert "County Executive" in whole and "U.S. Congress, District 6" in whole
    assert not any(
        t.startswith(("State Senator", "House of Delegates", "County Council,")) for t in whole
    )
    out = ticket.outran(p)
    jenkins = next(r for r in out["rows"] if r["name"] == "Chuck Jenkins")
    assert (jenkins["votes"], jenkins["top_votes"]) == (54034, 46040)


def test_real_gg22_calvert_residency_districts_and_dorchester_orphans_court():
    p = _real_page("GG22", "calvert")
    contests = [c for g in p["groups"] for c in g["contests"]]
    top = sum(c["total"] for c in contests[0]["candidates"])
    flags = dict(zip([c["title"] for c in contests], ticket.countywide(contests, top), strict=True))
    assert flags["County Commissioner, District 1"]
    p = _real_page("GG22", "dorchester")
    contests = [c for g in p["groups"] for c in g["contests"]]
    top = sum(c["total"] for c in contests[0]["candidates"])
    flags = dict(zip([c["title"] for c in contests], ticket.countywide(contests, top), strict=True))
    assert not flags["Judge Orphans' Court, District 2"]
