"""The press-room turnout stat files: fetch, parse and reconcile one election at a time.

Each snapshot is identified by the "As of: <date>" the SBE prints in the document itself.
"""

from __future__ import annotations

import logging

import datetime
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import hashlib

import httpx
import polars as pl
from python_calamine import CalamineError, CalamineWorkbook

from skipjack.pipeline.vrar import COUNTY_NORMALIZE
from skipjack.pipeline.fetch import (
    ARCHIVES_DIR,
    MANIFEST_PATH,
    SOURCE_NAME,
    append_to_manifest,
)
from skipjack.pipeline.provenance import (
    ProvenanceSidecar,
    SourceRecord,
    read_manifest,
)

logger = logging.getLogger(__name__)

# results.elections.maryland.gov redirects here.
SBE_BASE = "https://elections.maryland.gov"


@dataclass
class FamilyFile:
    """One URL of one report family of one election."""

    family: str  # "requests" or "sent-returned"
    geography: str  # "county", "congressional", "legislative"
    kind: str  # "pdf" or "xlsx"
    url: str


@dataclass
class Election:
    """One election's press-room stat files, keyed by the SBE's election code."""

    code: str  # "GG26"
    name: str  # "2026 general election"
    election_day: str  # "2026-11-03"
    files: list[FamilyFile] = field(default_factory=list)


def _gg26_files() -> list[FamilyFile]:
    """The files published under press_room/2026_stats/GG26/ (see the press-room page)."""
    base = f"{SBE_BASE}/press_room/2026_stats/GG26"
    return [
        FamilyFile(
            "requests", "county", "pdf", f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"
        ),
        FamilyFile(
            "sent-returned", "county", "xlsx", f"{base}/Absentees_Sent_and_Returned_by_County.xlsx"
        ),
        FamilyFile(
            "sent-returned",
            "congressional",
            "xlsx",
            f"{base}/Absentees_Sent_and_Returned_by_Congressional.xlsx",
        ),
        FamilyFile(
            "sent-returned",
            "legislative",
            "xlsx",
            f"{base}/Absentees_Sent_and_Returned_by_Legislative.xlsx",
        ),
    ]


# --- GP26 and the certified families ----------------------------------------------------------


def _gp26_files() -> list[FamilyFile]:
    """Every file published under press_room/2026_stats/GP26/ except the election-night
    unofficial turnout report (see the press-room page). Both formats where both live;
    the parse prefers the xlsx with the PDF twin as the fallback. The by-party early
    voting and same-day files are their own geography ("county-party") so the two views
    of the same counties cannot collide."""
    base = f"{SBE_BASE}/press_room/2026_stats/GP26"
    files: list[FamilyFile] = []
    for family, geographies in (
        ("requests", ("county",)),
        ("requests-pdf-twin", ("county",)),
        ("sent-returned", ("county", "congressional", "legislative")),
        ("left-to-count", ("county", "congressional", "legislative")),
        ("official-turnout", ("county", "congressional", "legislative")),
        ("eligible-active", ("county", "congressional", "legislative", "councilmanic")),
        ("eligible-inactive", ("county",)),
    ):
        for geo in geographies:
            files.append(_gp26_xlsx(family, geo, base))
    for family, geography, stem in (
        ("early-voting", "county-party", "EarlyVoting  by Party"),  # two spaces, as published
        ("early-voting", "county", "EarlyVoting by County"),
        ("early-voting", "congressional", "EarlyVoting by Congressional District"),
        ("early-voting", "legislative", "EarlyVoting  by Legislative District"),
        ("early-voting", "councilmanic", "EarlyVoting  by Councilmanic District"),
        ("early-voting", "location", "EarlyVoting  by Location"),
        ("same-day", "county-party", "SameDayRegistration by Party"),
        ("same-day", "county", "SameDayRegistration by County"),
        ("same-day", "congressional", "SameDayRegistration by Congressional District"),
        ("same-day", "legislative", "SameDayRegistration by Legislative District"),
        ("same-day", "councilmanic", "SameDayRegistration by Councilmanic District"),
        ("same-day", "location", "SameDayRegistration by Location"),
    ):
        files.append(FamilyFile(family, geography, "pdf", f"{base}/{stem}.pdf"))
    for family, geography, stem in (
        ("county-canvass", "county", "Official County and Canvass"),
        ("votes-accepted", "county", "Official Votes Accepted by County and Canvass"),
        ("mail-voting", "county", "Official Mail-In Voting Voting"),
        ("provisional", "county", "Official Provisional Voting"),
    ):
        files.append(FamilyFile(family, geography, "pdf", f"{base}/{stem}.pdf"))
    return files


_GP26_XLSX_NAMES = {
    ("requests", "county"): "Mail-in_Ballot_Request_Counts_with_Chart.xlsx",
    ("sent-returned", "county"): "Absentees_Sent_and_Returned_by_County.xlsx",
    ("sent-returned", "congressional"): "Absentees_Sent_and_Returned_by_Congressional.xlsx",
    ("sent-returned", "legislative"): "Absentees_Sent_and_Returned_by_Legislative.xlsx",
    ("left-to-count", "county"): "Absentees_Ballots_Left_To_Count_by_County.xlsx",
    ("left-to-count", "congressional"): "Absentees_Ballots_Left_To_Count_by_Congressional.xlsx",
    ("left-to-count", "legislative"): "Absentees_Ballots_Left_To_Count_by_Legislative.xlsx",
    ("official-turnout", "county"): "Official_by_Party_and_County.xlsx",
    ("official-turnout", "congressional"): "Official_by_Party_and_Congressional.xlsx",
    ("official-turnout", "legislative"): "Official_by_Party_and_Legislative.xlsx",
    ("eligible-active", "county"): "Eligible_Active_Voters_by_County_-_GP26.xlsx",
    ("eligible-active", "congressional"): "Eligible_Active_Voters_by_Congressional_-_GP26.xlsx",
    ("eligible-active", "legislative"): "Eligible_Active_Voters_by_Legislative_-_GP26.xlsx",
    ("eligible-active", "councilmanic"): "Eligible_Active_Voters_by_Councilmanic_-_GP26.xlsx",
    ("eligible-inactive", "county"): "Eligible_Inactive_Voters_-_GP26.xlsx",
}


def _gp26_xlsx(family: str, geography: str, base: str) -> FamilyFile:
    """The xlsx of one GP26 file family, from the file names as published."""
    if family == "requests-pdf-twin":
        return FamilyFile(
            "requests", "county", "pdf", f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"
        )
    stem = _GP26_XLSX_NAMES[(family, geography)]
    return FamilyFile(family, geography, "xlsx", f"{base}/{stem}")


ELECTIONS: dict[str, Election] = {
    "GG26": Election(
        code="GG26",
        name="2026 general election",
        election_day="2026-11-03",
        files=_gg26_files(),
    ),
    "GP26": Election(
        code="GP26",
        name="2026 primary election",
        election_day="2026-06-23",
        files=_gp26_files(),
    ),
}

AS_OF_RE = re.compile(r"As of:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def extract_as_of_text(text: str) -> str | None:
    """The as-of date "YYYY-MM-DD" from an "As of: October 9, 2026 6 PM" line, else None.

    The regex accepts month/day shapes strptime rejects ("Sept 30, 2026", "October 32,
    2026"); those are treated as no date, not a parse error.
    """
    if not (found := AS_OF_RE.search(text)):
        return None
    try:
        parsed = datetime.datetime.strptime(found.group(1), "%B %d, %Y")
    except ValueError:
        return None
    return parsed.date().isoformat()


def extract_as_of_xlsx(path: Path) -> str | None:
    """The as-of date from the header cells of an SBE xlsx, None when absent or unreadable.

    The exports put the header block in odd spots (observed: sheet named after the file,
    the cell a few rows and columns in). Two failures mean "no date" and are caught here,
    never propagated: OSError from the filesystem, CalamineError from a corrupt,
    password-protected or malformed workbook (SBE's own exports are sometimes zip-corrupt,
    raising "ZipError: could not find EOCD", a CalamineError subclass). A file with no
    parseable date is not an error — the parse command decides later what it means.
    """
    try:
        wb = CalamineWorkbook.from_path(str(path))
        for sheet in wb.sheet_names:
            rows = wb.get_sheet_by_name(sheet).to_python(skip_empty_area=False)[:20]
            for row in rows:
                for cell in row[:20]:
                    if isinstance(cell, str) and "As of" in cell:
                        return extract_as_of_text(cell)
    except (OSError, CalamineError):
        return None
    return None


class TurnoutParseError(RuntimeError):
    """A turnout file did not parse with its own printed totals."""


# The site's canonical spelling (see the monthly reports); the SBE prints "Saint
# Mary's" in these files.
COUNTY_FIXES = {"Saint Mary's": "St. Mary's"}

# The canonical county spellings, as the monthly-report parser's SBE-label fixes
# normalize to: a requests row whose county is not one of these is a misread cell,
# not a 25th county.
CANONICAL_COUNTIES = frozenset(COUNTY_NORMALIZE.values())


def canonical_county(name: str) -> str:
    """Title-cased site spelling of a county name ("Saint Mary's" -> "St. Mary's").

    str.title() capitalizes the letter after an apostrophe ("Prince George'S"), so
    that is undone before the spelling fixes are applied; upper-case SBE spellings
    reach the fixes in the same shape as title-cased ones.
    """
    name = name.strip().title().replace("'S", "'s")
    return COUNTY_FIXES.get(name, name)


# Column order as printed by the requests PDF's header row.
REQUESTS_METHODS = ["agent", "faxed", "in-person", "mailed", "others", "print-at-home", "total"]

REQUESTS_ROW_RE = re.compile(
    r"^(?P<county>[A-Z][A-Za-z' .]+?)\s+(?P<numbers>(?:\d[\d,]*\s+){6}\d[\d,]*)\s*$"
)


def parse_requests_text(text: str, election: str) -> tuple[str, pl.DataFrame]:
    """The county x delivery-method table of one requests report's text.

    Returns the report's as-of date and one row per county and delivery method,
    party and category always "ALL" (the delivery method is the measure). The
    table is reconciled against itself before anything is returned: every county's
    methods must sum to that county's printed total, every printed TOTAL cell must
    equal the sum of its column, and every county label must be one of Maryland's
    canonical county names — a repeated, missing, mislabeled or misread county row
    fails the parse instead of publishing an unreconcilable table.
    """
    as_of = extract_as_of_text(text)
    if as_of is None:
        raise TurnoutParseError("No 'As of:' line in the requests report")
    rows: list[dict[str, str | int]] = []
    state_total: list[int] | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("TOTAL"):
            tokens = line.split()[1:]
            # A TOTAL-prefixed line is the totals row only when every following
            # token is a number; a footnote or heading that merely starts with
            # TOTAL is skipped like other header noise, and a table whose real
            # totals row never appears fails the guard below.
            if tokens and all(t.replace(",", "").isdigit() for t in tokens):
                state_total = [int(t.replace(",", "")) for t in tokens]
                if len(state_total) != len(REQUESTS_METHODS):
                    raise TurnoutParseError(
                        f"Unexpected TOTAL row in the requests report: {line!r}"
                    )
            continue
        if matched := REQUESTS_ROW_RE.match(line):
            county = canonical_county(matched["county"])
            if county not in CANONICAL_COUNTIES:
                raise TurnoutParseError(
                    f"Unrecognized county label {matched['county']!r} in the requests report"
                )
            nums = [int(n.replace(",", "")) for n in matched["numbers"].split()]
            if nums[-1] != sum(nums[:-1]):
                raise TurnoutParseError(f"{county}'s request counts do not add up: {nums}")
            for measure, value in zip(REQUESTS_METHODS, nums, strict=True):
                rows.append(
                    {
                        "as_of": as_of,
                        "election": election,
                        "family": "requests",
                        "geography": "county",
                        "county": county,
                        "district": "",
                        "category": "ALL",
                        "party": "ALL",
                        "measure": measure,
                        "value": value,
                    }
                )
    if state_total is None or not rows:
        raise TurnoutParseError("The requests report has no county rows or no TOTAL row")
    # One row set per county: a repeated county line (say, a two-page repeat of the
    # table) would reconcile through the per-county checks but publish duplicates.
    if len(rows) != len(REQUESTS_METHODS) * len({r["county"] for r in rows}):
        raise TurnoutParseError("A county row appears more than once in the requests report")
    for measure, printed in zip(REQUESTS_METHODS, state_total, strict=True):
        collected = sum(r["value"] for r in rows if r["measure"] == measure)
        if collected != printed:
            raise TurnoutParseError(
                f"County {measure} counts sum to {collected}, not the printed statewide total {printed}"
            )
    return as_of, pl.DataFrame(rows)


# The request workbook's delivery-method header labels as printed, each to its measure.
# The GG26 export spells the web column PRINT-AT-HOME (the PDF text above) and the GP26
# workbooks print WEB DELIVERY where their fixtures re-print WEB DELIVERED; both web
# spellings are one measure so a family's vocabulary does not fork on the SBE's wording.
REQUESTS_METHOD_CELLS: dict[str, str] = {
    "AGENT": "agent",
    "FAXED": "faxed",
    "IN PERSON": "in-person",
    "MAILED": "mailed",
    "OTHERS": "others",
    "WEB DELIVERED": "web-delivered",
    "WEB DELIVERY": "web-delivered",
    "PRINT AT HOME": "print-at-home",
    "PRINT-AT-HOME": "print-at-home",
    "TOTAL": "total",
}


def parse_requests_bytes(data: bytes, election: str) -> tuple[str | None, pl.DataFrame]:
    """One Mail-in Ballot Requests workbook as long rows, the way parse_requests_text
    does its PDF.

    The exports print the county table against a two-line header -- a COUNTY row (its
    own next rows can carry only the merged HOW THE BALLOTS WILL BE DELIVERED caption)
    and the delivery-method labels on a following line -- and shift every label from
    column to column, so the county column and each method's column are read from the
    header they were printed under, never by position. Rows are published for the
    county rows alone (geography "county", party and category "ALL"). Nothing comes
    out until the table has been reconciled: a county's methods must sum to its
    printed total, and every printed totals row must equal the counties' column sums
    (the totals rows omit columns they do not print; columns with no county data fail).
    """
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
    except (OSError, CalamineError) as err:
        raise TurnoutParseError(f"the requests workbook could not be read: {err}") from err
    as_of = None
    published: list[dict] = []
    seen: set[str] = set()  # per document: a repeated county table would publish twice
    for sheet in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if as_of is None:
            as_of = _first_as_of(rows)
        published += _requests_sheet_rows(sheet, rows, election, as_of, seen)
    if not published:
        raise TurnoutParseError("the requests workbook has no county rows")
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def _printed_cells(
    sheet: str, index: int, row: list, mapping: dict[int, str], *, strict: bool
) -> dict[str, int]:
    """One row's printed cells as integers.

    A data row's cells are all required (strict: a blank or non-numeric cell is a
    misread cell and fails); the printed totals row keeps only the cells it printed --
    its own label cell and any blank cells are not totals -- but a cell that does carry
    a number must still be an integer.
    """
    cells: dict[str, int] = {}
    for position, measure in mapping.items():
        raw = row[position] if position < len(row) else None
        if raw in (None, ""):
            if strict:
                raise TurnoutParseError(f"{sheet} row {index}: the {measure} cell is blank")
            continue
        if not strict:
            try:
                as_float = float(str(raw))
            except (TypeError, ValueError):
                continue
        else:
            try:
                as_float = float(str(raw))
            except (TypeError, ValueError):
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {measure} cell is not a number ({raw!r})"
                ) from None
        if as_float != int(as_float):
            raise TurnoutParseError(
                f"{sheet} row {index}: the {measure} cell is not an integer ({raw!r})"
            )
        cells[measure] = int(as_float)
    return cells


# --- the certified official-turnout family (xlsx) -------------------------------------------
# A certified snapshot: seven party blocks per sheet (the Statewide block plus one per
# SBE party), each its own little table closed by a printed TOTAL(S) row. The block
# label's text cell, the area column's header label and the measure columns all shift
# between blocks, so every block is read from its own printed row names.

AS_OF_CERTIFIED_RE = re.compile(r"As of:?\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def extract_as_of_cell(text: str) -> str | None:
    """A certified report cell's snapshot date, "YYYY-MM-DD", else None.

    The live-updated families print "As of: July 7, 2026 6 PM" (colon required, as
    extract_as_of_text reads); the certified exports print the date either beside the
    election date in the same cell ("Election Date: June 23, 2026\\nAs of: July 23,
    2026") or colonless under its own asterisks ("Election Date: June 23, 2026\\n**As
    of June 6, 2026"), so the colon is optional here. The election date itself is not
    a snapshot: a cell that carries only "Election Date: ..." returns None, and an
    unparseable month shape is no date, as in extract_as_of_text.
    """
    if not (found := AS_OF_CERTIFIED_RE.search(text)):
        return None
    try:
        parsed = datetime.datetime.strptime(found.group(1), "%B %d, %Y")
    except ValueError:
        return None
    return parsed.date().isoformat()


def _first_snapshot_date(rows: list[list]) -> str | None:
    """The first As-of cell of a certified sheet's header region, in reading order."""
    for row in rows[:25]:
        for cell in row[:25]:
            if isinstance(cell, str) and "As of" in cell:
                found = extract_as_of_cell(cell)
                if found:
                    return found
    return None


# A block's party code, from its label row as the live GP26 files print it (the county
# file spells the Green block GRN; the congressional and legislative files spell Green).
OFFICIAL_BLOCK_CELLS: dict[str, str] = {
    "STATEWIDE": "ALL",
    "DEMOCRAT": "DEM",
    "REPUBLICAN": "REP",
    "WCP": "WCP",
    "OTHER PARTIES": "OTH",
    "UNAFFILIATED": "UNA",
    "GRN": "GRN",
    "GREEN": "GRN",
}
# The area column's header label, one printed spelling per geography.
OFFICIAL_AREA_CELLS: dict[str, str] = {
    "LBE": "county",
    "CONGRESSIONAL DISTRICT CODE": "district",
    "LEGISLATIVE DISTRICT CODE": "district",
}
# The five printed measures; the TURNOUT column's printed shares are not a measure.
OFFICIAL_MEASURE_CELLS: dict[str, str] = {
    "ELECTION DAY": "election-day",
    "EARLY VOTING": "early-voting",
    "VOTE BY MAIL": "vote-by-mail",
    "PROVISIONAL": "provisional",
    "ELIGIBLE VOTERS": "eligible",
}


def parse_turnout_official_bytes(
    data: bytes, election: str, geography: str
) -> tuple[str | None, pl.DataFrame]:
    """One Official Turnout (By Party and ...) workbook as long rows.

    Returns the snapshot date (None when no sheet carries one) and the rows. Nothing
    comes out until every printed total has been checked: each block's TOTAL(S) row
    must equal its area rows' column sums, and -- the identity the SBE prints its own
    Statewide block against -- each area's Statewide row must equal the sum of the
    party blocks' rows for that area within the sheet carrying them (verified against
    the live GP26 files, including the congressional and legislative Undetermined rows,
    whose provisional counts appear in every party block). A workbook that does not
    add up, or a block label that is not one of the SBE's, raises.
    """
    if geography not in ("county", "congressional", "legislative"):
        raise TurnoutParseError(f"no official-turnout files for geography {geography!r}")
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
    except (OSError, CalamineError) as err:
        raise TurnoutParseError(f"the official-turnout workbook could not be read: {err}") from err
    as_of = None
    published: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (party, area) per document, not per sheet
    for sheet in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if as_of is None:
            as_of = _first_snapshot_date(rows)
        published += _official_sheet_rows(sheet, rows, election, geography, as_of, seen)
    if not published:
        raise TurnoutParseError("the official-turnout workbook has no area rows")
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def _official_sheet_rows(
    sheet: str,
    rows: list[list],
    election: str,
    geography: str,
    as_of: str | None,
    seen: set[tuple[str, str]],
) -> list[dict]:
    """The published rows of one sheet, party block by party block.

    A block-label row carries one text cell and no numbers; its label must be one of
    the SBE's (an unrecognized one fails, either at once or when the block's header
    arrives). A block's header row carries the area label and at least three of the
    five measures, and every other label in it must be one the SBE prints, so a
    renamed column fails instead of going silently unread. An area row publishes its
    measures; the block's TOTAL(S) row -- the label sits in the block's own area
    column -- closes it against the rows it printed. A block that ends without its
    totals row (the next block's label row, a new header, or the sheet's end) fails
    the sheet.
    """
    published: list[dict] = []
    party: str | None = None
    bad_block_label: str | None = None  # an unrecognized block-label row, until a header
    area_col: int | None = None
    mapping: dict[int, str] | None = None
    block_sums: dict[str, int] = {}  # measure -> the block's area rows printed so far
    sheet_rows: dict[str, dict[str, dict[str, int]]] = {}  # party -> area -> measure -> value

    def close_open_block(index: int, why: str) -> None:
        if mapping is not None and block_sums:
            raise TurnoutParseError(
                f"{sheet} row {index}: the {party} block's rows are left without a "
                f"printed TOTAL(S) row by {why}"
            )

    for index, row in enumerate(rows, start=1):
        row = row or []
        labels = [_normalize_label(cell) for cell in row]
        text_labels = [label for label in labels if label]
        one_label_row = len(text_labels) == 1 and not any(
            isinstance(cell, (int, float)) for cell in row
        )
        measures = {
            position: OFFICIAL_MEASURE_CELLS[label]
            for position, label in enumerate(labels)
            if label in OFFICIAL_MEASURE_CELLS
        }
        areas = {
            position: OFFICIAL_AREA_CELLS[label]
            for position, label in enumerate(labels)
            if label in OFFICIAL_AREA_CELLS
        }
        if one_label_row:
            if text_labels[0] in OFFICIAL_BLOCK_CELLS:
                close_open_block(index, "the next block's label row")
                party = OFFICIAL_BLOCK_CELLS[text_labels[0]]
                bad_block_label = None
            else:
                # a title row above the first block, or a block label the parser does
                # not know: it fails only if a header still claims it
                bad_block_label = text_labels[0]
            continue
        if len(measures) >= 3 and len(areas) == 1:
            close_open_block(index, "a new header row")
            if party is None:
                raise TurnoutParseError(
                    f"{sheet} row {index}: "
                    + (
                        f"unrecognized party block label {bad_block_label!r}"
                        if bad_block_label
                        else "a header row with no party block label above it"
                    )
                )
            bad_block_label = None
            unknown = [
                label
                for label in labels
                if label
                and label not in OFFICIAL_MEASURE_CELLS
                and label not in OFFICIAL_AREA_CELLS
                and label != "TURNOUT"
            ]
            if unknown:
                raise TurnoutParseError(
                    f"{sheet} row {index}: unrecognized column label {unknown[0]!r} in "
                    f"the {party} block's header"
                )
            (area_col, mapping) = (next(iter(areas)), measures)
            block_sums = {}
            continue
        if mapping is None:
            continue  # title rows, dates, and sheet notes before a block's header
        area_raw = row[area_col] if area_col is not None and area_col < len(row) else None
        if str(area_raw or "").strip().upper() in ("TOTAL", "TOTALS"):
            printed = _printed_cells(sheet, index, row, mapping, strict=True)
            missing = [measure for measure in block_sums if measure not in printed]
            if missing:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the printed TOTAL row omits the {missing[0]} total"
                )
            for measure, value in printed.items():
                collected = block_sums.get(measure, 0)
                if collected != value:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the printed {measure} total says {value}, "
                        f"but the block's rows sum to {collected}"
                    )
            block_sums = {}
            continue
        if not str(area_raw or "").strip():
            if any(isinstance(cell, (int, float)) for cell in row):
                raise TurnoutParseError(f"{sheet} row {index}: a row of counts with no area")
            continue  # blank lines between blocks carry no cells
        cells = _printed_cells(sheet, index, row, mapping, strict=True)
        area = _identity_cell(area_raw)
        if not area:
            raise TurnoutParseError(f"{sheet} row {index}: an area row with no printed area")
        if geography == "county":
            area_key = canonical_county(area)
            if area_key not in CANONICAL_COUNTIES:
                raise TurnoutParseError(f"{sheet} row {index}: unrecognized county label {area!r}")
            county, district = area_key, ""
        else:
            # the area keeps its printed text, down to the file's Undetermined rows
            area_key, county, district = area, "", area
        if (party, area_key) in seen:
            raise TurnoutParseError(
                f"{sheet} row {index}: a {party} row for {area!r} appears more than once"
            )
        seen.add((party, area_key))
        for measure, value in cells.items():
            block_sums[measure] = block_sums.get(measure, 0) + value
        sheet_rows.setdefault(party, {})[area] = dict(cells)
        for measure, value in cells.items():
            published.append(
                {
                    "as_of": as_of or "",
                    "election": election,
                    "family": "official-turnout",
                    "geography": geography,
                    "county": county,
                    "district": district,
                    "category": "ALL",
                    "party": party,
                    "measure": measure,
                    "value": value,
                }
            )
    close_open_block(len(rows), "the end of the sheet")
    # The Statewide block is the file's own arithmetic: each area's row there must
    # equal the sum of the party blocks' rows for that area, per measure, within the
    # sheet that carries the blocks together.
    if (statewide := sheet_rows.get("ALL")) is not None:
        for area in sorted({a for party_rows in sheet_rows.values() for a in party_rows}):
            if area not in statewide:
                raise TurnoutParseError(
                    f"{sheet}: a party block prints {area!r} and the Statewide block does not"
                )
        for area, sw_row in statewide.items():
            for measure, printed in sw_row.items():
                collected = sum(
                    party_rows[area][measure]
                    for party, party_rows in sheet_rows.items()
                    if party != "ALL" and area in party_rows and measure in party_rows[area]
                )
                if collected != printed:
                    raise TurnoutParseError(
                        f"{sheet}: the party blocks' {area} {measure} counts sum to "
                        f"{collected}, not the Statewide block's {printed}"
                    )
    return published


def _requests_sheet_rows(
    sheet: str, rows: list[list], election: str, as_of: str | None, seen: set[str]
) -> list[dict]:
    """The county rows of one sheet, reconciled against the sheet's printed totals rows.

    The totals row announces itself in whatever cell the export prints its label under
    (the county cell in the GP26 file, a method column in the fixture's layout). A
    county block left open by the sheet's end is missing its totals row and fails: rows
    are published only against the totals they add up to.
    """
    published: list[dict] = []
    mapping: dict[int, str] | None = None  # column -> measure, from the header row(s)
    county_col: int | None = None
    county_pending: int | None = None  # a COUNTY header row before the method-label row
    run: list[dict[str, int]] = []  # county rows printed since the last totals row

    for index, row in enumerate(rows, start=1):
        row = row or []
        labels = [_normalize_label(cell) for cell in row]
        if mapping is None:
            methods = {
                position: REQUESTS_METHOD_CELLS[label]
                for position, label in enumerate(labels)
                if label in REQUESTS_METHOD_CELLS
            }
            if county_pending is None:
                if "COUNTY" in labels:
                    if len(methods) >= 3:  # one-line header: COUNTY and the labels together
                        mapping, county_col = methods, labels.index("COUNTY")
                    else:
                        county_pending = labels.index("COUNTY")  # two-line header
                continue
            if len(methods) >= 3:  # the method-label row of a two-line header
                mapping, county_col = methods, county_pending
                county_pending = None
            continue  # title rows, notes, and blank lines have no label to read

        if any(label in ("TOTAL", "TOTALS") for label in labels):
            if not run:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the printed TOTAL row has no county rows above it"
                )
            printed = _printed_cells(sheet, index, row, mapping, strict=False)
            for measure, value in printed.items():
                collected = sum(one[measure] for one in run)
                if collected != value:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the printed {measure} total says {value}, "
                        f"but the counties sum to {collected}"
                    )
            run = []
            continue

        county_raw = row[county_col] if county_col is not None and county_col < len(row) else None
        if county_raw in (None, ""):
            continue  # blank lines and the notes under the table have no county cell
        cells = _printed_cells(sheet, index, row, mapping, strict=True)
        county_text = str(county_raw).strip()
        county = canonical_county(county_text)
        if county not in CANONICAL_COUNTIES:
            raise TurnoutParseError(
                f"{sheet} row {index}: unrecognized county label {county_text!r}"
            )
        if county in seen:
            raise TurnoutParseError(
                f"{sheet} row {index}: a county row for {county} appears more than once"
            )
        seen.add(county)
        methods_sum = sum(value for measure, value in cells.items() if measure != "total")
        if methods_sum != cells["total"]:
            raise TurnoutParseError(
                f"{sheet} row {index}: {county}'s request counts do not add up "
                f"({methods_sum} != {cells['total']})"
            )
        run.append(dict(cells))
        published += _requests_rows(county, cells, election, as_of)
    if run:
        raise TurnoutParseError(
            f"{sheet}: {len(run)} county rows at the sheet's end have no printed TOTAL row"
        )
    return published


def _requests_rows(
    county: str, cells: dict[str, int], election: str, as_of: str | None
) -> list[dict]:
    """The published rows of one county's request row set."""
    return [
        {
            "as_of": as_of or "",
            "election": election,
            "family": "requests",
            "geography": "county",
            "county": county,
            "district": "",
            "category": "ALL",
            "party": "ALL",
            "measure": measure,
            "value": value,
        }
        for measure, value in cells.items()
    ]


# --- the sent-returned family (xlsx) --------------------------------------------------------

# The five voter categories every sent-returned export uses, in printed order.
SENT_CATEGORIES = (
    "ALL",
    "Civilian Overseas",
    "Domestic Civilian",
    "Military Overseas",
    "Military US",
)
SENT_MEASURES = ("sent", "received", "accepted")
TURNOUT_COLUMNS = {
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
# Header labels mapped to (party, measure): the exports print OTH and OTHR, and
# party totals into a TOTAL column.
SENT_MEASURE_CELLS: dict[str, tuple[str, str]] = {
    f"{label} {measure.upper()}": (party, measure)
    for measure in SENT_MEASURES
    for label, party in (
        ("DEM", "DEM"),
        ("REP", "REP"),
        ("OTH", "OTH"),
        ("OTHR", "OTH"),
        ("TOTAL", "ALL"),
    )
}
# The left-to-count exports add one more printed column, as their own single count:
# LEFT TO COUNT, with no DEM/REP/OTH breakdown to its name.
SENT_LEFT_TO_COUNT_CELLS: dict[str, tuple[str, str]] = {"LEFT TO COUNT": ("ALL", "left-to-count")}
SENT_IDENTITY_CELLS = {"CATEGORY": "category", "COUNTY NAME": "county", "DISTRICT": "district"}


def parse_sent_returned_bytes(
    data: bytes,
    election: str,
    geography: str,
    measures: tuple[str, ...] = SENT_MEASURES,
    family: str = "sent-returned",
) -> tuple[str | None, pl.DataFrame]:
    """One Absentees Sent and Returned workbook as long rows.

    Returns the as-of date (None when no sheet carries one) and the published rows. The
    exports repeat a CATEGORY header before every block and shift their physical
    columns between blocks, so each block is read against its own header. Rows are
    published at one grain only -- county rows, or district rows (where the file reads
    ALL or nothing in the county cell); a district's county detail is reconciled
    against the district's printed total but never published. Nothing comes out until
    every printed total has been checked; a workbook that does not add up raises.

    `measures` carries the printed columns to read: the sent-returned files' three, or
    -- for the left-to-count files, the same shape re-printed wider -- the three plus
    the LEFT TO COUNT column (then the row's own printed arithmetic -- left-to-count
    equals the accepted total subtracted from the received total, negative where the
    canvass sat between the two -- and the column's printed totals are checked too).
    """
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
    except (OSError, CalamineError) as err:
        raise TurnoutParseError(f"the sent-returned workbook could not be read: {err}") from err
    as_of = None
    published: list[dict] = []
    seen: set[tuple[str, str, str]] = set()  # per document, not per sheet: the
    # legislative file's detail region reprints districts the sections already carry
    for sheet in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if as_of is None:
            as_of = _first_as_of(rows)
        published += _sent_sheet_rows(
            sheet, rows, election, family, geography, as_of, seen, measures
        )
    if not published:
        raise TurnoutParseError(f"the {family} workbook has no county or district rows")
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def parse_left_to_count_bytes(
    data: bytes, election: str, geography: str
) -> tuple[str | None, pl.DataFrame]:
    """One Mail-in Ballots Left To Count workbook: the sent-returned exports' shape plus
    the 13th printed column, LEFT TO COUNT, which the SBE prints as one count without a
    party breakdown. The sent-returned machinery reads it with the extra measure: the

    column's totals are printed and reconciled at every printed Total row (366 of them
    in the live GP26 files), and each row carries the file's own arithmetic -- the left
    to count equals the row's accepted total subtracted from its received total, a
    printed identity that sits at -52 in the live county file's Anne Arundel row. The
    DEM+REP+OTH == ALL identity stays with the three tallied measures.
    """
    return parse_sent_returned_bytes(
        data,
        election,
        geography,
        measures=(*SENT_MEASURES, "left-to-count"),
        family="left-to-count",
    )


def _first_as_of(rows: list[list]) -> str | None:
    """The first "As of" cell of a sheet's header region, in reading order."""
    for row in rows[:25]:
        for cell in row[:25]:
            if isinstance(cell, str) and "As of" in cell:
                return extract_as_of_text(cell)
    return None


def _normalize_label(text: object) -> str | None:
    """A header cell's label: upper-cased, underscores and runs of space collapsed."""
    if not isinstance(text, str):
        return None
    label = " ".join(text.split()).replace("_", " ").strip().upper()
    return label or None


def _identity_cell(value: object) -> str:
    """An identity cell's text. Statewide and ALL -- the SBE's two spellings of "the
    whole geography" -- collapse to nothing, so a row reads as county-plus-no-district
    or district-plus-no-county."""
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return "" if text.upper() in ("STATEWIDE", "ALL") else text


def _sent_section(
    labels: list[str | None], measures: tuple[str, ...] = SENT_MEASURES
) -> dict[int, tuple[str, object]]:
    """One block's column map. Unknown labels (notes, headings) are ignored; the
    left-to-count label maps only where the caller asks for its measure."""
    mapping: dict[int, tuple[str, object]] = {}
    for index, label in enumerate(labels):
        if label is None:
            continue
        if label in SENT_IDENTITY_CELLS:
            mapping[index] = ("id", SENT_IDENTITY_CELLS[label])
        elif label in SENT_MEASURE_CELLS and SENT_MEASURE_CELLS[label][1] in measures:
            mapping[index] = ("cell", SENT_MEASURE_CELLS[label])
        elif "left-to-count" in measures and label in SENT_LEFT_TO_COUNT_CELLS:
            mapping[index] = ("cell", SENT_LEFT_TO_COUNT_CELLS["LEFT TO COUNT"])
    return mapping


def _sent_add(target: dict[tuple[str, str], int], cells: dict[tuple[str, str], int]) -> None:
    for cell, value in cells.items():
        target[cell] = target.get(cell, 0) + value


def _sent_text(value: object) -> str:
    return "not printed" if value is None else str(value)


def _sent_check(
    sheet: str,
    index: int,
    label: str,
    expected: dict[tuple[str, str], int],
    printed: dict[tuple[str, str], int | None],
) -> None:
    """One printed total row against the rows it is meant to close."""
    for cell, value in expected.items():
        if printed.get(cell, "not printed") != value:
            raise TurnoutParseError(
                f"{sheet} row {index}: the printed {label} says {cell[0]} {cell[1]} "
                f"{_sent_text(printed.get(cell))}, but the rows it closes sum to {value}"
            )
    for cell, value in printed.items():
        if (cell not in expected or not expected[cell]) and value not in (None, 0):
            raise TurnoutParseError(
                f"{sheet} row {index}: the printed {label} has a {cell[0]} {cell[1]} column "
                f"({_sent_text(value)}) the rows it closes do not"
            )


def _sent_sheet_rows(
    sheet: str,
    rows: list[list],
    election: str,
    family: str,
    geography: str,
    as_of: str | None,
    seen: set[tuple[str, str, str]],
    measures: tuple[str, ...] = SENT_MEASURES,
) -> list[dict]:
    """All the data rows of one sheet, reconciled against the sheet's printed totals.

    A printed total announces itself in an identity cell. A plain "Total", or a
    "Something Total" in the county or district cell, closes the rows printed above it.
    A category's "X Total" row closes everything the category has accumulated at county
    grain. A "Grand Total" row must add up to the printed category totals. Rows that
    carry both a county and a district (a district's county detail, and the district's
    own aggregate row printed under it) are checked but never published.
    """
    published: list[dict] = []
    mapping: dict[int, tuple[str, object]] | None = None
    run: list[dict[tuple[str, str], int]] = []  # rows printed since the last total
    detail: dict[tuple[str, str], dict[tuple[str, str], int]] = {}  # (category, district) -> sums
    open_detail: set[tuple[str, str]] = set()
    category_sums: dict[str, dict[tuple[str, str], int]] = {}  # county-grain rows only
    printed_totals: dict[str, dict[tuple[str, str], int]] = {}  # "X Total" rows, for the grand
    last_category = ""

    for index, row in enumerate(rows, start=1):
        row = row or []
        labels = [_normalize_label(cell) for cell in row]
        if "CATEGORY" in labels:
            mapping = _sent_section(labels, measures)
            run = []  # a new section resets what its "Total" rows will close
            continue
        if mapping is None:
            continue

        ids = {"category": "", "county": "", "district": ""}
        cells: dict[tuple[str, str], int | None] = {}
        for position, (kind, payload) in mapping.items():
            if kind == "id":
                # identity cells keep their printed text; the ALL/Statewide collapsing
                # that a county or district cell gets happens where it is used
                ids[payload] = str(row[position]).strip() if position < len(row) else ""
                continue
            raw = row[position] if position < len(row) else None
            if raw in (None, ""):
                cells[payload] = None
                continue
            try:
                as_float = float(str(raw))
            except (TypeError, ValueError):
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {payload[0]} {payload[1]} cell is not a "
                    f"number ({raw!r})"
                ) from None
            if as_float != int(as_float):
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {payload[0]} {payload[1]} cell is not an "
                    f"integer ({raw!r})"
                )
            cells[payload] = int(as_float)
        if all(value is None for value in cells.values()):
            continue  # a heading, not a row of numbers

        total_key = next(
            (
                key
                for key in ("category", "county", "district")
                if (text := ids[key]) and (text == "Total" or text.endswith(" Total"))
            ),
            None,
        )

        if total_key:
            ids_text = ids[total_key]
            if ids_text == "Grand Total":
                if not printed_totals:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: a grand total with no category totals before it"
                    )
                expected: dict[tuple[str, str], int] = {}
                for closed in printed_totals.values():
                    _sent_add(expected, closed)
                _sent_check(sheet, index, "grand total", expected, cells)
            elif ids_text in ("Total",) or total_key != "category":
                if not run:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the printed {ids_text or 'total'} row has no "
                        "rows above it"
                    )
                expected = {}
                for above in run:
                    _sent_add(expected, above)
                _sent_check(
                    sheet,
                    index,
                    f"{_sent_text(ids_text).strip() or 'total'} total",
                    expected,
                    cells,
                )
                run = []
            else:
                category = ids_text.removesuffix("Total").strip()
                if category not in SENT_CATEGORIES:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: unrecognized category {category!r}"
                    )
                if not category_sums.get(category):
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the printed {category} total has no detail rows"
                    )
                _sent_check(sheet, index, f"{category} total", category_sums[category], cells)
                printed_totals[category] = {
                    cell: value for cell, value in cells.items() if value is not None
                }
                del category_sums[category]
            continue

        category = ids["category"] or last_category
        if not category:
            raise TurnoutParseError(f"{sheet} row {index}: no category for a data row")
        if category not in SENT_CATEGORIES:
            raise TurnoutParseError(f"{sheet} row {index}: unrecognized category {category!r}")
        district = _identity_cell(ids["district"])
        county = (
            canonical_county(_identity_cell(ids["county"])) if _identity_cell(ids["county"]) else ""
        )
        if county and county not in CANONICAL_COUNTIES:
            raise TurnoutParseError(
                f"{sheet} row {index}: unrecognized county label {ids['county']!r}"
            )
        if not county and not district:
            raise TurnoutParseError(
                f"{sheet} row {index}: a {category} row with no county or district"
            )
        for measure in measures:
            if measure == "left-to-count":
                # the left-to-count column is one count with no party breakdown; its
                # printed identity is the row's own arithmetic, not a three-way sum
                left = cells.get(("ALL", "left-to-count"))
                if left is None:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the {category} left-to-count cell is blank"
                    )
                received_total = cells.get(("ALL", "received"))
                accepted_total = cells.get(("ALL", "accepted"))
                if None not in (received_total, accepted_total) and (
                    left != received_total - accepted_total
                ):
                    raise TurnoutParseError(
                        f"{sheet} row {index}: the {category} left-to-count cell says "
                        f"{left}, but the row's received {received_total} minus its "
                        f"accepted {accepted_total} is {received_total - accepted_total}"
                    )
                continue
            four = {party: cells.get((party, measure)) for party in ("DEM", "REP", "OTH", "ALL")}
            if any(value is None for value in four.values()):
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {category} {measure} counts are incomplete"
                )
            if four["DEM"] + four["REP"] + four["OTH"] != four["ALL"]:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {category} {measure} counts do not add up "
                    f"({four['DEM']} + {four['REP']} + {four['OTH']} != {four['ALL']})"
                )
        if county and district:
            # A district's county detail: checked when the district's own row appears,
            # and against the category's total, but never published.
            _sent_add(detail.setdefault((category, district), {}), cells)
            open_detail.add((category, district))
            _sent_add(category_sums.setdefault(category, {}), cells)
            run.append(dict(cells))  # the district's printed total closes these
        elif county:
            if county not in CANONICAL_COUNTIES:
                raise TurnoutParseError(
                    f"{sheet} row {index}: unrecognized county label {county!r}"
                )
            identity = (category, county, district)
            if identity in seen:
                raise TurnoutParseError(
                    f"{sheet} row {index}: a {category} row for {county} appears more than once"
                )
            seen.add(identity)
            run.append(dict(cells))
            for (party, measure), value in cells.items():
                published.append(
                    {
                        "as_of": as_of or "",
                        "election": election,
                        "family": family,
                        "geography": geography,
                        "county": county,
                        "district": district,
                        "category": category,
                        "party": party,
                        "measure": measure,
                        "value": value,
                    }
                )
        else:
            if (category, district) in open_detail:
                _sent_check(
                    sheet,
                    index,
                    f"{category} {district} total",
                    detail[(category, district)],
                    cells,
                )
                del detail[(category, district)]
                open_detail.discard((category, district))
            else:
                identity = (category, "", district)
                if identity in seen:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: a {category} row for {district} appears more than once"
                    )
                seen.add(identity)
                run.append(dict(cells))
                for (party, measure), value in cells.items():
                    published.append(
                        {
                            "as_of": as_of or "",
                            "election": election,
                            "family": family,
                            "geography": geography,
                            "county": "",
                            "district": district,
                            "category": category,
                            "party": party,
                            "measure": measure,
                            "value": value,
                        }
                    )
        last_category = category

    return published


# --- the eligible-voters family (xlsx) -------------------------------------------------------
# One reader covers the county, inactive, and district files the press room publishes:
# a header row (County, an optional District column for the district files, the six
# party columns DEM..WCP, Total) read from wherever the export printed it, then rows
# until the section's next header. The district files' first section prints the district
# grain against a COUNTY cell that reads STATEWIDE; a later section re-prints the same
# population at district x county grain, checked and never published -- the way the
# sent-returned exports hand over their detail regions.

ELIGIBLE_PARTIES = ("DEM", "REP", "GRN", "OTH", "UNA", "WCP")
ELIGIBLE_MEASURES = {"eligible-active": "active", "eligible-inactive": "inactive"}
ELIGIBLE_GEOGRAPHIES = ("county", "congressional", "legislative", "councilmanic")


def label_upper(text: str) -> str:
    """A district label's key for matching a file's own spellings of it (and for the
    two sections that spell 'councilmanic' differently in the live file)."""
    return " ".join(text.split()).upper()


def parse_eligible_bytes(
    data: bytes, election: str, family: str, geography: str
) -> tuple[str | None, pl.DataFrame]:
    """One Eligible Active/Inactive Voters workbook as long rows.

    Returns the date the file prints ('**As of June 6, 2026' under the election date in
    the live GP26 files; None when no sheet carries one) and the rows: one row per
    printed cell of the file's own grain -- county rows for the county and inactive
    files, district rows (verbatim codes, STATEWIDE county cell collapsed) for the
    district files. Blank party cells publish nothing: the export suppresses cells, and
    the legislative file's suppressed GRN cell for district 01C is a real 32 (the
    Statewide export prints it), not a zero -- so the row checks hold only over the
    cells that did print, a suppressed row staying under its printed Total. The grand
    total rows' party columns are the one printed layer this family does not reconcile:
    the live inactive file's DEM cell is 1327 under its own rows while its Total column
    and every county row add up, so the rows and the Total columns are what is enforced.
    """
    if family not in ELIGIBLE_MEASURES:
        raise TurnoutParseError(f"no eligible-voters family {family!r}")
    if geography not in ELIGIBLE_GEOGRAPHIES:
        raise TurnoutParseError(f"no eligible-voters files for geography {geography!r}")
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
    except (OSError, CalamineError) as err:
        raise TurnoutParseError(f"the eligible-voters workbook could not be read: {err}") from err
    as_of = None
    published: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (party, county) or (party, district), per document
    for sheet in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if as_of is None:
            as_of = _first_snapshot_date(rows)
        published += _eligible_sheet_rows(
            sheet, rows, election, family, ELIGIBLE_MEASURES[family], geography, as_of, seen
        )
    if not published:
        raise TurnoutParseError("the eligible-voters workbook has no county or district rows")
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def _eligible_section(
    labels: list[str | None],
) -> tuple[dict[int, tuple[str, object]], int | None, int | None]:
    """One section's column map: the identity cells and the party columns.

    Returns the map and the district/county columns (None where the section has none).
    A header must carry at least three party columns, one of the two identity labels,
    and its Total column; every other label it carries fails -- a party the parser
    does not know means a file the parser should not read.
    """
    mapping: dict[int, tuple[str, object]] = {}
    district_col = county_col = None
    for index, label in enumerate(labels):
        if label is None:
            continue
        if label == "DISTRICT":
            district_col, mapping[index] = index, ("id", "district")
        elif label == "COUNTY":
            county_col, mapping[index] = index, ("id", "county")
        elif label == "TOTAL":
            mapping[index] = ("cell", "ALL")
        elif label in ELIGIBLE_PARTIES:
            mapping[index] = ("cell", label)
        else:
            raise TurnoutParseError(f"unrecognized column label {label!r} in the header row")
    if sum(1 for kind, _ in mapping.values() if kind == "cell") < 3:
        raise TurnoutParseError("a header row with fewer than three party columns")
    if district_col is None and county_col is None:
        raise TurnoutParseError("a header row with no COUNTY or DISTRICT column")
    if not any(mapping[index] == ("cell", "ALL") for index in mapping):
        raise TurnoutParseError("a header row with no Total column")
    return mapping, district_col, county_col


def _eligible_sheet_rows(
    sheet: str,
    rows: list[list],
    election: str,
    family: str,
    measure: str,
    geography: str,
    as_of: str | None,
    seen: set[tuple[str, str]],
) -> list[dict]:
    """One sheet's published rows, section by section.

    Within a section: the section's printed Total row (the label sits in the county or
    district column) must equal the rows' Total column, exactly. The district files'
    detail rows -- a district's label over its county rows -- are checked while they
    open against the district's printed grain row wherever that is on the sheet, and
    never published.
    """
    published: list[dict] = []
    mapping: dict[int, tuple[str, object]] | None = None
    district_col = county_col = None
    grain: dict[str, dict[str, int]] = {}  # upper-cased district label -> party -> printed cell
    open_group: tuple[str, str, dict[str, int], dict[str, bool]] | None = None
    section_rows: list[dict[str, int]] = []

    def close_group(index: int) -> None:
        nonlocal open_group
        if open_group is None:
            return
        label, sums, full = open_group
        open_group = None
        if (grain_row := grain.get(label_upper(label))) is None:
            raise TurnoutParseError(
                f"{sheet} row {index}: the detail rows for {label!r} have no printed "
                "district row above them"
            )
        for party, printed in grain_row.items():
            if not full.get(party) or printed is None:
                continue  # a suppressed cell on either side is not a count to compare
            if sums.get(party, 0) != printed:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the detail rows sum to {sums.get(party, 0)} for "
                    f"{party}, not the printed {label}'s {printed}"
                )

    def check_row_total(index: int, county_or_district: str, cells: dict[str, int | None]) -> None:
        total = cells.get("ALL")
        printed = {party: value for party, value in cells.items() if party != "ALL"}
        if any(value is None for value in printed.values()):
            collected = sum(value for value in printed.values() if value is not None)
            if collected > total:
                raise TurnoutParseError(
                    f"{sheet} row {index}: {county_or_district}'s printed party cells sum to "
                    f"{collected}, over the printed Total {total}"
                )
            return  # the row's Total covers cells the export did not print
        collected = sum(printed.values())
        if collected != total:
            raise TurnoutParseError(
                f"{sheet} row {index}: {county_or_district}'s party cells sum to {collected}, "
                f"not the printed Total {total}"
            )

    def publish(kind_key: str, label: str, cells: dict[str, int | None]) -> None:
        county, district = (label, "") if kind_key == "county" else ("", label)
        for party, value in cells.items():
            if value is None:
                continue  # a cell the export did not print
            published.append(
                {
                    "as_of": as_of or "",
                    "election": election,
                    "family": family,
                    "geography": geography,
                    "county": county,
                    "district": district,
                    "category": "ALL",
                    "party": party,
                    "measure": measure,
                    "value": value,
                }
            )

    for index, row in enumerate(rows, start=1):
        row = row or []
        labels = [_normalize_label(cell) for cell in row]
        if sum(1 for label in labels if label and label in ELIGIBLE_PARTIES) >= 3:
            mapping, district_col, county_col = _eligible_section(labels)
            section_rows = []  # a new header closes what the last one reconciled
            continue
        if mapping is None:
            continue  # title rows, the date cell, blank lines

        cells: dict[str, int] = {}
        for index_col, (kind, key) in mapping.items():
            raw = row[index_col] if index_col < len(row) else None
            if kind == "id":
                cells[key] = raw if isinstance(raw, str) else None
            elif raw in (None, ""):
                cells[key] = None
            elif isinstance(raw, (int, float)) and raw == int(raw):
                cells[key] = int(raw)
            else:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {key} cell is not an integer ({raw!r})"
                )

        district_text = (cells["district"] or "").strip() if "district" in cells else ""
        county_text = (cells["county"] or "").strip() if "county" in cells else ""
        total_label = district_text.upper() in ("TOTAL", "TOTALS") or county_text.upper() in (
            "TOTAL",
            "TOTALS",
        )
        counts: dict[str, int | None] = {party: cells.get(party) for party in ELIGIBLE_PARTIES}
        counts["ALL"] = cells.get("ALL")
        if total_label and "ALL" in counts and counts["ALL"] is not None:
            close_group(index)
            collected = sum(one["ALL"] or 0 for one in section_rows)
            printed = counts["ALL"]
            if collected != printed:
                raise TurnoutParseError(
                    f"{sheet} row {index}: the printed Total row says {printed}, but the "
                    f"section's rows sum to {collected}"
                )
            section_rows = []
            continue
        if counts["ALL"] is None:
            if all(value is None for value in counts.values()):
                continue  # a note row under the table carries no counts
            raise TurnoutParseError(
                f"{sheet} row {index}: the Total cell is blank over printed party cells"
            )
        district = _identity_cell(district_text)
        county_text = _identity_cell(county_text)
        if county_text:
            county = canonical_county(county_text)
            if county not in CANONICAL_COUNTIES:
                raise TurnoutParseError(
                    f"{sheet} row {index}: unrecognized county label {county_text!r}"
                )
        else:
            county = ""
        if district and county:
            # the file's detail grain: same population, finer breakdown, not published
            if open_group is None or open_group[0].upper() != district.upper():
                close_group(index)
                open_group = (district, {}, {})
            label, sums, full = open_group
            for party, value in counts.items():
                if party == "ALL":
                    continue
                if value is not None:
                    sums[party] = sums.get(party, 0) + value
                full[party] = full.get(party, True) and value is not None
            open_group = (label, sums, full)
            section_rows.append(dict(counts))
        elif not district and county and open_group is not None:
            # the district's label is merged to blank on the rows after its first row:
            # the detail group continues
            label, sums, full = open_group
            for party, value in counts.items():
                if party == "ALL":
                    continue
                if value is not None:
                    sums[party] = sums.get(party, 0) + value
                full[party] = full.get(party, True) and value is not None
            open_group = (label, sums, full)
            section_rows.append(dict(counts))
        elif district:
            close_group(index)
            if geography == "county":
                section_rows.append(dict(counts))
                continue  # a district-grain row in a county file is not this file's grain
            check_row_total(index, f"district {district!r}", counts)
            grain[label_upper(district)] = {p: v for p, v in counts.items()}
            for party in ELIGIBLE_PARTIES + ("ALL",):
                if (party, district) in seen:
                    raise TurnoutParseError(
                        f"{sheet} row {index}: a {party} row for {district!r} appears more "
                        "than once"
                    )
                seen.add((party, district))
            publish("district", district, counts)
            section_rows.append(dict(counts))
        else:  # a county row (no district printed over it)
            close_group(index)
            if geography == "county":
                check_row_total(index, county, counts)
                for party in ELIGIBLE_PARTIES + ("ALL",):
                    if (party, county) in seen:
                        raise TurnoutParseError(
                            f"{sheet} row {index}: a {party} row for {county} appears more "
                            "than once"
                        )
                    seen.add((party, county))
                publish("county", county, counts)
            section_rows.append(dict(counts))
    close_group(len(rows) + 1)
    if section_rows:
        raise TurnoutParseError(
            f"{sheet}: {len(section_rows)} rows at the sheet's end have no printed Total row"
        )
    return published


# --- the GP26 PDF families -------------------------------------------------------------------
# Five of the GP26 report families ship as PDFs only, and the four one-page
# "Official" sheets below (the two canvass tables, the mail-in sheet and the
# provisional sheet) share one shape: a county column, printed count cells, and the
# unlabelled % cell right of each count it is a share of. The cells come through
# pdf_table_rows(), and the parsers take those rows -- lists of the strings the file
# prints -- so the parse is testable against fixtures copied from the live files.


def pdf_table_rows(path: Path) -> list[list[str]]:
    """Every page's extracted tables' rows of a PDF, flattened, cells as strings.

    natural-pdf has no error taxonomy; an unreadable page raises whatever its own
    reader raises and the callers turn that into a TurnoutParseError. Every cell that
    printed nothing reads as "", and a row of all-empty cells is dropped. The sheets'
    header lines carry inside them ("# Accepted in Full", "ADDRCHG\\nDAY1"); the
    parsers normalize them the way the workbook families do theirs.
    """
    from natural_pdf import PDF

    rows: list[list[str]] = []
    for page in PDF(str(path)).pages:
        for table in page.extract_tables():
            for row in table:
                cells = ["" if cell is None else str(cell) for cell in row]
                if any(cell.strip() for cell in cells):
                    rows.append(cells)
    return rows


def read_pdf_text(path: Path) -> str:
    """Every page's text of a PDF: the report and election dates print above its tables."""
    from natural_pdf import PDF

    return "\n".join(page.extract_text() or "" for page in PDF(str(path)).pages)


REPORT_DATE_RE = re.compile(r"Report Date:\s*(\d{1,2}/\d{1,2}/\d{4})")
ELECTION_DATE_RE = re.compile(r"Election Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def extract_report_date_text(text: str) -> str | None:
    """The date a "Report Date: 7/23/2026" line carries, "YYYY-MM-DD", else None.

    The four Official one-pagers print the day they were run, as month/day/year --
    a shape neither "As of" reader accepts -- and their snapshots carry no "As of" of
    their own, so this is the date the parse reads.
    """
    if not (found := REPORT_DATE_RE.search(text)):
        return None
    try:
        parsed = datetime.datetime.strptime(found.group(1), "%m/%d/%Y")
    except ValueError:
        return None
    return parsed.date().isoformat()


def extract_election_date_text(text: str) -> str | None:
    """The date an "Election Date: June 23, 2026" line carries, "YYYY-MM-DD", else None.

    The eight-day early-voting and same-day files print their election date and no
    snapshot date of their own; the eligible-actives footnote under a file's header
    ("County-Wide Eligible Active Voters are as of : June 18, 2026") is lower-case,
    which a case-sensitive "As of" match lets pass.
    """
    if not (found := ELECTION_DATE_RE.search(text)):
        return None
    try:
        parsed = datetime.datetime.strptime(found.group(1), "%B %d, %Y")
    except ValueError:
        return None
    return parsed.date().isoformat()


def pdf_snapshot_date(text: str) -> str | None:
    """A GP26 PDF's snapshot date: its Report Date when it prints one, else its "As of",
    else the Election Date the file is headed by.

    The one date the four Official sheets print is the report's own; the eight-day
    files print only their election date, which is both the period they cover and the
    only snapshot they carry.
    """
    return (
        extract_report_date_text(text)
        or extract_as_of_text(text)
        or extract_election_date_text(text)
    )


def _pdf_number(where: str, measure: str, cell: str) -> int:
    """One printed count cell as an integer: the counts print with or without commas."""
    text = (cell or "").strip()
    if not text:
        raise TurnoutParseError(f"{where}: the {measure} cell is blank")
    try:
        return int(text.replace(",", ""))
    except ValueError:
        raise TurnoutParseError(f"{where}: the {measure} cell is not a number ({cell!r})") from None


def _pdf_share(where: str, measure: str, cell: str) -> float | None:
    """One printed share cell as a fraction ("60.99%" -> 0.6099), None when not printed."""
    text = (cell or "").strip()
    if not text:
        return None
    try:
        return float(text.rstrip("%")) / 100.0
    except ValueError:
        raise TurnoutParseError(
            f"{where}: the {measure} share cell is not a percentage ({cell!r})"
        ) from None


def share_matches(count: int, denom: int, printed: float, tolerance: float = 0.01) -> bool:
    """Whether a printed share equals count over denom, within the tolerance.

    The printed shares are percentages read as fractions, so the tolerance is a
    percentage point: it leaves room for the two printings' rounding (the live GP26
    shares sit within 0.0005 of count/total) but not for a count over the wrong
    denominator.
    """
    if denom == 0:
        return printed == 0
    return abs(printed - count / denom) <= tolerance


# The county cell of the four sheets' own state rows ("Statewide" in the two canvass
# sheets and the mail-in sheet, "State Totals" in the provisional sheet).
PDF_STATE_CELLS = frozenset({"STATEWIDE", "STATE TOTALS"})


class PrintedSheet:
    """One printed sheet's columns: the labelled counts and what its shares are of.

    The SBE prints a count cell, then the labelless % cell that belongs to it, so every
    count's share column is the count column's own right neighbor unless another count
    sits there. The "modes" counts make up the base count -- a canvass row's modes sum
    to its printed total, a mail-in row's accepted and rejected sum to its received --
    and the printed shares are checked against the count over the measure named here.
    """

    def __init__(
        self,
        cells: dict[str, str],
        base: str,
        modes: tuple[str, ...],
        share_of: dict[str, str],
    ) -> None:
        self.cells = cells
        self.base = base
        self.modes = modes
        self.share_of = share_of

    def section(self, where: str, labels: list[str]) -> tuple[int, dict[int, str], dict[int, str]]:
        """One header row's county column, count columns, and share columns.

        Every non-empty label must be one the sheet prints: a renamed column is a sheet
        the parser should not read, not a column to skip.
        """
        county_col: int | None = None
        counts: dict[int, str] = {}
        for index, label in enumerate(labels):
            if not label:
                continue
            if label == "COUNTY":
                county_col = index
                continue
            if label not in self.cells:
                raise TurnoutParseError(f"{where}: unrecognized column label {label!r}")
            counts[index] = self.cells[label]
        if county_col is None or not counts:
            raise TurnoutParseError(
                f"{where}: a header row with no county column or no count column"
            )
        shares = {
            index + 1: measure for index, measure in counts.items() if index + 1 not in counts
        }
        return county_col, counts, shares

    def cells_of(
        self, where: str, counts: dict[int, str], shares: dict[int, str], row: list[str]
    ) -> tuple[dict[str, int], dict[str, float]]:
        """One row's printed counts as integers and its printed shares as fractions."""
        printed = {
            measure: _pdf_number(where, measure, row[index] if index < len(row) else "")
            for index, measure in counts.items()
        }
        printed_shares: dict[str, float] = {}
        for index, measure in shares.items():
            found = _pdf_share(where, measure, row[index] if index < len(row) else "")
            if found is not None:
                printed_shares[measure] = found
        return printed, printed_shares


CANVASS_SHEET = PrintedSheet(
    cells={
        "EARLY VOTING": "early-voting",
        "ELECTION DAY": "election-day",
        "VOTE BY MAIL": "vote-by-mail",
        "PROVISIONAL": "provisional",
        "TOTAL": "total",
    },
    base="total",
    modes=("early-voting", "election-day", "vote-by-mail", "provisional"),
    share_of={
        "early-voting": "total",
        "election-day": "total",
        "vote-by-mail": "total",
        "provisional": "total",
    },
)


def _tallied_check(
    where: str, county: str, cells: dict[str, int], shares: dict[str, float], sheet: PrintedSheet
) -> None:
    """One row's own arithmetic: its modes sum to its base count, and every printed share
    is that count over the base's measure."""
    modes_sum = sum(cells[measure] for measure in sheet.modes)
    if modes_sum != cells[sheet.base]:
        raise TurnoutParseError(
            f"{where}: {county}'s counts do not add up ({modes_sum} != {cells[sheet.base]})"
        )
    for measure, printed in shares.items():
        base = sheet.share_of[measure]
        if not share_matches(cells[measure], cells[base], printed):
            raise TurnoutParseError(
                f"{where}: the printed {measure} share is not {cells[measure]} of "
                f"{cells[base]} ({printed:.2%} printed)"
            )


def _tallied_rows(
    family: str,
    rows: list[tuple[int, list[str]]],
    election: str,
    geography: str,
    as_of: str | None,
    sheet: PrintedSheet,
) -> tuple[dict[str, int] | None, list[dict]]:
    """One sheet's county rows, read against its own header, with the file's own checks.

    Every county row publishes one value per printed count, and the row's base count
    must equal the sum of its modes; the state row is checked against the counties'
    column sums and not published -- nothing is returned until the sheet adds up.
    """
    county_col: int | None = None
    if geography != "county":
        raise TurnoutParseError(f"no {family} files for geography {geography!r}")
    counts: dict[int, str] = {}
    shares: dict[int, str] = {}
    sums: dict[str, int] = {}
    seen: set[str] = set()
    published: list[dict] = []
    state_cells: dict[str, int] | None = None
    for index, row in rows:
        where = f"{family} row {index}"
        labels = [_normalize_label(cell) for cell in row]
        if "COUNTY" in labels:
            county_col, counts, shares = sheet.section(where, labels)
            sums = {}
            continue
        if county_col is None:
            continue  # title and date lines print before any table
        county_text = str(row[county_col] if county_col < len(row) else "").strip()
        if county_text.upper() in PDF_STATE_CELLS:
            printed, printed_shares = sheet.cells_of(where, counts, shares, row)
            _tallied_check(where, county_text, printed, printed_shares, sheet)
            if not sums:
                raise TurnoutParseError(
                    f"{where}: the printed {county_text} row has no county rows above it"
                )
            for measure, value in printed.items():
                collected = sums.get(measure, 0)
                if collected != value:
                    raise TurnoutParseError(
                        f"{where}: the counties' {measure} counts sum to {collected}, not "
                        f"the printed {county_text} total {value}"
                    )
            state_cells = dict(printed)
            sums = {}
            continue
        if state_cells is not None:
            raise TurnoutParseError(f"{where}: a county row after the sheet's printed state row")
        if not county_text:
            raise TurnoutParseError(f"{where}: a row of counts with no county")
        county = canonical_county(county_text)
        if county not in CANONICAL_COUNTIES:
            raise TurnoutParseError(f"{where}: unrecognized county label {county_text!r}")
        if county in seen:
            raise TurnoutParseError(f"{where}: a county row for {county} appears more than once")
        seen.add(county)
        printed, printed_shares = sheet.cells_of(where, counts, shares, row)
        _tallied_check(where, county, printed, printed_shares, sheet)
        for measure, value in printed.items():
            sums[measure] = sums.get(measure, 0) + value
        published += _county_rows(county, printed, election, family, geography, as_of)
    if state_cells is None:
        raise TurnoutParseError(
            f"{family}: the county rows have no printed Statewide row to reconcile them"
        )
    return state_cells, published


def _county_rows(
    county: str,
    printed: dict[str, int],
    election: str,
    family: str,
    geography: str,
    as_of: str | None,
    district: str = "",
    party: str = "ALL",
) -> list[dict]:
    """One printed row set as TURNOUT_COLUMNS long rows, category always ALL."""
    return [
        {
            "as_of": as_of or "",
            "election": election,
            "family": family,
            "geography": geography,
            "county": county,
            "district": district,
            "category": "ALL",
            "party": party,
            "measure": measure,
            "value": value,
        }
        for measure, value in printed.items()
    ]


def parse_county_canvass_rows(
    rows: list[list[str]],
    election: str,
    geography: str = "county",
    as_of: str | None = None,
) -> tuple[str | None, pl.DataFrame]:
    """One Official County and Canvass PDF's table rows as long rows.

    Returns the snapshot date passed in (the report's own date, read from the file's
    text by the pdf wrapper) and the rows: one per county and printed measure -- the
    four canvass modes plus the county's total, the official-turnout measures' shared
    vocabulary. The published shares stay out of the frame; the printed Statewide row
    is the counties' own sums and not a row of them.
    """
    _, published = _tallied_rows(
        "county-canvass",
        list(enumerate(rows, start=1)),
        election,
        geography,
        as_of,
        CANVASS_SHEET,
    )
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def parse_county_canvass_pdf(
    path: Path, election: str, geography: str = "county"
) -> tuple[str | None, pl.DataFrame]:
    """One Official County and Canvass PDF, table rows first, its date from its text."""
    try:
        rows, text = pdf_table_rows(path), read_pdf_text(path)
    except Exception as err:  # natural-pdf has no error taxonomy; a broken PDF fails here
        raise TurnoutParseError(f"could not read the county-canvass PDF ({err})") from err
    return parse_county_canvass_rows(rows, election, geography, as_of=pdf_snapshot_date(text))


def parse_votes_accepted_rows(
    rows: list[list[str]],
    election: str,
    geography: str = "county",
    as_of: str | None = None,
) -> tuple[str | None, pl.DataFrame]:
    """One Official Votes Accepted by County and Canvass PDF's table rows as long rows.

    The canvass sheet's shape at its "Total Votes Counted" table: the same columns as
    the county canvass, the same five measures, and the same checks against itself --
    each county's modes must sum to its printed total (which this sheet prints with or
    without a comma), the printed shares must still be their counts over the row's
    total, and the printed Statewide row must be the counties' sums.
    """
    _, published = _tallied_rows(
        "votes-accepted",
        list(enumerate(rows, start=1)),
        election,
        geography,
        as_of,
        CANVASS_SHEET,
    )
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


def parse_votes_accepted_pdf(
    path: Path, election: str, geography: str = "county"
) -> tuple[str | None, pl.DataFrame]:
    """One Official Votes Accepted by County and Canvass PDF, rows first, its date from
    its text."""
    try:
        rows, text = pdf_table_rows(path), read_pdf_text(path)
    except Exception as err:  # natural-pdf has no error taxonomy; a broken PDF fails here
        raise TurnoutParseError(f"could not read the votes-accepted PDF ({err})") from err
    return parse_votes_accepted_rows(rows, election, geography, as_of=pdf_snapshot_date(text))


TURNOUT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
PARSER_VERSION = "skipjack 0.2.0"


def download(url: str, timeout: float = 30.0) -> bytes:
    """One press-room file's bytes. SBE answers 200 with an HTML page for files that do
    not exist yet, so the caller sniffs the content, not the status code."""
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


def sniff(data: bytes) -> str | None:
    """The file kind from its first bytes; None when it is SBE's HTML placeholder."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK"):
        return "xlsx"
    return None


def extract_as_of_bytes(data: bytes, kind: str) -> str | None:
    """A downloaded file's as-of date, read the way the parse command reads the file.

    A workbook SBE exported unreadably reads as unknown (the parse command decides what
    a snapshot with no date means); a PDF's text layer does not appear in the raw
    bytes, so the text is extracted with natural-pdf from a temp copy.
    """
    if kind == "pdf":
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
            fh.write(data)
            fh.flush()
            try:
                return extract_as_of_text(read_requests_pdf(Path(fh.name)))
            except Exception:  # natural-pdf has no error taxonomy; unreadable reads as unknown
                return None
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
        for sheet in workbook.sheet_names:
            if found := _first_as_of(
                workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
            ):
                return found
    except (OSError, CalamineError):
        return None
    return None


def turnout_fetch(force: bool = False, election: str | None = None) -> list[SourceRecord]:
    """Download every configured turnout file whose content changed since the archive's
    newest revision and record it.

    An unchanged file (same bytes as the newest archived revision) is skipped, as is a
    file SBE has not published yet (their server answers with an HTML page). Archived
    files are named by content (`__<sha>.<ext>`), so two revisions fetched in the same
    second can never write the same path.
    """
    fetched: list[SourceRecord] = []
    failed: list[str] = []
    for e in ELECTIONS.values() if election is None else [ELECTIONS[election]]:
        for f in e.files:
            try:
                data = download(f.url)
            except httpx.HTTPError as err:
                logger.error("Download failed for %s: %s", f.url, err)
                failed.append(f.url)
                continue
            kind = sniff(data)
            if kind is None:
                logger.info("Not published yet: %s", f.url)
                continue
            as_of = extract_as_of_bytes(data, kind)
            digest = hashlib.sha256(data).hexdigest()
            if not force and _latest_sha(e.code, f.family, f.geography, kind) == digest:
                logger.info("Already archived: %s (%s)", f.family, f.geography)
                continue
            dest = (
                ARCHIVES_DIR
                / "sbe"
                / "campaign_stats"
                / e.code
                / f"{f.family}__{f.geography}__{as_of or 'unknown'}__{digest[:12]}.{kind}"
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(data)
            record = SourceRecord.create(
                source_id=(
                    f"sbe-turnout-{e.code.lower()}-{f.family}-{f.geography}-"
                    f"{(as_of or 'unknown').replace('-', '')}-{digest[:12]}"
                ),
                source_url=f.url,
                source_name=SOURCE_NAME,
                local_path=dest,
                document_title=f"{e.code} {f.family} ({f.geography}) as of {as_of or 'unknown'}",
            )
            append_to_manifest(MANIFEST_PATH, record)
            fetched.append(record)
            logger.info("Archived %s (as of %s)", dest.name, as_of or "unknown")
    if failed:
        raise TurnoutParseError(f"downloads failed for: {', '.join(failed)}")
    return fetched


def _latest_sha(election: str, family: str, geography: str, kind: str) -> str | None:
    """The SHA-256 of the newest archived revision of this file, from the manifest."""
    prefix = f"sbe-turnout-{election.lower()}-{family}-{geography}-"
    for record in reversed(read_manifest(MANIFEST_PATH)):
        if record.source_id.startswith(prefix) and Path(record.local_path).suffix == "." + kind:
            return record.sha256
    return None


def read_requests_pdf(path: Path) -> str:
    """Every page's text of a requests PDF, read the same way."""
    return read_pdf_text(path)


def turnout_parse(
    as_of: str | None = None,
    force: bool = False,
    data_dir: Path | None = None,
) -> tuple[int, list[str]]:
    """Every archived turnout revision that has not been parsed yet, as CSV snapshots.

    Snapshots are keyed by the as-of date parsed from the document; when the manifest
    has more than one revision of a snapshot the newest one is parsed. A CSV that
    already exists is skipped unless force. Failures are reported and written nowhere.
    Returns how many snapshots were written and the failures as strings.
    """
    data_dir = data_dir or TURNOUT_DATA_DIR
    snapshots: dict[tuple[str, str, str, str], list[SourceRecord]] = {}
    for record in read_manifest(MANIFEST_PATH):
        if not record.source_id.startswith("sbe-turnout-"):
            continue
        try:
            election_code, family, geography, snapshot = _split_snapshot_name(
                Path(record.local_path)
            )
        except ValueError:
            raise TurnoutParseError(
                f"unexpected turnout archive name {record.local_path!r}"
            ) from None
        snapshots.setdefault((election_code, family, geography, snapshot), []).append(record)

    written, failures = 0, []
    for (election_code, family, geography, snapshot), revisions in sorted(snapshots.items()):
        if as_of and snapshot not in (as_of, "unknown"):
            continue
        record = revisions[-1]  # the newest revision of this snapshot wins
        path = Path(record.local_path)
        out_dir = data_dir / "turnout" / election_code / family / geography
        if snapshot != "unknown" and not force:
            existing = out_dir / f"{snapshot}.csv"
            if existing.exists() and not _revision_updated(existing, record):
                continue  # this CSV came from this same revision
        if not path.exists():
            failures.append(f"{record.source_id}: the archived file is missing ({path.name})")
            continue
        try:
            snapshot_date, frame = _parse_file(path, record, election_code, family, geography)
            if snapshot_date is None:
                raise TurnoutParseError(f"no As-of date in {path.name}")
            if as_of and snapshot_date != as_of:
                continue  # an "unknown" fetch-time snapshot belongs to a different date
            out_csv = out_dir / f"{snapshot_date}.csv"
            if out_csv.exists() and not force and not _revision_updated(out_csv, record):
                continue  # parsed before, and the newest revision is the same bytes
            out_dir.mkdir(parents=True, exist_ok=True)
            sidecar = ProvenanceSidecar.from_source(
                record,
                parser_version=PARSER_VERSION,
                extraction_method=_extraction_of(path),
                checks={"rows": frame.height, "as_of": snapshot_date},
                notes=f"{election_code} {family} ({geography}) snapshot, reconciled against its printed totals.",
            )
            sidecar.election, sidecar.family, sidecar.geography = election_code, family, geography
            sidecar.write(out_csv)  # provenance first, so a crash leaves no bare CSV
            frame.write_csv(out_csv)
            written += 1
            logger.info("Parsed %s (%s rows)", out_csv.name, frame.height)
        except TurnoutParseError as err:
            failures.append(f"{record.source_id}: {err}")
    return written, failures


def _revision_updated(csv_path: Path, record: SourceRecord) -> bool:
    """Whether the manifest revision differs from the one the CSV was parsed from.

    A reposted file (SBE updates a snapshot later the same day) compares byte-for-byte
    against the sidecar's sha, so the CSV tracks the newest archived revision.
    """
    sidecar_path = csv_path.with_name(csv_path.stem + "_provenance.json")
    if not sidecar_path.exists():
        return True
    sidecar = ProvenanceSidecar.read(sidecar_path)
    return sidecar.sha256_original != record.sha256


def _split_snapshot_name(path: Path) -> tuple[str, str, str, str]:
    """An archived file's (election, family, geography, as-of): the layout turnout_fetch
    writes, `<election>/<family>__<geography>__<as-of>__<stamp>.<ext>`."""
    parts = path.name.split("__")
    if len(parts) != 4 or not path.parent.parent.parent.name:
        raise ValueError(str(path))
    if not all(parts):
        raise ValueError(str(path))
    election = path.parent.name
    if not election or " " in election:
        raise ValueError(str(path))
    return election, parts[0], parts[1], parts[2]


def _parse_file(
    path: Path, record: SourceRecord, election_code: str, family: str, geography: str
) -> tuple[str | None, pl.DataFrame]:
    """One archived file, by its family.

    The requests report parses from either format: the workbook where it is readable,
    its PDF twin where not (the GG26 export is corrupt on SBE's side)."""
    if family == "requests" and path.suffix == ".pdf":
        try:
            text = read_requests_pdf(path)
        except (
            Exception
        ) as err:  # natural-pdf has no error taxonomy; a broken PDF is a parse failure
            raise TurnoutParseError(f"could not read the requests PDF ({err})") from err
        return parse_requests_text(text, election_code)
    if family == "requests":
        return parse_requests_bytes(path.read_bytes(), election_code)
    if family == "sent-returned":
        return parse_sent_returned_bytes(path.read_bytes(), election_code, geography)
    if family == "left-to-count":
        return parse_left_to_count_bytes(path.read_bytes(), election_code, geography)
    if family == "official-turnout":
        return parse_turnout_official_bytes(path.read_bytes(), election_code, geography)
    if family in ("eligible-active", "eligible-inactive"):
        return parse_eligible_bytes(path.read_bytes(), election_code, family, geography)
    if family == "county-canvass":
        return parse_county_canvass_pdf(path, election_code, geography)
    if family == "votes-accepted":
        return parse_votes_accepted_pdf(path, election_code, geography)
    raise TurnoutParseError(f"{record.source_id}: no parser for family {family!r}")


def _extraction_of(path: Path) -> str:
    return "pdf text" if path.suffix == ".pdf" else "xlsx (calamine)"


# --- GP26: the requests workbook -----------------------------------------------------------------
