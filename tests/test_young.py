"""Tests for the young voters page: new registrations and party choice.

The first half uses made-up snapshots in an in-memory database. The second half checks the page
against the real aggregates, including that they reproduce the figures in the R analysis the page
is based on, and is skipped without skipjack.db.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from skipjack.models import SCHEMA_SQL
from skipjack.web import young
from skipjack.web.app import app
from skipjack.web.db import DB_PATH
from skipjack.web.routes.young import above_or_below, bullets, more_or_fewer

# --- made-up snapshots ---------------------------------------------------------------------------


def add_snapshot(conn, date, new=(), weeks=(), age=(), cutoff="08-12"):
    meta = {
        "counties": ["Allegany", "Kent"],
        "new_registrants": {
            "first_year": 2010,
            "age_bands": ["18-22", "23-29", "30+"],
            "cutoff": cutoff,
        },
    }
    conn.execute(
        "INSERT INTO voter_file_snapshots (snapshot_date, meta_json) VALUES (?, ?)",
        (date, json.dumps(meta)),
    )
    rows = [(date, "new_registrants", c, g, b, y, w, None, n) for c, g, b, y, w, n in new]
    rows += [(date, "new_registrant_weeks", None, None, b, y, None, wk, n) for y, wk, b, n in weeks]
    rows += [(date, "age_detail_by_party", c, g, b, None, None, None, n) for c, g, b, n in age]
    conn.executemany(
        """INSERT INTO voter_file_counts
           (snapshot_date, metric, county, party_group, age_band, reg_year, reg_window, reg_week, count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def state(group, band, year, window, n):
    return ("Maryland", group, band, year, window, n)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    latest_new = [
        state("ALL", "18-22", 2018, "to_date", 1000), state("ALL", "18-22", 2018, "rest", 1000),
        state("ALL", "18-22", 2022, "to_date", 1500), state("ALL", "18-22", 2022, "rest", 1300),
        state("ALL", "18-22", 2026, "to_date", 1600),
        *[state("ALL", "23-29", y, "to_date", n) for y, n in ((2018, 1500), (2022, 2000), (2026, 1900))],
        *[state("ALL", "30+", y, "to_date", 1000) for y in (2018, 2022, 2026)],
        *[state("UNA", "18-22", y, "to_date", n) for y, n in ((2018, 260), (2022, 555), (2026, 688))],
        *[state("REP", "18-22", y, "to_date", n) for y, n in ((2018, 140), (2022, 240), (2026, 144))],
        *[state("DEM", "18-22", y, "to_date", n) for y, n in ((2018, 580), (2022, 660), (2026, 720))],
        ("Allegany", "ALL", "18-22", 2022, "to_date", 300), ("Allegany", "ALL", "18-22", 2026, "to_date", 400),
        ("Allegany", "REP", "18-22", 2022, "to_date", 90), ("Allegany", "REP", "18-22", 2026, "to_date", 80),
        ("Allegany", "ALL", "23-29", 2022, "to_date", 100), ("Allegany", "ALL", "23-29", 2026, "to_date", 150),
        ("Kent", "ALL", "18-22", 2022, "to_date", 100), ("Kent", "ALL", "18-22", 2026, "to_date", 120),
    ]  # fmt: skip
    latest_weeks = [(2018, 1, "18-22", 1000), (2018, 33, "18-22", 1000), (2022, 1, "18-22", 1500),
                    (2022, 40, "18-22", 1300), (2026, 1, "18-22", 1600)]  # fmt: skip
    age = []
    for band, n, una, dem, rep in (("18-22", 1000, 380, 430, 160), ("23-29", 1000, 290, 500, 180),
                                   ("30-44", 1000, 280, 510, 190), ("45-64", 1000, 220, 500, 260),
                                   ("65+", 1000, 140, 560, 290)):  # fmt: skip
        age += [("Maryland", "ALL", band, n), ("Maryland", "UNA", band, una),
                ("Maryland", "DEM", band, dem), ("Maryland", "REP", band, rep)]  # fmt: skip
    age += [("Maryland", "ALL", "Under 18", 50), ("Maryland", "ALL", "Unknown", 5)]
    for county, young_n, older_n in (("Allegany", 100, 300), ("Kent", 100, 300)):
        age += [(county, "ALL", "18-22", young_n), (county, "UNA", "18-22", young_n * 0.4),
                (county, "ALL", "23-29", older_n), (county, "UNA", "23-29", older_n * 0.3)]  # fmt: skip
    add_snapshot(c, "2026-08-12", latest_new, latest_weeks, age)
    # The earlier file saw more of the same earlier registrants, and its own cutoff is September 11.
    earlier_new = [state("ALL", "18-22", 2018, "to_date", 1500), state("ALL", "18-22", 2018, "rest", 700),
                   state("ALL", "18-22", 2022, "to_date", 1900), state("ALL", "18-22", 2022, "rest", 1000)]  # fmt: skip
    earlier_weeks = [(2018, 1, "18-22", 1160), (2018, 33, "18-22", 1000), (2022, 1, "18-22", 1750),
                     (2022, 40, "18-22", 1000)]  # fmt: skip
    add_snapshot(c, "2024-09-11", earlier_new, earlier_weeks, cutoff="09-11")
    yield c
    c.close()


def test_week_of_the_year_ignores_leap_days():
    assert young.nonleap_week(1, 1) == 1
    assert young.nonleap_week(3, 4) == 9 and young.nonleap_week(3, 5) == 10
    assert young.nonleap_week(12, 31) == 53
    assert young.nonleap_week(2, 29) == young.nonleap_week(
        2, 28
    )  # a leap-day snapshot does not crash
    assert young.week_start_label(1) == "Jan 1" and young.week_start_label(53) == "Dec 31"


def test_a_hidden_cell_hides_a_sum_but_a_missing_cell_is_zero(conn):
    cells = young.Cells(conn, "2026-08-12")
    assert cells.registrants("Maryland", "ALL", ("18-22",), 2018) == 2000  # to date plus the rest
    assert cells.registrants("Maryland", "ALL", ("18-22",), 2026) == 1600  # no "rest" row: zero
    conn.execute(
        "UPDATE voter_file_counts SET count = NULL WHERE reg_year = 2018 AND reg_window = 'rest'"
    )
    assert young.Cells(conn, "2026-08-12").registrants("Maryland", "ALL", ("18-22",), 2018) is None
    assert (
        young.Cells(conn, "2026-08-12").registrants("Maryland", "ALL", ("18-22",), 2018, "to_date")
        == 1000
    )


def test_other_is_what_is_left_so_a_tiny_party_cannot_blank_it(conn):
    cells = young.Cells(conn, "2026-08-12")
    shares = young._party_shares(cells, "Maryland", ("18-22",), 2026)
    assert shares["other"] == pytest.approx(100 * (1600 - 688 - 144 - 720) / 1600)
    assert shares["UNA"] == pytest.approx(43.0)
    conn.execute(
        "UPDATE voter_file_counts SET count = 1598 WHERE party_group = 'DEM' AND reg_year = 2026"
    )
    tiny = young._party_shares(young.Cells(conn, "2026-08-12"), "Maryland", ("18-22",), 2026)
    assert tiny["other"] is None  # a leftover of 1 to 9 voters stays hidden


def test_hidden_weeks_are_solved_so_the_line_ends_at_the_known_total():
    class C:
        weeks = {
            (2026, 1, "18-22"): 10,
            (2026, 2, "18-22"): None,
            (2026, 3, "18-22"): 20,
            (2026, 4, "18-22"): None,
        }

    cells = C()
    ends = lambda known: young.running_total(cells, 2026, 4, known)["values"][3]  # noqa: E731
    assert ends(40) == 40  # 30 seen, 10 left over for two hidden weeks: 5 and 5
    assert ends(35) == 35  # 5 left over: 3 and 2
    assert ends(100) == 40  # impossible (more than 9 per hidden week): fall back to 5 each
    assert ends(None) == 40
    run = young.running_total(cells, 2026, 4, 35)
    assert run["hidden"] == 2 and run["values"][4:] == [None] * 49  # nothing past the cutoff week


def test_weeks_through_counts_hidden_weeks_as_five():
    class C:
        weeks = {(2022, 1, "18-22"): 100, (2022, 2, "18-22"): None, (2022, 3, "18-22"): 50}

    assert young.weeks_through(C(), 2022, 3) == (155, 1)
    assert young.weeks_through(C(), 2022, 1) == (100, 0)


def test_the_trend_sentence_names_when_the_lines_pulled_apart():
    years = list(range(2010, 2018))
    flat = {
        "years": years,
        "young": [25, 26, 24, 25, 26, 30, 31, 32],
        "older": [25, 25, 25, 25, 25, 25, 25, 25],
    }
    note = young._trend_note(flat)
    assert "From 2010 through 2014" in note and "within 1 points" in note
    assert "Since 2015" in note and "roughly 5 to 7 points" in note
    never = {"years": years, "young": [25] * 8, "older": [25] * 8}
    assert young._trend_note(never) == ""  # no sustained gap, so no claim
    always = {"years": years, "young": [40] * 8, "older": [25] * 8}
    assert young._trend_note(always) == ""  # apart from the start: nothing to say about "before"


def test_the_page_compares_this_year_with_the_last_two_election_years(conn):
    d = young.build_young(conn)
    assert (d["year"], d["prior"], d["first"], d["compare"]) == (
        2026,
        2022,
        2018,
        [2018, 2022, 2026],
    )
    assert [(r["year"], r["window"], r["full"]) for r in d["pace"]] == [
        (2018, 1000, 2000), (2022, 1500, 2800), (2026, 1600, None),
    ]  # fmt: skip
    assert d["pace"][0]["after_share"] == pytest.approx(50.0)
    assert d["changes"][2022] == pytest.approx(100 * 100 / 1500)
    assert d["changes"][2018] == pytest.approx(60.0)
    # 1600 / (1 - 46.4%) is about 2,990 and 1600 / (1 - 50%) is 3,200: both are 3,000 to the thousand.
    assert d["estimate"] == (3000, 3000)


def test_leavers_are_put_back_using_the_earlier_file(conn):
    d = young.build_young(conn)
    by = {s["year"]: s for s in d["shrink"]}
    assert (by[2018]["was"], by[2018]["now"]) == (1160, 1000)
    assert by[2018]["lost_pct"] == pytest.approx(100 * 160 / 1160)
    assert d["adjusted"][2022]["restored"] == 1750  # 1500 back to what the earlier file saw
    assert d["adjusted"][2022]["change"] == pytest.approx(100 * (1600 - 1750) / 1750)
    assert d["adjusted"][2018]["change"] == pytest.approx(100 * (1600 - 1160) / 1160)
    assert d["shrink_label"] == "September 2024"


def test_counties_with_too_few_young_registrants_are_not_compared(conn):
    counties = {c["county"]: c for c in young.build_young(conn)["counties"]}
    allegany, kent = counties["Allegany"], counties["Kent"]
    assert allegany["enough"] is True  # 300 and 400, both above the minimum of 250
    assert allegany["rep_points"] == pytest.approx(100 * 80 / 400 - 100 * 90 / 300)
    assert allegany["young_change"] == pytest.approx(100 * (400 - 300) / 300)
    assert allegany["all_change"] == pytest.approx(100 * ((400 + 150) - (300 + 100)) / (300 + 100))
    assert kent["enough"] is False and kent["rep_points"] is None and kent["rep_now"] is None
    assert kent["young_change"] == pytest.approx(20.0)  # the registration pace still shows


@pytest.mark.parametrize(("count", "enough"), [(249, False), (250, True)])
def test_the_county_minimum_is_250_in_each_year(conn, count, enough):
    conn.execute(
        "UPDATE voter_file_counts SET count = ? WHERE county = 'Allegany' AND metric = 'new_registrants' "
        "AND party_group = 'ALL' AND age_band = '18-22' AND reg_year = 2022",
        (count,),
    )
    assert (
        next(c for c in young.build_young(conn)["counties"] if c["county"] == "Allegany")["enough"]
        is enough
    )


def test_the_age_table_and_what_it_leaves_out(conn):
    d = young.build_young(conn)
    assert [r["band"] for r in d["age_rows"]] == [
        "18-22",
        "23-29",
        "30-44",
        "45-64",
        "65+",
        "All 23 and older",
    ]
    assert d["age_rows"][0]["UNA"] == pytest.approx(38.0)
    assert d["age_rows"][-1]["n"] == 4000 and d["age_rows"][-1]["UNA"] == pytest.approx(
        100 * 930 / 4000
    )
    assert d["left_out"] == {"under_18": 50, "unknown": 5}


def test_no_page_without_a_snapshot_that_has_the_metrics():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    assert young.build_young(c) is None
    c.close()


def test_phrases_for_changes():
    assert more_or_fewer(2.9) == "3% more than" and more_or_fewer(-35.3) == "35% fewer than"
    assert more_or_fewer(0.2) == "about the same as" and more_or_fewer(None) == ""
    assert above_or_below(-4.0) == "4% below" and above_or_below(16.9) == "17% above"
    assert above_or_below(0.3) == "level with"


def test_the_headline_sentences_state_both_the_raw_and_the_adjusted_comparison(conn):
    text = " ".join(bullets(young.build_young(conn)))
    assert (
        "Counted straight from the file, that is 7% more than in the same stretch of 2022 and 60% more than in 2018."
        in text
    )
    assert "this year's count is 9% below 2022's and 38% above 2018's" in text
    assert "even this flatters this year" in text


# --- against the built database ------------------------------------------------------------------


def _has_young_metrics() -> bool:
    if not DB_PATH.exists():
        return False
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return bool(young.snapshots_with_young(c))
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


needs_young = pytest.mark.skipif(
    not _has_young_metrics(), reason="skipjack.db has no snapshot with the young voter metrics"
)
client = TestClient(app)


def page(path: str = "/voters/young/") -> str:
    """A page's text with entities decoded and whitespace collapsed, so sentences match whole."""
    return re.sub(r"\s+", " ", html_lib.unescape(client.get(path).text))


def real():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


@needs_young
def test_the_aggregates_reproduce_the_figures_in_the_r_analysis():
    """Numbers from young_voters_2026_summary.md, computed there from the raw file in R."""
    with real() as c:
        d = young.build_young(c)
    window = {r["year"]: r["window"] for r in d["pace"]}
    full = {r["year"]: r["full"] for r in d["pace"]}
    assert window == {2018: 11_564, 2022: 15_203, 2026: 15_646}
    assert (full[2018], full[2022]) == (23_281, 28_024)
    assert [round(r["young_share"]) for r in d["pace"]] == [30, 27, 28]
    assert [round(r["after_share"]) for r in d["pace"][:2]] == [50, 46]
    assert round(d["changes"][2022]) == 3 and round(d["changes"][2018]) == 35
    assert d["estimate"] == (29_000, 31_000)
    shares = {
        p["year"]: {
            k: {g: round(v) for g, v in p[k].items() if g != "total"} for k in ("young", "older")
        }
        for p in d["parties"]
    }
    assert shares[2018]["young"] == {"DEM": 58, "REP": 14, "UNA": 26, "other": 2}
    assert shares[2022]["older"] == {"DEM": 48, "REP": 18, "UNA": 30, "other": 3}
    assert shares[2026]["young"] == {"DEM": 45, "REP": 9, "UNA": 43, "other": 3}
    assert shares[2026]["older"] == {"DEM": 45, "REP": 15, "UNA": 38, "other": 3}
    ages = {r["band"]: r for r in d["age_rows"]}
    assert (ages["18-22"]["n"], ages["23-29"]["n"], ages["30-44"]["n"], ages["45-64"]["n"]) == (
        315_145, 511_385, 1_178_881, 1_423_454,
    )  # fmt: skip
    assert [round(ages[b]["UNA"]) for b in ("18-22", "23-29", "30-44", "45-64", "65+")] == [
        38,
        29,
        28,
        22,
        14,
    ]
    counties = {c["county"]: c for c in d["counties"]}
    for name, change in (("Prince George's", 17), ("Baltimore County", 16), ("Howard", 55), ("Montgomery", -20), ("St. Mary's", -22), ("Cecil", -26)):  # fmt: skip
        assert round(counties[name]["young_change"]) == change, name
    assert round(counties["Baltimore County"]["all_change"]) == 5
    assert round(counties["Montgomery"]["all_change"]) == -22
    eligible = sorted(c["county"] for c in d["counties"] if c["enough"])
    assert len(eligible) == 15 and "Carroll" in eligible and "Allegany" not in eligible


@needs_young
def test_the_page_says_what_the_file_cannot_and_what_it_measured():
    html = page()
    assert "These counts are of people still on the list." in html
    assert "this year's count is 4% below 2022's and 17% above 2018's" in html
    assert "13,382 to 11,564 (13.6% gone)" in html and "16,296 to 15,203 (6.7% gone)" in html
    assert "roughly 29,000 to 31,000" in html and "That is an estimate, not a finding." in html
    assert "Voters who pre-registered at 16 or 17 are left out." in html
    assert (
        "9,552 registered voters under 18" in html
        and "68 whose birth dates imply an age over 115" in html
    )


@needs_young
def test_the_page_names_the_biggest_county_drops_and_the_unaffiliated_gap():
    html = page()
    assert (
        "The biggest county drops were in Calvert, Cecil and Washington, by 14 to 16 points."
        in html
    )
    assert (
        "12 to 18 points more unaffiliated than voters 23 and older in every one of Maryland's 24 counties"
        in html
    )
    assert 'href="/voters/file/county/calvert/"' in html and "too few" in html


@needs_young
def test_the_chart_data_ends_at_the_exact_totals():
    raw = client.get("/voters/young/").text
    data = json.loads(
        re.search(r'id="young-data" type="application/json">(.*?)</script>', raw, re.S).group(1)
    )
    ends = {s["year"]: [v for v in s["values"] if v is not None][-1] for s in data["series"]}
    assert ends == {2018: 23_281, 2022: 28_024, 2026: 15_646}
    assert data["cutoff_week"] == 32 and data["years"][0] == 2010 and data["years"][-1] == 2026
    assert data["primary_weeks"] == {"2018": 26, "2022": 29, "2026": 25}
    assert len(data["young"]) == len(data["older"]) == len(data["years"])


@needs_young
def test_the_page_is_linked_from_the_site():
    assert 'href="/voters/young/"' in page("/")  # the nav and a card
    assert ">Young voters</a>" in page(
        "/voters/file/"
    )  # the link in the voter file's own row of links
    # An older snapshot has no young voters page of its own to point to; only the nav link remains.
    assert ">Young voters</a>" not in page("/voters/file/?snapshot=2024-09-11")
