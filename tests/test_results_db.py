"""Tests for the results tables: schema and the build-db loader for the county CSVs."""

from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import ResultsLoadError, load_results
from skipjack.pipeline.results import RESULTS_ELECTIONS, county_slug


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def columns_for(code):
    e = RESULTS_ELECTIONS[code]
    cols = ["election", "county", "party_file", "office", "district", "candidate", "party"]
    cols += ["winner", "write_in"]
    for _, stem in e.vote_types:
        cols.append(f"{stem}_votes")
        if e.kind == "general":
            cols.append(f"{stem}_against")
    cols.append("total_votes")
    if e.kind == "general":
        cols.append("total_against")
    return cols


def write_county(
    data_dir,
    code,
    number,
    county,
    party_file,
    rows,
    sidecar=True,
    election=None,
    csv_county=None,
    checks=None,
    source_id=None,
):
    """One county CSV (rows are lists in the election's column order after the id columns)."""
    d = data_dir / "results" / code / "county"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{number}-{county_slug(county)}-{party_file}"
    with (d / f"{stem}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns_for(code))
        for r in rows:
            w.writerow([election or code, csv_county or county, party_file, *r])
    if sidecar:
        (d / f"{stem}_provenance.json").write_text(
            json.dumps(
                {
                    "source_id": source_id or f"sbe-results-{code}-county-{number}-{party_file}",
                    "election": code,
                    "family": "results",
                    "geography": "county",
                    "source_url": "https://elections.maryland.gov/x.csv",
                    "source_name": "Maryland State Board of Elections",
                    "document_title": "test",
                    "fetch_date": "2026-10-10T12:00:00+00:00",
                    "parse_date": "2026-10-10T12:00:01+00:00",
                    "sha256_original": "0" * 64,
                    "parser_version": "skipjack 0.2.0",
                    "notes": "test",
                    "extraction_method": "csv",
                    "checks": checks or {},
                }
            )
        )


# GP26 vote types: early, election_night, mail_in_1, provisional, mail_in_2
PRIMARY = [
    [
        "Governor / Lt. Governor",
        "",
        "Wes Moore and Aruna Miller",
        "DEM",
        1,
        0,
        10,
        20,
        30,
        4,
        5,
        69,
    ],
    ["U.S. Congress", "06", "April McClain Delaney", "DEM", 1, 0, 1, 1, 1, 1, 1, 5],
    ["U.S. Congress", "1A", "Someone", "DEM", 0, 0, 0, 0, 0, 0, 0, 0],
]
# PG24 vote types add an "against" column after each; judges carry them
GENERAL = [
    ["President - Vice Pres", "", "Kamala D. Harris and Tim Walz", "DEM", 1, 0,
     1, "", 2, "", 3, "", 1, "", 3, "", 10, ""],
    ["President - Vice Pres", "", "Write-In", "", 0, 1, 1, "", 0, "", 0, "", 0, "", 0, "", 1, ""],
    ["Judge Court of Appeals", "01", "Jane Roe", "BOT", 1, 0,
     3, 1, 3, 1, 3, 1, 0, 0, 1, 0, 10, 3],
]  # fmt: skip


def test_schema_has_results_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"results_county", "results_votes"} <= tables
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_results_county_contest", "idx_results_county_county"} <= indexes
    assert "idx_results_votes_result" in indexes


def test_no_results_directory_loads_nothing(conn, tmp_path):
    assert load_results(conn, tmp_path) == 0


def test_loads_primary_and_melts_vote_types(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY)
    assert load_results(conn, tmp_path) == 3
    row = conn.execute("SELECT * FROM results_county WHERE candidate LIKE 'Wes%'").fetchone()
    assert row["election"] == "GP26" and row["county"] == "Allegany"
    assert row["party_file"] == "dem" and row["winner"] == 1 and row["write_in"] == 0
    assert row["total_votes"] == 69 and row["total_against"] is None
    assert row["source_id"] == "sbe-results-GP26-county-01-dem"
    votes = conn.execute(
        "SELECT vote_type, votes, votes_against FROM results_votes WHERE result_id = ? "
        "ORDER BY rowid",
        (row["id"],),
    ).fetchall()
    assert [tuple(v) for v in votes] == [
        ("early", 10, None),
        ("election_night", 20, None),
        ("mail_in_1", 30, None),
        ("provisional", 4, None),
        ("mail_in_2", 5, None),
    ]
    assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


def test_votes_sum_to_total_votes_per_row(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY)
    write_county(tmp_path, "PG24", "03", "Baltimore City", "all", GENERAL)
    load_results(conn, tmp_path)
    bad = conn.execute(
        """SELECT c.id FROM results_county c JOIN results_votes v ON v.result_id = c.id
           GROUP BY c.id HAVING sum(v.votes) != c.total_votes"""
    ).fetchall()
    assert bad == []
    assert conn.execute("SELECT count(*) FROM results_county").fetchone()[0] == 6


def test_loads_general_with_against_and_write_in(conn, tmp_path):
    write_county(tmp_path, "PG24", "03", "Baltimore City", "all", GENERAL)
    assert load_results(conn, tmp_path) == 3
    wi = conn.execute("SELECT * FROM results_county WHERE candidate = 'Write-In'").fetchone()
    assert wi["write_in"] == 1 and wi["winner"] == 0 and wi["party"] == ""
    judge = conn.execute("SELECT * FROM results_county WHERE office LIKE 'Judge%'").fetchone()
    assert judge["total_against"] == 3
    votes = conn.execute(
        "SELECT vote_type, votes_against FROM results_votes WHERE result_id = ? ORDER BY rowid",
        (judge["id"],),
    ).fetchall()
    assert [tuple(v) for v in votes] == [
        ("early", 1), ("election_night", 1), ("mail_in_1", 1), ("provisional", 0), ("mail_in_2", 0),
    ]  # fmt: skip


def test_blank_against_is_null_and_zero_stays_zero(conn, tmp_path):
    write_county(tmp_path, "PG24", "03", "Baltimore City", "all", GENERAL)
    load_results(conn, tmp_path)
    pres = conn.execute(
        "SELECT v.votes_against FROM results_votes v JOIN results_county c ON c.id = v.result_id "
        "WHERE c.candidate LIKE 'Kamala%'"
    ).fetchall()
    assert [r[0] for r in pres] == [None] * 5
    zero = conn.execute(
        "SELECT v.votes_against FROM results_votes v JOIN results_county c ON c.id = v.result_id "
        "WHERE c.candidate = 'Jane Roe' AND v.vote_type = 'provisional'"
    ).fetchone()
    assert zero[0] == 0


def test_districts_are_text_and_normalized(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY)
    load_results(conn, tmp_path)
    got = {
        r["candidate"]: (r["district"], type(r["district"]))
        for r in conn.execute("SELECT candidate, district FROM results_county")
    }
    assert got["April McClain Delaney"] == ("6", str)  # "06" -> "6"
    assert got["Someone"] == ("1A", str)
    assert got["Wes Moore and Aruna Miller"] == ("", str)  # blank is '' not NULL


def test_zero_padded_and_plain_districts_meet(conn, tmp_path):
    padded = [["U.S. Congress", "01", "A B", "DEM", 1, 0, 1, 0, 0, 0, 0, 1]]
    plain = [["U.S. Congress", "1", "A B", "DEM", 1, 0, 2, 0, 0, 0, 0, 2]]
    write_county(tmp_path, "PP24", "10", "Dorchester", "dem", padded)
    write_county(tmp_path, "PP24", "05", "Calvert", "dem", plain)
    load_results(conn, tmp_path)
    rows = conn.execute(
        "SELECT district, sum(total_votes) FROM results_county GROUP BY 1"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("1", 3)]


def test_rejects_a_csv_without_sidecar(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY, sidecar=False)
    with pytest.raises(ResultsLoadError, match="no provenance sidecar"):
        load_results(conn, tmp_path)


def test_rejects_election_mismatch(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY, election="GP22")
    with pytest.raises(ResultsLoadError, match="election column"):
        load_results(conn, tmp_path)


def test_rejects_county_mismatch(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY, csv_county="Kent")
    with pytest.raises(ResultsLoadError, match="county column"):
        load_results(conn, tmp_path)


def test_rejects_unknown_election(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY)
    (tmp_path / "results" / "GP26").rename(tmp_path / "results" / "XX99")
    with pytest.raises(ResultsLoadError, match="unknown election"):
        load_results(conn, tmp_path)


def test_rejects_total_that_is_not_the_sum(conn, tmp_path):
    bad = [["U.S. Congress", "", "A B", "DEM", 1, 0, 1, 0, 0, 0, 0, 9]]
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", bad)
    with pytest.raises(ResultsLoadError, match="sum of vote types"):
        load_results(conn, tmp_path)


def test_rejects_row_count_that_disagrees_with_the_sidecar(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY, checks={"rows": 2})
    with pytest.raises(ResultsLoadError, match=r"01-allegany-dem\.csv.*3 data rows"):
        load_results(conn, tmp_path)


def test_accepts_matching_row_count(conn, tmp_path):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY, checks={"rows": 3})
    assert load_results(conn, tmp_path) == 3


def test_rejects_sidecar_for_another_source(conn, tmp_path):
    write_county(
        tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY,
        source_id="sbe-results-GP26-county-02-dem",
    )  # fmt: skip
    with pytest.raises(ResultsLoadError, match="source_id"):
        load_results(conn, tmp_path)


def test_rejects_votes_against_that_do_not_sum_to_total_against(conn, tmp_path):
    bad = [
        [
            "Judge Court of Appeals",
            "01",
            "Jane Roe",
            "BOT",
            1,
            0,
            3,
            1,
            3,
            1,
            3,
            1,
            0,
            0,
            1,
            0,
            10,
            4,
        ]
    ]
    write_county(tmp_path, "PG24", "03", "Baltimore City", "all", bad)
    with pytest.raises(ResultsLoadError, match="against"):
        load_results(conn, tmp_path)


@pytest.mark.parametrize("column", ["winner", "write_in", "total_votes"])
def test_blank_required_cell_names_file_and_row(conn, tmp_path, column):
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", PRIMARY)
    path = tmp_path / "results" / "GP26" / "county" / "01-allegany-dem.csv"
    lines = path.read_text().splitlines()
    header = lines[0].split(",")
    cells = lines[2].split(",")
    cells[header.index(column)] = ""
    lines[2] = ",".join(cells)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ResultsLoadError, match=rf"01-allegany-dem\.csv.*blank {column}.*row 3"):
        load_results(conn, tmp_path)


def test_non_ascii_digit_district_is_left_alone(conn, tmp_path):
    from skipjack.pipeline.build_db import _normalize_district

    assert _normalize_district("\u00b2") == "\u00b2"
    assert _normalize_district("00") == "0"
    assert _normalize_district("01") == "1" and _normalize_district("1A") == "1A"
    rows = [["U.S. Congress", "\u00b2", "A B", "DEM", 1, 0, 1, 0, 0, 0, 0, 1]]
    write_county(tmp_path, "GP26", "01", "Allegany", "dem", rows)
    assert load_results(conn, tmp_path) == 1


# GG10 vote types: early, election_night, absentees, provisional, absentees_2, absentees_3, each
# with an against column
GG10_GENERAL = [
    ["Governor / Lt. Governor", "", "Martin O'Malley", "DEM", 1, 0,
     10, "", 20, "", 3, "", 4, "", 1, "", 2, "", 40, ""],
    ["Judge Court of Appeals", "01", "Judge Jane", "", 1, 0,
     3, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 6, 2],
]  # fmt: skip


def test_loads_2010_general_with_six_vote_types(conn, tmp_path):
    assert len(columns_for("GG10")) == 9 + 12 + 2
    write_county(tmp_path, "GG10", "01", "Allegany", "all", GG10_GENERAL)
    assert load_results(conn, tmp_path) == 2
    judge = conn.execute("SELECT * FROM results_county WHERE office LIKE 'Judge%'").fetchone()
    assert judge["total_votes"] == 6 and judge["total_against"] == 2
    votes = conn.execute(
        "SELECT vote_type, votes, votes_against FROM results_votes WHERE result_id = ? "
        "ORDER BY rowid",
        (judge["id"],),
    ).fetchall()
    assert [tuple(v) for v in votes] == [
        ("early", 3, 1), ("election_night", 3, 1), ("absentees", 0, 0), ("provisional", 0, 0),
        ("absentees_2", 0, 0), ("absentees_3", 0, 0),
    ]  # fmt: skip
    gov = conn.execute("SELECT * FROM results_county WHERE candidate LIKE 'Martin%'").fetchone()
    assert gov["total_against"] is None
    third = conn.execute(
        "SELECT votes, votes_against FROM results_votes WHERE result_id = ? "
        "AND vote_type = 'absentees_3'",
        (gov["id"],),
    ).fetchone()
    assert tuple(third) == (2, None)


# Baltimore City's 2010 general: the SBE prints a Total Votes below the sum on 15 write-in rows
# (results.ROW_TOTAL_EXCEPTIONS); the parser keeps the printed total and lists each row in the
# sidecar's checks.row_total_exceptions, and the loader accepts exactly those rows
JAFFE = ["Governor / Lt. Governor", "", "Ralph Jaffe", "DEM", 0, 1,
         9, "", 60, "", 1, "", 1, "", 0, "", 0, "", 62, ""]  # fmt: skip
JAFFE_EXCEPTION = {
    "office": "Governor / Lt. Governor",
    "district": "",
    "candidate": "Ralph Jaffe",
    "total_votes": 62,
    "sum_of_vote_types": 71,
}


def test_loads_a_listed_row_total_exception_with_the_printed_total(conn, tmp_path):
    write_county(
        tmp_path, "GG10", "03", "Baltimore City", "all", [*GG10_GENERAL, JAFFE],
        checks={"rows": 3, "row_total_exceptions": [JAFFE_EXCEPTION]},
    )  # fmt: skip
    assert load_results(conn, tmp_path) == 3
    jaffe = conn.execute("SELECT * FROM results_county WHERE candidate = 'Ralph Jaffe'").fetchone()
    assert jaffe["total_votes"] == 62 and jaffe["write_in"] == 1
    votes = conn.execute(
        "SELECT SUM(votes) FROM results_votes WHERE result_id = ?", (jaffe["id"],)
    ).fetchone()[0]
    assert votes == 71


def test_rejects_a_row_total_mismatch_the_sidecar_does_not_list(conn, tmp_path):
    write_county(tmp_path, "GG10", "03", "Baltimore City", "all", [*GG10_GENERAL, JAFFE])
    with pytest.raises(
        ResultsLoadError, match="Ralph Jaffe: total votes 62 != sum of vote types 71"
    ):
        load_results(conn, tmp_path)
    # listed with other figures
    other = {**JAFFE_EXCEPTION, "sum_of_vote_types": 72}
    write_county(
        tmp_path, "GG10", "03", "Baltimore City", "all", [*GG10_GENERAL, JAFFE],
        checks={"row_total_exceptions": [other]},
    )  # fmt: skip
    with pytest.raises(
        ResultsLoadError, match="Ralph Jaffe: total votes 62 != sum of vote types 71"
    ):
        load_results(conn, tmp_path)


def test_rejects_a_listed_row_total_exception_in_a_file_the_registry_does_not_name(conn, tmp_path):
    write_county(
        tmp_path, "GG10", "01", "Allegany", "all", [*GG10_GENERAL, JAFFE],
        checks={"row_total_exceptions": [JAFFE_EXCEPTION]},
    )  # fmt: skip
    with pytest.raises(ResultsLoadError, match="01-allegany-all.csv.*no row-total exceptions"):
        load_results(conn, tmp_path)


def test_rejects_a_sidecar_listing_a_row_total_exception_the_file_lacks(conn, tmp_path):
    write_county(
        tmp_path, "GG10", "03", "Baltimore City", "all", GG10_GENERAL,
        checks={"row_total_exceptions": [JAFFE_EXCEPTION]},
    )  # fmt: skip
    with pytest.raises(
        ResultsLoadError, match="lists a row-total exception.*Ralph Jaffe.*no such row"
    ):
        load_results(conn, tmp_path)
