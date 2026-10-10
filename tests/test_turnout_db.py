"""Tests for the turnout tables: schema, the build-db loader, and its CSV layouts."""

from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import TurnoutLoadError, load_turnout


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def test_schema_has_turnout_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "turnout_sources" in tables
    assert "turnout_counts" in tables


COLUMNS = "as_of,election,family,geography,county,district,category,party,measure,value"


def write_snapshot(data_dir, election, family, geography, as_of, rows, source_id="s", sidecar=True):
    """One snapshot CSV plus its provenance sidecar; rows are turnout_counts rows."""
    d = data_dir / "turnout" / election / family / geography
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{as_of}.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS.split(","))
        writer.writerows(rows)
    if not sidecar:
        return
    (d / f"{as_of}_provenance.json").write_text(
        json.dumps(
            {
                "source_id": source_id,
                "election": election,
                "family": family,
                "geography": geography,
                "source_url": "https://elections.maryland.gov/x.pdf",
                "source_name": "Maryland State Board of Elections",
                "document_title": "Mail-in Ballot Request Counts - GG26",
                "fetch_date": "2026-10-10T12:00:00+00:00",
                "parse_date": "2026-10-10T12:00:01+00:00",
                "sha256_original": "0" * 64,
                "parser_version": "skipjack 0.2.0",
                "notes": "test",
                "extraction_method": "test",
                "checks": {},
            }
        )
    )


def test_load_turnout(conn, tmp_path):
    rows = [
        ("2026-10-09", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "agent", 1),
        ("2026-10-09", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 285),
    ]
    write_snapshot(
        tmp_path, "GG26", "requests", "county", "2026-10-09", rows, "sbe-turnout-gg26-requests"
    )
    n = load_turnout(conn, tmp_path)
    assert n == 2
    src = conn.execute(
        "SELECT * FROM turnout_sources WHERE source_id = 'sbe-turnout-gg26-requests'"
    ).fetchone()
    assert (
        src["election"] == "GG26" and src["family"] == "requests" and src["as_of"] == "2026-10-09"
    )
    got = conn.execute(
        "SELECT county, measure, value FROM turnout_counts "
        "WHERE source_id = 'sbe-turnout-gg26-requests' AND district IS NULL ORDER BY value"
    ).fetchall()
    assert [(r["county"], r["measure"], r["value"]) for r in got] == [
        ("Allegany", "agent", 1),
        ("Allegany", "total", 285),
    ]


def test_load_turnout_district_rows_have_a_null_county(conn, tmp_path):
    rows = [
        (
            "2026-10-09",
            "GG26",
            "sent-returned",
            "congressional",
            "",
            "Congressional District 1",
            "ALL",
            "DEM",
            "sent",
            12,
        )
    ]
    write_snapshot(tmp_path, "GG26", "sent-returned", "congressional", "2026-10-09", rows)
    assert load_turnout(conn, tmp_path) == 1
    got = conn.execute(
        "SELECT county, district FROM turnout_counts "
        "WHERE county IS NULL AND district = 'Congressional District 1'"
    ).fetchall()
    assert len(got) == 1


def test_load_turnout_keeps_the_county_in_a_quoted_cell(conn, tmp_path):
    rows = [
        (
            "2026-10-09",
            "GG26",
            "requests",
            "county",
            "Baltimore, City",
            "",
            "ALL",
            "ALL",
            "total",
            285,
        )
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows)
    assert load_turnout(conn, tmp_path) == 1
    got = conn.execute("SELECT county FROM turnout_counts").fetchall()
    assert [r["county"] for r in got] == ["Baltimore, City"]


def test_load_turnout_two_snapshots_replace_their_source_rows(conn, tmp_path):
    def rows_for(as_of, total):
        return [(as_of, "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", total)]

    write_snapshot(
        tmp_path, "GG26", "requests", "county", "2026-10-08", rows_for("2026-10-08", 250), "rev-1"
    )
    write_snapshot(
        tmp_path, "GG26", "requests", "county", "2026-10-09", rows_for("2026-10-09", 285), "rev-2"
    )
    assert load_turnout(conn, tmp_path) == 2
    srcs = conn.execute("SELECT source_id, as_of FROM turnout_sources ORDER BY as_of").fetchall()
    assert [(r["source_id"], r["as_of"]) for r in srcs] == [
        ("rev-1", "2026-10-08"),
        ("rev-2", "2026-10-09"),
    ]
    # loading the same snapshot again replaces its source row, never duplicates it
    write_snapshot(
        tmp_path, "GG26", "requests", "county", "2026-10-08", rows_for("2026-10-08", 250), "rev-1"
    )
    load_turnout(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM turnout_sources").fetchone()[0] == 2


def test_load_turnout_rejects_rows_contradicting_the_file_name(conn, tmp_path):
    rows = [
        ("2026-10-08", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 285)
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows)
    with pytest.raises(TurnoutLoadError) as e:
        load_turnout(conn, tmp_path)
    assert "2026-10-08" in str(e.value) and "2026-10-09" in str(e.value)


def test_load_turnout_rejects_rows_contradicting_the_sidecar(conn, tmp_path):
    rows = [
        ("2026-10-09", "GP26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 285)
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows)  # sidecar says GG26
    with pytest.raises(TurnoutLoadError) as e:
        load_turnout(conn, tmp_path)
    assert "election" in str(e.value) and "GP26" in str(e.value) and "GG26" in str(e.value)


def test_load_turnout_rejects_a_mixed_election_column(conn, tmp_path):
    rows = [
        ("2026-10-09", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 285),
        ("2026-10-09", "GP26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 100),
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows)
    with pytest.raises(TurnoutLoadError) as e:
        load_turnout(conn, tmp_path)
    assert "election" in str(e.value)


def test_load_turnout_rejects_a_snapshot_without_a_sidecar(conn, tmp_path):
    rows = [
        ("2026-10-09", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", 285)
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows, sidecar=False)
    with pytest.raises(TurnoutLoadError) as e:
        load_turnout(conn, tmp_path)
    assert "2026-10-09" in str(e.value)


def test_load_turnout_rejects_a_non_integer_value(conn, tmp_path):
    rows = [
        ("2026-10-09", "GG26", "requests", "county", "Allegany", "", "ALL", "ALL", "total", "NA")
    ]
    write_snapshot(tmp_path, "GG26", "requests", "county", "2026-10-09", rows)
    with pytest.raises(TurnoutLoadError) as e:
        load_turnout(conn, tmp_path)
    assert "2026-10-09" in str(e.value)
