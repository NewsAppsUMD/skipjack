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
    # the printed TOTAL row: the label in the county column, and only the columns it
    # cares about (this export omits the agent and faxed totals)
    ["TOTAL", "", "", "", "", "", 126, 8922, 0, 894, "", 9945],
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
    assert "turnout" not in set(df["measure"])  # the printed shares are not a measure
    printed_shares = {0.2797, 0.2824, 0.2723, 0.3201, 0.2741, 0.2944}
    assert not (set(df["value"].to_list()) & printed_shares)  # no share reaches the frame
    assert df.schema["value"] == pl.Int64


def test_official_turnout_turnout_column_is_excluded_from_the_cell_checks():
    # the shares column is not a mapped measure, so a share cell that is not an
    # integer never reaches the parser's cell checks; only counts do
    bad = [row[:] for row in OFFICIAL_COUNTY_ROWS]
    bad[4][16] = -0.5  # a torn share in the Statewide block's TURNOUT column
    as_of, df = turnout.parse_turnout_official_bytes(sent_worksheet(bad), "GP26", "county")
    assert as_of == "2026-07-23" and len(df) == 30


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


# --- the eligible-voters family --------------------------------------------------------------
# Header-driven like the rest: County / District / the six party columns / Total, with
# spacer columns that move between sections. The district files' first section prints
# the district grain against a COUNTY cell that reads STATEWIDE (collapsed to no
# county); their second section prints the same population at district x county grain,
# where the district's own label repeats on every row (the councilmanic file) or is
# merged and reads only on the group's first row (the congressional/legislative files).
# A blank party cell is a value the export did not print: the legislative file's
# district 01C shows GRN blank with its Total 32 above the printed cells' sum (the
# Statewide export prints the same 32), so blank cells publish nothing and a row's
# checks hold only over the cells that did print. The grand totals' party columns are
# the one layer the family does not reconcile -- the live inactive file's DEM total is
# 1327 under its own rows while its total column and every county row add up -- so a
# consistent total row and a torn one parse the same way (the county rows carry the
# published data; the grand total is never data).

ELIGIBLE_COUNTY_HEADER = ["County", "", "DEM", "", "REP", "GRN", "OTH", "UNA", "WCP", "", "Total"]


def eligible_county_row(county, dem, rep, grn, oth, una, wcp, total):
    """A county row of the county/inactive shape, spacer cells as printed."""
    return [county, "", dem, "", rep, grn, oth, una, wcp, "", total]


def eligible_values(dem=0, rep=0, grn=0, oth=0, una=0, wcp=0):
    return dem, rep, grn, oth, una, wcp


ELIGIBLE_COUNTY_ROWS = [
    ["", "", "", "Eligible Active Voters on the Precinct Register - By County", "", ""],
    ["", "", "", "Election Date: June 23, 2026\n**As of June 6, 2026", "", "", ""],
    ELIGIBLE_COUNTY_HEADER,
    eligible_county_row("Allegany", 10758, 23608, 32, 286, 3226, 36, 37946),
    eligible_county_row("Anne Arundel", 172770, 128551, 0, 0, 0, 0, 301321),
    eligible_county_row("TOTAL", 183528, 152159, 32, 286, 3226, 36, 339267),
]


def test_eligible_active_county_fixture():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_COUNTY_ROWS), "GP26", "eligible-active", "county"
    )
    assert as_of == "2026-06-06"  # the colonless As of line the date cell ends with
    assert set(df["family"]) == {"eligible-active"}
    assert set(df["measure"]) == {"active"}
    assert set(df["party"]) == {"DEM", "REP", "GRN", "OTH", "UNA", "WCP", "ALL"}
    assert set(df["category"]) == {"ALL"}
    assert "TOTAL" not in set(df["county"])  # the printed grand total is not a row
    assert len(df) == 14  # two counties x seven printed cells


def test_eligible_county_values():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_COUNTY_ROWS), "GP26", "eligible-active", "county"
    )
    allegany = df.filter(df["county"] == "Allegany")
    assert dict(zip(allegany["party"], allegany["value"])) == {
        "DEM": 10758,
        "REP": 23608,
        "GRN": 32,
        "OTH": 286,
        "UNA": 3226,
        "WCP": 36,
        "ALL": 37946,
    }


ELIGIBLE_INACTIVE_ROWS = [
    ELIGIBLE_COUNTY_ROWS[0],
    ELIGIBLE_COUNTY_ROWS[1],
    ELIGIBLE_COUNTY_HEADER,
    eligible_county_row("Allegany", 761, 1101, 4, 23, 281, 7, 2177),
    eligible_county_row("Anne Arundel", 12138, 8893, 0, 0, 0, 0, 21031),
    eligible_county_row("TOTAL", 12899, 9994, 4, 23, 281, 7, 23208),
]


def test_eligible_inactive_fixture():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_INACTIVE_ROWS), "GP26", "eligible-inactive", "county"
    )
    assert as_of == "2026-06-06"
    assert set(df["measure"]) == {"inactive"}
    assert set(df["family"]) == {"eligible-inactive"}


def test_eligible_tolerates_a_grand_total_cell_that_disagrees_with_its_rows():
    # the live inactive export's DEM grand total is 1327 under the rows above it while
    # its TOTAL column and every county row reconcile; the eligible family checks the
    # rows and the total column, never the grand total's party cells (the DEM cell here
    # prints the live file's wrong-looking 1327-under number for the same reason)
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(
            [
                ELIGIBLE_COUNTY_HEADER,
                eligible_county_row("Allegany", 761, 1101, 4, 23, 281, 7, 2177),
                eligible_county_row("TOTAL", 124383, 60650, 300, 2265, 35517, 140, 2177),
            ]
        ),
        "GP26",
        "eligible-inactive",
        "county",
    )
    assert as_of is None
    assert len(df) == 7  # the county row's printed cells, not the grand total's


def test_eligible_rejects_a_row_total_that_understates_its_parties():
    torn = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    torn[5][10] = 300  # Allegany's Total is 32 under its printed party cells
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "county")


def test_eligible_rejects_a_section_total_that_understates_its_rows():
    torn = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    torn[5][10] = 30000  # the section's Total column no longer equals the rows' totals
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "county")


def test_eligible_rejects_an_unknown_county_label():
    torn = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    torn[4][0] = "Princes Georges"
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "county")


def test_eligible_rejects_a_duplicated_county_row():
    dup = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    dup.insert(5, eligible_county_row("Allegany", 700, 800, 0, 0, 0, 0, 1500))
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(dup), "GP26", "eligible-active", "county")


def test_eligible_rejects_an_unknown_party_label():
    torn = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    torn[2] = ["County", "", "DEM", "", "REP", "LIB", "OTH", "UNA", "WCP", "", "Total"]
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "county")


def test_eligible_rejects_a_fractional_cell():
    torn = [row[:] for row in ELIGIBLE_COUNTY_ROWS]
    torn[4][2] = 10758.5
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "county")


def test_eligible_rejects_an_unknown_family():
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(
            sent_worksheet(ELIGIBLE_COUNTY_ROWS), "GP26", "eligible-retired", "county"
        )


def test_eligible_frame_schema():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_COUNTY_ROWS), "GP26", "eligible-active", "county"
    )
    assert as_of == "2026-06-06"
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


def test_eligible_rejects_a_corrupt_workbook():
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(b"PK garbage", "GP26", "eligible-active", "county")


# Legislative shape: the grain's header puts District in a column of its own with the
# COUNTY cell reading STATEWIDE, and spacer cells move the party columns; the file's
# second section prints the same population at district x county grain, where a
# district's label repeats only on its group's first row. Values are the live district
# 01A and 01C grain cells -- 01C's GRN cell is the blank one (its Total is 32 over its
# printed cells' sum; the Statewide export prints the same 32), so 01C publishes six
# cells and 01A all seven.

ELIGIBLE_LD_GRAIN_HEADER = [
    "District",
    "",
    "",
    "",
    "",
    "County",
    "",
    "",
    "DEM",
    "",
    "REP",
    "GRN",
    "",
    "OTH",
    "UNA",
    "",
    "WCP",
    "",
    "Total",
]
ELIGIBLE_LD_DETAIL_HEADER = [
    "",
    "District",
    "",
    "",
    "County",
    "",
    "",
    "DEM",
    "",
    "REP",
    "",
    "GRN",
    "OTH",
    "",
    "UNA",
    "WCP",
    "",
    "Total",
    "",
]


def eligible_ld_grain_row(district, cells, total):
    dem, rep, grn, oth, una, wcp = cells
    return [
        district,
        "",
        "",
        "",
        "",
        "STATEWIDE",
        "",
        "",
        dem,
        "",
        rep,
        grn,
        "",
        oth,
        una,
        "",
        wcp,
        "",
        total,
    ]


def eligible_ld_detail_row(district, county, cells, total):
    dem, rep, grn, oth, una, wcp = cells
    return [
        "",
        district,
        "",
        "",
        county,
        "",
        "",
        dem,
        "",
        rep,
        "",
        grn,
        oth,
        "",
        una,
        wcp,
        "",
        total,
        "",
    ]


ELIGIBLE_LD_ROWS = [
    ["", "Eligible Active Voters on the Precinct Register - By Legislative District", ""],
    ELIGIBLE_LD_GRAIN_HEADER,
    eligible_ld_grain_row(
        "Legislative District Code 01A",
        eligible_values(5253, 19234, 0, 0, 0, 0),
        24487,
    ),
    eligible_ld_grain_row(
        "Legislative District Code 01C",
        (6653, 16694, None, 375, 5902, 41),
        29697,
    ),
    eligible_ld_grain_row("TOTAL", (11906, 35928, 32, 375, 5902, 41), 54184),
    ELIGIBLE_LD_DETAIL_HEADER,
    eligible_ld_detail_row(
        "Legislative District Code 01A", "Allegany", eligible_values(1891, 5231, 0, 0, 0, 0), 7122
    ),
    eligible_ld_detail_row("", "Garrett", (3362, 14003, None, None, None, 0), 17365),
    eligible_ld_detail_row("TOTAL", "", (5253, 19234, 0, 0, 0, 0), 24487),
]


def test_eligible_district_grain_rows_publish_their_codes():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_LD_ROWS), "GP26", "eligible-active", "legislative"
    )
    assert as_of is None  # a two-section excerpt; the live file carries the date cell
    assert set(df["measure"]) == {"active"}
    codes = set(df["district"])
    assert codes == {"Legislative District Code 01A", "Legislative District Code 01C"}
    assert set(df["county"]) == {""}  # STATEWIDE collapsed
    assert len(df) == 13  # 01A: seven printed cells; 01C: six (the blank GRN)


def test_eligible_01c_row_skips_the_blank_grn_cell():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_LD_ROWS), "GP26", "eligible-active", "legislative"
    )
    one_c = df.filter(df["district"] == "Legislative District Code 01C")
    got = dict(zip(one_c["party"], one_c["value"]))
    assert got == {"DEM": 6653, "REP": 16694, "OTH": 375, "UNA": 5902, "WCP": 41, "ALL": 29697}


def test_eligible_skips_the_district_county_detail():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_LD_ROWS), "GP26", "eligible-active", "legislative"
    )
    # the detail section's counties are reconcile-only: the frame stays at district grain
    assert set(df["county"]) == {""}
    assert "Allegany" not in set(df["county"]) and "Garrett" not in set(df["county"])
    assert "TOTAL" not in set(df["district"])
    assert 7122 not in set(df["value"])  # the detail rows' own totals are not data
    assert 17365 not in set(df["value"])
    assert 24487 in set(df["value"])  # the grain row's own printed Total, at its grain


def test_eligible_rejects_a_torn_detail_group():
    torn = [row[:] for row in ELIGIBLE_LD_ROWS]
    torn[6][7] = 1892  # the Allegany detail row's DEM no longer sums to the grain row
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "legislative")


def test_eligible_rejects_a_detail_group_without_a_printed_grain_row():
    torn = [row[:] for row in ELIGIBLE_LD_ROWS]
    torn[6][1] = "Legislative District Code 99X"  # no such district row printed above
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_eligible_bytes(sent_worksheet(torn), "GP26", "eligible-active", "legislative")


# Councilmanic shape: the grain section carries a leading statewide row (a blank
# district cell with STATEWIDE in the county cell) whose numbers are the population the
# councilmanic codes do not cover -- printed again in the file's detail section as those
# counties' own rows -- and the grain's district label is spelled lowercase there
# ("councilmanic District Code 000") while the detail section spells it capitalized; the
# detail group still reconciles against the grain row by the label.

ELIGIBLE_COUNCILMANIC_GRAIN_HEADER = [
    "",
    "",
    "District",
    "",
    "",
    "",
    "",
    "County",
    "",
    "DEM",
    "",
    "REP",
    "",
    "GRN",
    "",
    "OTH",
    "",
    "UNA",
    "",
    "WCP",
    "",
    "TOTAL",
    "",
]
ELIGIBLE_COUNCILMANIC_DETAIL_HEADER = [
    "District",
    "",
    "",
    "",
    "",
    "",
    "County",
    "",
    "DEM",
    "",
    "REP",
    "",
    "GRN",
    "",
    "OTH",
    "",
    "UNA",
    "",
    "WCP",
    "",
    "TOTAL",
    "",
]


def eligible_councilmanic_row(district, county, cells, total, lead=2):
    """A row of the councilmanic file's grain section (District one column in)."""
    dem, rep, grn, oth, una, wcp = cells
    row = [""] * lead
    return row + [
        district,
        "",
        "",
        "",
        "",
        county,
        "",
        dem,
        "",
        rep,
        "",
        grn,
        "",
        oth,
        "",
        una,
        "",
        wcp,
        "",
        total,
        "",
    ]


def eligible_councilmanic_detail_row(district, county, cells, total):
    """A row of the councilmanic file's detail section (District / County side by side)."""
    dem, rep, grn, oth, una, wcp = cells
    return [
        district,
        "",
        "",
        "",
        "",
        "",
        county,
        "",
        dem,
        "",
        rep,
        "",
        grn,
        "",
        oth,
        "",
        una,
        "",
        wcp,
        "",
        total,
        "",
    ]


ELIGIBLE_COUNCILMANIC_ROWS = [
    ELIGIBLE_COUNCILMANIC_GRAIN_HEADER,
    eligible_councilmanic_row("", "STATEWIDE", eligible_values(100, 50), 150),
    eligible_councilmanic_row(
        "councilmanic District Code 000", "STATEWIDE", eligible_values(10, 5), 15
    ),
    eligible_councilmanic_row("TOTAL", "", eligible_values(110, 55), 165),
    ELIGIBLE_COUNCILMANIC_DETAIL_HEADER,
    eligible_councilmanic_detail_row("", "Allegany", eligible_values(100, 50), 150),
    eligible_councilmanic_detail_row(
        "Councilmanic District Code 000", "Talbot", eligible_values(10, 5), 15
    ),
    eligible_councilmanic_detail_row("TOTAL", "", eligible_values(110, 55), 165),
]


def test_eligible_councilmanic_grain_and_detail_reconcile():
    as_of, df = turnout.parse_eligible_bytes(
        sent_worksheet(ELIGIBLE_COUNCILMANIC_ROWS), "GP26", "eligible-active", "councilmanic"
    )
    assert as_of is None
    # published at the district grain; the detail rows and the statewide row stay out
    assert set(df["district"]) == {"councilmanic District Code 000"}  # verbatim, lower-case
    assert set(df["county"]) == {""}
    assert len(df) == 7
    got = dict(
        zip(df.filter(df["district"] == "councilmanic District Code 000")["party"], df["value"])
    )
    assert got == {
        "DEM": 10,
        "REP": 5,
        "GRN": 0,
        "OTH": 0,
        "UNA": 0,
        "WCP": 0,
        "ALL": 15,
    }


# --- the left-to-count family --------------------------------------------------------------
# The sent-returned exports' shape with one more printed column: LEFT TO COUNT sits at
# the header's end with no DEM/REP/OTH breakdown and no party total -- LEFT TO COUNT is
# its own single count. The live GP26 files print AND reconcile the column at every
# printed Total row, and each row carries the file's own arithmetic
# (LEFT TO COUNT = accepted's TOTAL ACCEPTED subtracted from ... received - accepted),
# including negative cells (the county file's Anne Arundel row reads -52 while the
# canvass sits between received and accepted counts). The fixture row set is the real
# county Sheet1's first two ALL rows plus their printed Total.

LEFT_TO_COUNT_HEADER = [
    "CATEGORY",
    "COUNTY NAME",
    "DISTRICT",
    "",
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
    "DEM ACCEPTED",
    "",
    "",
    "",
    "REP ACCEPTED",
    "OTH ACCEPTED",
    "TOTAL ACCEPTED",
    "LEFT TO COUNT",
]


def left_to_count_row(category, county, cells):
    """One county row and its printed cells; LEFT TO COUNT last."""
    dem_s, rep_s, oth_s, tot_s, dem_r, rep_r, oth_r, tot_r, dem_a, rep_a, oth_a, tot_a, left = cells
    return [
        category,
        county,
        "",
        "",
        "",
        "",
        dem_s,
        rep_s,
        oth_s,
        tot_s,
        dem_r,
        rep_r,
        oth_r,
        tot_r,
        dem_a,
        "",
        "",
        "",
        rep_a,
        oth_a,
        tot_a,
        left,
    ]


LEFT_TO_COUNT_ROWS = [
    ["", "", "", "Mail-in Ballots Left To Count by County", "", ""],
    ["", "", "", "", "As of: July 7, 2026 6 PM\nNote: ballots logged as received", "", ""],
    LEFT_TO_COUNT_HEADER,
    left_to_count_row(
        "ALL",
        "ALLEGANY",
        (2041, 1723, 263, 4027, 1346, 1010, 100, 2456, 1333, 993, 97, 2423, 33),
    ),
    left_to_count_row(
        "ALL",
        "ANNE ARUNDEL",
        (36060, 13162, 0, 49222, 23039, 5992, 0, 29031, 22952, 5961, 170, 29083, -52),
    ),
    left_to_count_row(
        "",
        "Total",
        (38101, 14885, 263, 53249, 24385, 7002, 100, 31487, 24285, 6954, 267, 31506, -19),
    ),
]


def test_left_to_count_parses_with_the_extra_measure():
    as_of, df = turnout.parse_left_to_count_bytes(
        sent_worksheet(LEFT_TO_COUNT_ROWS), "GP26", "county"
    )
    assert as_of == "2026-07-07"
    assert set(df["family"]) == {"left-to-count"}
    assert set(df["measure"]) == {"sent", "received", "accepted", "left-to-count"}
    assert set(df["party"]) == {"DEM", "REP", "OTH", "ALL"}
    assert len(df) == 26  # two counties x twelve tallied cells plus two left-to-count cells


def test_left_to_count_keeps_the_negative_cells():
    as_of, df = turnout.parse_left_to_count_bytes(
        sent_worksheet(LEFT_TO_COUNT_ROWS), "GP26", "county"
    )
    left = df.filter((df["measure"] == "left-to-count") & (df["county"] == "Anne Arundel"))
    assert left["value"].item() == -52  # the live file's own negative cell, as printed


def test_left_to_count_rejects_a_torn_left_to_count_cell():
    # the row's own arithmetic: LEFT TO COUNT must equal the accepted Total subtracted
    torn = [row[:] for row in LEFT_TO_COUNT_ROWS]
    torn[3][21] = 34  # Allegany's left-to-count says 34 over its received (2456) - accepted (2423)
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_left_to_count_bytes(sent_worksheet(torn), "GP26", "county")


def test_left_to_count_reconciles_the_printed_column_totals():
    torn = [row[:] for row in LEFT_TO_COUNT_ROWS]
    torn[5][21] = -18  # the printed Total's left-to-count no longer equals its rows' sum
    with pytest.raises(turnout.TurnoutParseError):
        turnout.parse_left_to_count_bytes(sent_worksheet(torn), "GP26", "county")


def test_sent_returned_ignores_a_left_to_count_column():
    # the sent-returned parser's own files have no LEFT TO COUNT column; a file that
    # carries one parses anyway with the column ignored (the GP26 sent-returned files
    # are theirs; the left-to-count files are the same shape re-printed wider)
    as_of, df = turnout.parse_sent_returned_bytes(
        sent_worksheet(LEFT_TO_COUNT_ROWS), "GP26", "county"
    )
    assert as_of == "2026-07-07"
    assert set(df["measure"]) == {"sent", "received", "accepted"}
    assert len(df) == 24


def test_left_to_count_skips_the_district_county_detail():
    # the legislative file: the district roll-up rows publish, and the second section's
    # district x county rows reconcile under their own printed district Total rows
    header = [
        "CATEGORY",
        "DISTRICT",
        "",
        "COUNTY NAME",
        "DEM SENT",
        "REP SENT",
        "OTH SENT",
        "TOTAL SENT",
        "DEM RECEIVED",
        "REP RECEIVED",
        "OTH RECEIVED",
        "TOTAL RECEIVED",
        "DEM ACCEPTED",
        "REP ACCEPTED",
        "OTH ACCEPTED",
        "TOTAL ACCEPTED",
        "LEFT TO COUNT",
    ]

    def cells(category, district, county, numbers):
        return [category, district, "", county, *numbers]

    rows = [
        ["", "", "Mail-in Ballots Left To Count by Legislative District", ""],
        ["", "", "", "As of: July 7, 2026 6 PM"],
        header,
        cells(
            "ALL",
            "Legislative District Code 01A",
            "ALL",
            (1079, 1209, 404, 2692, 407, 400, 115, 922, 0, 0, 0, 0, 922),
        ),
        cells(
            "ALL",
            "Legislative District Code 01B",
            "ALL",
            (1604, 1209, 560, 3373, 542, 394, 170, 1106, 0, 0, 0, 0, 1106),
        ),
        cells("", "Total", "", (2683, 2418, 964, 6065, 949, 794, 285, 2028, 0, 0, 0, 0, 2028)),
        header,
        cells(
            "Civilian Overseas",
            "Legislative District Code 01A",
            "ALLEGANY",
            (8, 0, 1, 9, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        ),
        cells(
            "Civilian Overseas",
            "Legislative District Code 01A",
            "GARRETT",
            (13, 1, 5, 19, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        ),
        [
            "",
            "Legislative District Code 01A Total",
            "",
            "",
            21,
            1,
            6,
            28,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    ]
    as_of, df = turnout.parse_left_to_count_bytes(sent_worksheet(rows), "GP26", "legislative")
    assert as_of == "2026-07-07"
    assert set(df["district"]) == {"Legislative District Code 01A", "Legislative District Code 01B"}
    assert set(df["county"]) == {""}  # the detail's counties stay out of the frame
    assert len(df) == 2 * 13
    assert "Civilian Overseas" not in set(df["category"])
    assert 28 not in set(df["value"])


def test_parse_file_routes_the_new_families(tmp_path):
    # the archived revisions parse through _parse_file by family: each new GP26
    # family's workbook now has a parser the parse command can reach
    import types

    cases = (
        ("left-to-count", "county", LEFT_TO_COUNT_ROWS, "left-to-count", "2026-07-07"),
        ("official-turnout", "county", OFFICIAL_COUNTY_ROWS, "election-day", "2026-07-23"),
        ("eligible-active", "county", ELIGIBLE_COUNTY_ROWS, "active", "2026-06-06"),
        ("eligible-inactive", "county", ELIGIBLE_INACTIVE_ROWS, "inactive", "2026-06-06"),
    )
    for family, geography, rows, measure, date in cases:
        path = tmp_path / f"{family}__{geography}__{date}__abc123.xlsx"
        path.write_bytes(sent_worksheet(rows))
        as_of, df = turnout._parse_file(
            path, types.SimpleNamespace(source_id="x"), "GP26", family, geography
        )
        assert as_of == date, family
        assert set(df["family"]) == {family}, family
        assert measure in set(df["measure"]), family


# --- the PDF families: the certified one-page canvass reports ---------------------------------
# The GP26 four ("Official County and Canvass", "Official Votes Accepted by County and
# Canvass", "Official Mail-In Voting Voting", "Official Provisional Voting") ship as
# PDFs only, and the parsers take the rows natural-pdf's table extraction returns, as
# lists of the cell strings the files print (counts keep their commas, the shares print
# as "%"-suffixed fractions). Fixtures copy the real Allegany / Anne Arundel / Worcester
# rows; each excerpt's Statewide row is its own three counties' sums, because a
# three-county excerpt cannot carry the other 21 counties' share of the state row.

# county-canvass: a county x canvass-mode sheet -- four modes, each count followed by
# the unlabelled % cell that shares it, then the county's total. Measures are the
# official-turnout vocabulary (early-voting, election-day, vote-by-mail, provisional)
# plus the row's own 'total'; the state row reconciles the counties and is not published.

CANVASS_HEADER = [
    "County",
    "Early Voting",
    "",
    "Election Day",
    "",
    "Vote by Mail",
    "",
    "Provisional",
    "",
    "Total",
]


def canvass_row(county, ev, ev_share, ed, ed_share, vbm, vbm_share, prov, prov_share, total):
    return [county, ev, ev_share, ed, ed_share, vbm, vbm_share, prov, prov_share, total]


CANVASS_ROWS = [
    CANVASS_HEADER,
    canvass_row(
        "Allegany",
        "1,401",
        "13.20%",
        "6,478",
        "61.03%",
        "2,452",
        "23.10%",
        "284",
        "2.68%",
        "10,615",
    ),
    canvass_row(
        "Anne Arundel",
        "13,827",
        "18.13%",
        "29,462",
        "38.64%",
        "29,206",
        "38.30%",
        "3,757",
        "4.93%",
        "76,252",
    ),
    canvass_row(
        "Worcester",
        "1,566",
        "16.44%",
        "5,125",
        "53.81%",
        "2,573",
        "27.02%",
        "260",
        "2.73%",
        "9,524",
    ),
    canvass_row(
        "Statewide",
        "16,794",
        "17.42%",
        "41,065",
        "42.60%",
        "34,231",
        "35.51%",
        "4,301",
        "4.46%",
        "96,391",
    ),
]


def test_county_canvass_parses_the_county_rows():
    as_of, df = turnout.parse_county_canvass_rows(
        CANVASS_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert as_of == "2026-07-23"
    assert len(df) == 3 * 5  # three counties x five measures
    assert set(df["measure"]) == {
        "early-voting",
        "election-day",
        "vote-by-mail",
        "provisional",
        "total",
    }
    assert set(df["family"]) == {"county-canvass"}
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    assert set(df["party"]) == {"ALL"} and set(df["category"]) == {"ALL"}
    assert set(df["county"]) == {"Allegany", "Anne Arundel", "Worcester"}


def test_county_canvass_values_are_the_printed_counts():
    as_of, df = turnout.parse_county_canvass_rows(
        CANVASS_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    allegany = df.filter((df["county"] == "Allegany"))
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "early-voting": 1401,
        "election-day": 6478,
        "vote-by-mail": 2452,
        "provisional": 284,
        "total": 10615,
    }


def test_county_canvass_shares_are_never_stored():
    as_of, df = turnout.parse_county_canvass_rows(
        CANVASS_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert not (set(df["value"].to_list()) & {0.1320, 0.2797})  # the printed shares stay out
    assert set(df["value"]) == {
        1401,
        6478,
        2452,
        284,
        10615,
        13827,
        29462,
        29206,
        3757,
        76252,
        1566,
        5125,
        2573,
        260,
        9524,
    }


def test_county_canvass_state_row_is_checked_not_published():
    as_of, df = turnout.parse_county_canvass_rows(
        CANVASS_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert not any(("Statewide" in c) or c == "State Totals" for c in df["county"])
    ev = df.filter(df["measure"] == "early-voting")["value"].sum()
    assert ev == 16794  # the counties' own sum is what the state row is checked against


def test_county_canvass_state_row_must_agree_with_the_counties():
    torn = [r[:] for r in CANVASS_ROWS]
    torn[4][9] = "96,392"  # the Statewide total no longer equals the counties'
    with pytest.raises(turnout.TurnoutParseError, match="96,392|96392"):
        turnout.parse_county_canvass_rows(torn, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_rejects_a_mode_that_does_not_sum_to_the_total():
    for col, value in ((1, "1,402"), (5, "2,453")):
        torn = [r[:] for r in CANVASS_ROWS]
        torn[1][col] = value  # one of Allegany's mode counts, off by one
        with pytest.raises(turnout.TurnoutParseError, match="do not add up"):
            turnout.parse_county_canvass_rows(torn, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_rejects_a_share_that_is_not_its_count_over_the_total():
    bad = [r[:] for r in CANVASS_ROWS]
    bad[1][2] = "99.99%"  # Allegany's early-voting share
    with pytest.raises(turnout.TurnoutParseError, match="share"):
        turnout.parse_county_canvass_rows(bad, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_rejects_a_duplicated_county_row():
    dup = [r[:] for r in CANVASS_ROWS]
    dup.insert(2, CANVASS_ROWS[1][:])  # Allegany printed twice
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_county_canvass_rows(dup, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in CANVASS_ROWS]
    bad[2][0] = "Princes Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_county_canvass_rows(bad, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_requires_its_state_row():
    no_state = CANVASS_ROWS[:-1]
    with pytest.raises(turnout.TurnoutParseError, match="Statewide"):
        turnout.parse_county_canvass_rows(no_state, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_rejects_a_renamed_column():
    bad = [r[:] for r in CANVASS_ROWS]
    bad[0][7] = "Provisionals"  # a label the file does not print
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized column"):
        turnout.parse_county_canvass_rows(bad, "GP26", "county", as_of="2026-07-23")


def test_county_canvass_only_runs_at_the_county_geography():
    with pytest.raises(turnout.TurnoutParseError, match="geography"):
        turnout.parse_county_canvass_rows(CANVASS_ROWS, "GP26", "congressional", as_of="2026-07-23")


def test_parse_file_reaches_the_pdf_families(tmp_path):
    # _parse_file dispatches the PDF families to their own readers: a path that no
    # reader can open fails in that family's reader, not the "no parser" fallthrough
    import types

    missing = tmp_path / "no-such-file.pdf"
    for family, geography in (("county-canvass", "county"),):
        with pytest.raises(turnout.TurnoutParseError, match=f"could not read the {family} PDF"):
            turnout._parse_file(
                missing, types.SimpleNamespace(source_id="x"), "GP26", family, geography
            )


def test_report_date_reads_the_official_sheets():
    # the four Official one-pagers date themselves by the day the report was run, as
    # month/day/year -- a neither "As of" reader accepts
    assert (
        turnout.extract_report_date_text("2026 Gubernatorial Primary Report Date: 7/23/2026")
        == "2026-07-23"
    )
    assert turnout.extract_report_date_text("no date here") is None
    assert turnout.extract_report_date_text("Report Date: 13/32/2026") is None


def test_election_date_reads_the_daily_files():
    assert turnout.extract_election_date_text("Election Date: June 23, 2026") == "2026-06-23"
    assert turnout.extract_election_date_text("no date here") is None


def test_pdf_snapshot_date_prefers_the_report_date_then_the_as_of():
    # the Official sheets carry only a report date; the requests reports' "As of"
    # keeps the earlier rule; the eight-day files' date is their election date, and a
    # lower-case footnote ("Eligible Active Voters are as of : June 18, 2026") must
    # not win over it
    text = (
        "Unofficial Early Voting Turnout* (By Party)\n"
        "Election: 2026 Gubernatorial Primary Election\n"
        "Election Date: June 23, 2026\n"
        "*Turnout Totals do not include Provisional or Absentee Voters "
        "**County-Wide Eligible Active Voters are as of : June 18, 2026"
    )
    assert turnout.pdf_snapshot_date(text) == "2026-06-23"
    assert (
        turnout.pdf_snapshot_date("2026 Gubernatorial Primary Report Date: 7/23/2026")
        == "2026-07-23"
    )
    assert turnout.pdf_snapshot_date("As of: October 9, 2026 6 PM") == "2026-10-09"
    assert turnout.pdf_snapshot_date("") is None


# votes-accepted: "Official Votes Accepted by County and Canvass", the same county x
# canvass-mode sheet at the "Total Votes Counted" table -- same labels, same checks,
# the same measure vocabulary as county-canvass and the official-turnout workbooks.
# The real file prints its Total column without a comma where the canvass sheet keeps
# them; both forms are one count.

VOTES_ACCEPTED_ROWS = [
    CANVASS_HEADER,
    canvass_row(
        "Allegany", "1,401", "13.31%", "6,478", "61.53%", "2,423", "23.01%", "227", "2.16%", "10529"
    ),
    canvass_row(
        "Anne Arundel",
        "13,827",
        "18.40%",
        "29,462",
        "39.20%",
        "29,083",
        "38.69%",
        "2,793",
        "3.72%",
        "75165",
    ),
    canvass_row(
        "Worcester", "1,566", "16.52%", "5,125", "54.07%", "2,546", "26.86%", "242", "2.55%", "9479"
    ),
    canvass_row(
        "Statewide",
        "16,794",
        "17.65%",
        "41,065",
        "43.19%",
        "34,052",
        "35.78%",
        "3,262",
        "3.43%",
        "95173",
    ),
]


def test_votes_accepted_parses_the_county_rows():
    as_of, df = turnout.parse_votes_accepted_rows(
        VOTES_ACCEPTED_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert as_of == "2026-07-23"
    assert len(df) == 3 * 5  # three counties x five measures
    assert set(df["measure"]) == {
        "early-voting",
        "election-day",
        "vote-by-mail",
        "provisional",
        "total",
    }
    assert set(df["family"]) == {"votes-accepted"}
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    assert set(df["party"]) == {"ALL"} and set(df["category"]) == {"ALL"}


def test_votes_accepted_reads_an_unpunctuated_total():
    as_of, df = turnout.parse_votes_accepted_rows(
        VOTES_ACCEPTED_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    allegany = df.filter((df["county"] == "Allegany"))
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "early-voting": 1401,
        "election-day": 6478,
        "vote-by-mail": 2423,
        "provisional": 227,
        "total": 10529,
    }


def test_votes_accepted_state_row_is_checked_not_published():
    as_of, df = turnout.parse_votes_accepted_rows(
        VOTES_ACCEPTED_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert not any(("Statewide" in c) or c == "State Totals" for c in df["county"])
    accepted = df.filter(df["measure"] == "total")["value"].sum()
    assert accepted == 95173


def test_votes_accepted_rejects_its_own_bad_arithmetic():
    # the same checks at the same names: a mode that does not sum, a share that lies,
    # a state row that disagrees with the counties
    torn = [r[:] for r in VOTES_ACCEPTED_ROWS]
    torn[1][3] = "6,479"  # Allegany's election-day count
    with pytest.raises(turnout.TurnoutParseError, match="do not add up"):
        turnout.parse_votes_accepted_rows(torn, "GP26", "county", as_of="2026-07-23")
    lying = [r[:] for r in VOTES_ACCEPTED_ROWS]
    lying[2][4] = "9.99%"  # Anne Arundel's vote-by-mail share
    with pytest.raises(turnout.TurnoutParseError, match="share"):
        turnout.parse_votes_accepted_rows(lying, "GP26", "county", as_of="2026-07-23")
    statewide = [r[:] for r in VOTES_ACCEPTED_ROWS]
    statewide[4][3] = "41,066"  # the Statewide election-day count, off by one ...
    statewide[4][9] = "95174"  # ... and its total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="counties' election-day counts"):
        turnout.parse_votes_accepted_rows(statewide, "GP26", "county", as_of="2026-07-23")


def test_votes_accepted_rejects_a_duplicated_county_row():
    dup = [r[:] for r in VOTES_ACCEPTED_ROWS]
    dup.insert(2, VOTES_ACCEPTED_ROWS[1][:])
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_votes_accepted_rows(dup, "GP26", "county", as_of="2026-07-23")


def test_votes_accepted_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in VOTES_ACCEPTED_ROWS]
    bad[1][0] = "Prince Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_votes_accepted_rows(bad, "GP26", "county", as_of="2026-07-23")


# mail-voting: "Official Mail-In Voting Voting", one page at two tables. The county
# table sends, receives, accepts and rejects ballots per county (each count, then the
# % cell that shares it); the second table is the state's rejected mail-in ballots
# broken down by their printed rejection reasons. Measures are the sent-returned
# vocabulary (sent, received, accepted) plus 'rejected', which the reasons borrow for
# their own rows: one row per printed reason, category = the reason as printed (the
# line wrap the extraction carries inside a reason cell collapses), county and
# district empty, so the reasons' sum IS the state's rejected cell.

MAIL_HEADER = [
    "County",
    "Ballots Sent",
    "Ballots Received",
    "",
    "Ballots Accepted",
    "",
    "Ballots Rejected",
    "",
]

MAIL_VOTING_ROWS = [
    MAIL_HEADER,
    ["Allegany", "4,027", "2,456", "60.99%", "2,423", "98.66%", "33", "1.34%"],
    ["Anne Arundel", "49,224", "29,206", "59.33%", "29,083", "99.58%", "123", "0.42%"],
    ["Worcester", "4,419", "2,582", "58.43%", "2,546", "98.61%", "36", "1.39%"],
    ["Statewide", "57,670", "34,244", "59.38%", "34,052", "99.44%", "192", "0.56%"],
    # the second table: the state's 192 rejected ballots by their printed reason
    ["Board Action", "35", "18.23%"],
    ["Late for election", "100", "52.08%"],
    ["No signature", "50", "26.04%"],
    ["No mail-in ballot application", "2", "1.04%"],
    ["Voted more than one ballot", "5", "2.60%"],
    ["Total", "192", ""],
]


def test_mail_voting_parses_the_county_rows():
    as_of, df = turnout.parse_mail_voting_rows(
        MAIL_VOTING_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert as_of == "2026-07-23"
    assert len(df) == 3 * 4 + 5  # three counties x four measures, plus five reasons
    assert set(df["measure"]) == {"sent", "received", "accepted", "rejected"}
    assert set(df["family"]) == {"mail-voting"}
    assert set(df["geography"]) == {"county"}
    allegany = df.filter((df["county"] == "Allegany") & (df["category"] == "ALL"))
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "sent": 4027,
        "received": 2456,
        "accepted": 2423,
        "rejected": 33,
    }


def test_mail_voting_publishes_the_rejection_reasons():
    as_of, df = turnout.parse_mail_voting_rows(
        MAIL_VOTING_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    reasons = df.filter((df["category"] != "ALL"))
    assert set(reasons["category"]) == {
        "Board Action",
        "Late for election",
        "No signature",
        "No mail-in ballot application",
        "Voted more than one ballot",
    }
    assert set(reasons["measure"]) == {"rejected"}
    assert set(reasons["county"]) == {""} and set(reasons["district"]) == {""}
    assert set(reasons["party"]) == {"ALL"}
    late = reasons.filter(reasons["category"] == "Late for election")
    assert late["value"].item() == 100
    # the reasons are the state's rejected mail-in ballots' breakdown: their sum is
    # the state row's rejected cell
    assert reasons["value"].sum() == 192


def test_mail_voting_collapses_a_wrapped_reason_text():
    # the live file wraps a long reason across the cell's lines
    rows = [r[:] for r in MAIL_VOTING_ROWS]
    rows[5] = [
        "Identification was received but did not satisfy the identification\nrequirements",
        "2",
        "1.04%",
    ]
    rows[7] = ["No signature", "83", "43.23%"]  # takes the 33, so the table still adds up
    as_of, df = turnout.parse_mail_voting_rows(rows, "GP26", "county", as_of="2026-07-23")
    published = df.filter((df["category"] != "ALL"))["category"].to_list()
    assert (
        "Identification was received but did not satisfy the identification requirements"
        in published
    )


def test_mail_voting_state_row_is_checked_not_published():
    as_of, df = turnout.parse_mail_voting_rows(
        MAIL_VOTING_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert set(df["county"]) <= {"", "Allegany", "Anne Arundel", "Worcester"}
    received = df.filter((df["measure"] == "received") & (df["category"] == "ALL"))["value"].sum()
    assert received == 34244  # what the state row's own arithmetic was checked against


MAIL_VOTING_FAILURES = (
    # an accepted count that its rejected no longer sums into the received cell
    ((1, 4), "2,424", "do not add up"),
    # a received share that is not the received count over the ballots sent
    ((1, 3), "99.99%", "share"),
    # a rejected share that is not the rejected count over the ballots received
    ((1, 7), "9.99%", "share"),
)


def test_mail_voting_rejects_the_torn_arithmetic():
    for (row, col), value, where in MAIL_VOTING_FAILURES:
        torn = [r[:] for r in MAIL_VOTING_ROWS]
        torn[row][col] = value
        with pytest.raises(turnout.TurnoutParseError, match=where):
            turnout.parse_mail_voting_rows(torn, "GP26", "county", as_of="2026-07-23")


def test_mail_voting_rejects_reasons_that_do_not_add_up_to_the_state():
    torn = [r[:] for r in MAIL_VOTING_ROWS]
    torn[10][1] = "193"  # the printed reasons total
    with pytest.raises(turnout.TurnoutParseError, match="reasons total|reasons sum"):
        turnout.parse_mail_voting_rows(torn, "GP26", "county", as_of="2026-07-23")
    under = [r[:] for r in MAIL_VOTING_ROWS]
    under[5][1] = "36"  # a reason's count moves, off the printed total it feeds
    with pytest.raises(turnout.TurnoutParseError, match="reasons total|reasons sum"):
        turnout.parse_mail_voting_rows(under, "GP26", "county", as_of="2026-07-23")


def test_mail_voting_rejects_a_duplicated_county_row():
    dup = [r[:] for r in MAIL_VOTING_ROWS]
    dup.insert(2, MAIL_VOTING_ROWS[1][:])
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_mail_voting_rows(dup, "GP26", "county", as_of="2026-07-23")


def test_mail_voting_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in MAIL_VOTING_ROWS]
    bad[1][0] = "Prince Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_mail_voting_rows(bad, "GP26", "county", as_of="2026-07-23")


def test_mail_voting_requires_the_reason_table():
    missing = [r for r in MAIL_VOTING_ROWS if len(r) > 3]
    with pytest.raises(turnout.TurnoutParseError, match="reason"):
        turnout.parse_mail_voting_rows(missing, "GP26", "county", as_of="2026-07-23")


def test_parse_file_reaches_the_mail_voting_pdf(tmp_path):
    # the same dispatch proof as the canvass family's: a PDF the readers cannot open
    # fails in the mail-voting reader, not the "no parser" fallthrough
    import types

    with pytest.raises(turnout.TurnoutParseError, match="could not read the mail-voting PDF"):
        turnout._parse_file(
            tmp_path / "no-such.pdf",
            types.SimpleNamespace(source_id="x"),
            "GP26",
            "mail-voting",
            "county",
        )


# provisional: "Official Provisional Voting", one page at two tables. The county table
# casts, accepts in full, accepts in part and rejects ballots (the accepted/rejected
# counts, each followed by its %); there is no Total column to read -- the row's
# arithmetic is full + part + rejected == cast. Measures are cast, accepted-in-full,
# accepted-in-part, rejected. The state row prints as "State Totals", the counties'
# sums. The reasons table is the state's rejected provisionals by their printed codes
# ("01 - Applicant is not registered to vote", "GP26 Other Rejection"), published like
# the mail-in sheet's, with the shares the file prints at one decimal.

PROVISIONAL_HEADER = [
    "County",
    "Provisional\nBallots Cast",
    "# Accepted in Full",
    "",
    "# Accepted in Part",
    "",
    "# Rejected",
    "",
]

PROVISIONAL_ROWS = [
    PROVISIONAL_HEADER,
    ["Allegany", "284", "220", "77.5%", "7", "2.5%", "57", "20.1%"],
    ["Anne Arundel", "3,764", "2,422", "64.3%", "371", "9.9%", "971", "25.8%"],
    ["Worcester", "263", "222", "84.4%", "20", "7.6%", "21", "8.0%"],
    ["State Totals", "4,311", "2,864", "66.4%", "398", "9.2%", "1,049", "24.3%"],
    # the second table: the state's 1,049 rejected provisionals (the excerpt's own three
    # counties' rejected counts), by their printed reason codes
    ["01 - Applicant is not registered to vote", "7", "0.7%"],
    ["03 - Applicant voted the wrong party primary ballot", "371", "35.4%"],
    ["05 - Applicant not eligible to receive a provisional ballot", "383", "36.5%"],
    ["07 - Incomplete information was provided on the application", "66", "6.3%"],
    ["GP26 Other Rejection", "222", "21.2%"],
    ["Total", "1,049", ""],
]


def test_provisional_parses_the_county_rows():
    as_of, df = turnout.parse_provisional_rows(
        PROVISIONAL_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert as_of == "2026-07-23"
    assert len(df) == 3 * 4 + 5  # three counties x four measures, plus five reasons
    assert set(df["measure"]) == {"cast", "accepted-in-full", "accepted-in-part", "rejected"}
    assert set(df["family"]) == {"provisional"}
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    allegany = df.filter((df["county"] == "Allegany") & (df["category"] == "ALL"))
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "cast": 284,
        "accepted-in-full": 220,
        "accepted-in-part": 7,
        "rejected": 57,
    }


def test_provisional_reasons_publish_their_printed_codes():
    as_of, df = turnout.parse_provisional_rows(
        PROVISIONAL_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    reasons = df.filter(df["category"] != "ALL")
    assert set(reasons["category"]) == {
        "01 - Applicant is not registered to vote",
        "03 - Applicant voted the wrong party primary ballot",
        "05 - Applicant not eligible to receive a provisional ballot",
        "07 - Incomplete information was provided on the application",
        "GP26 Other Rejection",
    }
    assert set(reasons["measure"]) == {"rejected"}
    assert reasons["value"].sum() == 1049  # the state row's rejected cell


def test_provisional_state_totals_are_checked_not_published():
    as_of, df = turnout.parse_provisional_rows(
        PROVISIONAL_ROWS, "GP26", "county", as_of="2026-07-23"
    )
    assert "State Totals" not in set(df["county"])
    cast = df.filter((df["measure"] == "cast") & (df["category"] == "ALL"))["value"].sum()
    assert cast == 4311


PROVISIONAL_FAILURES = (
    # a cast count its full/part/rejected do not sum to
    ((1, 1), "285", "do not add up"),
    # an accepted-in-full share that is not the count over the cast
    ((1, 3), "99.9%", "share"),
    # a rejected share that is not the count over the cast
    ((2, 7), "99.9%", "share"),
)


def test_provisional_rejects_the_torn_arithmetic():
    for (row, col), value, where in PROVISIONAL_FAILURES:
        torn = [r[:] for r in PROVISIONAL_ROWS]
        torn[row][col] = value
        with pytest.raises(turnout.TurnoutParseError, match=where):
            turnout.parse_provisional_rows(torn, "GP26", "county", as_of="2026-07-23")


def test_provisional_rejects_reasons_that_do_not_add_up_to_the_state():
    torn = [r[:] for r in PROVISIONAL_ROWS]
    torn[10][1] = "1,050"  # the printed reasons total no longer equals the counties' sum
    with pytest.raises(turnout.TurnoutParseError, match="the printed reasons total says 1050"):
        turnout.parse_provisional_rows(torn, "GP26", "county", as_of="2026-07-23")
    short = [r[:] for r in PROVISIONAL_ROWS]
    short[7] = ["05 - Applicant not eligible to receive a provisional ballot", "370", "35.3%"]
    with pytest.raises(turnout.TurnoutParseError, match="the printed reasons total says 1049"):
        turnout.parse_provisional_rows(short, "GP26", "county", as_of="2026-07-23")


def test_provisional_rejects_a_duplicated_county_row():
    dup = [r[:] for r in PROVISIONAL_ROWS]
    dup.insert(2, PROVISIONAL_ROWS[1][:])
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_provisional_rows(dup, "GP26", "county", as_of="2026-07-23")


def test_provisional_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in PROVISIONAL_ROWS]
    bad[1][0] = "Prince Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_provisional_rows(bad, "GP26", "county", as_of="2026-07-23")


def test_parse_file_reaches_the_provisional_pdf(tmp_path):
    # the same dispatch proof as the canvass family's: a PDF the readers cannot open
    # fails in the provisional reader, not the "no parser" fallthrough
    import types

    with pytest.raises(turnout.TurnoutParseError, match="could not read the provisional PDF"):
        turnout._parse_file(
            tmp_path / "no-such.pdf",
            types.SimpleNamespace(source_id="x"),
            "GP26",
            "provisional",
            "county",
        )


# early-voting: the six "EarlyVoting ..." files, one parser over their five grains.
# Every row prints its eight day counts (DAY1..DAY8), their total, and the eligible
# actives the file's own footnote dates at an earlier day -- published as measures
# day-1 .. day-8, 'total' and 'eligible'. The printed %DayN / %Total cells are shares
# of the row's eligible actives and are never stored nor checked: the live CD file's
# own per-county Total rows print shares against a different denominator than their
# day counts (Allegany's Total row is 222 over the same 37,946 its county row shows,
# and prints 0.57% where the county row prints 0.59%), so no single rule reproduces
# the file's shares. The checks that do run: a row's eight days sum to its total, a
# county-party block's TOTAL row and a district file's county "Total" row equal the
# rows above them per column, and the by-County / same-day files' state row equals
# the counties' column sums.

EV_COUNTY_HEADER = [
    "County Name",
    "DAY1",
    "%Day1",
    "DAY2",
    "%Day2",
    "DAY3",
    "%Day3",
    "DAY4",
    "%Day4",
    "DAY5",
    "%Day5",
    "DAY6",
    "%Day6",
    "DAY7",
    "%Day7",
    "DAY8",
    "%Day8",
    "TOTAL",
    "%Total",
    "Eligible Actives",
]


def ev_county_row(county, d1, d2, d3, d4, d5, d6, d7, d8, total, eligible):
    # the day counts sit where the file prints them; the printed %Day cells between
    # them are shares of the row's eligible actives, which none of these files'
    # shares reproduce from their own rows, so the fixture marks them as text where
    # the real file prints a percentage -- the parser reads neither of them
    return [
        county,
        d1,
        "%Day1",
        d2,
        "%Day2",
        d3,
        "%Day3",
        d4,
        "%Day4",
        d5,
        "%Day5",
        d6,
        "%Day6",
        d7,
        "%Day7",
        d8,
        "%Day8",
        total,
        "%Total",
        eligible,
    ]


# The three real county rows (Allegany, Anne Arundel, Worcester as printed), and the
# excerpt's own state row -- the three counties' sums, with its shares recomputed off
# them, because the excerpt cannot carry the other 21 counties' share of the 185,031.

EV_COUNTY_ROWS = [
    EV_COUNTY_HEADER,
    ev_county_row(
        "Allegany", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "37,946"
    ),
    ev_county_row(
        "Anne Arundel",
        "1,764",
        "1,497",
        "814",
        "739",
        "1,596",
        "1,845",
        "2,219",
        "3,354",
        "13,828",
        "301,320",
    ),
    ev_county_row(
        "Worcester", "258", "199", "78", "45", "193", "241", "249", "303", "1,566", "33,718"
    ),
    ev_county_row(
        "TOTAL",
        "2,244",
        "1,873",
        "955",
        "837",
        "1,958",
        "2,285",
        "2,684",
        "3,959",
        "16,795",
        "372,984",
    ),
]

EV_PARTY_HEADER = [
    "County",
    "PARTY CODE",
    "Day1",
    "%Day1",
    "Day2",
    "%Day2",
    "Day3",
    "%Day3",
    "Day4",
    "% Day4",
    "Day5",
    "%Day5",
    "Day6",
    "%Day6",
    "Day7",
    "%Day7",
    "Day8",
    "%Day8",
    "Total",
    "%Total",
    "Eligible Actives",
]


def ev_party_row(county, code, d1, d2, d3, d4, d5, d6, d7, d8, total, eligible):
    return [
        county,
        code,
        d1,
        "%Day1",
        d2,
        "%Day2",
        d3,
        "%Day3",
        d4,
        "% Day4",
        d5,
        "%Day5",
        d6,
        "%Day6",
        d7,
        "%Day7",
        d8,
        "%Day8",
        total,
        "%Total",
        eligible,
    ]


# Allegany's and Worcester's own blocks: the party rows as the file prints them, each
# county's block closed by its PARTY CODE == TOTAL row
EV_PARTY_ROWS = [
    EV_PARTY_HEADER,
    ev_party_row(
        "Allegany", "DEM", "59", "67", "16", "17", "50", "43", "60", "106", "418", "10,758"
    ),
    ev_party_row("", "GRN", "0", "0", "0", "0", "0", "0", "0", "0", "0", "32"),
    ev_party_row("", "OTH", "1", "0", "0", "0", "0", "0", "2", "2", "5", "286"),
    ev_party_row("", "REP", "156", "110", "47", "36", "117", "156", "152", "188", "962", "23,608"),
    ev_party_row("", "UNA", "6", "0", "0", "0", "2", "0", "2", "6", "16", "3,226"),
    ev_party_row("", "WCP", "0", "0", "0", "0", "0", "0", "0", "0", "0", "36"),
    ev_party_row(
        "", "TOTAL", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "37,946"
    ),
    ev_party_row(
        "Worcester", "DEM", "86", "69", "29", "17", "52", "58", "80", "93", "484", "13,257"
    ),
    # the live file re-prints the county on a block's first row after a page break
    ev_party_row("Worcester", "GRN", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"),
    ev_party_row("", "OTH", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"),
    ev_party_row(
        "", "REP", "172", "130", "49", "28", "141", "183", "169", "210", "1,082", "20,461"
    ),
    ev_party_row("", "UNA", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"),
    ev_party_row("", "WCP", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"),
    ev_party_row(
        "", "TOTAL", "258", "199", "78", "45", "193", "241", "249", "303", "1,566", "33,718"
    ),
]

EV_CD_HEADER = [
    "County Name",
    "CONGRESSIONAL\nDISTRICT",
    *EV_COUNTY_HEADER[1:],
]


def ev_district_row(county, code, d1, d2, d3, d4, d5, d6, d7, d8, total, eligible):
    return [
        county,
        code,
        d1,
        "%Day1",
        d2,
        "%Day2",
        d3,
        "%Day3",
        d4,
        "%Day4",
        d5,
        "%Day5",
        d6,
        "%Day6",
        d7,
        "%Day7",
        d8,
        "%Day8",
        total,
        "%Total",
        eligible,
    ]


# The congressional file's grain: a county's district rows, closed by the county's own
# "Total" row -- which is the by-County file's county row, as Anne Arundel's proves.
EV_CONGRESSIONAL_ROWS = [
    EV_CD_HEADER,
    ev_district_row(
        "Allegany", "06", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "37,946"
    ),
    ev_district_row(
        "", "Total", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "37,946"
    ),
    ev_district_row(
        "Anne Arundel",
        "03",
        "1,194",
        "1,038",
        "533",
        "488",
        "1,085",
        "1,245",
        "1,543",
        "2,250",
        "9,376",
        "218,152",
    ),
    ev_district_row(
        "", "05", "570", "459", "281", "251", "511", "600", "676", "1,104", "4,452", "83,168"
    ),
    ev_district_row(
        "",
        "Total",
        "1,764",
        "1,497",
        "814",
        "739",
        "1,596",
        "1,845",
        "2,219",
        "3,354",
        "13,828",
        "301,320",
    ),
]

# The legislative file's grain, Allegany's three subdistricts and their county total.
EV_LEGISLATIVE_ROWS = [
    [x if x != "CONGRESSIONAL\nDISTRICT" else "LEGISLATIVE\nDISTRICT" for x in EV_CD_HEADER],
    ev_district_row(
        "Allegany", "01A", "28", "31", "9", "8", "27", "31", "30", "48", "212", "7,122"
    ),
    ev_district_row(
        "", "01B", "178", "133", "51", "42", "125", "144", "155", "229", "1,057", "27,335"
    ),
    ev_district_row("", "01C", "16", "13", "3", "3", "17", "24", "31", "25", "132", "3,489"),
    ev_district_row(
        "", "Total", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "37,946"
    ),
]

# The councilmanic file's grain: counties with councils print their districts (Harford
# spells them A..F), counties without one print their county row at an empty district,
# and some rows carry no eligible cell at all (the real file's) -- the days and total
# publish without it.
EV_COUNCILMANIC_ROWS = [
    [x if x != "LEGISLATIVE\nDISTRICT" else "COUNCILMANICDIST\nRICT" for x in EV_CD_HEADER],
    ev_district_row(
        "Allegany", "", "222", "177", "63", "53", "169", "199", "216", "302", "1,401", "42,866"
    ),
    ev_district_row(
        "Harford", "A", "207", "134", "80", "65", "158", "144", "180", "297", "1,265", "21,748"
    ),
    ev_district_row(
        "", "B", "213", "214", "100", "98", "243", "263", "260", "390", "1,781", "25,880"
    ),
    ev_district_row(
        "", "C", "295", "338", "138", "96", "276", "294", "342", "473", "2,252", "23,492"
    ),
    ev_district_row(
        "Baltimore County",
        "008",
        "242",
        "253",
        "143",
        "147",
        "265",
        "313",
        "361",
        "554",
        "2,278",
        "",
    ),
]

EV_LOCATION_HEADER = [
    "COUNTY",
    "LOCATION NAME",
    *EV_COUNTY_HEADER[1:],
]

# The location file's grain: the county prints on its first location's row and the
# next locations inherit it.
EV_LOCATION_ROWS = [
    EV_LOCATION_HEADER,
    ev_district_row(
        "Allegany",
        "Allegany County Office\nComplex",
        "222",
        "177",
        "63",
        "53",
        "169",
        "199",
        "216",
        "302",
        "1,401",
        "37,946",
    ),
    ev_district_row(
        "Anne Arundel",
        "Anne Arundel County Board of\nElections",
        "252",
        "178",
        "119",
        "90",
        "209",
        "229",
        "273",
        "447",
        "1,797",
        "301,320",
    ),
    ev_district_row(
        "",
        "Broadneck Library",
        "118",
        "91",
        "38",
        "54",
        "91",
        "144",
        "158",
        "190",
        "884",
        "301,320",
    ),
]


EV_MEASURES = {f"day-{n}" for n in range(1, 9)} | {"total", "eligible"}


def test_early_voting_county_rows_publish_their_days():
    as_of, df = turnout.parse_early_voting_rows(
        EV_COUNTY_ROWS, "GP26", "county", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert len(df) == 3 * 10  # three counties x ten measures
    assert set(df["measure"]) == EV_MEASURES
    assert set(df["family"]) == {"early-voting"} and set(df["geography"]) == {"county"}
    assert set(df["district"]) == {""} and set(df["party"]) == {"ALL"}
    assert set(df["category"]) == {"ALL"}
    allegany = df.filter(df["county"] == "Allegany")
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "day-1": 222,
        "day-2": 177,
        "day-3": 63,
        "day-4": 53,
        "day-5": 169,
        "day-6": 199,
        "day-7": 216,
        "day-8": 302,
        "total": 1401,
        "eligible": 37946,
    }


def test_early_voting_county_state_row_is_checked_not_published():
    as_of, df = turnout.parse_early_voting_rows(
        EV_COUNTY_ROWS, "GP26", "county", as_of="2026-06-23"
    )
    assert "TOTAL" not in set(df["county"])
    assert df.filter(df["measure"] == "day-1")["value"].sum() == 2244


def test_early_voting_county_requires_its_state_row():
    with pytest.raises(turnout.TurnoutParseError, match="TOTAL row|Statewide"):
        turnout.parse_early_voting_rows(EV_COUNTY_ROWS[:-1], "GP26", "county", as_of="2026-06-23")


def test_early_voting_county_state_row_must_agree_with_the_counties():
    torn = [r[:] for r in EV_COUNTY_ROWS]
    torn[4][1] = "2,245"  # the printed TOTAL row's D1, off by one
    torn[4][17] = "16,796"  # ... and its total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="county rows sum to 2244"):
        turnout.parse_early_voting_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_early_voting_rejects_a_row_whose_days_do_not_sum_to_its_total():
    torn = [r[:] for r in EV_COUNTY_ROWS]
    torn[1][17] = "1,402"  # Allegany's printed total
    with pytest.raises(turnout.TurnoutParseError, match="do not add up"):
        turnout.parse_early_voting_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_early_voting_rejects_a_duplicated_county_row():
    dup = [r[:] for r in EV_COUNTY_ROWS]
    dup.insert(2, EV_COUNTY_ROWS[1][:])
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_early_voting_rows(dup, "GP26", "county", as_of="2026-06-23")


def test_early_voting_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in EV_COUNTY_ROWS]
    bad[1][0] = "Prince Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_early_voting_rows(bad, "GP26", "county", as_of="2026-06-23")


def test_early_voting_rejects_a_blank_day_cell():
    torn = [r[:] for r in EV_COUNTY_ROWS]
    torn[2][1] = ""  # Anne Arundel's first day
    with pytest.raises(turnout.TurnoutParseError, match="cell is blank"):
        turnout.parse_early_voting_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_early_voting_party_rows_publish_by_party():
    as_of, df = turnout.parse_early_voting_rows(
        EV_PARTY_ROWS, "GP26", "county-party", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["party"]) == {"DEM", "GRN", "OTH", "REP", "UNA", "WCP"}
    assert set(df["geography"]) == {"county-party"}
    assert len(df) == 12 * 10  # twelve party rows x ten measures
    dem = df.filter((df["county"] == "Allegany") & (df["party"] == "DEM"))
    assert dict(zip(dem["measure"], dem["value"])) == {
        "day-1": 59,
        "day-2": 67,
        "day-3": 16,
        "day-4": 17,
        "day-5": 50,
        "day-6": 43,
        "day-7": 60,
        "day-8": 106,
        "total": 418,
        "eligible": 10758,
    }
    # the county's block TOTAL row is the party rows' sums, checked and not published
    assert "TOTAL" not in set(df["party"])
    assert set(df["county"]) <= {"Allegany", "Worcester"}


def test_early_voting_party_blocks_recover_their_county():
    as_of, df = turnout.parse_early_voting_rows(
        EV_PARTY_ROWS, "GP26", "county-party", as_of="2026-06-23"
    )
    worcester = set(df.filter(df["county"] == "Worcester")["party"])
    assert worcester == {"DEM", "GRN", "OTH", "REP", "UNA", "WCP"}  # the blank county cells


def test_early_voting_rejects_a_block_total_that_disagrees_with_its_party_rows():
    torn = [r[:] for r in EV_PARTY_ROWS]
    torn[7][2] = "223"  # Allegany's block TOTAL's printed day-1 count ...
    torn[7][18] = "1,402"  # ... and its total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="party detail rows sum to 222"):
        turnout.parse_early_voting_rows(torn, "GP26", "county-party", as_of="2026-06-23")


def test_early_voting_congressional_rows_print_their_own_codes():
    as_of, df = turnout.parse_early_voting_rows(
        EV_CONGRESSIONAL_ROWS, "GP26", "congressional", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["district"]) == {"06", "03", "05"}  # verbatim, as printed
    assert set(df["county"]) == {"Allegany", "Anne Arundel"}  # the rows carry their county
    aa = df.filter((df["district"] == "03") & (df["measure"] == "total"))
    assert aa["value"].item() == 9376
    assert len(df) == 3 * 10  # three district rows x ten measures


def test_early_voting_county_totals_are_checked_not_published():
    # the congressional file's per-county "Total" row is the rows above it
    as_of, df = turnout.parse_early_voting_rows(
        EV_CONGRESSIONAL_ROWS, "GP26", "congressional", as_of="2026-06-23"
    )
    assert "Total" not in set(df["district"])
    torn = [r[:] for r in EV_CONGRESSIONAL_ROWS]
    torn[5][2] = "1,765"  # Anne Arundel's printed county total's day-1 count ...
    torn[5][18] = "13,829"  # ... and its total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="district detail rows sum to 1764"):
        turnout.parse_early_voting_rows(torn, "GP26", "congressional", as_of="2026-06-23")


def test_early_voting_legislative_rows_print_their_own_codes():
    as_of, df = turnout.parse_early_voting_rows(
        EV_LEGISLATIVE_ROWS, "GP26", "legislative", as_of="2026-06-23"
    )
    assert set(df["district"]) == {"01A", "01B", "01C"}
    c = df.filter((df["district"] == "01C") & (df["measure"].is_in(["total", "eligible"])))
    assert dict(zip(c["measure"], c["value"])) == {"total": 132, "eligible": 3489}
    # the counties' own total: 01A + 01B + 01C sum to the same 1,401 the county file
    # prints for Allegany, per column -- which is the block's "Total" row (checked,
    # and so never published)
    day1 = df.filter(df["measure"] == "day-1")["value"].sum()
    assert day1 == 222
    eligible = df.filter(df["measure"] == "eligible")["value"].sum()
    assert eligible == 37946


def test_early_voting_councilmanic_rows_publish_blank_districts():
    # the councilmanic file's rows carry the district codes as they print (Harford's
    # council spells A..F); a county with no council prints its county row against an
    # empty district, and a row whose eligible cell the file does not print publishes
    # without it
    as_of, df = turnout.parse_early_voting_rows(
        EV_COUNCILMANIC_ROWS, "GP26", "councilmanic", as_of="2026-06-23"
    )
    assert set(df["district"]) == {"", "A", "B", "C", "008"}
    assert {"Allegany", "Harford", "Baltimore County"} <= set(df["county"])
    blank = df.filter((df["district"] == "008") & (df["measure"] == "total"))
    assert blank["value"].item() == 2278
    assert df.filter((df["district"] == "008") & (df["measure"] == "eligible")).height == 0
    assert df.filter((df["district"] == "008") & (df["measure"] == "day-7"))["value"].item() == 361


def test_early_voting_location_rows_carry_their_location():
    as_of, df = turnout.parse_early_voting_rows(
        EV_LOCATION_ROWS, "GP26", "location", as_of="2026-06-23"
    )
    assert set(df["district"]) == {
        "Allegany County Office Complex",
        "Anne Arundel County Board of Elections",
        "Broadneck Library",
    }  # the line wrap inside a location cell collapses
    anne_arundel = set(df.filter(df["county"] == "Anne Arundel")["district"])
    assert anne_arundel == {
        "Anne Arundel County Board of Elections",
        "Broadneck Library",
    }  # the continuation row inherited its county


def test_early_voting_rejects_a_renamed_column():
    bad = [r[:] for r in EV_COUNTY_ROWS]
    bad[0][19] = "Eligible Activer"  # the eligible column's label, mangled
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized column label"):
        turnout.parse_early_voting_rows(bad, "GP26", "county", as_of="2026-06-23")


def test_early_voting_rejects_a_county_geography_file_at_the_party_grain():
    # the by-Party file prints a PARTY CODE column; the county files print none
    with pytest.raises(turnout.TurnoutParseError, match="PARTY CODE|party"):
        turnout.parse_early_voting_rows(EV_PARTY_ROWS, "GP26", "county", as_of="2026-06-23")


def test_early_voting_only_runs_at_its_geographies():
    for geography in (
        "county",
        "county-party",
        "congressional",
        "legislative",
        "councilmanic",
        "location",
    ):
        as_of, df = turnout.parse_early_voting_rows(
            {
                "county": EV_COUNTY_ROWS,
                "county-party": EV_PARTY_ROWS,
                "congressional": EV_CONGRESSIONAL_ROWS,
                "legislative": EV_LEGISLATIVE_ROWS,
                "councilmanic": EV_COUNCILMANIC_ROWS,
                "location": EV_LOCATION_ROWS,
            }[geography],
            "GP26",
            geography,
            as_of="2026-06-23",
        )
        assert as_of == "2026-06-23", geography
    with pytest.raises(turnout.TurnoutParseError, match="geography"):
        turnout.parse_early_voting_rows(EV_COUNTY_ROWS, "GP26", "statewide", as_of="2026-06-23")


def test_parse_file_reaches_the_early_voting_pdfs(tmp_path):
    # the same dispatch proof as the canvass family's: a PDF the readers cannot open
    # fails in the early-voting reader, which is where the six files' grains go
    import types

    with pytest.raises(turnout.TurnoutParseError, match="could not read the early-voting PDF"):
        turnout._parse_file(
            tmp_path / "no-such.pdf",
            types.SimpleNamespace(source_id="x"),
            "GP26",
            "early-voting",
            "legislative",
        )


# same-day: the six "SameDayRegistration ..." files, the same five grains as the
# early-voting ones, at a printed sheet whose columns print the same day twice over --
# NEW registrations and ADDRCHG (currently registered voters with an address change)
# -- plus the election day's NEW ("NEW ED") and the three printed totals. The measures
# are the family's own printed vocabulary, and they are exactly these twenty:
# new-day-1 .. new-day-8, addrchg-day-1 .. addrchg-day-8, new-election-day, new-total,
# addrchg-total, total (the row's two totals together). A row's NEW day counts, with
# its election day, must sum to its printed NEW TOTAL; its address-change counts sum
# to the printed ADDRCHG TOTAL (the sheet prints them no election-day column, checked
# only through the row's total); and the two totals sum to the row's printed total.
# The state row is "TOTAL" in the county file's county cell and "Grand Total" in the
# location file's -- the rows' sums, checked and not published.

SAME_DAY_HEADER = [
    "COUNTY",
    "NEW\nDAY1",
    "ADDRCHG\nDAY1",
    "NEW\nDAY2",
    "ADDRCHG\nDAY2",
    "NEW\nDAY3",
    "ADDRCHG\nDAY3",
    "NEW\nDAY4",
    "ADDRCHG\nDAY4",
    "NEW\nDAY5",
    "ADDRCHG\nDAY5",
    "NEW\nDAY6",
    "ADDRCHG\nDAY6",
    "NEW\nDAY7",
    "ADDRCHG\nDAY7",
    "NEW\nDAY8",
    "ADDRCHG\nDAY8",
    "NEW\nED",
    "NEW\nTOTAL",
    "ADDRCHG\nTOTAL",
    "TOTAL",
]

SAME_DAY_PARTY_HEADER = ["COUNTY", "PARTY\nCODE", *SAME_DAY_HEADER[1:]]

SAME_DAY_MEASURE_VOCABULARY = (
    "new-day-1",
    "new-day-2",
    "new-day-3",
    "new-day-4",
    "new-day-5",
    "new-day-6",
    "new-day-7",
    "new-day-8",
    "addrchg-day-1",
    "addrchg-day-2",
    "addrchg-day-3",
    "addrchg-day-4",
    "addrchg-day-5",
    "addrchg-day-6",
    "addrchg-day-7",
    "addrchg-day-8",
    "new-election-day",
    "new-total",
    "addrchg-total",
    "total",
)


def same_day_row(county, code, new_days, addrchg_days, ed, new_total, addrchg_total, total):
    # the county files print no party column; the by-Party file adds the PARTY CODE one
    row = [county] if code is None else [county, code]
    for new, moved in zip(new_days, addrchg_days, strict=True):
        row += [new, moved]
    return row + [ed, new_total, addrchg_total, total]


# The county file's own Allegany row (the numbers the shape above pins), with Anne
# Arundel's and Worcester's beside it -- and the excerpt's printed TOTAL row, the three
# counties' sums, because the excerpt cannot carry the other 21 counties' cells.
SAME_DAY_COUNTY_ROWS = [
    SAME_DAY_HEADER,
    same_day_row(
        "Allegany",
        None,
        ("1", "0", "1", "2", "0", "0", "0", "1"),
        ("0", "0", "0", "0", "3", "0", "1", "3"),
        "4",
        "9",
        "7",
        "16",
    ),
    same_day_row(
        "Anne Arundel",
        None,
        ("3", "3", "1", "3", "2", "6", "4", "12"),
        ("1", "4", "0", "0", "3", "5", "6", "8"),
        "74",
        "108",
        "27",
        "135",
    ),
    same_day_row(
        "Worcester",
        None,
        ("1", "0", "0", "1", "0", "0", "0", "0"),
        ("1", "0", "0", "0", "1", "1", "3", "0"),
        "3",
        "5",
        "6",
        "11",
    ),
    same_day_row(
        "TOTAL",
        None,
        ("5", "3", "2", "6", "2", "6", "4", "13"),
        ("2", "4", "0", "0", "7", "6", "10", "11"),
        "81",
        "122",
        "40",
        "162",
    ),
]

# Allegany's own block of the by-Party file: the six party rows as it prints them, no
# rollup and no state row of its own.
SAME_DAY_PARTY_ROWS = [
    SAME_DAY_PARTY_HEADER,
    same_day_row(
        "Allegany",
        "DEM",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "1"),
        "1",
        "1",
        "1",
        "2",
    ),
    same_day_row(
        "",
        "REP",
        ("1", "0", "1", "2", "0", "0", "0", "1"),
        ("0", "0", "0", "0", "3", "0", "1", "0"),
        "3",
        "8",
        "4",
        "12",
    ),
    same_day_row(
        "",
        "GRN",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        "0",
        "0",
        "0",
        "0",
    ),
    same_day_row(
        "",
        "WCP",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        "0",
        "0",
        "0",
        "0",
    ),
    same_day_row(
        "",
        "UNA",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "2"),
        "0",
        "0",
        "2",
        "2",
    ),
    same_day_row(
        "",
        "OTH",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        "0",
        "0",
        "0",
        "0",
    ),
]


def test_same_day_publishes_its_printed_measure_vocabulary():
    as_of, df = turnout.parse_same_day_rows(
        SAME_DAY_COUNTY_ROWS, "GP26", "county", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["measure"]) == set(SAME_DAY_MEASURE_VOCABULARY)
    assert set(df["family"]) == {"same-day"}
    assert set(df["geography"]) == {"county"} and set(df["district"]) == {""}
    assert set(df["party"]) == {"ALL"} and set(df["category"]) == {"ALL"}
    assert len(df) == 3 * 20  # three counties x twenty measures


def test_same_day_allegany_sums_as_the_file_prints_it():
    # the real Allegany row: the new counts 1+0+1+2+0+0+0+1 = 5 with an election day of
    # 4 for a NEW TOTAL of 9; the address-change counts 0+0+0+0+3+0+1+3 = 7 for their
    # own total; and the two together 16
    as_of, df = turnout.parse_same_day_rows(
        SAME_DAY_COUNTY_ROWS, "GP26", "county", as_of="2026-06-23"
    )
    allegany = df.filter(df["county"] == "Allegany")
    assert dict(zip(allegany["measure"], allegany["value"])) == {
        "new-day-1": 1,
        "addrchg-day-1": 0,
        "new-day-2": 0,
        "addrchg-day-2": 0,
        "new-day-3": 1,
        "addrchg-day-3": 0,
        "new-day-4": 2,
        "addrchg-day-4": 0,
        "new-day-5": 0,
        "addrchg-day-5": 3,
        "new-day-6": 0,
        "addrchg-day-6": 0,
        "new-day-7": 0,
        "addrchg-day-7": 1,
        "new-day-8": 1,
        "addrchg-day-8": 3,
        "new-election-day": 4,
        "new-total": 9,
        "addrchg-total": 7,
        "total": 16,
    }


def test_same_day_state_row_is_checked_not_published():
    as_of, df = turnout.parse_same_day_rows(
        SAME_DAY_COUNTY_ROWS, "GP26", "county", as_of="2026-06-23"
    )
    assert "TOTAL" not in set(df["county"]) and "Grand Total" not in set(df["county"])
    new_total = df.filter(df["measure"] == "new-total")["value"].sum()
    assert new_total == 122  # what the state row's own arithmetic is checked against
    torn = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    torn[4][15] = "14"  # the printed TOTAL row's NEW day-8 count, off by one ...
    torn[4][18] = "123"  # ... with its new total and ...
    torn[4][20] = "163"  # ... its row total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="county rows sum to 13"):
        turnout.parse_same_day_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_same_day_requires_the_county_files_state_row():
    with pytest.raises(turnout.TurnoutParseError, match="Statewide|TOTAL row"):
        turnout.parse_same_day_rows(SAME_DAY_COUNTY_ROWS[:-1], "GP26", "county", as_of="2026-06-23")


def test_same_day_rejects_a_new_total_that_its_days_and_election_day_do_not_reach():
    torn = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    torn[2][18] = "109"  # Anne Arundel's printed NEW TOTAL, off by one
    with pytest.raises(turnout.TurnoutParseError, match="do not add up|not add up"):
        turnout.parse_same_day_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_same_day_rejects_an_address_change_total_its_days_do_not_reach():
    torn = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    torn[2][19] = "108"  # keep the printed NEW TOTAL ...
    torn[2][12] = "6"  # ... move an address-change count off Anne Arundel's total
    with pytest.raises(turnout.TurnoutParseError, match="address-change counts do not add up"):
        turnout.parse_same_day_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_same_day_rejects_a_row_total_off_its_two_parts():
    torn = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    torn[1][20] = "17"  # Allegany's printed total, off by one
    with pytest.raises(turnout.TurnoutParseError, match="is not the printed total"):
        turnout.parse_same_day_rows(torn, "GP26", "county", as_of="2026-06-23")


def test_same_day_rejects_a_duplicated_county_row():
    dup = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    dup.insert(2, SAME_DAY_COUNTY_ROWS[1][:])
    with pytest.raises(turnout.TurnoutParseError, match="more than once"):
        turnout.parse_same_day_rows(dup, "GP26", "county", as_of="2026-06-23")


def test_same_day_rejects_an_unrecognized_county_label():
    bad = [r[:] for r in SAME_DAY_COUNTY_ROWS]
    bad[1][0] = "Prince Georges"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized county"):
        turnout.parse_same_day_rows(bad, "GP26", "county", as_of="2026-06-23")


def test_same_day_party_rows_publish_by_party():
    as_of, df = turnout.parse_same_day_rows(
        SAME_DAY_PARTY_ROWS, "GP26", "county-party", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["party"]) == {"DEM", "REP", "GRN", "WCP", "UNA", "OTH"}
    assert set(df["geography"]) == {"county-party"} and set(df["district"]) == {""}
    assert len(df) == 6 * 20  # six party rows x twenty measures
    rep = df.filter(df["party"] == "REP")
    assert rep["county"].unique().to_list() == ["Allegany"]  # the blank county cells

    # the party rows add up to the county's by-County row -- not a printed layer here,
    # but the arithmetic the two files share (Allegany's REP row's eight new days sum
    # with its election day to the county's printed NEW TOTAL)
    assert df.filter(df["measure"] == "new-total")["value"].sum() == 1 + 8 + 0 + 0 + 0 + 0


def test_same_day_rejects_an_unrecognized_party_code():
    bad = [r[:] for r in SAME_DAY_PARTY_ROWS]
    bad[2][1] = "DEMOCRATS"
    with pytest.raises(turnout.TurnoutParseError, match="unrecognized party code"):
        turnout.parse_same_day_rows(bad, "GP26", "county-party", as_of="2026-06-23")


def test_parse_file_reaches_the_same_day_pdfs(tmp_path):
    # the same dispatch proof as the canvass family's: a PDF the readers cannot open
    # fails in the same-day reader, which is where the six files' grains go
    import types

    with pytest.raises(turnout.TurnoutParseError, match="could not read the same-day PDF"):
        turnout._parse_file(
            tmp_path / "no-such.pdf",
            types.SimpleNamespace(source_id="x"),
            "GP26",
            "same-day",
            "location",
        )


# The congressional file's grain, Montgomery's three districts: a county re-prints on
# the rows it starts (the live file prints Montgomery on its 04 and 06 rows, and leaves
# its 08 row's county cell empty, which is the continuation the by-Party files carry).
SD_CONGRESSIONAL_ROWS = [
    ["COUNTY", "CONGRESSIONAL\nDISTRICT", *SAME_DAY_HEADER[1:]],
    same_day_row(
        "Montgomery",
        "04",
        ("1", "0", "0", "0", "2", "1", "0", "1"),
        ("0", "0", "1", "0", "2", "0", "3", "3"),
        "7",
        "12",
        "9",
        "21",
    ),
    same_day_row(
        "Montgomery",
        "06",
        ("3", "5", "2", "4", "1", "3", "6", "6"),
        ("0", "6", "3", "4", "5", "3", "4", "12"),
        "62",
        "92",
        "37",
        "129",
    ),
    same_day_row(
        "",
        "08",
        ("10", "11", "6", "7", "8", "15", "14", "28"),
        ("12", "8", "11", "12", "9", "17", "14", "33"),
        "252",
        "351",
        "116",
        "467",
    ),
]

# The location file's grain: the county, then the location it printed over -- and the
# sheet's own Grand Total, the location rows' sums.
SD_LOCATION_ROWS = [
    ["COUNTY", "LOCATION NAME", *SAME_DAY_HEADER[1:]],
    same_day_row(
        "Allegany",
        "Allegany County\nOffice Complex",
        ("1", "0", "1", "2", "0", "0", "0", "1"),
        ("0", "0", "0", "0", "3", "0", "1", "3"),
        "0",
        "5",
        "7",
        "12",
    ),
    same_day_row(
        "",
        "Eckhart School",
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        "1",
        "1",
        "0",
        "1",
    ),
    same_day_row(
        "Anne Arundel",
        "Anne Arundel\nCounty Board of\nElections",
        ("1", "1", "1", "1", "0", "1", "0", "4"),
        ("0", "0", "0", "0", "0", "0", "0", "0"),
        "0",
        "9",
        "0",
        "9",
    ),
    # the same-day location file's own state row: "Grand Total" in the county cell,
    # the location cell it prints over empty
    same_day_row(
        "Grand Total",
        "",
        ("2", "1", "2", "3", "0", "1", "0", "5"),
        ("0", "0", "0", "0", "3", "0", "1", "3"),
        "1",
        "15",
        "7",
        "22",
    ),
]


def test_same_day_congressional_rows_print_their_own_codes():
    as_of, df = turnout.parse_same_day_rows(
        SD_CONGRESSIONAL_ROWS, "GP26", "congressional", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["district"]) == {"04", "06", "08"}  # verbatim, as printed
    assert set(df["county"]) == {"Montgomery"}  # the county cell re-prints and carries
    assert len(df) == 3 * 20


def test_same_day_location_rows_carry_their_location_and_their_county():
    as_of, df = turnout.parse_same_day_rows(
        SD_LOCATION_ROWS, "GP26", "location", as_of="2026-06-23"
    )
    assert as_of == "2026-06-23"
    assert set(df["district"]) == {
        "Allegany County Office Complex",  # the wrapped location text, one line
        "Eckhart School",
        "Anne Arundel County Board of Elections",
    }
    assert set(df.filter(df["county"] == "Anne Arundel")["district"]) == {
        "Anne Arundel County Board of Elections"
    }
    assert "Grand Total" not in set(df["county"])  # the state row is not published
    total = df.filter(df["measure"] == "total")["value"].sum()
    assert total == 22  # what the printed Grand Total row was checked against


def test_same_day_location_grand_total_must_agree_with_its_locations():
    torn = [r[:] for r in SD_LOCATION_ROWS]
    torn[-1][2] = "3"  # the printed Grand Total's NEW day-1 count, off by one ...
    torn[-1][19] = "16"  # ... with its new total and ...
    torn[-1][21] = "23"  # ... its total, so the row itself still adds up
    with pytest.raises(turnout.TurnoutParseError, match="county rows sum to 2"):
        turnout.parse_same_day_rows(torn, "GP26", "location", as_of="2026-06-23")
