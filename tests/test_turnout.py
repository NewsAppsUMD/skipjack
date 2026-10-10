"""Tests for the turnout pipeline: as-of extraction, parsers, reconciliation, fetching.

The xlsx fixtures are built with openpyxl in the same messy shape the SBE exports use
(blank spacers, labels in the header row); the PDF text tests use the real lines.
"""

from __future__ import annotations

import openpyxl

from skipjack.pipeline import turnout


def test_as_of_text_parses_the_date():
    assert turnout.extract_as_of_text("As of: October 9, 2026 6 PM") == "2026-10-09"
    assert turnout.extract_as_of_text("As of: October 9, 2026") == "2026-10-09"
    assert turnout.extract_as_of_text("As of: December 31, 2025") == "2025-12-31"
    assert turnout.extract_as_of_text("As of: January 2, 2026") == "2026-01-02"


def test_as_of_text_rejects_garbage():
    assert turnout.extract_as_of_text("no date here") is None
    assert turnout.extract_as_of_text("As of: 2026-10-09") is None  # SBE never prints it that way


def test_as_of_xlsx_finds_the_cell(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Absentees_Sent_and_Returned_by_County"  # real sheet names are long
    ws.cell(row=3, column=7, value="As of: October 9, 2026")  # odd position, as SBE writes
    path = tmp_path / "t.xlsx"
    wb.save(path)
    assert turnout.extract_as_of_xlsx(path) == "2026-10-09"


def test_as_of_xlsx_missing(tmp_path):
    wb = openpyxl.Workbook()
    wb.active["A1"] = "nothing"
    path = tmp_path / "t.xlsx"
    wb.save(path)
    assert turnout.extract_as_of_xlsx(path) is None


def test_as_of_xlsx_corrupt_file_returns_none(tmp_path):
    # SBE's own exports can be zip-corrupt; a corrupt file has no date, not an exception.
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"PK\x03\x04 not really a zip")
    assert turnout.extract_as_of_xlsx(path) is None


def test_election_config_matches_the_live_press_room():
    gg26 = turnout.ELECTIONS["GG26"]
    assert gg26.code == "GG26" and gg26.name == "2026 general election"
    assert gg26.election_day == "2026-11-03"
    urls = {(f.family, f.geography, f.kind): f.url for f in gg26.files}
    base = "https://elections.maryland.gov/press_room/2026_stats/GG26"
    assert urls[("requests", "county", "pdf")] == f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"
    for geo in ("county", "congressional", "legislative"):
        assert (
            urls[("sent-returned", geo, "xlsx")]
            == f"{base}/Absentees_Sent_and_Returned_by_{geo.capitalize()}.xlsx"
        )