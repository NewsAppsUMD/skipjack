"""Tests for the precinct results tables and their build-db loader."""

from __future__ import annotations

import csv
import gzip
import io
import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline import build_db, results
from skipjack.pipeline.build_db import ResultsLoadError, load_results_precinct
from skipjack.pipeline.results import RESULTS_ELECTIONS, county_slug

# Not tests.<module>: another project's `tests` package on PYTHONPATH can shadow ours.
from test_results_precinct import fill_precincts


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def precinct_columns(code):
    e = RESULTS_ELECTIONS[code]
    cols = ["election", "county", "party_file", "precinct", "congressional", "legislative"]
    cols += ["office", "district", "candidate", "party", "winner", "write_in"]
    for _, stem in e.precinct_vote_types:
        cols.append(f"{stem}_votes")
        if e.kind == "general":
            cols.append(f"{stem}_against")
    cols.append("total_votes")
    if e.kind == "general":
        cols.append("total_against")
    return cols


def write_precinct(data_dir, code, number, county, party_file, rows, source_id=None, sidecar=True):
    """One gzipped precinct CSV (rows after the election, county and party_file columns)."""
    d = data_dir / "results" / code / "precinct"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{number}-{county_slug(county)}-{party_file}"
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(precinct_columns(code))
    for r in rows:
        w.writerow([code, county, party_file, *r])
    (d / f"{stem}.csv.gz").write_bytes(gzip.compress(text.getvalue().encode()))
    if sidecar:
        (d / f"{stem}_provenance.json").write_text(
            json.dumps(
                {
                    "source_id": source_id or f"sbe-results-{code}-precinct-{number}-{party_file}",
                    "election": code,
                    "family": "results",
                    "geography": "precinct",
                    "source_url": "https://elections.maryland.gov/x.csv",
                    "source_name": "Maryland State Board of Elections",
                    "document_title": "test",
                    "fetch_date": "2026-11-25T12:00:00+00:00",
                    "parse_date": "2026-11-25T12:00:01+00:00",
                    "sha256_original": "0" * 64,
                    "parser_version": "skipjack 0.4.0",
                    "notes": "test",
                    "extraction_method": "csv",
                    "checks": {"rows": len(rows)},
                }
            )  # fmt: skip
        )


# GG18: election-night votes only; [precinct, cong, legs, office, district, candidate, party,
# winner, write_in, election_night_votes, election_night_against, total_votes, total_against]
GG18_ROWS = [
    ["001-001", "01", "36", "Governor / Lt. Governor", "", "Larry Hogan", "REP", 1, 0, 420, "", 420, ""],
    ["001-001", "01", "36", "State Senator", "06", "Sen Six", "DEM", 1, 0, 5, "", 5, ""],
]  # fmt: skip


def test_load_precinct_rows_and_votes(conn, tmp_path):
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS)
    assert load_results_precinct(conn, tmp_path) == 2
    rows = conn.execute(
        "SELECT precinct, congressional, legislative, district, candidate, total_votes, "
        "total_against FROM results_precinct ORDER BY id"
    ).fetchall()
    assert rows[0] == ("001-001", "01", "36", "", "Larry Hogan", 420, None)
    assert rows[1][3] == "6"  # numeric districts drop their zeros, as in results_county
    votes = conn.execute(
        "SELECT vote_type, votes FROM results_precinct_votes ORDER BY result_id"
    ).fetchall()
    assert votes == [("election_night", 420), ("election_night", 5)]
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)


def test_load_precinct_refuses_a_total_that_is_not_the_sum(conn, tmp_path):
    bad = [[*GG18_ROWS[0][:11], 421, ""]]
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", bad)
    with pytest.raises(ResultsLoadError, match="total votes 421"):
        load_results_precinct(conn, tmp_path)


def test_load_precinct_refuses_a_total_against_that_is_not_the_sum(conn, tmp_path):
    bad = [["001-001", "01", "36", "Judge", "", "Jane Roe", "", 1, 0, 5, 2, 5, 3]]
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", bad)
    with pytest.raises(ResultsLoadError, match="total against 3"):
        load_results_precinct(conn, tmp_path)


def test_load_precinct_refuses_a_file_without_its_sidecar_or_with_the_wrong_source(conn, tmp_path):
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS, sidecar=False)
    with pytest.raises(ResultsLoadError, match="no provenance sidecar"):
        load_results_precinct(conn, tmp_path)
    write_precinct(
        tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS, source_id="sbe-results-GG18-county-15-all"
    )
    with pytest.raises(ResultsLoadError, match="expected 'sbe-results-GG18-precinct-15-all'"):
        load_results_precinct(conn, tmp_path)


def test_no_precinct_files_load_nothing(conn, tmp_path):
    assert load_results_precinct(conn, tmp_path) == 0


def test_load_what_the_parser_wrote(conn, env):
    """The parser's own gzipped output (a quoted "" district, the county_source check) loads."""
    fill_precincts(env)
    assert results.results_parse("PG24", data_dir=env / "data", level="precinct") == (24, [])
    assert load_results_precinct(conn, env / "data") == 24 * 4
    row = conn.execute(
        "SELECT precinct, congressional, legislative, district, party, total_votes, "
        "total_against FROM results_precinct WHERE county = 'Kent' ORDER BY id LIMIT 1"
    ).fetchone()
    assert row == ("001-001", "01", "36", "", "DEM", 388, None)
    n = conn.execute("SELECT COUNT(*) FROM results_precinct_votes").fetchone()[0]
    assert n == 24 * 4 * 5
    checks = json.loads(
        conn.execute(
            "SELECT checks_json FROM sources WHERE source_id = 'sbe-results-PG24-precinct-15-all'"
        ).fetchone()[0]
    )
    assert checks["county_source"]["source_id"] == "sbe-results-PG24-county-15-all"


def test_build_counts_the_precinct_rows(tmp_path):
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS)
    db = build_db.build(tmp_path / "x.db", tmp_path)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM results_precinct").fetchone() == (2,)
