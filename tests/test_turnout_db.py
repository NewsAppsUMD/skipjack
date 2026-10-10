"""Tests for the turnout tables: schema, the build-db loader, and its CSV layouts."""

from __future__ import annotations

import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import load_turnout


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


def write_snapshot(data_dir, election, family, geography, as_of, rows, source_id="s"):
    """One snapshot CSV plus its provenance sidecar; rows are turnout_counts rows."""
    d = data_dir / "turnout" / election / family / geography
    d.mkdir(parents=True)
    (d / f"{as_of}.csv").write_text(
        "as_of,election,family,geography,county,district,category,party,measure,value\n"
        + "\n".join(",".join(str(c) for c in row) for row in rows)
        + "\n"
    )
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
        "SELECT county, measure, value FROM turnout_counts WHERE source_id = 'sbe-turnout-gg26-requests' ORDER BY value"
    ).fetchall()
    assert [(r["county"], r["measure"], r["value"]) for r in got] == [
        ("Allegany", "agent", 1),
        ("Allegany", "total", 285),
    ]
