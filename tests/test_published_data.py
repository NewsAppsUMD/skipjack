"""Checks on the committed aggregates, which the site publishes.

These run in CI before every deploy. They look at the files that would go public, not at
the code that made them.
"""

from __future__ import annotations

import json

import pytest

from skipjack.web.downloads import download_files
from skipjack.web.paths import DATA_DIR

SNAPSHOTS = sorted(p for p in (DATA_DIR / "voter_file").glob("*") if (p / "snapshot.json").exists())
pytestmark = pytest.mark.skipif(not SNAPSHOTS, reason="no voter file aggregates committed")

PERSONAL = (
    "VTR_ID",
    "LastName",
    "FirstName",
    "MiddleName",
    "BirthDate",
    "HouseNumber",
    "StreetName",
)
METRIC_FILES = [
    f for s in SNAPSHOTS for f in sorted(s.glob("*.json")) if not f.name.startswith("snapshot")
]


@pytest.mark.parametrize("path", METRIC_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_cells_are_suppressed_and_rows_hold_only_declared_columns(path):
    payload = json.loads(path.read_text())
    threshold = payload["suppress_below"]
    allowed = set(payload["dims"]) | set(payload["values"])
    assert threshold >= 10
    for row in payload["rows"]:
        assert set(row) <= allowed, f"undeclared column in {path.name}: {set(row) - allowed}"
        for value in payload["values"]:
            n = row.get(value)
            assert n is None or n == 0 or n >= threshold, f"{path.name}: unsuppressed cell {row}"


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.name)
def test_snapshot_files_hold_no_personal_data_or_file_names(snapshot):
    for path in snapshot.glob("*.json"):
        text = path.read_text()
        for field in PERSONAL:
            assert field not in text, f"{field} appears in {path.name}"
        assert "List_Part" not in text and "Statewide VR List" not in text
        assert "lnash" not in text.lower()  # the requesting agency's staff username


def test_downloads_only_include_expected_folders_and_types():
    files = download_files(DATA_DIR)
    assert files
    assert {f.parts[0] for f in files} <= {"voter_registration", "voter_file"}
    assert {f.suffix for f in files} <= {".csv", ".json"}
