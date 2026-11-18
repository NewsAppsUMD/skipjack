"""The statewide races as one wide CSV per contest: a row per county, a column per candidate
on the ballot, then ``others`` (every write-in row added up) and ``total``.

    jurisdiction,harris,trump,oliver,stein,kennedy,others,total
    Allegany,9231,22141,130,136,363,136,32137

The shape is the one a classroom expects (a column per candidate, not a row per candidate),
so the files are built from the contest pages' data rather than stored under data/. There is
one for every contest the pages class as statewide whose office is President, U.S. Senate,
Governor, Comptroller or Attorney General: each general, each party's primary, and the 2026
general's unofficial count while it is live (a blank cell is a count the county has not
reported yet; the file goes when the certified results are published). The judges'
retention questions are for/against votes and get no file.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata

from skipjack.web import results as certified
from skipjack.web import results_unofficial as unofficial
from skipjack.web.offices import OFFICE_NAMES, UNOFFICIAL_OFFICE_NAMES

# office key -> the file's office part; only these offices get a file
FILE_OFFICES = {
    "president": "president",
    "us_senate": "us-senate",
    "governor": "governor",
    "comptroller": "comptroller",
    "attorney_general": "attorney-general",
}
FIXED_COLUMNS = ("jurisdiction", "others", "total")
# the reference file's spelling of the one county the results files abbreviate
JURISDICTIONS = {"St. Mary's": "Saint Mary's"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_FILE = re.compile(r"^([A-Z]{2}\d{2})-([a-z-]+?)(?:-(dem|rep|non))?\.csv$")


def _plain(token: str) -> str:
    """Lower-case ASCII letters and digits only: 'Andrè' -> 'andre', 'Jr.' -> 'jr'."""
    folded = unicodedata.normalize("NFKD", token).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _name_tokens(name: str) -> list[str]:
    """The first person on a ballot line, as plain tokens without a Jr./Sr. suffix."""
    first = re.split(r"\s+and\s+", name, maxsplit=1)[0].split(",")[0]
    tokens = [_plain(t) for t in first.split()]
    return [t for t in tokens if t and t not in _SUFFIXES]


def column_name(name: str) -> str:
    """The lower-case last name of the first person on a ballot line."""
    tokens = _name_tokens(name)
    return tokens[-1] if tokens else ""


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        chosen = name
        while chosen in seen or chosen in FIXED_COLUMNS:
            seen[name] = seen.get(name, 1) + 1
            chosen = f"{name}_{seen[name]}"
        seen.setdefault(chosen, 1)
        out.append(chosen)
    return out


def column_names(candidates: list[dict]) -> list[str]:
    """A column per candidate: the last name, first_last where two share one, and a numbered
    name where that still collides or would shadow a fixed column."""
    tokens = [_name_tokens(c["name"]) for c in candidates]
    last = [t[-1] if t else "candidate" for t in tokens]
    names = []
    for i, t in enumerate(tokens):
        if last.count(last[i]) > 1 and len(t) > 1:
            names.append(f"{t[0]}_{t[-1]}")
        else:
            names.append(last[i])
    return _unique(names)


def contest_csv(contest: dict) -> str | None:
    """A contest page's county table as the wide CSV, or None for a for/against question."""
    if contest["has_against"]:
        return None
    candidates = contest["candidates"]
    # columns in the SBE's ballot order, as the reference file has them (the page's table is
    # by votes)
    by_ballot = sorted(range(len(candidates)), key=lambda i: candidates[i].get("ballot", i))
    ballot = [i for i in by_ballot if not candidates[i]["write_in"]]
    write_ins = [i for i, c in enumerate(candidates) if c["write_in"]]
    names = column_names([candidates[i] for i in ballot])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["jurisdiction", *names, "others", "total"])
    for row in contest["county_rows"]:
        cells = row["cells"]
        counted = [cells[i] for i in write_ins if cells[i] is not None]
        others = sum(counted) if counted or not write_ins else None
        writer.writerow(
            [
                JURISDICTIONS.get(row["county"], row["county"]),
                *(_cell(cells[i]) for i in ballot),
                _cell(others),
                row["total"],
            ]
        )
    return out.getvalue()


def _cell(n: int | None) -> int | str:
    return "" if n is None else n


def file_for(code: str, office: str, party_file: str, scope: str) -> str | None:
    """The file name of a contest's CSV, or None when the contest gets none."""
    key = OFFICE_NAMES.get(office) or UNOFFICIAL_OFFICE_NAMES.get(office)
    if scope != "statewide" or key not in FILE_OFFICES:
        return None
    suffix = "" if party_file == "all" else f"-{party_file}"
    return f"{code}-{FILE_OFFICES[key]}{suffix}.csv"


def _module(code: str):
    return unofficial if unofficial.is_unofficial(code) else certified


def downloads(conn: sqlite3.Connection) -> list[dict]:
    """Every statewide CSV the site offers, newest election first and in hub order within
    one: file, code, election, slug, title, unofficial."""
    out = []
    try:
        elections = unofficial.site_elections(conn)
    except sqlite3.OperationalError:  # an older database without the results tables
        return []
    for e in elections:
        code = e["code"]
        for c in _module(code)._pages(conn, code):
            file = file_for(code, c["office"], c["party_file"], c["scope"])
            if file is None:
                continue
            out.append(
                {
                    "file": file,
                    "code": code,
                    "election": e,
                    "slug": c["slug"],
                    "title": c["title"],
                    "unofficial": unofficial.is_unofficial(code),
                }
            )
    return out


def csv_for(conn: sqlite3.Connection, file: str) -> str | None:
    """The CSV text of the file called ``file``, or None when the site offers no such file."""
    m = _FILE.match(file)
    if m is None:
        return None
    code = m.group(1)
    found = next((d for d in downloads(conn) if d["file"] == file and d["code"] == code), None)
    if found is None:
        return None
    contest = _module(code).contest(conn, code, found["slug"])
    return contest_csv(contest) if contest else None
