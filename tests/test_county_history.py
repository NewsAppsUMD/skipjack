"""A county's whole record in one document: registration, turnout and results."""

from __future__ import annotations

import json
import sqlite3

import pytest

from skipjack.pipeline.results import COUNTY_CODES
from skipjack.web.county_history import county_document
from skipjack.web.db import DB_PATH, get_db
from skipjack.web.urls import slugify


def _built() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM results_county").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


needs_db = pytest.mark.skipif(not _built(), reason="skipjack.db not built")


@needs_db
def test_an_unknown_county_has_no_document():
    with get_db() as conn:
        assert county_document(conn, "atlantis") is None


@needs_db
def test_the_document_has_every_section_and_is_plain_json():
    with get_db() as conn:
        doc = county_document(conn, "st-marys")
    assert doc["county"] == "St. Mary's" and doc["slug"] == "st-marys"
    assert json.loads(json.dumps(doc)) == doc  # nothing but JSON types

    reg = doc["registration"]
    assert list(reg[0]) == ["report_date", "county", "party", "party_name", "active_voters"]
    assert reg[0]["report_date"] <= reg[-1]["report_date"]
    assert {r["county"] for r in reg} == {"St. Mary's"}

    turnout = doc["turnout"]
    assert {r["geography"] for r in turnout} == {"county"}
    # one snapshot per (election, family): the newest
    snapshots = {(r["election"], r["family"], r["as_of"]) for r in turnout}
    assert len(snapshots) == len({(e, f) for e, f, _ in snapshots})
    # a certified election's unofficial early-voting tallies are never published
    assert ("GG22", "early-voting") not in {(e, f) for e, f, _ in snapshots}

    results = doc["results"]
    assert {"GG22", "GG10"} <= {r["election"] for r in results}
    assert "total_votes" in results[0]

    assert doc["results_over_time"]["county"] == "St. Mary's"


@needs_db
def test_every_county_has_all_three_row_sections():
    with get_db() as conn:
        for name in COUNTY_CODES.values():
            doc = county_document(conn, slugify(name))
            assert doc and doc["registration"] and doc["turnout"] and doc["results"], name
