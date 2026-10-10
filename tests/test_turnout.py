"""Tests for the turnout pipeline: as-of extraction, parsers, reconciliation, fetching.

The xlsx fixtures are built with openpyxl in the same messy shape the SBE exports use
(blank spacers, labels in the header row); the PDF text tests use the real lines.
"""

from __future__ import annotations

import openpyxl
import polars as pl
import pytest

from skipjack.pipeline import turnout


def test_as_of_text_parses_the_date():
    assert turnout.extract_as_of_text("As of: October 9, 2026 6 PM") == "2026-10-09"
    assert turnout.extract_as_of_text("As of: October 9, 2026") == "2026-10-09"
    assert turnout.extract_as_of_text("As of: December 31, 2025") == "2025-12-31"
    assert turnout.extract_as_of_text("As of: January 2, 2026") == "2026-01-02"


def test_as_of_text_rejects_garbage():
    assert turnout.extract_as_of_text("no date here") is None
    assert turnout.extract_as_of_text("As of: 2026-10-09") is None  # SBE never prints it that way


def test_as_of_text_rejects_unparseable_dates():
    assert turnout.extract_as_of_text("As of: Sept 30, 2026") is None  # abbreviated month, regex still matches
    assert turnout.extract_as_of_text("As of: October 32, 2026") is None  # no such day


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


def test_as_of_xlsx_multi_line_cell(tmp_path):
    # The real cell is multi-line: a "6 PM" suffix and a note follow the date on later lines,
    # so the find-anywhere semantics (search, not a whole-string match) are load-bearing.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Absentees_Sent_and_Returned_by_County"
    ws.cell(
        row=2,
        column=5,
        value="As of: October 9, 2026 6 PM\nNote: Data includes Print at Home ballots.",
    )
    path = tmp_path / "t.xlsx"
    wb.save(path)
    assert turnout.extract_as_of_xlsx(path) == "2026-10-09"


def test_as_of_xlsx_first_sheet_wins(tmp_path):
    # Sheets are scanned in workbook order: the first sheet's as-of date wins.
    wb = openpyxl.Workbook()
    first = wb.active
    first.title = "County"
    first.cell(row=2, column=5, value="As of: October 9, 2026")
    second = wb.create_sheet("Legislative")
    second.cell(row=3, column=7, value="As of: October 8, 2026")
    path = tmp_path / "t.xlsx"
    wb.save(path)
    assert turnout.extract_as_of_xlsx(path) == "2026-10-09"


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


REQUESTS_TEXT = """
Mail-in Ballot Requests by County
Election: 2026 Gubernatorial General Election
As of: October 9, 2026 6 PM
HOW THE BALLOTS WILL BE DELIVERED
COUNTY AGENT FAXED IN PERSON MAILED OTHERS PRINT-AT- TOTAL
HOME
Allegany 1 0 122 4,119 0 285 4,527
Prince George's 1 2 11 73,342 7 8,864 82,227
Saint Mary's 0 0 101 7,603 0 883 8,587
TOTAL 2 2 234 85,064 7 10,032 95,341
"""


def test_parse_requests_text():
    # The county rows are the real Allegany, Prince George's and Saint Mary's rows
    # of the live 2026-10-09 file (a three-county excerpt; no TOTAL row reaches the
    # frame). The live file's TOTAL covers all 24 counties, so the excerpt's TOTAL
    # here is the sum of its own three counties -- which is what lets the statewide
    # reconciliation hold, and what the does-not-add-up test below perturbs.
    as_of, df = turnout.parse_requests_text(REQUESTS_TEXT, "GG26")
    assert as_of == "2026-10-09"
    assert len(df) == 21  # 3 counties x 7 measures
    assert set(df["party"]) == {"ALL"}
    assert set(df["category"]) == {"ALL"}
    assert set(df["geography"]) == {"county"}
    assert set(df["family"]) == {"requests"}
    assert set(df["election"]) == {"GG26"}
    assert set(df["district"]) == {""}  # county rows carry no district


def test_requests_numbers_are_positional():
    as_of, df = turnout.parse_requests_text(REQUESTS_TEXT, "GG26")
    a = df.filter(pl.col("county") == "Allegany")
    got = dict(zip(a["measure"], a["value"]))
    assert got == {
        "agent": 1,
        "faxed": 0,
        "in-person": 122,
        "mailed": 4119,
        "others": 0,
        "print-at-home": 285,
        "total": 4527,
    }


def test_requests_canonicalizes_saint_marys():
    as_of, df = turnout.parse_requests_text(REQUESTS_TEXT, "GG26")
    assert "St. Mary's" in set(df["county"])


def test_requests_rejects_a_torn_county_row():
    # a county's delivery-method counts must sum to the county's printed total
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT.replace("285 4,527", "286 4,527"), "GG26")


def test_requests_rejects_a_statewide_total_that_does_not_add_up():
    # the printed TOTAL row must equal the sum of the county totals
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT.replace("95,341", "95,342"), "GG26")


def test_requests_rejects_text_without_an_as_of():
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT.replace("As of: October 9, 2026 6 PM", ""), "GG26")


def test_requests_rejects_a_duplicated_county_row():
    # a repeated county line doubles its counts in any per-county sum yet stays
    # invisible to the statewide check through its per-county lookup, so a repeat
    # fails the parse outright
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT + "Allegany 1 0 122 4,119 0 285 4,527\n", "GG26")


def test_requests_rejects_a_whole_table_repeated():
    # a two-page repeat of the whole table must not reconcile and publish twice
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT + REQUESTS_TEXT, "GG26")


def test_requests_rejects_an_unrecognized_county_label():
    # a truncated or misread county name must not publish under a label that is
    # not one of Maryland's counties
    mangled = REQUESTS_TEXT.replace("Prince George's", "Prince Georges")
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(mangled, "GG26")


def test_requests_rejects_a_county_name_split_across_lines():
    # the live PDF wraps the header onto a second line, so an extraction that
    # wraps a county name the same way leaves a row whose label is not a county
    split = REQUESTS_TEXT.replace("Prince George's 1 2 11", "Prince\nGeorge's 1 2 11")
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(split, "GG26")


def test_requests_accepts_the_canonical_counties_with_their_schema():
    # every canonical county spelling parses, and the frame carries exactly the
    # columns and types the loader writes into turnout_counts
    assert len(turnout.CANONICAL_COUNTIES) == 24  # the monthly reports' county set
    lines = [f"{county} 1 1 2 3 1 2 10" for county in sorted(turnout.CANONICAL_COUNTIES)]
    text = "\n".join(
        [
            "As of: October 9, 2026 6 PM",
            "COUNTY AGENT FAXED IN PERSON MAILED OTHERS PRINT-AT- TOTAL\nHOME",
            *lines,
            "TOTAL 24 24 48 72 24 48 240",  # sums of uniform 1 1 2 3 1 2 10 rows
        ]
    )
    as_of, df = turnout.parse_requests_text(text, "GG26")
    assert as_of == "2026-10-09"
    assert len(df) == 7 * len(turnout.CANONICAL_COUNTIES)
    assert df.schema == {
        "as_of": pl.String,
        "election": pl.String,
        "family": pl.String,
        "geography": pl.String,
        "county": pl.String,
        "district": pl.String,
        "category": pl.String,
        "party": pl.String,
        "measure": pl.String,
        "value": pl.Int64,
    }


def test_requests_rejects_a_torn_single_column():
    # not just the TOTAL column: every printed TOTAL cell must equal its column's sum
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(REQUESTS_TEXT.replace("85,064", "85,640"), "GG26")


def test_requests_rejects_a_missing_county_row():
    # a county row the regex cannot read is a missing county, never a smaller table
    mangled = REQUESTS_TEXT.replace("Saint Mary's 0 0 101 7,603 0 883 8,587\n", "")
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(mangled, "GG26")


def test_requests_skips_a_footnoted_total_line():
    # a TOTAL-prefixed line that is not wholly numeric is noise beside the real row
    as_of, df = turnout.parse_requests_text(REQUESTS_TEXT + "TOTAL 21 31 footnote\n", "GG26")
    assert as_of == "2026-10-09"
    assert len(df) == 21


def test_requests_rejects_text_whose_only_total_line_is_not_numeric():
    # when the ONLY TOTAL-prefixed line is not the numeric totals row, the missing
    # totals row is an error, not a silently unreconciled parse
    mangled = REQUESTS_TEXT.replace(
        "TOTAL 2 2 234 85,064 7 10,032 95,341",
        "TOTAL 2 2 234 85,064 7 10,032 footnote",
    )
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(mangled, "GG26")


def test_requests_rejects_a_total_row_with_the_wrong_number_of_cells():
    mangled = REQUESTS_TEXT.replace("TOTAL 2 2 234 85,064 7 10,032 95,341", "TOTAL 2 2 234 85,064 7 10,032")
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(mangled, "GG26")


def test_requests_rejects_text_without_a_total_row():
    mangled = REQUESTS_TEXT.replace("TOTAL 2 2 234 85,064 7 10,032 95,341\n", "")
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_text(mangled, "GG26")


def test_requests_parses_a_header_without_the_wrapped_word():
    # the live header wraps "PRINT-AT-" / "HOME"; a variant that stays on one line
    # is the same table and must parse identically
    as_of, df = turnout.parse_requests_text(
        REQUESTS_TEXT.replace(
            "COUNTY AGENT FAXED IN PERSON MAILED OTHERS PRINT-AT- TOTAL\nHOME",
            "COUNTY AGENT FAXED IN PERSON MAILED OTHERS PRINT AT HOME TOTAL",
        ),
        "GG26",
    )
    assert as_of == "2026-10-09"
    assert len(df) == 21
