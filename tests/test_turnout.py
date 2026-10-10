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
    """A turnout fetch/parse environment: manifest and archives under tmp_path, and the
    fetch sees only GG26's files (the GP26 files are their own tests)."""
    monkeypatch.setattr(turnout, "ARCHIVES_DIR", tmp_path / "archives")
    monkeypatch.setattr(turnout, "MANIFEST_PATH", tmp_path / "manifest.csv")
    monkeypatch.setattr(turnout, "ELECTIONS", {"GG26": turnout.ELECTIONS["GG26"]})
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


# --- review fixes: the four Important findings (see the fix-commit message) -----------------


def test_fetch_survives_a_corrupt_workbook(turnout_env, monkeypatch, live_bodies):
    # one PK-signed but unreadable xlsx must not abort the rest of the day's fetch
    broken = dict(live_bodies)
    broken[GG26.files[1].url] = b"PK\x03\x04 not really a zip"
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: broken[url])
    records = turnout.turnout_fetch()
    assert len(records) == 4  # the corrupt one is archived under "unknown"


def test_fetch_continues_when_one_download_fails(turnout_env, monkeypatch, live_bodies):
    import httpx

    def flaky(url, timeout=30.0):
        if url == GG26.files[1].url:
            raise httpx.ConnectError("down")
        return live_bodies[url]

    monkeypatch.setattr(turnout, "download", flaky)
    # the other three files are archived anyway, and the day ends loudly
    with pytest.raises(turnout.TurnoutParseError, match="downloads failed"):
        turnout.turnout_fetch()
    from skipjack.pipeline.provenance import read_manifest

    rows = read_manifest(turnout.MANIFEST_PATH)
    assert len(rows) == 3
    assert not any(r.source_url == GG26.files[1].url for r in rows)


def test_a_newer_revision_with_the_same_date_is_parsed(turnout_env, monkeypatch, live_bodies):
    # SBE reposted the same snapshot at a later time: the CSV must track the newer file
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    monkeypatch.setattr(turnout, "read_requests_pdf", lambda path: REQUESTS_TEXT)
    turnout.turnout_fetch()
    written, failed = turnout.turnout_parse(data_dir=turnout_env / "data")
    assert written == 4
    county_csv = (
        turnout_env / "data" / "turnout" / "GG26" / "sent-returned" / "county" / "2026-10-09.csv"
    )
    assert "2054" in county_csv.read_text()

    # a revision with changed numbers, same as-of date
    # a revision with changed numbers (the row, its printed total, and the block
    # totals all move together, since the parser checks every layer)
    rows = [r[:] for r in SENT_COUNTY_ROWS]
    rows[4][5] = 2055  # Allegany's DEM SENT
    rows[4][8] = 4467  # the row's own printed total
    rows[6][5] = 4657  # the block's printed DEM SENT total
    rows[6][8] = 9840  # the block's printed total of totals
    live_bodies[GG26.files[1].url] = sent_worksheet(with_as_of("As of: October 9, 2026", rows))
    assert len(turnout.turnout_fetch()) == 1
    written, _ = turnout.turnout_parse(data_dir=turnout_env / "data")
    assert written == 1  # only the sent-returned county snapshot changed
    assert "2055" in county_csv.read_text() and "2054" not in county_csv.read_text()


def test_as_of_filter_skips_an_unknown_snapshot_whose_date_is_not_the_filter(
    turnout_env, monkeypatch, live_bodies
):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    monkeypatch.setattr(turnout, "read_requests_pdf", lambda path: REQUESTS_TEXT)
    turnout.turnout_fetch()
    written, _ = turnout.turnout_parse(data_dir=turnout_env / "data", as_of="2025-01-01")
    assert written == 0


def test_sent_returned_rejects_a_repeat_across_sheets():
    # the same table pasted onto two sheets would double-publish its counties
    wb = openpyxl.Workbook()
    wb.active["A1"] = None
    first = wb.active
    for row in SENT_COUNTY_ROWS:
        first.append(row)
    second = wb.create_sheet("Sheet2")
    for row in SENT_COUNTY_ROWS:
        second.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(buf.read(), "GG26", "county")


def test_sent_returned_rejects_a_block_without_a_measure_column():
    torn = [r[:] for r in SENT_COUNTY_ROWS]
    torn[3][5] = "SENT"  # the "DEM SENT" label renamed away -> its column is unmapped
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(torn), "GG26", "county")


def test_sent_returned_rejects_a_fractional_cell():
    torn = [r[:] for r in SENT_COUNTY_ROWS]
    torn[4][5] = 2054.5
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_sent_returned_bytes(sent_worksheet(torn), "GG26", "county")


def test_sidecar_precedes_the_csv(turnout_env, monkeypatch, live_bodies):
    # the sequence in which the snapshot is written: provenance first, CSV second, so a
    # crash between them leaves an unwatched sidecar (harmless), not an unloaded CSV
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: live_bodies[url])
    monkeypatch.setattr(turnout, "read_requests_pdf", lambda path: REQUESTS_TEXT)
    turnout.turnout_fetch()
    turnout.turnout_parse(data_dir=turnout_env / "data")
    prov = (
        turnout_env
        / "data"
        / "turnout"
        / "GG26"
        / "requests"
        / "county"
        / "2026-10-09_provenance.json"
    )
    assert prov.exists()
    from skipjack.pipeline.provenance import ProvenanceSidecar

    sidecar = ProvenanceSidecar.read(prov)
    from skipjack.pipeline.provenance import read_manifest

    manifest_row = next(
        r for r in read_manifest(turnout.MANIFEST_PATH) if "requests" in r.source_id
    )
    assert sidecar.source_id == manifest_row.source_id
    assert sidecar.sha256_original == manifest_row.sha256


# --- GP26: the certified families ------------------------------------------------------------
# Config and the requests workbook of the 2026 primary (the backfill increment).


def test_gp26_config_matches_the_live_press_room():
    gp26 = turnout.ELECTIONS["GP26"]
    assert gp26.code == "GP26" and gp26.name == "2026 primary election"
    assert gp26.election_day == "2026-06-23"
    have = {(f.family, f.geography, f.kind): f.url for f in gp26.files}
    base = "https://elections.maryland.gov/press_room/2026_stats/GP26"
    # every report family but the election-night unofficial report is configured
    assert (
        have["requests", "county", "pdf"] == f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"
    )
    assert (
        have["requests", "county", "xlsx"]
        == f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.xlsx"
    )
    for geo in ("county", "congressional", "legislative"):
        assert (
            have["left-to-count", geo, "xlsx"]
            == f"{base}/Absentees_Ballots_Left_To_Count_by_{geo.capitalize()}.xlsx"
        )
        assert (
            have["official-turnout", geo, "xlsx"]
            == f"{base}/Official_by_Party_and_{geo.capitalize()}.xlsx"
        )
        assert (
            have["sent-returned", geo, "xlsx"]
            == f"{base}/Absentees_Sent_and_Returned_by_{geo.capitalize()}.xlsx"
        )
    for geo in ("county", "congressional", "legislative", "councilmanic"):
        assert (
            have["eligible-active", geo, "xlsx"]
            == f"{base}/Eligible_Active_Voters_by_{geo.capitalize()}_-_GP26.xlsx"
        )
    # the by-party early-voting file is its own geography so the two views can't collide
    assert (
        have["early-voting", "county-party", "pdf"] == f"{base}/EarlyVoting  by Party.pdf"
    )  # two spaces, as published
    assert have["early-voting", "county", "pdf"] == f"{base}/EarlyVoting by County.pdf"
    assert have["same-day", "location", "pdf"] == f"{base}/SameDayRegistration by Location.pdf"
    assert have["county-canvass", "county", "pdf"] == f"{base}/Official County and Canvass.pdf"
    assert (
        have["votes-accepted", "county", "pdf"]
        == f"{base}/Official Votes Accepted by County and Canvass.pdf"
    )
    assert have["mail-voting", "county", "pdf"] == f"{base}/Official Mail-In Voting Voting.pdf"
    assert have["provisional", "county", "pdf"] == f"{base}/Official Provisional Voting.pdf"
    assert (
        have["eligible-inactive", "county", "xlsx"]
        == f"{base}/Eligible_Inactive_Voters_-_GP26.xlsx"
    )
    assert all("unofficial" not in url.lower() for url in have.values())


GP26_REQUESTS_XLSX = [
    ["", "", "", "Mail-in Ballot Requests by County", "", "", ""],
    ["", "", "", "", "Election: 2026 Gubernatorial Primary Election"],
    ["", "", "", "", "", "", "As of: June 19, 2026 1 PM"],
    ["COUNTY", "", "HOW THE BALLOTS WILL BE DELIVERED", "", "", "", "", "", "", "", "", ""],
    [
        "",
        "",
        "AGENT",
        "",
        "FAXED",
        "",
        "IN PERSON",
        "MAILED",
        "OTHERS",
        "WEB DELIVERED",
        "",
        "TOTAL",
    ],
    ["Allegany", "", 2, "", 0, "", 112, 4041, 0, 295, "", 4450],
    ["Worcester", "", 1, "", 0, "", 14, 4881, 0, 599, "", 5495],
    ["", "", "TOTAL", "", "", "", 126, 8922, 0, 894, "", 9945],
]


def test_gp26_requests_xlsx_parses():
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(GP26_REQUESTS_XLSX), "GP26")
    assert as_of == "2026-06-19"
    assert len(df) == 14  # 2 counties x 7 measures
    allegany = df.filter((df["county"] == "Allegany") & (df["measure"] == "mailed"))
    assert allegany["value"].item() == 4041
    assert set(df["measure"]) == {
        "agent",
        "faxed",
        "in-person",
        "mailed",
        "others",
        "web-delivered",
        "total",
    }


def test_gp26_requests_reconciliation():
    # a county's delivery methods must sum to the county's printed total ...
    torn = [r[:] for r in GP26_REQUESTS_XLSX]
    torn[5][11] = 4451
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(torn), "GP26")
    # ... and the printed TOTAL row must equal the counties' column sums
    statewide = [r[:] for r in GP26_REQUESTS_XLSX]
    statewide[7][9] = 895  # the WEB DELIVERED total no longer matches the counties
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(statewide), "GP26")


# The request workbook's labels are read from the header row, never positionally: the
# real file's header carries AGENT/FAXED/IN PERSON/MAILED/OTHERS/WEB DELIVERY/TOTAL
# (the export later reprints the web column as WEB DELIVERED in the GG26 file, so both
# spellings are the same measure), with spacer columns between every label. The printed
# TOTAL row only prints the columns it cares about (this fixture omits the agent and
# faxed totals); a column the totals row does not print is not checked.


def test_gp26_requests_totals_row_is_not_data():
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(GP26_REQUESTS_XLSX), "GP26")
    assert as_of == "2026-06-19"
    assert "TOTAL" not in set(df["county"])  # the printed totals row never becomes a row
    assert "Worcester" in set(df["county"])


def test_gp26_requests_reads_the_real_web_delivery_label():
    # the GP26 export prints WEB DELIVERY where the fixture and the GG26 file print
    # WEB DELIVERED -- both are the same web-delivered measure, so a family's measure
    # vocabulary does not fork on the SBE's spelling
    rows = [r[:] for r in GP26_REQUESTS_XLSX]
    rows[4][9] = "WEB DELIVERY"
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(rows), "GP26")
    assert as_of == "2026-06-19"
    assert "web-delivered" in set(df["measure"])
    worcester = df.filter((df["county"] == "Worcester") & (df["measure"] == "web-delivered"))
    assert worcester["value"].item() == 599


def test_gp26_requests_reads_a_one_line_header():
    # COUNTY and the delivery-method labels on the same row are the same table
    rows = [r[:] for r in GP26_REQUESTS_XLSX[:3]]
    rows.append(
        [
            "COUNTY",
            "",
            "AGENT",
            "",
            "FAXED",
            "",
            "IN PERSON",
            "MAILED",
            "OTHERS",
            "WEB DELIVERED",
            "",
            "TOTAL",
        ]
    )
    rows += [r[:] for r in GP26_REQUESTS_XLSX[5:]]
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(rows), "GP26")
    assert as_of == "2026-06-19"
    assert len(df) == 14


def test_gp26_requests_tolerates_blank_rows_between_the_header_lines():
    rows = [r[:] for r in GP26_REQUESTS_XLSX]
    rows.insert(5, ["", "", "", "", "", "", "", "", "", "", "", ""])  # blank line
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(rows), "GP26")
    assert as_of == "2026-06-19"
    assert len(df) == 14


def test_gp26_requests_tolerates_a_blank_row_in_the_county_block():
    # the live file has one blank row between Wicomico and Worcester's county rows
    rows = [r[:] for r in GP26_REQUESTS_XLSX]
    rows.insert(7, ["", "", "", "", "", "", "", "", "", "", "", ""])
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(rows), "GP26")
    assert as_of == "2026-06-19"
    assert len(df) == 14


def test_gp26_requests_frame_schema():
    as_of, df = turnout.parse_requests_bytes(sent_worksheet(GP26_REQUESTS_XLSX), "GP26")
    assert as_of == "2026-06-19"
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
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    assert set(df["party"]) == {"ALL"} and set(df["category"]) == {"ALL"}


def test_gp26_requests_rejects_a_duplicated_county_row():
    dup = [r[:] for r in GP26_REQUESTS_XLSX]
    dup.append(dup[5][:])  # Allegany's row repeated after the totals row
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(dup), "GP26")


def test_gp26_requests_rejects_an_unrecognized_county_label():
    mangled = [r[:] for r in GP26_REQUESTS_XLSX]
    mangled[6][0] = "Princes Georges"
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(mangled), "GP26")


def test_gp26_requests_rejects_a_non_numeric_cell():
    torn = [r[:] for r in GP26_REQUESTS_XLSX]
    torn[5][7] = "n/a"
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(torn), "GP26")


def test_gp26_requests_rejects_a_fractional_cell():
    torn = [r[:] for r in GP26_REQUESTS_XLSX]
    torn[6][7] = 4041.5  # Worcester's MAILED cell
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(torn), "GP26")


def test_gp26_requests_rejects_a_totals_row_with_no_county_rows():
    only = [r[:] for r in GP26_REQUESTS_XLSX[:5]]
    only.append([r_c for r_c in GP26_REQUESTS_XLSX[7]])
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(only), "GP26")


def test_gp26_requests_rejects_a_table_without_a_totals_row():
    torn = [r[:] for r in GP26_REQUESTS_XLSX[:-1]]
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(sent_worksheet(torn), "GP26")


def test_gp26_requests_rejects_a_corrupt_workbook():
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_requests_bytes(b"PK garbage", "GP26")


# --- the certified official-turnout family ---------------------------------------------------
# Fixtures copy the real GP26 files' shape (verified against the live downloads): a
# block-label row (Statewide/Democrat/...) whose one text cell sits at any column, then
# a header row carrying the geography's area label (LBE, CONGRESSIONAL DISTRICT CODE,
# LEGISLATIVE DISTRICT CODE) and the five measures plus TURNOUT -- the printed shares
# and their column are never stored. A date cell reads the two-line
# "Election Date: June 23, 2026\nAs of: July 23, 2026" the county file prints. Every
# block closes with a printed TOTAL(S) row in its own area column, and the Statewide
# block's row for an area equals the sum of the party blocks' rows for that area.
# The area rows below carry the real Allegany/Worcester and district 01/02 values; the
# Statewide block's rows are those sums, because a two-party excerpt cannot carry the
# other four parties' share of the real Statewide rows.


OFFICIAL_COUNTY_HEADER = [
    "LBE",
    "",
    "",
    "",
    "Election Day",
    "",
    "",
    "",
    "Early Voting",
    "",
    "Vote By Mail",
    "",
    "Provisional",
    "",
    "Eligible Voters",
    "",
    "TURNOUT",
]
OFFICIAL_DISTRICT_HEADER = [
    "CONGRESSIONAL DISTRICT CODE",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "Election Day",
    "",
    "",
    "",
    "Early Voting",
    "",
    "",
    "",
    "Vote By Mail",
    "",
    "",
    "",
    "Provisional",
    "",
    "",
    "",
    "Eligible Voters",
    "",
    "",
    "",
    "TURNOUT",
    "",
    "",
    "",
]


def spacers(text):
    """A header row with a blank spacer column after every label, as the files print."""
    row = [""]
    for cell in text:
        row += [cell]
    return row


def official_county_row(area, ed, ev, vbm, prov, elig, turnout=0.0, lead=0):
    """A county area row, values a spacer apart from the area cell on."""
    row = [""] * lead
    return row + [area, "", "", "", ed, "", "", "", ev, "", vbm, "", prov, "", elig, "", turnout]


def official_district_row(area, ed, ev, vbm, prov, elig, turnout=0.0, lead=0):
    """A district area row of the real congressional layout (spacer columns between)."""
    row = [""] * lead + [area] + [""] * 10
    return row + [
        ed,
        "",
        "",
        "",
        ev,
        "",
        "",
        "",
        vbm,
        "",
        "",
        "",
        prov,
        "",
        "",
        "",
        elig,
        "",
        "",
        "",
        turnout,
        "",
        "",
        "",
    ]


def official_totals_row(label="TOTAL", lead=0):
    """The block-closing printed totals row for the county shape."""
    row = [""] * lead
    return row + [label, "", "", "", "", "", "", "", "", "", "", ""]


OFFICIAL_COUNTY_ROWS = [
    ["", "", "", "Official Turnout (By Party and County)", "", "", "", ""],
    ["", "", "", "Election Date: June 23, 2026\nAs of: July 23, 2026", "", "", "", ""],
    ["Statewide"],
    ["", *OFFICIAL_COUNTY_HEADER],  # the Statewide block's table starts a column in
    official_county_row("Allegany", 6375, 1380, 2353, 231, 34366, 0.2797, lead=1),
    official_county_row("Worcester", 5125, 1566, 2573, 245, 33718, 0.2824, lead=1),
    official_county_row("TOTAL", 11500, 2946, 4926, 476, 68084, 0.2723, lead=1),
    ["Democrat"],
    OFFICIAL_COUNTY_HEADER,
    official_county_row("Allegany", 1562, 418, 1343, 121, 10758),
    official_county_row("Worcester", 1406, 484, 1610, 135, 13257),
    official_county_row("TOTAL", 2968, 902, 2953, 256, 24015),
    ["Republican"],
    OFFICIAL_COUNTY_HEADER,
    official_county_row("Allegany", 4813, 962, 1010, 110, 23608),
    official_county_row("Worcester", 3719, 1082, 963, 110, 20461),
    official_county_row("TOTAL", 8532, 2044, 1973, 220, 44069),
]


def test_official_turnout_county_fixture():
    as_of, df = turnout.parse_turnout_official_bytes(
        sent_worksheet(OFFICIAL_COUNTY_ROWS), "GP26", "county"
    )
    assert as_of == "2026-07-23"  # from the combined Election Date / As-of cell
    assert set(df["party"]) == {"ALL", "DEM", "REP"}
    assert set(df["measure"]) == {
        "election-day",
        "early-voting",
        "vote-by-mail",
        "provisional",
        "eligible",
    }
    assert set(df["county"]) == {"Allegany", "Worcester"}
    assert len(df) == 3 * 2 * 5  # three blocks x two counties x five measures


def test_official_turnout_values():
    as_of, df = turnout.parse_turnout_official_bytes(
        sent_worksheet(OFFICIAL_COUNTY_ROWS), "GP26", "county"
    )
    dem = df.filter((df["party"] == "DEM") & (df["county"] == "Allegany"))
    assert dict(zip(dem["measure"], dem["value"])) == {
        "election-day": 1562,
        "early-voting": 418,
        "vote-by-mail": 1343,
        "provisional": 121,
        "eligible": 10758,
    }
    rep = df.filter((df["party"] == "REP") & (df["county"] == "Worcester"))
    assert dict(zip(rep["measure"], rep["value"]))["eligible"] == 20461
    sw = df.filter((df["party"] == "ALL") & (df["county"] == "Allegany"))
    assert dict(zip(sw["measure"], sw["value"]))["election-day"] == 6375  # 1562 + 4813


def test_official_turnout_shares_are_never_stored():
    as_of, df = turnout.parse_turnout_official_bytes(
        sent_worksheet(OFFICIAL_COUNTY_ROWS), "GP26", "county"
    )
    assert "turnout" not in set(df["measure"])
    assert not any(round(v, 4) != v for v in df["value"])  # shares like 0.2797 are kept out
    assert 0.2797 not in set(df["value"].to_list()) or 0.2797 == 0  # no float shares anywhere
    assert df.schema["value"] == pl.Int64


def test_official_turnout_rejects_a_bad_block_label():
    bad = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    bad[12][0] = "Independents"  # the Republican block's label row
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")


def test_official_turnout_rejects_a_torn_block_total():
    bad = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    bad[6][5] = 11501  # the Statewide block's printed Election Day total
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")


def test_official_turnout_rejects_a_statewide_row_that_forks_from_the_party_blocks():
    bad = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    bad[4][5] = 6376  # the Statewide Allegany row no longer equals DEM + REP
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")


def test_official_turnout_rejects_a_block_without_a_totals_row():
    bad = [r for i, r in enumerate(OFFICIAL_COUNTY_ROWS) if i != 6]  # drop the Statewide TOTAL
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")


def test_official_turnout_rejects_a_fractional_measure_cell():
    bad = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    bad[9][4] = 1562.5  # a DEM election-day count printed with a fraction
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")


def test_official_turnout_ignores_a_fractional_share_cell():
    # TURNOUT is the printed share column; a torn share is not a torn count and the
    # share column is never stored, so a bad share does not fail the parse
    bad = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    bad[4][16] = "shares are broken"
    as_of, df = turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")
    assert as_of == "2026-07-23"
    assert len(df) == 30


def test_official_turnout_rejects_a_repeated_area_row():
    dup = [r[:] for r in OFFICIAL_COUNTY_ROWS]
    dup.insert(5, official_county_row("Allegany", 6375, 1380, 2353, 231, 34366, lead=1))
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(sent_worksheet(dup), "GP26", "county")


def test_official_turnout_frame_schema():
    as_of, df = turnout.parse_turnout_official_bytes(
        sent_worksheet(OFFICIAL_COUNTY_ROWS), "GP26", "county"
    )
    assert as_of == "2026-07-23"
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
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    assert set(df["category"]) == {"ALL"}


def test_official_turnout_rejects_a_corrupt_workbook():
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_turnout_official_bytes(b"PK garbage", "GP26", "county")


# District shape (the congressional file): an area column that reads the district
# codes (verbatim), and an Undetermined row per block for the provisional ballots whose
# district could not be determined (the live file's carry 2155 in the Statewide block,
# and their party shares in every party block).


def test_official_turnout_district_rows_print_their_own_codes():
    as_of, df = turnout.parse_turnout_official_bytes(
        sent_worksheet(OFFICIAL_CD_ROWS), "GP26", "congressional"
    )
    assert as_of is None  # no date cell in this excerpt
    assert {"01", "02", "Undetermined"} <= set(df["district"])  # verbatim, as printed
    assert set(df["county"]) == {""}
    assert len(df) == 3 * 3 * 5
    dem1 = df.filter(
        (df["district"] == "01") & (df["party"] == "DEM") & (df["measure"] == "vote-by-mail")
    )
    assert dem1["value"].item() == 18758
    # the Statewide block's Undetermined provisional count is the sum of the parties'
    parties = df.filter(
        (df["district"] == "Undetermined")
        & (df["party"].is_in(["DEM", "REP"]))
        & (df["measure"] == "provisional")
    )["value"].sum()
    assert parties == 389
    assert (
        df.filter(
            (df["district"] == "Undetermined")
            & (df["party"] == "ALL")
            & (df["measure"] == "provisional")
        )["value"].item()
        == 389
    )


def official_party_district_header():
    """The party blocks' own header: the codes column one in, measures one column on."""
    row = [""] + ["CONGRESSIONAL DISTRICT CODE"] + [""] * 10
    return row + [
        "Election Day",
        "",
        "",
        "",
        "Early Voting",
        "",
        "",
        "",
        "Vote By Mail",
        "",
        "",
        "",
        "Provisional",
        "",
        "",
        "",
        "Eligible Voters",
        "",
        "",
        "",
        "TURNOUT",
        "",
        "",
    ]


OFFICIAL_CD_ROWS = [
    ["Statewide"],
    OFFICIAL_DISTRICT_HEADER,
    official_district_row("01", 58710, 28418, 29107, 2800, 432002),
    official_district_row("02", 47226, 23224, 40582, 4329, 412553),
    official_district_row("Undetermined", 0, 0, 0, 389, 0),
    official_district_row("TOTALS", 105936, 51642, 69689, 7518, 844555),
    ["Democrat"],
    official_party_district_header(),
    official_district_row("01", 18006, 10061, 18758, 1580, 184109, lead=1),
    official_district_row("02", 28603, 15572, 33122, 3451, 262835, lead=1),
    official_district_row("Undetermined", 0, 0, 0, 260, 0, lead=1),
    official_district_row("TOTALS", 46609, 25633, 51880, 5291, 446944, lead=1),
    ["Republican"],
    official_party_district_header(),
    official_district_row("01", 40704, 18357, 10349, 1220, 247893, lead=1),
    official_district_row("02", 18623, 7652, 7460, 878, 149718, lead=1),
    official_district_row("Undetermined", 0, 0, 0, 129, 0, lead=1),
    official_district_row("TOTALS", 59327, 26009, 17809, 2227, 397611, lead=1),
]
