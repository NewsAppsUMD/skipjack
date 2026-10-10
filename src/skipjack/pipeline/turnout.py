"""The press-room turnout stat files: fetch, parse and reconcile one election at a time.

Each snapshot is identified by the "As of: <date>" the SBE prints in the document itself.
"""

from __future__ import annotations

import datetime
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
        FamilyFile("requests", "county", "pdf", f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf"),
        FamilyFile("sent-returned", "county", "xlsx", f"{base}/Absentees_Sent_and_Returned_by_County.xlsx"),
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
                    raise TurnoutParseError(f"Unexpected TOTAL row in the requests report: {line!r}")
            continue
        if matched := REQUESTS_ROW_RE.match(line):
            county = canonical_county(matched["county"])
            if county not in CANONICAL_COUNTIES:
                raise TurnoutParseError(f"Unrecognized county label {matched['county']!r} in the requests report")
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
