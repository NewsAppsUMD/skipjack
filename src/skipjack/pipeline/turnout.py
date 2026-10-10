"""The press-room turnout stat files: fetch, parse and reconcile one election at a time.

Each snapshot is identified by the "As of: <date>" the SBE prints in the document itself.
"""

from __future__ import annotations

import datetime
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
from python_calamine import CalamineError, CalamineWorkbook

from skipjack.pipeline.vrar import COUNTY_NORMALIZE

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


ELECTIONS: dict[str, Election] = {
    "GG26": Election(
        code="GG26",
        name="2026 general election",
        election_day="2026-11-03",
        files=_gg26_files(),
    )
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
SENT_IDENTITY_CELLS = {"CATEGORY": "category", "COUNTY NAME": "county", "DISTRICT": "district"}


def parse_sent_returned_bytes(
    data: bytes, election: str, geography: str
) -> tuple[str | None, pl.DataFrame]:
    """One Absentees Sent and Returned workbook as long rows.

    Returns the as-of date (None when no sheet carries one) and the published rows. The
    exports repeat a CATEGORY header before every block and shift their physical
    columns between blocks, so each block is read against its own header. Rows are
    published at one grain only -- county rows, or district rows (where the file reads
    ALL or nothing in the county cell); a district's county detail is reconciled
    against the district's printed total but never published. Nothing comes out until
    every printed total has been checked; a workbook that does not add up raises.
    """
    try:
        workbook = CalamineWorkbook.from_object(io.BytesIO(data))
    except (OSError, CalamineError) as err:
        raise TurnoutParseError(f"the sent-returned workbook could not be read: {err}") from err
    as_of = None
    published: list[dict] = []
    for sheet in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if as_of is None:
            as_of = _first_as_of(rows)
        published += _sent_sheet_rows(sheet, rows, election, geography, as_of)
    if not published:
        raise TurnoutParseError("the sent-returned workbook has no county or district rows")
    return as_of, pl.DataFrame(published, schema=TURNOUT_COLUMNS, strict=True)


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


def _sent_section(labels: list[str | None]) -> dict[int, tuple[str, object]]:
    """One block's column map. Unknown labels (notes, headings) are ignored."""
    mapping: dict[int, tuple[str, object]] = {}
    for index, label in enumerate(labels):
        if label is None:
            continue
        if label in SENT_IDENTITY_CELLS:
            mapping[index] = ("id", SENT_IDENTITY_CELLS[label])
        elif label in SENT_MEASURE_CELLS:
            mapping[index] = ("cell", SENT_MEASURE_CELLS[label])
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
    geography: str,
    as_of: str | None,
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
    seen: set[tuple[str, str, str]] = set()  # published identities, to catch repeats
    last_category = ""

    for index, row in enumerate(rows, start=1):
        row = row or []
        labels = [_normalize_label(cell) for cell in row]
        if "CATEGORY" in labels:
            mapping = _sent_section(labels)
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
                cells[payload] = int(float(str(raw)))
            except (TypeError, ValueError):
                raise TurnoutParseError(
                    f"{sheet} row {index}: the {payload[0]} {payload[1]} cell is not a "
                    f"number ({raw!r})"
                ) from None
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
        for measure in SENT_MEASURES:
            four = {party: cells[(party, measure)] for party in ("DEM", "REP", "OTH", "ALL")}
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
                        "family": "sent-returned",
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
                            "family": "sent-returned",
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
