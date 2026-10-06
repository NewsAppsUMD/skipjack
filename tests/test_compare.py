"""Tests for comparing two voter file snapshots.

The first half uses two made-up snapshots written into an in-memory database. The second half
checks the page against the real aggregates in skipjack.db and is skipped without it.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from skipjack.models import SCHEMA_SQL
from skipjack.web import compare
from skipjack.web.app import app
from skipjack.web.db import DB_PATH

# --- made-up snapshots --------------------------------------------------------------------------


def meta(**extra):
    return {
        "counties": ["Allegany", "Kent"],
        "age_bands": ["Under 18", "18-24", "25-34", "Unknown"],
        "elections": [{"key": "2016-PG", "short": "2016 General"}],
        "vrar_cross_check": None,
        "limitations": [],
        **extra,
    }


def add_snapshot(conn, date, cells, age, cohorts, **meta_extra):
    conn.execute(
        "INSERT INTO voter_file_snapshots (snapshot_date, meta_json) VALUES (?, ?)",
        (date, json.dumps(meta(**meta_extra))),
    )
    rows = [(date, "party_by_county", c, g, s, None, None, n) for c, g, s, n in cells]
    rows += [(date, "age_by_party", "Maryland", g, None, band, None, n) for g, band, n in age]
    rows += [
        (date, "registration_cohorts", "Maryland", "ALL", None, None, y, n) for y, n in cohorts
    ]
    conn.executemany(
        """INSERT INTO voter_file_counts
           (snapshot_date, metric, county, party_group, status, age_band, reg_year, count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def cells(registered_by_group, county="Maryland", inactive_share=0.1):
    out = []
    for group, n in registered_by_group.items():
        inactive = round(n * inactive_share)
        out += [(county, group, "A", n - inactive), (county, group, "I", inactive)]
    return out


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    before = cells({"ALL": 1000, "DEM": 500, "REP": 300, "UNA": 200}) + cells(
        {"ALL": 400, "DEM": 200, "REP": 150, "UNA": 50}, "Allegany"
    )
    after = cells({"ALL": 1100, "DEM": 500, "REP": 330, "UNA": 270}) + cells(
        {"ALL": 380, "DEM": 180, "REP": 150, "UNA": 50}, "Allegany"
    )
    add_snapshot(
        c, "2024-09-11", before,
        age=[("ALL", "18-24", 300), ("DEM", "18-24", 150), ("REP", "18-24", 60), ("UNA", "18-24", 90),
             ("ALL", "Under 18", 40)],
        cohorts=[(1990, 200), (2023, 100), (2024, 50)],
        vrar_cross_check={"vrar_month": "2024-09", "file_active": 900, "vrar_active": 920, "difference_pct": -2.17},
    )  # fmt: skip
    add_snapshot(
        c, "2026-08-12", after,
        age=[("ALL", "18-24", 330), ("DEM", "18-24", 150), ("REP", "18-24", 60), ("UNA", "18-24", 120),
             ("ALL", "Under 18", None)],
        cohorts=[(1990, 180), (2023, 97), (2024, 90)],
        vrar_cross_check={"vrar_month": "2026-08", "file_active": 990, "vrar_active": 1000, "difference_pct": -1.0},
    )  # fmt: skip
    yield c
    c.close()


def test_snapshot_pairs_are_consecutive_and_oldest_first():
    assert compare.snapshot_pairs(["2026-08-12", "2024-09-11", "2025-06-01"]) == [
        ("2024-09-11", "2025-06-01"),
        ("2025-06-01", "2026-08-12"),
    ]
    assert compare.snapshot_pairs(["2026-08-12"]) == []


def test_percent_helpers_survive_missing_and_zero():
    assert compare.pct_change(200, 250) == pytest.approx(25.0)
    assert compare.pct_change(None, 250) is None and compare.pct_change(0, 5) is None
    assert (
        compare.share(1, 4) == 25.0
        and compare.share(None, 4) is None
        and compare.share(1, 0) is None
    )


def test_statewide_tiles_and_party_shares(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")
    assert c["tiles"]["registered"] == {
        "a": 1000,
        "b": 1100,
        "change": 100,
        "pct": pytest.approx(10.0),
    }
    assert c["tiles"]["inactive"]["a"] == 100 and c["tiles"]["inactive"]["b"] == 110
    una = next(p for p in c["parties"] if p["group"] == "UNA")
    assert una["registered"]["change"] == 70
    assert una["share"]["a"] == pytest.approx(20.0) and una["share"]["b"] == pytest.approx(
        100 * 270 / 1100
    )
    assert una["share"]["points"] == pytest.approx(100 * 270 / 1100 - 20.0)
    assert [p["group"] for p in c["parties"]] == ["ALL", "DEM", "REP", "UNA", "GRN", "WCP", "OTH"]


def test_a_party_missing_from_both_snapshots_has_no_numbers_rather_than_errors(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")
    green = next(p for p in c["parties"] if p["group"] == "GRN")
    assert green["registered"]["a"] is None and green["registered"]["change"] is None
    assert green["share"]["points"] is None


def test_county_rows_and_share_moves_in_points(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")
    allegany = next(r for r in c["counties"] if r["county"] == "Allegany")
    assert allegany["registered"]["change"] == -20
    assert allegany["shares"]["DEM"]["points"] == pytest.approx(100 * 180 / 380 - 100 * 200 / 400)
    assert [r["county"] for r in c["counties"]] == ["Allegany", "Kent"]


def test_age_rows_skip_unknown_and_keep_suppressed_cells_empty(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")
    assert [r["band"] for r in c["ages"]] == ["Under 18", "18-24", "25-34"]
    young = c["ages"][0]
    assert young["registered"]["a"] == 40 and young["registered"]["b"] is None
    assert young["registered"]["change"] is None and young["registered"]["pct"] is None
    adults = c["ages"][1]
    assert adults["shares"]["UNA"]["points"] == pytest.approx(100 * 120 / 330 - 30.0)


def test_cohort_survival_leaves_out_the_year_the_earlier_snapshot_was_taken(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")["cohorts"]
    assert [r["year"] for r in c["rows"]] == [1990, 2023]  # 2024 was part-way through
    assert c["total_a"] == 300 and c["total_b"] == 277
    assert c["kept"] == pytest.approx(100 * 277 / 300)
    assert c["rows"][0]["kept"] == pytest.approx(90.0)
    assert c["last_year"] == 2023


def test_the_official_check_compares_changes_and_says_whether_they_agree(conn):
    c = compare.build_comparison(conn, "2024-09-11", "2026-08-12")["official"]
    assert c["file"]["change"] == 90 and c["official"]["change"] == 80 and c["agree"] is True
    assert c["a"] == {"month": "2024-09", "gap_pct": -2.17}


def test_no_official_check_without_cross_checks(conn):
    conn.execute(
        "UPDATE voter_file_snapshots SET meta_json = ? WHERE snapshot_date = '2024-09-11'",
        (json.dumps(meta()),),
    )
    assert compare.build_comparison(conn, "2024-09-11", "2026-08-12")["official"] is None


def test_shared_elections_are_listed(conn):
    e = compare.build_comparison(conn, "2024-09-11", "2026-08-12")["elections"]
    assert e["shared"] == ["2016-PG"]


def test_unknown_snapshot_raises(conn):
    with pytest.raises(KeyError):
        compare.build_comparison(conn, "2020-01-01", "2026-08-12")


# --- against the built database ------------------------------------------------------------------


def _two_snapshots() -> bool:
    if not DB_PATH.exists():
        return False
    c = sqlite3.connect(DB_PATH)
    try:
        return c.execute("SELECT COUNT(*) FROM voter_file_snapshots").fetchone()[0] >= 2
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


needs_two = pytest.mark.skipif(
    not _two_snapshots(), reason="skipjack.db needs two voter file snapshots"
)
client = TestClient(app)


def page(path: str) -> str:
    return re.sub(r"\s+", " ", client.get(path).text)


@needs_two
def test_comparison_page_headline_numbers():
    html = page("/voters/file/compare/")
    assert "September 2024 to August 2026" in html
    assert "This compares totals, not people." in html
    for text in ("4,545,945", "4,561,126", "+15,181", "+80,186", "−65,005"):
        assert text in html
    assert "4,231,403" in html and "4,322,671" in html  # the State Board's monthly reports
    assert "Both sources show active voters rising." in html


@needs_two
def test_party_and_age_sections_use_both_files_party_codes():
    html = page("/voters/file/compare/")
    assert "+59,366" in html  # Unaffiliated, 1,003,986 to 1,063,352
    assert "(OGRN and OWCP)" in html and "(GRN and WCP)" in html
    assert 'href="/voters/file/county/prince-georges/"' in html
    assert "Under 18" in html and "data-sortable" in html


@needs_two
def test_cohort_section_reports_how_many_stayed():
    html = page("/voters/file/compare/")
    assert (
        "4,240,315 voters on the 2024-09-11 list had first registered between 1980 and 2023" in html
    )
    assert "3,971,687 of them (93.7%)" in html
    assert 'id="compare-data"' in html and 'src="/static/compare.js"' in html
    data = json.loads(
        re.search(
            r'id="compare-data" type="application/json">(.*?)</script>',
            client.get("/voters/file/compare/").text,
            re.S,
        ).group(1)
    )
    assert data["years"][0] == 1980 and data["years"][-1] == 2023
    assert all(0 <= v < 20 for v in data["left"])


@needs_two
def test_the_page_says_what_it_cannot_show():
    html = page("/voters/file/compare/")
    assert "Who changed party or who left." in html
    assert "No election is in both, so participation cannot be compared between them." in html
    assert "came without a readme" in html and "2024-PG" in html


@needs_two
def test_pair_urls_and_bad_requests():
    assert client.get("/voters/file/compare/2024-09-11/2026-08-12/").status_code == 200
    assert client.get("/voters/file/compare/2026-08-12/2024-09-11/").status_code == 404
    assert client.get("/voters/file/compare/2020-01-01/2026-08-12/").status_code == 404
    assert client.get("/voters/file/compare/2024-09-11/2030-01-01/").status_code == 404


@needs_two
def test_voter_file_pages_link_to_the_comparison_only_for_the_latest_snapshot():
    assert 'href="/voters/file/compare/"' in page("/voters/file/")
    assert "Compare with September 2024" in page("/voters/file/")
    assert "/voters/file/compare/" not in page("/voters/file/?snapshot=2024-09-11")


@needs_two
def test_older_snapshot_pages_describe_what_the_file_lacks():
    html = page("/voters/file/?snapshot=2024-09-11")
    assert "came without a readme" in html
    assert "2016 General" in html and "2024 General" not in html


@needs_two
def test_data_page_offers_both_snapshots():
    html = page("/data/")
    assert "2024-09-11" in html and "2026-08-12" in html
