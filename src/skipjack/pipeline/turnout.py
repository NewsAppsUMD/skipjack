"""The press-room turnout stat files: fetch, parse and reconcile one election at a time.

Each snapshot is identified by the "As of: <date>" the SBE prints in the document itself.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

from python_calamine import CalamineWorkbook

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
    files = [
        FamilyFile("requests", "county", "pdf", f"{base}/Mail-in_Ballot_Request_Counts_with_Chart.pdf")
    ]
    for geo in ("county", "congressional", "legislative"):
        files.append(
            FamilyFile(
                "sent-returned",
                geo,
                "xlsx",
                f"{base}/Absentees_Sent_and_Returned_by_{geo.capitalize()}.xlsx",
            )
        )
    return files


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
    """The as-of date "YYYY-MM-DD" from an "As of: October 9, 2026 6 PM" line, else None."""
    if not (found := AS_OF_RE.search(text)):
        return None
    parsed = datetime.datetime.strptime(found.group(1), "%B %d, %Y")
    return parsed.date().isoformat()


def extract_as_of_xlsx(path: Path) -> str | None:
    """The as-of date from the header cells of an SBE xlsx, None when absent or unreadable.

    The exports put the header block in odd spots (observed: sheet named after the file,
    the cell a few rows and columns in), and some are zip-corrupt, so scan every sheet's
    first 20 rows x 20 columns and let any read error read as "no date".
    """
    try:
        wb = CalamineWorkbook.from_path(str(path))
        for sheet in wb.sheet_names:
            rows = wb.get_sheet_by_name(sheet).to_python(skip_empty_area=False)[:20]
            for row in rows[:20]:
                for cell in row[:20]:
                    if isinstance(cell, str) and "As of" in cell:
                        return extract_as_of_text(cell)
    except Exception:
        return None
    return None