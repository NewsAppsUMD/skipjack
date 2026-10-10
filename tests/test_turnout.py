"""Tests for the turnout pipeline: as-of extraction, parsers, reconciliation, fetching.

The xlsx fixtures are built with openpyxl in the same messy shape the SBE exports use
(blank spacers, labels in the header row); the PDF text tests use the real lines.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

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
    assert (
        turnout.extract_as_of_text("As of: Sept 30, 2026") is None
    )  # abbreviated month, regex still matches
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
    assert (
        urls[("requests", "county", "pdf")]
        == f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"
    )
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
        turnout.parse_requests_text(
            REQUESTS_TEXT.replace("As of: October 9, 2026 6 PM", ""), "GG26"
        )


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
    mangled = REQUESTS_TEXT.replace(
        "TOTAL 2 2 234 85,064 7 10,032 95,341", "TOTAL 2 2 234 85,064 7 10,032"
    )
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


# --- the sent-returned xlsx family --------------------------------------------------------
# Fixtures copy the real 2026-10-09 GG26 files' shapes, verified against the live
# downloads: blank spacer cells in and around the header row, blank cells mid-row
# between the received and accepted column groups, label spellings that vary between
# files and even between a file's own repeated headers (REP/REP_SENT, OTH/OTHR
# ACCEPTED), and every printed block total equal to the sum of its block's member
# rows -- that last one is the arithmetic the parser enforces.


def sent_worksheet(rows, sheet_name="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# County-file shape (Sheet1): a title block that leaves the header lower than row 1,
# then five category blocks of county rows each closed by a printed Total row. Two
# of the five categories and two of the 24 counties are enough; ALL numbers are the
# real Allegany/Worcester values of the October 9 snapshot.
SENT_COUNTY_ROWS = [
    ["", "", "", "Mail-in Sent and Returned ", "Election: 2026 Gubernatorial General Election"],
    ["", "", "", "", ""],
    [
        "",
        "",
        "",
        "",
        "As of: October 9, 2026 6 PM\nNote: Data includes Print at Home ballots, "
        "Replacement ballots and ballots issued by Local Boards of Elections.",
    ],
    # the real header: COUNTY NAME before DISTRICT, spacer cells inside it
    [
        "CATEGORY",
        "COUNTY NAME",
        "DISTRICT",
        "",
        "",
        "DEM SENT",
        "REP SENT",
        "OTH SENT",
        "TOTAL SENT",
        "DEM RECEIVED",
        "REP RECEIVED",
        "OTH RECEIVED",
        "TOTAL RECEIVED",
        "",
        "",
        "",
        "DEM ACCEPTED",
        "REP ACCEPTED",
        "OTHR ACCEPTED",
        "TOTAL ACCEPTED",
    ],
    [
        "ALL",
        "ALLEGANY",
        "Statewide",
        "",
        "",
        2054,
        1701,
        711,
        4466,
        686,
        544,
        212,
        1442,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    [
        "ALL",
        "WORCESTER",
        "Statewide",
        "",
        "",
        2602,
        1816,
        955,
        5373,
        1023,
        641,
        293,
        1957,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    # the block's printed total: the sums of its member rows
    [
        "",
        "Total",
        "",
        "",
        "",
        4656,
        3517,
        1666,
        9839,
        1709,
        1185,
        505,
        3399,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    [
        "Civilian Overseas",
        "ALLEGANY",
        "Statewide",
        "",
        "",
        10,
        8,
        4,
        22,
        2,
        1,
        1,
        4,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    [
        "Civilian Overseas",
        "WORCESTER",
        "Statewide",
        "",
        "",
        5,
        3,
        2,
        10,
        1,
        0,
        1,
        2,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ],
]

# Congressional-file shape (Sheet1): DISTRICT second and COUNTY NAME reading ALL,
# spacer columns inside the header, and the underscore spelling the file's later
# blocks print. Values are the real District 1 and District 2 rows; the Total row is
# the sum of the two. No As-of cell in this fixture (the None date path).
SENT_CD_ROWS = [
    [
        "CATEGORY",
        "DISTRICT",
        "COUNTY NAME",
        "DEM SENT",
        "",
        "",
        "",
        "REP_SENT",
        "OTH SENT",
        "TOTAL SENT",
        "DEM RECEIVED",
        "REP RECEIVED",
        "OTH RECEIVED",
        "",
        "TOTAL RECEIVED",
        "",
        "",
        "DEM ACCEPTED",
        "REP ACCEPTED",
        "OTHR ACCEPTED",
        "TOTAL ACCEPTED",
    ],
    [
        "ALL",
        "Congressional District Code 01",
        "ALL",
        32040,
        "",
        "",
        "",
        19045,
        11871,
        62956,
        10270,
        5653,
        3043,
        "",
        18966,
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    [
        "ALL",
        "Congressional District Code 02",
        "ALL",
        55095,
        "",
        "",
        "",
        16131,
        14067,
        85293,
        11686,
        3664,
        2437,
        "",
        17787,
        "",
        "",
        0,
        0,
        0,
        0,
    ],
    [
        "",
        "Total",
        "",
        87135,
        "",
        "",
        "",
        35176,
        25938,
        148249,
        21956,
        9317,
        5480,
        "",
        36753,
        "",
        "",
        0,
        0,
        0,
        0,
    ],
]

# Legislative-file shape: a blank spacer COLUMN between every real column, the As-of
# sentence in a row of its own at an odd position, values as the real District 01A and
# 01B rows.
SENT_LD_HEADER = [
    "CATEGORY",
    "DISTRICT",
    "",
    "",
    "COUNTY NAME",
    "",
    "DEM SENT",
    "",
    "REP SENT",
    "",
    "OTH SENT",
    "",
    "TOTAL SENT",
    "",
    "DEM RECEIVED",
    "",
    "REP RECEIVED",
    "",
    "OTH RECEIVED",
    "",
    "TOTAL RECEIVED",
    "",
    "",
    "DEM ACCEPTED",
    "",
    "",
    "REP ACCEPTED",
    "",
    "",
    "OTH ACCEPTED",
    "",
    "TOTAL ACCEPTED",
    "",
]
SENT_LD_ROWS = [
    ["", "", "Mail-in Sent and Returned by Legislative District", ""],
    ["", "", "", "Election: 2026 Gubernatorial General Election"],
    ["", "", "", "", "", "As of: October 9, 2026"],
    SENT_LD_HEADER,
    [
        "ALL",
        "Legislative District Code 01A",
        "",
        "",
        "ALL",
        "",
        1079,
        "",
        1209,
        "",
        404,
        "",
        2692,
        "",
        407,
        "",
        400,
        "",
        115,
        "",
        922,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "ALL",
        "Legislative District Code 01B",
        "",
        "",
        "ALL",
        "",
        1604,
        "",
        1209,
        "",
        560,
        "",
        3373,
        "",
        542,
        "",
        394,
        "",
        170,
        "",
        1106,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "",
        "Total",
        "",
        "",
        "",
        "",
        2683,
        "",
        2418,
        "",
        964,
        "",
        6065,
        "",
        949,
        "",
        794,
        "",
        285,
        "",
        2028,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
]

# The legislative file's second section, below the district table under its own header:
# the same population broken down to district x county grain ("DEM SENT" sits one
# column further right, COUNTY NAME carries a real county). The per-district Total
# rows are the real file's values; the closing rows are this fixture's own sums.
SENT_LD_DETAIL_ROWS = [
    [
        "CATEGORY",
        "DISTRICT",
        "",
        "",
        "COUNTY NAME",
        "",
        "",
        "DEM SENT",
        "",
        "REP SENT",
        "",
        "OTH SENT",
        "",
        "TOTAL SENT",
        "",
        "DEM RECEIVED",
        "",
        "REP RECEIVED",
        "",
        "OTH RECEIVED",
        "",
        "TOTAL RECEIVED",
        "",
        "",
        "DEM ACCEPTED",
        "",
        "",
        "REP ACCEPTED",
        "",
        "",
        "OTH ACCEPTED",
        "",
        "TOTAL ACCEPTED",
        "",
    ],
    [
        "Civilian Overseas",
        "Legislative District Code 01A",
        "",
        "",
        "ALLEGANY",
        "",
        "",
        8,
        "",
        0,
        "",
        1,
        "",
        9,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "Civilian Overseas",
        "Legislative District Code 01A",
        "",
        "",
        "GARRETT",
        "",
        "",
        13,
        "",
        1,
        "",
        5,
        "",
        19,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "",
        "Legislative District Code 01A Total",
        "",
        "",
        "",
        "",
        "",
        21,
        "",
        1,
        "",
        6,
        "",
        28,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "Civilian Overseas",
        "Legislative District Code 01B",
        "",
        "",
        "ALLEGANY",
        "",
        "",
        22,
        "",
        4,
        "",
        6,
        "",
        32,
        "",
        2,
        "",
        0,
        "",
        1,
        "",
        3,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "",
        "Legislative District Code 01B Total",
        "",
        "",
        "",
        "",
        "",
        22,
        "",
        4,
        "",
        6,
        "",
        32,
        "",
        2,
        "",
        0,
        "",
        1,
        "",
        3,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    # the category's printed section total, and the sheet's Grand Total
    [
        "Civilian Overseas Total",
        "",
        "",
        "",
        "",
        "",
        "",
        43,
        "",
        5,
        "",
        12,
        "",
        60,
        "",
        2,
        "",
        0,
        "",
        1,
        "",
        3,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
    [
        "Grand Total",
        "",
        "",
        "",
        "",
        "",
        "",
        43,
        "",
        5,
        "",
        12,
        "",
        60,
        "",
        2,
        "",
        0,
        "",
        1,
        "",
        3,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        "",
        0,
        "",
        0,
        "",
    ],
]


def test_sent_returned_county_fixture():
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_COUNTY_ROWS), "GG26", "county"
    )
    assert as_of == "2026-10-09"
    assert set(df["party"]) == {"DEM", "REP", "OTH", "ALL"}
    assert set(df["measure"]) == {"sent", "received", "accepted"}
    assert set(df["category"]) == {"ALL", "Civilian Overseas"}
    # County rows carry no district: the file's DISTRICT cells all read "Statewide".
    assert set(df["district"]) == {""}
    # 2 categories x 2 counties x 3 measures x 4 parties; printed total rows are never data
    assert len(df) == 48


def test_sent_returned_values_by_measure():
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_COUNTY_ROWS), "GG26", "county"
    )
    sent = df.filter(
        (df["category"] == "ALL") & (df["measure"] == "sent") & (df["county"] == "Allegany")
    )
    assert dict(zip(sent["party"], sent["value"])) == {
        "DEM": 2054,
        "REP": 1701,
        "OTH": 711,
        "ALL": 4466,
    }
    accepted = df.filter(df["measure"] == "accepted")
    assert set(accepted["value"]) == {0}


def test_sent_returned_totals_are_not_data():
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_COUNTY_ROWS), "GG26", "county"
    )
    # nothing carries the printed block totals (DEM and ALL sent of the first block)
    assert 4656 not in set(df["value"])
    assert 9839 not in set(df["value"])


def test_sent_returned_rejects_a_torn_row():
    torn = [r[:] for r in SENT_COUNTY_ROWS]
    torn[4][8] = 4467  # Allegany's DEM+REP+OTH sent = 4466
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(torn), "GG26", "county")


def test_sent_returned_rejects_a_block_total_that_lies():
    bad = [r[:] for r in SENT_COUNTY_ROWS]
    bad[6][5] = 4657  # the block total's DEM SENT no longer matches its member rows
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(bad), "GG26", "county")


def test_sent_returned_labels_with_underscores_and_spacers_parse():
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_CD_ROWS), "GG26", "congressional"
    )
    assert as_of is None  # no As-of cell in this sheet
    # the district label is what the sheet prints, kept verbatim
    d1 = df.filter(
        (df["district"] == "Congressional District Code 01")
        & (df["party"] == "DEM")
        & (df["measure"] == "sent")
    )
    assert d1["value"].item() == 32040
    # district rows say so by carrying no county
    assert list(df["county"].unique()) == [""]
    assert len(df) == 2 * 12
    # the printed block total stays out of the frame
    assert 148249 not in set(df["value"])


def test_sent_returned_spacer_columns_in_the_legislative_layout():
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_LD_ROWS), "GG26", "legislative"
    )
    assert as_of == "2026-10-09"  # found in row 2, an odd position, in this one-sheet file
    d1 = df.filter(
        (df["district"] == "Legislative District Code 01A")
        & (df["party"] == "DEM")
        & (df["measure"] == "sent")
    )
    assert d1["value"].item() == 1079
    d2 = df.filter(
        (df["district"] == "Legislative District Code 01B")
        & (df["party"] == "REP")
        & (df["measure"] == "received")
    )
    assert d2["value"].item() == 394
    assert list(df["county"].unique()) == [""]  # the COUNTY NAME column reads ALL
    assert 6065 not in set(df["value"])  # the block total is not data


def test_sent_returned_skips_the_legislative_county_detail():
    # the legislative file carries a second section under its own header: the same
    # population at district x county grain. Its rows are reconciled like the rest
    # (the printed per-district and category totals are checked) but not published --
    # a row carrying both a county and a district is detail, and the data model keeps
    # county and district grains apart the way each block-level table does.
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet([*SENT_LD_ROWS, *SENT_LD_DETAIL_ROWS]), "GG26", "legislative"
    )
    assert as_of == "2026-10-09"
    assert set(df["county"]) == {""}
    assert set(df["district"]) == {"Legislative District Code 01A", "Legislative District Code 01B"}
    assert set(df["category"]) == {
        "ALL"
    }  # the detail section's Civilian Overseas rows are not published
    assert len(df) == 2 * 12


def test_sent_returned_rejects_a_corrupt_workbook():
    # an unreadable workbook is a loud failure for this family, not a silent skip
    # (the requests PDF fallback is a different file shape, parsed elsewhere)
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(b"PK garbage", "GG26", "county")


def test_sent_returned_rejects_a_workbook_without_a_measure_header():
    wb = openpyxl.Workbook()
    wb.active["A1"] = "not a sent-returned export"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(buf.read(), "GG26", "county")


def test_sent_returned_frame_schema():
    # the loader writes these rows straight into turnout_counts (Task 3's column contract)
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(SENT_COUNTY_ROWS), "GG26", "county"
    )
    assert as_of == "2026-10-09"
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


def test_sent_returned_rejects_an_unknown_category():
    mangled = [r[:] for r in SENT_COUNTY_ROWS]
    mangled[7][0] = "Federal Overseas"  # not one of the SBE's five categories
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(mangled), "GG26", "county")


def test_sent_returned_rejects_an_unrecognized_county_label():
    mangled = [r[:] for r in SENT_COUNTY_ROWS]
    mangled[4][1] = "NOTACOUNTY"  # not one of Maryland's counties once canonicalized
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(mangled), "GG26", "county")


def test_sent_returned_canonicalizes_the_all_caps_county_names():
    # the files print ALLEGANY and SAINT MARY'S; the frame keeps the site's canonical
    # spellings
    member = [
        "ALL",
        "SAINT MARY'S",
        "Statewide",
        "",
        "",
        2054,
        1701,
        711,
        4466,
        686,
        544,
        212,
        1442,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ]
    # the printed block total equals the lone member row's numbers, as the real rows do
    total = [
        "",
        "Total",
        "",
        "",
        "",
        2054,
        1701,
        711,
        4466,
        686,
        544,
        212,
        1442,
        "",
        "",
        "",
        0,
        0,
        0,
        0,
    ]
    rows = [*SENT_COUNTY_ROWS[:4], member, total]
    as_of, df = turnout.parse_sent_returned_bytes(sent_worksheet(rows), "GG26", "county")
    assert as_of == "2026-10-09"
    assert set(df["county"]) == {"St. Mary's"}


def test_sent_returned_rejects_a_repeated_row():
    # a repeated table would double up rows that every printed total still reconciles;
    # like the requests parser, a repeated identity fails the parse outright. The
    # repeat here ends the sheet, where no total row would notice.
    dup = [r[:] for r in SENT_COUNTY_ROWS]
    dup.append(dup[-1])  # the last Civilian Overseas county row twice
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(dup), "GG26", "county")


def test_sent_returned_rejects_a_non_numeric_cell():
    torn = [r[:] for r in SENT_COUNTY_ROWS]
    torn[4][7] = "n/a"  # Allegany's OTH SENT cell
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(torn), "GG26", "county")


# --- the turnout commands: fetch and parse -------------------------------------------------

GG26 = turnout.ELECTIONS["GG26"]


def with_as_of(as_of_text: str, rows: list) -> list:
    """Rows of a sheet preceded by a title row carrying the As-of cell (as the real
    files do), so the workbook has a snapshot date."""
    return [["Election: 2026 Gubernatorial General Election", as_of_text], *rows]


@pytest.fixture
def turnout_env(tmp_path, monkeypatch):
    """A turnout fetch/parse environment: manifest and archives under tmp_path."""
    monkeypatch.setattr(turnout, "ARCHIVES_DIR", tmp_path / "archives")
    monkeypatch.setattr(turnout, "MANIFEST_PATH", tmp_path / "manifest.csv")
    return tmp_path


@pytest.fixture
def live_bodies(turnout_env):
    """What SBE serves for the GG26 files today: the four live files' bytes."""
    return {
        GG26.files[
            0
        ].url: b"%PDF-1.4\nMail-in Ballot Requests by County\nAs of: October 9, 2026 6 PM\n",
        GG26.files[1].url: sent_worksheet(with_as_of("As of: October 9, 2026", SENT_COUNTY_ROWS)),
        GG26.files[2].url: sent_worksheet(with_as_of("As of: October 9, 2026", SENT_CD_ROWS)),
        GG26.files[3].url: sent_worksheet(
            with_as_of("As of: October 9, 2026", [*SENT_LD_ROWS, *SENT_LD_DETAIL_ROWS])
        ),
    }


def test_turnout_fetch_archives_each_live_file_once(turnout_env, monkeypatch, live_bodies):
    from skipjack.pipeline.provenance import read_manifest

    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    records = turnout.turnout_fetch()
    assert len(records) == 4
    rows = read_manifest(turnout.MANIFEST_PATH)
    assert len(rows) == 4
    for file in GG26.files:
        match = [r for r in rows if r.source_url == file.url]
        assert len(match) == 1
        archived = Path(match[0].local_path)
        assert archived.exists()
        assert archived.name.startswith(f"{file.family}__{file.geography}__")

    # unchanged files are not re-downloaded or re-archived
    assert turnout.turnout_fetch() == []
    assert len(read_manifest(turnout.MANIFEST_PATH)) == 4

    # a changed file is archived as a new revision
    live_bodies[GG26.files[0].url] = b"%PDF-1.4\nAs of: October 10, 2026 6 PM\n"
    newer = turnout.turnout_fetch()
    assert len(newer) == 1
    assert newer[0].source_id != ""
    assert len(read_manifest(turnout.MANIFEST_PATH)) == 5


def test_turnout_fetch_skips_unpublished_files(turnout_env, monkeypatch):
    monkeypatch.setattr(
        turnout, "download", lambda url, timeout=30.0: b"<html>placeholder page</html>"
    )
    assert turnout.turnout_fetch() == []
    from skipjack.pipeline.provenance import read_manifest

    assert read_manifest(turnout.MANIFEST_PATH) == []


def test_turnout_parse_writes_the_snapshots(turnout_env, monkeypatch, live_bodies):
    from skipjack.pipeline.build_db import load_turnout
    from skipjack.pipeline.provenance import ProvenanceSidecar

    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    turnout.turnout_fetch()
    # the requests PDF gets its text by extraction; the fixture PDF above is not a real
    # document, so the fake download's text stands in for what extraction would return
    monkeypatch.setattr(turnout, "read_requests_pdf", lambda path: REQUESTS_TEXT)

    written, failed = turnout.turnout_parse(data_dir=turnout_env / "data")
    assert written == 4 and failed == []
    for file in GG26.files:
        stem = turnout_env / "data" / "turnout" / "GG26" / file.family / file.geography
        assert (stem / "2026-10-09.csv").exists(), f"missing {stem / '2026-10-09.csv'}"
        prov = ProvenanceSidecar.read(stem / "2026-10-09_provenance.json")
        assert prov.election == "GG26" and prov.family == file.family
        assert prov.geography == file.geography and prov.source_id.startswith("sbe-turnout-")

    # a second run is a no-op (the CSV exists), and forcing rewrites it
    written, _ = turnout.turnout_parse(data_dir=turnout_env / "data")
    assert written == 0
    written, _ = turnout.turnout_parse(data_dir=turnout_env / "data", force=True)
    assert written == 4

    # the CSVs flow through the build-db loader: every row lands in turnout_counts
    from skipjack.models import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    # 168 request cells (24 counties x x7), 48 county, 24 congressional and 24
    # legislative cells (the fixtures carry two counties and two districts each)
    assert (
        load_turnout(conn, turnout_env / "data") == 117
    )  # 21 requests + 48 county + 24 congressional + 24 legislative
    conn.close()


def test_turnout_parse_reports_failures(turnout_env, monkeypatch, live_bodies):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    turnout.turnout_fetch()
    monkeypatch.setattr(
        turnout, "read_requests_pdf", lambda path: REQUESTS_TEXT.replace("4,527", "9,9999")
    )
    written, failed = turnout.turnout_parse(data_dir=turnout_env / "data")
    # the three workbook snapshots parse; the torn requests one does not
    assert written == 3 and len(failed) == 1
    assert "requests" in failed[0] and "county" in failed[0]
    written_csvs = list((turnout_env / "data").glob("turnout/*/*/*/*.csv"))
    assert sorted(csv.relative_to(turnout_env).as_posix() for csv in written_csvs) == [
        "data/turnout/GG26/sent-returned/congressional/2026-10-09.csv",
        "data/turnout/GG26/sent-returned/county/2026-10-09.csv",
        "data/turnout/GG26/sent-returned/legislative/2026-10-09.csv",
    ]
