"""Tests for the ballot-question tables and their build-db loader."""

from __future__ import annotations

import csv
import gzip
import io
import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline import build_db, results
from skipjack.pipeline.build_db import ResultsLoadError, load_results_questions
from skipjack.pipeline.results import RESULTS_ELECTIONS, county_slug

# Not tests.<module>: another project's `tests` package on PYTHONPATH can shadow ours.
from test_results_questions import fill_questions


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def question_columns(code, geography):
    """The parser's columns for a question file of this election and geography."""
    e = RESULTS_ELECTIONS[code]
    precinct = geography == "precinct"
    cols = ["election", "county"]
    cols += ["precinct", "congressional", "legislative"] if precinct else []
    cols += ["question", "how_it_runs", "winner"]
    sides = ["votes", "against"] + (["third"] if results.question_third(e) else [])
    for _, stem in e.precinct_vote_types if precinct else e.vote_types:
        cols += [f"{stem}_{side}" for side in sides]
    cols += [f"total_{side}" for side in sides]
    return cols


COUNTY_SHA = "c" * 64


def sidecar(code, number, geography, rows, source_id=None, county_sha=COUNTY_SHA, **extra):
    role = "question" if geography == "county" else "question-precinct"
    checks = {"rows": rows}
    if geography == "precinct":
        checks["county_source"] = {
            "source_id": f"sbe-results-{code}-question-{number}",
            "sha256": county_sha,
        }
    return {
        "source_id": source_id or f"sbe-results-{code}-{role}-{number}",
        "election": code, "family": "questions", "geography": geography,
        "source_url": "https://elections.maryland.gov/x.csv",
        "source_name": "Maryland State Board of Elections", "document_title": "test",
        "fetch_date": "2026-11-25T12:00:00+00:00", "parse_date": "2026-11-25T12:00:01+00:00",
        "sha256_original": COUNTY_SHA if geography == "county" else "0" * 64,
        "parser_version": "skipjack 0.4.0", "notes": "test", "extraction_method": "csv",
        "checks": checks,
    } | extra  # fmt: skip


def write_questions(data_dir, code, number, county, geography, rows, prov=None):
    """A question CSV as the parser writes it, county (plain) or precinct (gzipped), each row a
    dict of the columns after election and county (absent vote columns are blank), with its
    sidecar (``prov`` overrides it; False writes none)."""
    d = data_dir / "results" / code / "questions" / geography
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{number}-{county_slug(county)}"
    cols = question_columns(code, geography)
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(cols)
    for r in rows:
        w.writerow([{"election": code, "county": county, **r}.get(c, "") for c in cols])
    data = text.getvalue().encode()
    path = d / (f"{stem}.csv" if geography == "county" else f"{stem}.csv.gz")
    path.write_bytes(data if geography == "county" else gzip.compress(data))
    if prov is not False:
        prov = prov or sidecar(code, number, geography, len(rows))
        (d / f"{stem}_provenance.json").write_text(json.dumps(prov))
    return path


def votes(stems, side_values: dict[str, list]) -> dict:
    """{stem_side: value} for each side's list of values, one per stem."""
    return {f"{s}_{side}": v for side, vals in side_values.items() for s, v in zip(stems, vals)}


GG18_STEMS = [s for _, s in RESULTS_ELECTIONS["GG18"].vote_types]
# Kent GG18 question 01: For 6857, Against 964, no third choice
KENT_GG18 = {
    "question": "01", "how_it_runs": "State", "winner": 1,
    **votes(GG18_STEMS, {"votes": [2409, 4078, 178, 80, 112], "against": [290, 628, 31, 6, 9]}),
    "total_votes": 6857, "total_against": 964,
}  # fmt: skip
KENT_GG18_PRECINCT = {
    "precinct": "001-001", "congressional": "01", "legislative": "36", "question": "01",
    "how_it_runs": "State", "winner": 0, "election_night_votes": 4078,
    "election_night_against": 628, "total_votes": 4078, "total_against": 628,
}  # fmt: skip
# Wicomico PG16 question A: the third choice filled
PG16_STEMS = [s for _, s in RESULTS_ELECTIONS["PG16"].vote_types]
WICOMICO_A = {
    "question": "A", "how_it_runs": "County", "winner": 1,
    **votes(PG16_STEMS, {"votes": [2881, 6289, 383, 153, 92], "against": [5319, 13195, 719, 213, 175],
                         "third": [2253, 6514, 427, 182, 137]}),
    "total_votes": 9798, "total_against": 19621, "total_third": 9513,
}  # fmt: skip


def test_load_questions_at_both_geographies(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [KENT_GG18_PRECINCT])
    assert load_results_questions(conn, tmp_path) == 2
    rows = conn.execute(
        "SELECT geography, county, precinct, congressional, question, how_it_runs, winner, "
        "total_votes, total_against, total_third FROM results_questions ORDER BY id"
    ).fetchall()
    assert rows == [
        ("county", "Kent", None, None, "1", "State", 1, 6857, 964, None),
        ("precinct", "Kent", "001-001", "01", "1", "State", 0, 4078, 628, None),
    ]
    assert conn.execute("SELECT COUNT(*) FROM results_question_votes").fetchone() == (6,)
    assert conn.execute(
        "SELECT vote_type, votes, votes_against, votes_third FROM results_question_votes "
        "WHERE question_id = 2"
    ).fetchall() == [("election_night", 4078, 628, None)]
    sources = {r[0] for r in conn.execute("SELECT source_id FROM sources")}
    assert sources == {"sbe-results-GG18-question-15", "sbe-results-GG18-question-precinct-15"}


def test_load_the_third_choice(conn, tmp_path):
    write_questions(tmp_path, "PG16", "23", "Wicomico", "county", [WICOMICO_A])
    assert load_results_questions(conn, tmp_path) == 1
    assert conn.execute(
        "SELECT question, total_votes, total_against, total_third FROM results_questions"
    ).fetchone() == ("A", 9798, 19621, 9513)
    assert conn.execute(
        "SELECT vote_type, votes_third FROM results_question_votes ORDER BY rowid LIMIT 2"
    ).fetchall() == [("early", 2253), ("election_night", 6514)]


def test_a_coded_layout_question_has_no_third_choice(conn, tmp_path):
    stems = [s for _, s in RESULTS_ELECTIONS["PG24"].vote_types]
    row = {
        "question": "1", "how_it_runs": "State", "winner": 1,
        **votes(stems, {"votes": [2454, 3243, 801, 272, 1003], "against": [1047, 1534, 132, 70, 154]}),
        "total_votes": 7773, "total_against": 2937,
    }  # fmt: skip
    write_questions(tmp_path, "PG24", "15", "Kent", "county", [row])
    assert load_results_questions(conn, tmp_path) == 1
    assert conn.execute("SELECT total_third FROM results_questions").fetchone() == (None,)
    assert conn.execute(
        "SELECT COUNT(*) FROM results_question_votes WHERE votes_third IS NULL"
    ).fetchone() == (5,)


def test_question_numbers_drop_leading_zeros_only_when_numeric(conn, tmp_path):
    garrett = {**KENT_GG18, "question": "A 002 000", "how_it_runs": "County"}
    write_questions(tmp_path, "GG18", "12", "Garrett", "county", [KENT_GG18, garrett])
    load_results_questions(conn, tmp_path)
    assert [r[0] for r in conn.execute("SELECT question FROM results_questions ORDER BY id")] == [
        "1",
        "A 002 000",
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"total_votes": 6858}, "question 01: total votes 6858 != sum of vote types 6857"),
        ({"total_against": 965}, "question 01: total against 965 != sum of vote types 964"),
        ({"total_third": 1}, "question 01: total third 1 != sum of vote types None"),
        ({"early_third": 1}, "question 01: total third None != sum of vote types 1"),
        ({"early_votes": ""}, "question 01: blank early_votes"),
        ({"winner": ""}, "question 01: blank winner"),
    ],
)
def test_load_questions_refuses_rows_that_do_not_add_up(conn, tmp_path, change, message):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [{**KENT_GG18, **change}])
    with pytest.raises(ResultsLoadError, match=message):
        load_results_questions(conn, tmp_path)


def test_a_zero_third_total_over_blank_cells_loads(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [{**KENT_GG18, "total_third": 0}])
    assert load_results_questions(conn, tmp_path) == 1
    assert conn.execute("SELECT total_third FROM results_questions").fetchone() == (0,)


def test_the_precinct_total_third_is_its_election_night_third(conn, tmp_path):
    row = {**KENT_GG18_PRECINCT, "election_night_third": 13, "total_third": 13}
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [row])
    assert load_results_questions(conn, tmp_path) == 2
    assert conn.execute(
        "SELECT total_third FROM results_questions WHERE geography = 'precinct'"
    ).fetchone() == (13,)


def test_load_questions_refuses_a_file_without_its_sidecar_or_with_the_wrong_one(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18], prov=False)
    with pytest.raises(ResultsLoadError, match="no provenance sidecar"):
        load_results_questions(conn, tmp_path)
    prov = sidecar("GG18", "15", "county", 1, source_id="sbe-results-GG18-county-15-all")
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18], prov=prov)
    with pytest.raises(ResultsLoadError, match="expected 'sbe-results-GG18-question-15'"):
        load_results_questions(conn, tmp_path)
    prov = sidecar("GG18", "15", "county", 1, family="results")
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18], prov=prov)
    with pytest.raises(ResultsLoadError, match="family 'results', geography 'county'"):
        load_results_questions(conn, tmp_path)
    prov = sidecar("GG18", "15", "county", 2)
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18], prov=prov)
    with pytest.raises(ResultsLoadError, match="1 data rows but the sidecar's checks say 2"):
        load_results_questions(conn, tmp_path)


def test_load_questions_refuses_another_countys_rows(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [{**KENT_GG18, "county": "Somerset"}])
    with pytest.raises(ResultsLoadError, match=r"county column holds \['Somerset'\] but the file is 'Kent'"):  # fmt: skip
        load_results_questions(conn, tmp_path)


def test_load_questions_refuses_a_precinct_that_is_not_ddd_ppp(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    bad = {**KENT_GG18_PRECINCT, "precinct": "1-1"}
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [bad])
    with pytest.raises(ResultsLoadError, match="is not DDD-PPP"):
        load_results_questions(conn, tmp_path)


def test_a_precinct_question_file_loads_only_beside_its_county_revision(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [KENT_GG18_PRECINCT])
    with pytest.raises(ResultsLoadError, match="county file .*15-kent.csv .* is absent"):
        load_results_questions(conn, tmp_path)
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    prov = sidecar("GG18", "15", "precinct", 1, county_sha="a" * 64)
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [KENT_GG18_PRECINCT], prov=prov)
    with pytest.raises(
        ResultsLoadError,
        match="checked against county revision aaaaaaaaaaaa but the county file is cccccccccccc; "
        "re-run skipjack results-parse",
    ):
        load_results_questions(conn, tmp_path)


def test_no_question_files_load_nothing(conn, tmp_path):
    assert load_results_questions(conn, tmp_path) == 0


def test_the_candidate_loaders_do_not_read_the_question_files(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct", [KENT_GG18_PRECINCT])
    assert build_db.load_results(conn, tmp_path) == 0
    assert build_db.load_results_precinct(conn, tmp_path) == 0


def test_build_counts_the_question_rows(tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county", [KENT_GG18])
    db = build_db.build(tmp_path / "x.db", tmp_path)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM results_questions").fetchone() == (1,)


def test_load_what_the_parser_wrote(conn, env):
    fill_questions(env)
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (48, [])
    assert load_results_questions(conn, env / "data") == 24 + 24 * 2
    assert conn.execute(
        "SELECT precinct, question, total_votes, total_against FROM results_questions "
        "WHERE county = 'Kent' AND geography = 'precinct' ORDER BY id LIMIT 1"
    ).fetchone() == ("001-001", "1", 7070, 2850)
    checks = json.loads(
        conn.execute(
            "SELECT checks_json FROM sources "
            "WHERE source_id = 'sbe-results-PG24-question-precinct-15'"
        ).fetchone()[0]
    )
    assert checks["county_source"]["source_id"] == "sbe-results-PG24-question-15"
