"""Tests for the unofficial results tables and their build-db loader.

The loader is exercised on a copy of a committed snapshot (2026-11-04T160222), so the tests
read what the parse really writes.
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import ResultsLoadError, load_results_unofficial

REPO_DATA = Path(__file__).resolve().parents[1] / "data"
SNAP = "2026-11-04T160222"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


@pytest.fixture
def data(tmp_path):
    src = REPO_DATA / "results_unofficial" / "GG26" / SNAP
    dest = tmp_path / "results_unofficial" / "GG26" / SNAP
    shutil.copytree(src, dest)
    return tmp_path


def snap_dir(data: Path, snap: str = SNAP) -> Path:
    return data / "results_unofficial" / "GG26" / snap


def rewrite(path: Path, change) -> None:
    with path.open() as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0])
    change(rows)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def test_no_data_loads_nothing(conn, tmp_path):
    assert load_results_unofficial(conn, tmp_path) == 0


def test_a_snapshot_loads(conn, data):
    n = load_results_unofficial(conn, data)
    assert n == conn.execute("SELECT COUNT(*) FROM results_unofficial").fetchone()[0]
    assert n > 2000
    counties = conn.execute("SELECT COUNT(DISTINCT county) FROM results_unofficial").fetchone()[0]
    assert counties == 24
    moore = conn.execute(
        """SELECT SUM(total_votes) FROM results_unofficial
           WHERE office = 'Governor / Lt. Governor' AND candidate = 'Wes Moore and Aruna Miller'"""
    ).fetchone()[0]
    assert moore == 1164949  # the board's statewide page at 04:02:22 PM
    # four vote types per row; provisional is not reported anywhere yet
    per_row = conn.execute("SELECT COUNT(*) FROM results_unofficial_votes").fetchone()[0]
    assert per_row == 4 * n
    prov = conn.execute(
        "SELECT COUNT(*) FROM results_unofficial_votes WHERE vote_type = 'provisional' "
        "AND votes IS NOT NULL"
    ).fetchone()[0]
    assert prov == 0
    row = conn.execute(
        "SELECT * FROM results_unofficial WHERE kind = 'question' AND county = 'Allegany' "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    assert row["choice"] == "For the Constitutional Amendment"
    assert row["snapshot"] == SNAP
    assert row["last_refreshed"] == "11/04/2026 04:02:22 PM"
    assert row["text_url"].startswith("https://elections.maryland.gov/")
    src = conn.execute("SELECT * FROM sources WHERE source_id = ?", (row["source_id"],)).fetchone()
    assert src["source_url"].endswith("gen_results_2026_by_county_1.html")
    assert json.loads(src["checks_json"])["unofficial"] is True


def test_the_canvass_status_loads(conn, data):
    load_results_unofficial(conn, data)
    rows = conn.execute(
        "SELECT * FROM results_unofficial_status WHERE snapshot = ? ORDER BY rowid", (SNAP,)
    ).fetchall()
    assert len(rows) == 24
    assert rows[0]["county"] == "Allegany"
    assert {r["prov"] for r in rows} == {"not_reported"}


def test_two_snapshots_load_side_by_side(conn, data):
    later = "2026-11-05T100000"
    shutil.copytree(snap_dir(data), snap_dir(data, later))
    for path in snap_dir(data, later).rglob("*.csv"):
        rewrite(path, lambda rows: [r.update(snapshot=later) for r in rows])
    for side in snap_dir(data, later).rglob("*_provenance.json"):
        side.write_text(side.read_text().replace(SNAP, later))
    n = load_results_unofficial(conn, data)
    snaps = [r[0] for r in conn.execute(
        "SELECT snapshot FROM results_unofficial GROUP BY snapshot ORDER BY snapshot"
    )]  # fmt: skip
    assert snaps == [SNAP, later]
    assert (
        n
        == 2
        * conn.execute(
            "SELECT COUNT(*) FROM results_unofficial WHERE snapshot = ?", (SNAP,)
        ).fetchone()[0]
    )


def test_a_csv_without_its_sidecar_is_refused(conn, data):
    (snap_dir(data) / "county/03-baltimore-city_provenance.json").unlink()
    with pytest.raises(ResultsLoadError, match="no provenance sidecar"):
        load_results_unofficial(conn, data)


def test_a_csv_from_another_snapshot_is_refused(conn, data):
    rewrite(
        snap_dir(data) / "county/05-calvert.csv",
        lambda rows: [r.update(snapshot="2026-11-04T092122") for r in rows],
    )
    with pytest.raises(ResultsLoadError, match="snapshot"):
        load_results_unofficial(conn, data)


def test_a_county_column_that_disagrees_with_the_file_name_is_refused(conn, data):
    rewrite(snap_dir(data) / "county/05-calvert.csv", lambda rows: rows[0].update(county="Kent"))
    with pytest.raises(ResultsLoadError, match="county"):
        load_results_unofficial(conn, data)


def test_a_row_whose_vote_types_do_not_add_up_is_refused(conn, data):
    rewrite(snap_dir(data) / "county/05-calvert.csv", lambda rows: rows[0].update(total="1"))
    with pytest.raises(ResultsLoadError, match="add up"):
        load_results_unofficial(conn, data)


def test_a_dropped_row_is_refused(conn, data):
    rewrite(snap_dir(data) / "county/05-calvert.csv", lambda rows: rows.pop())
    with pytest.raises(ResultsLoadError, match="rows"):
        load_results_unofficial(conn, data)


def test_an_unknown_canvass_state_is_refused(conn, data):
    rewrite(snap_dir(data) / "canvass-status.csv", lambda rows: rows[0].update(prov="maybe"))
    with pytest.raises(ResultsLoadError, match="maybe"):
        load_results_unofficial(conn, data)
