"""County-level election results: fetch the SBE's CSVs, check them, write one CSV per file.

Everything is keyed by the SBE election code (GP26, PG24, ...). A parse is all-or-nothing per
election: every county file must read, every row's total must equal the sum of its vote types,
and the county sums must equal the SBE's statewide rows in a statewide check file (the
congressional breakdown files, or the archive's districts files). The 2010-2018 districts files
carry election-night votes only, so those elections are checked on that column.

The figures are the board's, never computed: where the SBE's own files disagree with themselves
(STATEWIDE_CHECK_EXCEPTIONS, ROW_TOTAL_EXCEPTIONS) the discrepancy is listed here, accepted
only as listed, and recorded in the sidecar.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import httpx
import polars as pl

from skipjack.pipeline.fetch import ARCHIVES_DIR, MANIFEST_PATH, SOURCE_NAME, resolve_archived
from skipjack.pipeline.provenance import (
    ProvenanceSidecar,
    SourceRecord,
    append_to_manifest,
    read_manifest,
    sidecar_path,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "skipjack 0.4.0"
RESULTS_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

ARCHIVE_BASE = "https://elections.maryland.gov/elections/archive/{year}/election_data"
# The current cycle is served from the live path; it moves to the archive path after the cycle.
LIVE_BASE = "https://elections.maryland.gov/elections/{year}/election_data"

# The SBE's county order; names are the repo's canonical ones ("Saint Mary's" is "St. Mary's").
COUNTY_CODES = {
    "01": "Allegany",
    "02": "Anne Arundel",
    "03": "Baltimore City",
    "04": "Baltimore County",
    "05": "Calvert",
    "06": "Caroline",
    "07": "Carroll",
    "08": "Cecil",
    "09": "Charles",
    "10": "Dorchester",
    "11": "Frederick",
    "12": "Garrett",
    "13": "Harford",
    "14": "Howard",
    "15": "Kent",
    "16": "Montgomery",
    "17": "Prince George's",
    "18": "Queen Anne's",
    "19": "St. Mary's",
    "20": "Somerset",
    "21": "Talbot",
    "22": "Washington",
    "23": "Wicomico",
    "24": "Worcester",
}

# (label as the SBE prints it, column stem). The columns are "<label> Votes" and, in general
# files, "<label> Votes Against"; the output CSVs use "<stem>_votes" / "<stem>_against".
VOTE_TYPES = [
    ("Early", "early"),
    ("Election Night", "election_night"),
    ("Mail-In Ballot 1", "mail_in_1"),
    ("Provisional", "provisional"),
    ("Mail-In Ballot 2", "mail_in_2"),
]

# 2020's vote types: the general and the primary use different labels, so each election carries
# its own list (the 2020 primary has no early or Mail-In Ballot 2 columns). Elections before 2020
# use the archive's labels; the 2010 general alone adds a third absentee canvass.
VOTE_TYPES_2020_GENERAL = [
    ("Early Voting", "early_voting"),
    ("Election Day", "election_day"),
    ("By Mail", "by_mail"),
    ("Prov.", "prov"),
    ("By Mail 2", "by_mail_2"),
]
VOTE_TYPES_2020_PRIMARY = [
    ("Vote By Mail", "vote_by_mail"),
    ("In Person Voting", "in_person_voting"),
    ("Provisional", "provisional"),
]
VOTE_TYPES_ARCHIVE = [
    ("Early", "early"),
    ("Election Night", "election_night"),
    ("Absentees", "absentees"),
    ("Provisional", "provisional"),
    ("2nd Absentees", "absentees_2"),
]
VOTE_TYPES_2010_GENERAL = VOTE_TYPES_ARCHIVE + [("3rd Absentees", "absentees_3")]

# 2010-2020 files are named by county, not number. Most are the county name with underscores;
# the irregular ones follow the SBE's own links (Baltimore City's files read
# "Baltimore_City_County_", the county's "Baltimore_County_").
COUNTY_FILE_STEMS = {code: name.replace(" ", "_") for code, name in COUNTY_CODES.items()} | {
    "04": "Baltimore",
    "17": "Prince_Georges",
    "18": "Queen_Annes",
    "19": "St._Marys",
}
PARTY_FILES_NAMED = {"dem": "Democratic", "rep": "Republican", "non": "NonPartisan"}

PARTY_FILES = {"dem": "Democratic", "rep": "Republican", "non": "Non-Partisan"}
# the SBE's own spelling of the non-partisan breakdown file
BREAKDOWN_SUFFIX = {"dem": "Democratic", "rep": "Republican", "non": "Non-Partisian"}
OUTPUT_ID_COLUMNS = [
    "election",
    "county",
    "party_file",
    "office",
    "district",
    "candidate",
    "party",
    "winner",
    "write_in",
]
PRECINCT_ID_COLUMNS = [
    "election", "county", "party_file", "precinct", "congressional", "legislative", "office",
    "district", "candidate", "party", "winner", "write_in",
]  # fmt: skip

# Statewide rows whose figure the SBE's own files disagree on: (election code, office, district,
# candidate, party) -> (what the county files sum to, what the state file's County 00 row says),
# both on the column the election is checked on (election-night votes for 2010-2018). The SBE's
# own figures disagree by two votes; recorded, not corrected. A mismatch is accepted only when
# it equals the listed pair; any other mismatch, for any other row, still fails the election.
STATEWIDE_CHECK_EXCEPTIONS: dict[tuple[str, str, str, str, str], tuple[int, int]] = {
    ("PG12", "President - Vice Pres", "", "Other Write-Ins", "BOT"): (7245, 7247),
    ("GG10", "U.S. Senator", "", "Other Write-Ins", "BOT"): (1359, 1361),
}

# County files in which the SBE's printed Total Votes is not the sum of the vote types on some
# rows: (election code, county, party file) -> exactly how many rows. The board's own arithmetic
# errors in that one file (Baltimore City's 2010 general: 15 write-in rows whose total is below
# the sum); the printed total is kept as total_votes and the rows are listed in the sidecar. A
# different count, or any mismatch in a file not listed (a primary's other party files
# included), still fails the election.
ROW_TOTAL_EXCEPTIONS: dict[tuple[str, str, str], int] = {("GG10", "Baltimore City", "all"): 15}

# Precinct rows that do not sum to the board's own county file: (election code, county, party
# file, office, district, candidate, party, column) -> (what the precincts sum to, what the county
# file says). Empty until a full run finds one; a mismatch is accepted only when it equals the
# listed pair, and any other fails the election.
PRECINCT_CHECK_EXCEPTIONS: dict[tuple[str, ...], tuple[int, int]] = {}


class ResultsParseError(RuntimeError):
    pass


@dataclass
class ResultsElection:
    code: str
    year: int
    kind: str  # "primary" or "general"
    name: str
    election_day: str
    base_url: str
    layout: str = "coded"  # "coded" (2022 on) or "named" (2010-2020)
    vote_types: list[tuple[str, str]] = field(default_factory=lambda: list(VOTE_TYPES))
    # where the statewide rows come from: "congressional" (the coded breakdown files, or the
    # archive's State_Congressional_Districts files), "legislative" (2010's districts files),
    # "all_state" (the 2020 general's All_State file) or "none" (the 2020 primary)
    check: str = "congressional"
    # which county column the statewide rows are compared with: "total" (the check file carries
    # full totals) or a vote-type stem, "election_night" for 2010-2018, whose districts files
    # carry election-day votes only
    check_votes: str = "total"
    # False until the SBE posts the files (it answers an HTML page meanwhile); flip it later
    published: bool = True
    # the board posts precinct files for every election but the 2020 primary
    precincts: bool = True
    # ballot questions are on general-election ballots only (set by _election)
    questions: bool = False

    @property
    def party_files(self) -> list[str]:
        return ["dem", "rep", "non"] if self.kind == "primary" else ["all"]

    @property
    def precinct_vote_types(self) -> list[tuple[str, str]]:
        """The vote types the precinct files carry: through 2018 only election-night votes are
        split by precinct; from 2020 every vote type is."""
        if self.year <= 2018:
            return [("Election Night", "election_night")]
        return list(self.vote_types)

    @property
    def precinct_every_vote_type(self) -> bool:
        """Whether the precinct files carry every vote type (from 2020), so their totals can be
        checked too; through 2018 they carry election-night votes only."""
        return self.precinct_vote_types == self.vote_types

    @property
    def precinct_total(self) -> bool:
        """Whether the precinct files print Total Votes (only the 2020 general's do); elsewhere
        the precinct total is the sum of the vote types."""
        return self.layout == "named" and self.year == 2020

    @property
    def question_check(self) -> bool:
        """Whether the board posts a statewide ballot-question breakdown (not for 2010)."""
        return self.questions and self.year >= 2012


def _election(
    code, year, kind, name, day, live=False, published=True, precincts=True
) -> ResultsElection:
    base = (LIVE_BASE if live else ARCHIVE_BASE).format(year=year)
    e = ResultsElection(code, year, kind, name, day, base, published=published)
    e.precincts = precincts
    e.questions = kind == "general"
    if year >= 2022:
        pass  # the coded layout, the dataclass defaults
    elif year == 2020:
        e.layout = "named"
        e.vote_types = list(
            VOTE_TYPES_2020_GENERAL if kind == "general" else VOTE_TYPES_2020_PRIMARY
        )
        e.check = "all_state" if kind == "general" else "none"
    elif 2010 <= year <= 2018:
        e.layout = "named"
        e.vote_types = list(
            VOTE_TYPES_2010_GENERAL if (year, kind) == (2010, "general") else VOTE_TYPES_ARCHIVE
        )
        e.check = "legislative" if year == 2010 else "congressional"
        e.check_votes = "election_night"
    else:
        raise ValueError(f"{code}: no results layout rule for {year}")
    return e


RESULTS_ELECTIONS: dict[str, ResultsElection] = {
    e.code: e
    for e in [
        _election("GP26", 2026, "primary", "2026 gubernatorial primary", "2026-06-23", live=True),
        # unofficial until certified; flip published when the SBE posts the files
        _election(
            "GG26",
            2026,
            "general",
            "2026 general election",
            "2026-11-03",
            live=True,
            published=False,
        ),  # fmt: skip
        _election("PG24", 2024, "general", "2024 presidential general election", "2024-11-05"),
        _election("PP24", 2024, "primary", "2024 presidential primary", "2024-05-14"),
        _election("GG22", 2022, "general", "2022 gubernatorial general election", "2022-11-08"),
        _election("GP22", 2022, "primary", "2022 gubernatorial primary", "2022-07-19"),
        _election("PG20", 2020, "general", "2020 presidential general election", "2020-11-03"),
        _election(
            "PP20", 2020, "primary", "2020 presidential primary", "2020-06-02", precincts=False
        ),
        # GG18 is also a turnout code (pipeline/turnout.py); the ids and directories differ
        _election("GG18", 2018, "general", "2018 gubernatorial general election", "2018-11-06"),
        _election("GP18", 2018, "primary", "2018 gubernatorial primary", "2018-06-26"),
        _election("PG16", 2016, "general", "2016 presidential general election", "2016-11-08"),
        _election("PP16", 2016, "primary", "2016 presidential primary", "2016-04-26"),
        _election("GG14", 2014, "general", "2014 gubernatorial general election", "2014-11-04"),
        _election("GP14", 2014, "primary", "2014 gubernatorial primary", "2014-06-24"),
        _election("PG12", 2012, "general", "2012 presidential general election", "2012-11-06"),
        _election("PP12", 2012, "primary", "2012 presidential primary", "2012-04-03"),
        _election("GG10", 2010, "general", "2010 gubernatorial general election", "2010-11-02"),
        _election("GP10", 2010, "primary", "2010 gubernatorial primary", "2010-09-14"),
    ]
}


# The precinct and question archives are gitignored (about 250 MB, re-fetchable); the county
# files stay in the committed results/ folder.
ARCHIVE_FOLDERS = {
    "county": "results",
    "check": "results",
    "precinct": "results-precinct",
    "question": "results-questions",
    "question-precinct": "results-questions",
    "question-check": "results-questions",
}
DOCUMENT_TITLES = {
    "county": "{code} {county} results ({pf})",
    "check": "{code} congressional breakdown ({pf})",
    "precinct": "{code} {county} results by precinct ({pf})",
    "question": "{code} {county} ballot questions",
    "question-precinct": "{code} {county} ballot questions by precinct",
    "question-check": "{code} ballot questions congressional breakdown",
}


@dataclass
class ResultsFile:
    election: ResultsElection
    county_code: str  # "00" for the statewide check files
    county: str
    party_file: str  # "dem" | "rep" | "non" | "all"
    # "county" | "check" | "precinct" | "question" | "question-precinct" | "question-check"
    role: str

    @property
    def file_name(self) -> str:
        if self.role not in ("county", "check"):
            return self._precinct_or_question_name()
        code = self.election.code
        year = self.election.year
        if self.election.layout == "named":
            if self.role == "check":
                # county_files makes no check file when check == "none"
                assert self.election.check != "none"
                if self.election.check == "all_state":
                    return "All_State_2020_General.csv"
                districts = (
                    "Legislative" if self.election.check == "legislative" else "Congressional"
                )
                if self.election.kind == "general":
                    return f"State_{districts}_Districts_{year}_General.csv"
                party = PARTY_FILES_NAMED[self.party_file]
                return f"State_{districts}_Districts_{party}_{year}_Primary.csv"
            stem = COUNTY_FILE_STEMS[self.county_code]
            if self.election.kind == "general":
                return f"{stem}_County_{year}_General.csv"
            return f"{stem}_County_{PARTY_FILES_NAMED[self.party_file]}_{year}_Primary.csv"
        if self.role == "check":
            if self.election.kind == "general":
                return f"{code}_CongressionalBreakDown.csv"
            return f"{code}_CongressionalBreakDown{BREAKDOWN_SUFFIX[self.party_file]}.csv"
        if self.election.kind == "general":
            return f"{code}_{self.county_code}CountyResults.csv"
        return f"{code}_{self.county_code}{PARTY_FILES[self.party_file]}Results.csv"

    def _precinct_or_question_name(self) -> str:
        e, cc = self.election, self.county_code
        if e.layout == "coded":
            if self.role == "precinct":
                if e.kind == "general":
                    return f"{e.code}_{cc}PrecinctsResults.csv"
                return f"{e.code}_{cc}Precincts{PARTY_FILES_NAMED[self.party_file]}.csv"
            return {
                "question": f"{e.code}_{cc}QuestionResults.csv",
                "question-precinct": f"{e.code}_{cc}QuestionbyPrecinctsResults.csv",
                "question-check": f"{e.code}_AllQuestionsCongressionalBreakDown.csv",
            }[self.role]
        if self.role == "question-check":
            return f"Questions_State_Congressional_Districts_{e.year}.csv"
        stem = COUNTY_FILE_STEMS[cc]
        if self.role == "precinct":
            if e.kind == "general":
                return f"{stem}_By_Precinct_{e.year}_General.csv"
            return f"{stem}_By_Precinct_{PARTY_FILES_NAMED[self.party_file]}_{e.year}_Primary.csv"
        level = "County" if self.role == "question" else "Precinct"
        return f"{stem}_Questions_{level}_{e.year}.csv"

    @property
    def url(self) -> str:
        return f"{self.election.base_url}/{self.file_name}"

    @property
    def source_id(self) -> str:
        code = self.election.code
        if self.role == "check":
            return f"sbe-results-{code}-check-{self.party_file}"
        if self.role in ("county", "precinct"):
            return f"sbe-results-{code}-{self.role}-{self.county_code}-{self.party_file}"
        if self.role == "question-check":
            return f"sbe-results-{code}-question-check"
        return f"sbe-results-{code}-{self.role}-{self.county_code}"  # question, question-precinct

    @property
    def local_path(self) -> Path:
        folder = ARCHIVE_FOLDERS[self.role]
        return ARCHIVES_DIR / "sbe" / folder / self.election.code / self.file_name

    @property
    def document_title(self) -> str:
        return DOCUMENT_TITLES[self.role].format(
            code=self.election.code, county=self.county, pf=self.party_file
        )


def county_files(election: ResultsElection) -> list[ResultsFile]:
    files = [
        ResultsFile(election, code, name, pf, "county")
        for code, name in COUNTY_CODES.items()
        for pf in election.party_files
    ]
    if election.check == "none":
        return files  # the 2020 primary has no statewide file
    files += [
        ResultsFile(election, "00", "State of Maryland", pf, "check") for pf in election.party_files
    ]
    return files


def precinct_files(election: ResultsElection) -> list[ResultsFile]:
    """One precinct file per county and party file (none for the 2020 primary)."""
    if not election.precincts:
        return []
    return [
        ResultsFile(election, code, name, pf, "precinct")
        for code, name in COUNTY_CODES.items()
        for pf in election.party_files
    ]


def question_files(election: ResultsElection) -> list[ResultsFile]:
    """A general's ballot-question files: each county's county and precinct files, and the
    statewide breakdown where the board posts one."""
    if not election.questions:
        return []
    files = [
        ResultsFile(election, code, name, "all", role)
        for role in ("question", "question-precinct")
        for code, name in COUNTY_CODES.items()
    ]
    if election.question_check:
        files.append(ResultsFile(election, "00", "State of Maryland", "all", "question-check"))
    return files


FETCH_LEVELS = ("county", "precinct", "questions", "all")


def files_for(election: ResultsElection, level: str = "all") -> list[ResultsFile]:
    """The files of one level ("county", "precinct", "questions") or of all three."""
    if level not in FETCH_LEVELS:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(FETCH_LEVELS)}")
    files = []
    if level in ("county", "all"):
        files += county_files(election)
    if level in ("precinct", "all"):
        files += precinct_files(election)
    if level in ("questions", "all"):
        files += question_files(election)
    return files


def county_slug(county: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", county.lower().replace("'", "")).strip("-")


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory and os.replace, so a crash never leaves a
    half-written file at `path`."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def newest_source(manifest_path: Path, source_id: str) -> SourceRecord | None:
    """The newest manifest row for a source id (`results_fetch --force` appends a row per change;
    provenance.find_source returns the first, which is the oldest)."""
    found = None
    for record in read_manifest(manifest_path):
        if record.source_id == source_id:
            found = record
    return found


# ---- reading --------------------------------------------------------------------------------


def _frame(data: bytes, where: str, optional_tail: tuple[str, ...] = ()) -> pl.DataFrame:
    """The file as a frame of stripped strings (BOM, quotes, and the stray trailing space).

    A row may stop short of the header's trailing columns named in ``optional_tail`` (PG20's
    precinct question files leave off their last Total columns); those cells read as blank. A
    row short by more than that, or long, fails."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")  # a few 2020 files hold a Windows-1252 n with tilde
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ResultsParseError(f"{where}: empty file")
    header = []
    for c in rows[0]:
        c = c.strip()
        # Dorchester's 2020 file repeats an unused "N/A Votes" column; keep the names unique
        header.append(f"{c}.{header.count(c)}" if c in header else c)
    width = len(header)
    tail = 0
    while tail < width and header[width - 1 - tail] in optional_tail:
        tail += 1
    body = []
    for r in rows[1:]:
        cells = [c.strip() for c in r]
        if width - tail <= len(cells) < width:
            cells += [""] * (width - len(cells))
        body.append(cells)
    if any(len(r) != width for r in body):
        raise ResultsParseError(f"{where}: rows do not all have {width} columns")
    return pl.DataFrame(body, schema={c: pl.Utf8 for c in header}, orient="row")


def _int(value: str, where: str, column: str) -> int | None:
    """An integer cell; blank, and the archive's "#NULL#" token, read as None."""
    value = value.replace(",", "").strip()
    if value == "" or value.upper() == "#NULL#":
        return None
    try:
        return int(value)
    except ValueError:
        raise ResultsParseError(f"{where}: {column!r} is not a number: {value!r}") from None


class ReadFile(NamedTuple):
    frame: pl.DataFrame
    # the rows whose printed total is not the sum of the vote types, accepted only for a file
    # in ROW_TOTAL_EXCEPTIONS: {office, district, candidate, party, total_votes,
    # sum_of_vote_types}
    row_total_exceptions: list[dict]


def _read_coded(data: bytes, election: ResultsElection, county: str, party_file: str) -> ReadFile:
    """One county file of the 2022-onward layout, as the wide output frame (checks row totals)."""
    return _read_wide(data, election, county, party_file, blank_is_zero=False)


def _read_named(data: bytes, election: ResultsElection, county: str, party_file: str) -> ReadFile:
    """One county file of the 2010-2020 layout: same column names as the coded layout, a
    different order, and each year's own vote types.

    Columns are read by name, so the reordering does not matter; the vote-type labels come from
    the election's own list, and unused columns (Dorchester's blank "N/A Votes") are ignored,
    which the row-total check keeps honest. Some files (Frederick's 2020) leave a zero vote cell
    blank, and the 2010-2018 files sometimes print "#NULL#" for one, or for a write-in's Total
    Votes (two 2014 files); all read as 0 here, and the total must still equal the sum. A general
    row with a blank Total Votes Against whose Against cells are blank or 0 (the 2012 general
    prints a lone 0 in Election Night Votes Against on 835 rows) has no Against figures: None
    throughout.
    """
    return _read_wide(data, election, county, party_file, blank_is_zero=True)


READERS = {"coded": _read_coded, "named": _read_named}


def read_precinct(data: bytes, election: ResultsElection, county: str, party_file: str) -> ReadFile:
    """One county's precinct file (a row per precinct and candidate) as the wide output frame,
    with precinct, congressional and legislative after party_file. Read like the county files of
    its layout; the total is checked where the file prints one and summed where it does not."""
    return _read_wide(
        data, election, county, party_file, blank_is_zero=election.layout == "named",
        level="precinct",
    )  # fmt: skip


def _precinct_id_columns(election: ResultsElection) -> list[str]:
    """The columns that place a precinct row: the coded layout's combined district-precinct
    column, or the named layout's two, with Cong and Legs from 2012 (2010's files have none)."""
    if election.layout == "coded":
        return ["County", "Election District - Precinct", "Congressional", "Legislative"]
    columns = ["County", "Election District", "Election Precinct"]
    return columns + (["Cong", "Legs"] if election.year >= 2012 else [])


def _precinct_id(r: dict, where: str) -> str:
    """The precinct as DDD-PPP, from either layout's columns."""
    if "Election District - Precinct" in r:
        value = r["Election District - Precinct"]
    else:
        value = f"{r['Election District']}-{r['Election Precinct']}"
    district, _, precinct = value.partition("-")
    if not all(p.isascii() and p.isdigit() for p in (district, precinct)):
        raise ResultsParseError(f"{where}: precinct {value!r} is not district-precinct")
    return f"{int(district):03d}-{int(precinct):03d}"


def _read_wide(
    data: bytes,
    election: ResultsElection,
    county: str,
    party_file: str,
    blank_is_zero: bool,
    level: str = "county",
) -> ReadFile:
    """One results file of either layout, read by column name, as the wide output frame.

    level "county" reads a county file, whose printed Total Votes must be the sum of the vote
    types. level "precinct" reads a precinct file: its id columns (the county code, checked
    against `county`, and the precinct as DDD-PPP, congressional and legislative district), the
    election's precinct vote types, and a Total Votes that is checked where the file prints one
    (election.precinct_total) and otherwise the sum of the vote types (total_against likewise:
    the sum of the Against cells, None where the row has none, and in the named layout None
    for a contest with no Against vote in any precinct: see _drop_lone_zero_against).
    """
    precinct = level == "precinct"
    where = f"{election.code} {county} {party_file}" + (" precincts" if precinct else "")
    raw = _frame(data, where)
    against = election.kind == "general"
    code = next((c for c, n in COUNTY_CODES.items() if n == county), "00")
    vote_types = election.precinct_vote_types if precinct else election.vote_types
    printed = not precinct or election.precinct_total
    need = ["Office Name", "Office District", "Candidate Name", "Party", "Winner"]
    if precinct:
        need = _precinct_id_columns(election) + need
    if printed:
        need.append("Total Votes")
    for label, _ in vote_types:
        need.append(f"{label} Votes")
        if against:
            need.append(f"{label} Votes Against")
    if against:
        need.append("Write-In?")
        if printed:
            need.append("Total Votes Against")
    missing = [c for c in need if c not in raw.columns]
    if missing:
        expected = ResultsFile(election, code, county, party_file, level).file_name
        raise ResultsParseError(
            f"{where}: expected {election.code} {election.layout} layout columns in {expected}; "
            f"missing {missing}"
        )
    unknown = sorted(
        {
            name
            for c in raw.columns
            if (name := re.sub(r"\.\d+$", "", c)).endswith(" Votes")
            and name != "Total Votes"
            and not name.startswith("N/A")
            and name not in need
        }
    )
    if unknown:
        raise ResultsParseError(f"{where}: vote columns not in the registry: {unknown}")
    if raw.height == 0:
        raise ResultsParseError(f"{where}: no data rows (header only)")

    rows = []
    allowed_total_mismatches = (
        0 if precinct else ROW_TOTAL_EXCEPTIONS.get((election.code, county, party_file), 0)
    )
    row_total_exceptions: list[dict] = []
    for r in raw.iter_rows(named=True):
        who = f"{where}: {r['Office Name']} / {r['Candidate Name']}"
        out = {"election": election.code, "county": county, "party_file": party_file}
        if precinct:
            if r["County"] != code:
                raise ResultsParseError(
                    f"{who}: County {r['County']!r} is not {county}'s code {code}"
                )
            out["precinct"] = _precinct_id(r, who)
            # 2010's named files carry no Cong/Legs: "" (need requires them where they exist)
            out["congressional"] = r.get("Congressional", r.get("Cong", ""))
            out["legislative"] = r.get("Legislative", r.get("Legs", ""))
        out |= {
            "office": r["Office Name"],
            "district": r["Office District"],
            "candidate": r["Candidate Name"],
            "party": r["Party"],
            "winner": 1 if r["Winner"].upper() in ("Y", "YES") else 0,
            "write_in": 1 if against and r["Write-In?"].upper() == "Y" else 0,
        }
        votes_sum, against_sum, against_seen = 0, 0, False
        for label, stem in vote_types:
            v = _int(r[f"{label} Votes"], who, f"{label} Votes")
            if v is None and blank_is_zero:
                v = 0
            if v is None:
                raise ResultsParseError(f"{who}: blank {label} Votes")
            out[f"{stem}_votes"] = v
            votes_sum += v
            if against:
                a = _int(r[f"{label} Votes Against"], who, f"{label} Votes Against")
                out[f"{stem}_against"] = a
                if a is not None:
                    against_sum += a
                    against_seen = True
        if printed:
            total = _int(r["Total Votes"], who, "Total Votes")
            if total is None and blank_is_zero:
                total = 0
            if total is None:
                raise ResultsParseError(f"{who}: blank Total Votes")
        else:
            total = votes_sum
        out["total_votes"] = total
        if total != votes_sum:
            if len(row_total_exceptions) >= allowed_total_mismatches:
                raise ResultsParseError(
                    f"{who}: total votes {total} != sum of vote types {votes_sum}"
                    + (
                        f" ({allowed_total_mismatches} such rows are listed for this file)"
                        if allowed_total_mismatches
                        else ""
                    )
                )
            row_total_exceptions.append(
                {
                    "office": out["office"],
                    "district": out["district"],
                    "candidate": out["candidate"],
                    "party": out["party"],
                    "total_votes": total,
                    "sum_of_vote_types": votes_sum,
                }
            )
        if against and not printed:
            out["total_against"] = against_sum if against_seen else None
        elif against:
            total_against = _int(r["Total Votes Against"], who, "Total Votes Against")
            if blank_is_zero and total_against is None and against_seen and against_sum == 0:
                # a lone 0 in one Against cell with no total: no Against figures on this row
                for _, stem in vote_types:
                    out[f"{stem}_against"] = None
                against_seen = False
            out["total_against"] = total_against
            if (total_against or 0) != against_sum or (total_against is None) == against_seen:
                raise ResultsParseError(
                    f"{who}: total against {total_against} != sum of vote types against "
                    f"{against_sum if against_seen else None}"
                )
        rows.append(out)
    if precinct and against and not printed and blank_is_zero:
        _drop_lone_zero_against(rows, vote_types)
    if len(row_total_exceptions) != allowed_total_mismatches:
        raise ResultsParseError(
            f"{where}: {len(row_total_exceptions)} rows whose total is not the sum of the vote "
            f"types, but {allowed_total_mismatches} are listed for this file"
        )

    ids = PRECINCT_ID_COLUMNS if precinct else OUTPUT_ID_COLUMNS
    schema = {c: pl.Utf8 for c in ids if c not in ("winner", "write_in")}
    schema |= {"winner": pl.Int64, "write_in": pl.Int64}
    cols = []
    for _, stem in vote_types:
        cols.append(f"{stem}_votes")
        if against:
            cols.append(f"{stem}_against")
    cols.append("total_votes")
    if against:
        cols.append("total_against")
    schema |= {c: pl.Int64 for c in cols}
    return ReadFile(pl.DataFrame(rows, schema=schema), row_total_exceptions)


def _drop_lone_zero_against(rows: list[dict], vote_types: list[tuple[str, str]]) -> None:
    """A named-layout precinct file prints no Total Votes Against, so the county reader's rule
    (a lone 0 in an Against cell with a blank total is no Against figure) is applied by contest:
    a contest whose Against cells are blank or 0 in every precinct has no Against figures, None
    throughout, as its county row reads. A contest with Against votes somewhere (a retention
    judge) keeps a precinct's 0, which is a count there.

    In theory a retention contest whose election-night Against is 0 or blank in every precinct,
    while absentee Against votes exist, would read 0 at the county and None here; the precinct
    check counts None as 0, so it would still pass. No county file 2010-2018 has such a contest.
    """
    stems = [f"{stem}_against" for _, stem in vote_types]
    counted = {(r["office"], r["district"]) for r in rows if any(r[c] for c in stems)}
    for r in rows:
        if (r["office"], r["district"]) not in counted:
            for c in stems:
                r[c] = None
            r["total_against"] = None


QUESTION_ID_COLUMNS = ["election", "county", "question", "how_it_runs", "winner"]
QUESTION_PRECINCT_ID_COLUMNS = [
    "election", "county", "precinct", "congressional", "legislative", "question",
    "how_it_runs", "winner",
]  # fmt: skip
QUESTION_TOTALS = ("Total Votes", "Total Votes Against", "Total 3rd option")


def question_third(election: ResultsElection) -> bool:
    """Whether the election's question files print a third choice (the named layout's 2016-2020
    "3rd option" columns)."""
    return election.layout == "named" and 2016 <= election.year <= 2020


def _question_sides(election: ResultsElection, precinct: bool, label: str) -> list[tuple[str, str]]:
    """(output suffix, the board's column) for one vote type: For, Against and, where printed,
    the third choice ("<label> 3rd option"; the 2016-2018 precinct files' "<label> Votes (3rd
    option)")."""
    sides = [("votes", f"{label} Votes"), ("against", f"{label} Votes Against")]
    if question_third(election):
        third = (
            f"{label} Votes (3rd option)"
            if precinct and election.year <= 2018
            else f"{label} 3rd option"
        )
        sides.append(("third", third))
    return sides


def read_questions(
    data: bytes, election: ResultsElection, county: str, level: str = "county"
) -> ReadFile:
    """One county's ballot-question file as a wide frame: by vote type, For in <stem>_votes,
    Against in <stem>_against and, where the file prints one, the third choice in <stem>_third;
    then total_votes, total_against and total_third.

    level "county" reads the county question file, "precinct" its precinct file (the election's
    precinct vote types, and the precinct id columns). Columns are matched by name: the question
    number is "Question Number" in the named layout ("01"), "QuestionNumber" in a coded county
    file and "Question" in a coded precinct file ("1"), kept as printed, as is How it Runs?
    ("State", "County", "Ed"). The named county file's County column is "00" for a statewide
    question (How it Runs? "State") and the county's code otherwise; every other file prints the
    county's code.

    The third choice (2016-2020) is carried as published, blank as None: only Wicomico's
    question A fills it (2016's three-way question, and 2018's files repeat 2016's third-choice
    figures). A blank For or Against reads as 0 in the named layout (an empty precinct prints a
    blank Against). A printed total must equal the sum of the vote types, the third choice
    included; the county files print them, the precinct files do not (PG20's Total cells are
    blank or missing), so there the totals are the sums.
    """
    precinct = level == "precinct"
    where = f"{election.code} {county} questions" + (" by precinct" if precinct else "")
    raw = _frame(data, where, optional_tail=QUESTION_TOTALS if precinct else ())
    code = next(c for c, n in COUNTY_CODES.items() if n == county)
    named = election.layout == "named"
    third = question_third(election)
    vote_types = election.precinct_vote_types if precinct else election.vote_types
    if named:
        number = "Question Number"
    else:
        number = "Question" if precinct else "QuestionNumber"
    need = (_precinct_id_columns(election) if precinct else ["County"]) + [
        number, "How it Runs?", "Winner",
    ]  # fmt: skip
    for label, _ in vote_types:
        need += [column for _, column in _question_sides(election, precinct, label)]
    totals = [("votes", "Total Votes"), ("against", "Total Votes Against")]
    if third:
        totals.append(("third", "Total 3rd option"))
    if not precinct:
        need += [column for _, column in totals]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        role = "question-precinct" if precinct else "question"
        expected = ResultsFile(election, code, county, "all", role).file_name
        raise ResultsParseError(
            f"{where}: expected {election.code} {election.layout} layout columns in {expected}; "
            f"missing {missing}"
        )
    unknown = sorted(
        c
        for c in raw.columns
        if c.endswith((" Votes", " Votes Against")) and c not in need and not c.startswith("Total")
    )
    if unknown:
        raise ResultsParseError(f"{where}: vote columns not in the registry: {unknown}")
    stray = sorted(
        c for c in raw.columns if "3rd option" in c and c not in need and c != "Total 3rd option"
    )
    if stray or (not third and "Total 3rd option" in raw.columns):
        raise ResultsParseError(
            f"{where}: third-option columns not in the registry: {stray or ['Total 3rd option']}"
        )
    if raw.height == 0:
        raise ResultsParseError(f"{where}: no data rows (header only)")

    rows = []
    for r in raw.iter_rows(named=True):
        who = f"{where}: question {r[number]}"
        out: dict = {"election": election.code, "county": county}
        runs = r["How it Runs?"]
        if precinct or not named:
            if r["County"] != code:
                raise ResultsParseError(
                    f"{who}: County {r['County']!r} is not {county}'s code {code}"
                )
        elif r["County"] != ("00" if runs == "State" else code):
            raise ResultsParseError(
                f"{who}: County {r['County']!r} for a question that runs {runs!r}"
            )
        if precinct:
            out["precinct"] = _precinct_id(r, who)
            # 2010's named files carry no Cong/Legs: "" (need requires them where they exist)
            out["congressional"] = r.get("Congressional", r.get("Cong", ""))
            out["legislative"] = r.get("Legislative", r.get("Legs", ""))
        out |= {
            "question": r[number],
            "how_it_runs": runs,
            "winner": 1 if r["Winner"].upper() in ("Y", "YES") else 0,
        }
        sums: dict[str, int | None] = {"votes": 0, "against": 0, "third": None}
        for label, stem in vote_types:
            for side, column in _question_sides(election, precinct, label):
                v = _int(r[column], who, column)
                if v is None and side != "third":
                    if not named:
                        raise ResultsParseError(f"{who}: blank {column}")
                    v = 0
                out[f"{stem}_{side}"] = v
                if v is not None:
                    sums[side] = (sums[side] or 0) + v
        for side, column in totals:
            printed = _int(r[column], who, column) if column in r else None
            if printed is None and not precinct and side != "third":
                raise ResultsParseError(f"{who}: blank {column}")
            # a county file's blank Total 3rd option must mean no third choice; a precinct
            # file prints no totals (PG20's are blank), so there only a printed one is checked
            checked = printed is not None or (not precinct and side == "third")
            if checked and printed != sums[side]:
                raise ResultsParseError(
                    f"{who}: {column} {printed} != sum of vote types {sums[side]}"
                )
            out[f"total_{side}"] = sums[side]
        rows.append(out)

    ids = QUESTION_PRECINCT_ID_COLUMNS if precinct else QUESTION_ID_COLUMNS
    schema = {c: pl.Utf8 for c in ids if c != "winner"} | {"winner": pl.Int64}
    for label, stem in vote_types:
        for side, _ in _question_sides(election, precinct, label):
            schema[f"{stem}_{side}"] = pl.Int64
    for side, _ in totals:
        schema[f"total_{side}"] = pl.Int64
    return ReadFile(pl.DataFrame(rows, schema=schema), [])


class StatewideCheck(NamedTuple):
    for_rows: int  # FOR rows compared
    against_rows: int  # AGAINST rows compared
    # the STATEWIDE_CHECK_EXCEPTIONS applied, as "office district / candidate (party): counties
    # N, statewide row M" (empty when every compared row matched)
    exceptions: list[str]


def _check_statewide(
    election: ResultsElection, party_file: str, frames: list[pl.DataFrame], breakdown: bytes
) -> StatewideCheck:
    """Compare the county sums with the breakdown file's statewide (County 00) rows.

    The breakdown files (and the archive's State_Congressional_Districts and
    State_Legislative_Districts files, whose per-district columns read "CONG 04" / "LEGS 37B")
    carry the statewide, congressional and legislative contests, not county offices (sheriff,
    school board), and not every office on the ballot in all 24 counties. So an office the
    breakdown carries with a blank district must match candidate by candidate in both
    directions; district contests are compared when present; other offices are not checked.
    Generals compare FOR rows (Vote Type 1) with the counties' total_votes, and, for contests the
    breakdown carries AGAINST rows (Vote Type 2) for (judges' retention questions), those with
    the counties' total_against; the primaries' files have no Vote Type column. The 2010-2018
    districts files carry election-night votes only (their County 00 rows sum to the counties'
    Election Night Votes, never the total), so those elections (check_votes == "election_night")
    compare election_night_votes and election_night_against instead.
    """
    where = f"{election.code} {party_file} breakdown"
    raw = _frame(breakdown, where)
    if election.kind == "general":
        vt = "Vote Type (FOR=1,AGAINST=2)"
        if vt not in raw.columns:
            raise ResultsParseError(f"{where}: no Vote Type column")
        against_raw = raw.filter(pl.col(vt) == "2")
        raw = raw.filter(pl.col(vt) == "1")
    else:
        against_raw = raw.clear()
    state: dict[tuple[str, str, str, str], int] = {}
    state_against: dict[tuple[str, str, str, str], int] = {}
    district_cols = [
        c
        for c in raw.columns
        if c.startswith("Congressional District") or re.fullmatch(r"(CONG|LEGS) \S+", c)
    ]
    if not district_cols:
        raise ResultsParseError(f"{where}: no district columns")
    for r in raw.filter(pl.col("County") == "00").iter_rows(named=True):
        key = (r["Office Name"], r["Office District"], r["Candidate Name"], r["Party"])
        total = sum(_int(r[c], where, c) or 0 for c in district_cols)
        state[key] = state.get(key, 0) + total
    for r in against_raw.filter(pl.col("County") == "00").iter_rows(named=True):
        key = (r["Office Name"], r["Office District"], r["Candidate Name"], r["Party"])
        total = sum(_int(r[c], where, c) or 0 for c in district_cols)
        state_against[key] = state_against.get(key, 0) + total
    return _compare_statewide(election, where, frames, state, state_against)


def _check_statewide_all_state(
    election: ResultsElection, frames: list[pl.DataFrame], all_state: bytes
) -> StatewideCheck:
    """2020 general: compare the county sums with All_State_2020_General's County 00 rows.

    That file carries the statewide totals directly (FOR votes in Total Votes; the judges'
    Against figures sit in their own columns), so it is the simpler source than the congressional
    districts file, whose district columns are in scrambled order. Same rule as the coded check.
    """
    where = f"{election.code} All_State_2020_General"
    raw = _frame(all_state, where)
    need = ["County", "Office Name", "Office District", "Candidate Name", "Party", "Total Votes"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        raise ResultsParseError(f"{where}: missing columns {missing}")
    state: dict[tuple[str, str, str, str], int] = {}
    state_against: dict[tuple[str, str, str, str], int] = {}
    for r in raw.filter(pl.col("County") == "00").iter_rows(named=True):
        key = (r["Office Name"], r["Office District"], r["Candidate Name"], r["Party"])
        state[key] = state.get(key, 0) + (_int(r["Total Votes"], where, "Total Votes") or 0)
        if "Total Votes Against" in raw.columns:
            against = _int(r["Total Votes Against"], where, "Total Votes Against")
            if against is not None:
                state_against[key] = state_against.get(key, 0) + against
    return _compare_statewide(election, where, frames, state, state_against)


def _compare_statewide(
    election: ResultsElection,
    where: str,
    frames: list[pl.DataFrame],
    state: dict[tuple[str, str, str, str], int],
    state_against: dict[tuple[str, str, str, str], int] | None = None,
) -> StatewideCheck:
    """Compare county sums with the statewide rows.

    The county column summed is the election's check_votes: total_votes / total_against, or a
    vote type's <stem>_votes / <stem>_against (election_night for 2010-2018). A FOR row whose
    sum and statewide figure are exactly the pair listed for it in STATEWIDE_CHECK_EXCEPTIONS is
    accepted and reported; any other mismatch fails.
    """
    votes_col, against_col = f"{election.check_votes}_votes", f"{election.check_votes}_against"
    sums: dict[tuple[str, str, str, str], int] = {}
    sums_against: dict[tuple[str, str, str, str], int] = {}
    for frame in frames:
        if votes_col not in frame.columns:
            raise ResultsParseError(f"{where}: no {votes_col} column to check the statewide rows")
        for r in frame.select("office", "district", "candidate", "party", votes_col).iter_rows():
            key = r[:4]
            sums[key] = sums.get(key, 0) + r[4]
        if against_col in frame.columns:
            for r in frame.select(
                "office", "district", "candidate", "party", against_col
            ).iter_rows():
                if r[4] is not None:
                    sums_against[r[:4]] = sums_against.get(r[:4], 0) + r[4]

    # An office is "statewide" when the check file carries it with a blank district. A county
    # office that shares a statewide office's name (Baltimore City's local Comptroller, say)
    # would be a latent collision: it would be summed into the statewide office. No such case in
    # the 2020-2026 files; the statewide comparison would flag it if one appeared.
    statewide = {k[0] for k in state if k[1] == ""}
    compared, compared_against, exceptions = 0, 0, []
    for key, total in sorted(sums.items()):
        if key not in state:
            if key[1] == "" and key[0] in statewide:
                raise ResultsParseError(
                    f"{where}: no statewide row for {key[0]} / {key[2]} (counties sum to {total})"
                )
            continue
        if state[key] != total:
            if STATEWIDE_CHECK_EXCEPTIONS.get((election.code, *key)) != (total, state[key]):
                raise ResultsParseError(
                    f"{where}: {key[0]} {key[1]} / {key[2]}: counties sum to {total}, "
                    f"statewide row is {state[key]}"
                )
            contest = f"{key[0]} {key[1]}".rstrip()
            exceptions.append(
                f"{contest} / {key[2]} ({key[3]}): counties {total}, statewide row {state[key]}"
            )
        compared += 1
    for key, total in sorted((state_against or {}).items()):
        if key not in sums:
            continue  # an AGAINST row for a contest no county file carries is not checked
        if sums_against.get(key, 0) != total:
            raise ResultsParseError(
                f"{where}: {key[0]} {key[1]} / {key[2]}: counties' votes against sum to "
                f"{sums_against.get(key, 0)}, statewide AGAINST row is {total}"
            )
        compared_against += 1
    for key in state:
        if key not in sums and key[1] == "" and key[0] in statewide:
            raise ResultsParseError(
                f"{where}: statewide row for {key[0]} / {key[2]} has no county rows"
            )
    return StatewideCheck(compared, compared_against, exceptions)


# ---- fetch ----------------------------------------------------------------------------------


def _selected(election: str | None) -> list[ResultsElection]:
    if election is None:
        return list(RESULTS_ELECTIONS.values())
    return [RESULTS_ELECTIONS[election]]


def download_status(url: str, timeout: float = 30.0) -> tuple[str, bytes, str]:
    """(status, body, detail): "ok", "unpublished" (the SBE's HTML page or a 404) or "error".

    A network failure, timeout or 5xx is an error, not "not published": for a Non-Partisan file
    the two would otherwise look alike and a transient failure would silently drop a county.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            body = resp.content
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 410):
            return "unpublished", b"", f"HTTP {e.response.status_code}"
        return "error", b"", f"HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        return "error", b"", f"{type(e).__name__}: {e}"
    if body.removeprefix(b"\xef\xbb\xbf").lstrip()[:1] in (b"<", b""):
        return "unpublished", b"", "HTML or empty body"
    return "ok", body, ""


def results_fetch(
    election: str | None = None,
    force: bool = False,
    failures: list[str] | None = None,
    level: str = "all",
) -> list[SourceRecord]:
    """Archive every file of the level (county, precinct, questions or all) not yet archived;
    returns the new manifest rows.

    A file whose bytes equal the newest recorded revision adds no manifest row (a forced
    re-fetch of unchanged files is a no-op). Download errors (as opposed to files the SBE has not
    published) are appended to `failures` when given, and the caller should treat them as fatal.
    """
    fetched: list[SourceRecord] = []
    failures = failures if failures is not None else []
    for e in _selected(election):
        if not e.published:
            logger.info("%s: not published", e.code)
            continue
        for f in files_for(e, level):
            newest = newest_source(MANIFEST_PATH, f.source_id)
            if not force and newest and resolve_archived(newest).exists():
                continue
            status, body, detail = download_status(f.url)
            if status == "error":
                failures.append(f"{e.code}: could not download {f.url} ({detail})")
                logger.warning("%s: download failed: %s (%s)", e.code, f.url, detail)
                continue
            if status == "unpublished":
                if f.party_file == "non" and f.role in ("county", "precinct"):
                    logger.info(
                        "%s: no non-partisan %s file for %s (normal)", e.code, f.role, f.county
                    )
                else:
                    logger.warning("%s: not available: %s", e.code, f.url)
                continue
            same = newest is not None and newest.sha256 == hashlib.sha256(body).hexdigest()
            if not same or not f.local_path.exists():
                f.local_path.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(f.local_path, body)
            if same:
                continue
            record = SourceRecord.create(
                source_id=f.source_id,
                source_url=f.url,
                source_name=SOURCE_NAME,
                local_path=f.local_path,
                document_title=f.document_title,
            )
            append_to_manifest(MANIFEST_PATH, record)
            fetched.append(record)
    return fetched


# ---- parse ----------------------------------------------------------------------------------


PARSE_LEVELS = ("county", "precinct", "all")


def results_parse(
    election: str | None = None,
    force: bool = False,
    data_dir: Path | None = None,
    level: str = "all",
) -> tuple[int, list[str]]:
    """Parse each election's archived files into data/results/<CODE>/: the county files, then
    the precinct files checked against them (level picks one). Each level is all-or-nothing per
    election: any failed check or missing required file writes nothing for that level. Under
    "all", an election whose county level failed has its precinct level skipped (and failed).

    A file whose CSV exists and was parsed from the newest archived revision is skipped unless
    force. Returns (files written, failures), each failure prefixed "<CODE> <level>: ".
    """
    if level not in PARSE_LEVELS:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(PARSE_LEVELS)}")
    data_dir = data_dir or RESULTS_DATA_DIR
    levels = list(PARSERS) if level == "all" else [level]
    written, failures = 0, []
    for e in _selected(election):
        if not e.published:
            logger.info("%s: not published", e.code)
            continue
        failed = set()
        for name in levels:
            if name == "precinct" and not e.precincts:
                continue  # the board posted no precinct files for this election
            if name == "precinct" and "county" in failed:
                # never write precinct files checked against county files that were refused
                failures.append(f"{e.code} precinct: not checked, the county level failed")
                continue
            try:
                written += PARSERS[name](e, force, data_dir)
            except ResultsParseError as err:
                failed.add(name)
                failures.append(f"{e.code} {name}: {err}")
    return written, failures


def _archived(f: ResultsFile) -> Path | None:
    record = newest_source(MANIFEST_PATH, f.source_id)
    if record is None:
        return None
    path = resolve_archived(record)
    return path if path.exists() else None


def _revision_updated(csv_path: Path, record: SourceRecord) -> bool:
    """Whether the CSV is missing or was parsed from different bytes than the newest revision
    (mirrors turnout's helper of the same name)."""
    sidecar = sidecar_path(csv_path)
    if not csv_path.exists() or not sidecar.exists():
        return True
    return ProvenanceSidecar.read(sidecar).sha256_original != record.sha256


def _statewide_check_text(e: ResultsElection, check_file: str, result: StatewideCheck) -> str:
    """The sidecar's statewide_check line for a check that compared rows: what matched, on
    which column, and any listed exception applied."""
    n = result.for_rows + result.against_rows
    text = (
        f"ok: {n} statewide rows ({result.for_rows} for, {result.against_rows} against) "
        f"match {check_file}"
    )
    if e.check_votes == "election_night":
        text += (
            " on election-night votes (the SBE's district files for 2010-2018 carry "
            "election-day votes only)"
        )
    if result.exceptions:
        text += (
            "; the board's own figures disagree on "
            + "; ".join(result.exceptions)
            + " (listed in STATEWIDE_CHECK_EXCEPTIONS, recorded, not corrected)"
        )
    return text


def _read_archived(f: ResultsFile) -> tuple[bytes, SourceRecord] | None:
    """The newest archived revision of a file and its manifest row, or None when it is not
    archived. Refuses bytes that are not the ones the manifest recorded."""
    path = _archived(f)
    if path is None:
        return None
    data = path.read_bytes()
    record = newest_source(MANIFEST_PATH, f.source_id)
    assert record is not None
    if hashlib.sha256(data).hexdigest() != record.sha256:
        raise ResultsParseError(
            f"{f.file_name}: archived bytes do not match the newest manifest sha256 "
            f"({f.source_id}); the archive and manifest are out of step, so re-fetch with "
            f"`skipjack results-fetch --force`"
        )
    return data, record


CountyFrames = dict[str, list[tuple[ResultsFile, SourceRecord, ReadFile]]]


def _read_county_files(
    e: ResultsElection, with_checks: bool = True
) -> tuple[CountyFrames, dict[str, bytes]]:
    """Every archived county file read with its layout's reader, by party file, and the
    statewide check files' bytes (skipped when with_checks is False). A missing non-partisan
    file is normal; any other missing file fails."""
    county_frames: CountyFrames = {}
    breakdowns: dict[str, bytes] = {}
    for f in county_files(e):
        if f.role == "check" and not with_checks:
            continue
        archived = _read_archived(f)
        if archived is None:
            if f.party_file == "non":
                # not every county has non-partisan contests; the breakdown may be absent too
                continue
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
        data, record = archived
        if f.role == "check":
            breakdowns[f.party_file] = data
            continue
        try:
            read = READERS[e.layout](data, e, f.county, f.party_file)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        county_frames.setdefault(f.party_file, []).append((f, record, read))
    return county_frames, breakdowns


def _parse_election(e: ResultsElection, force: bool, data_dir: Path) -> int:
    out_dir = data_dir / "results" / e.code / "county"
    files = county_files(e)
    county_frames, breakdowns = _read_county_files(e)

    compared: dict[str, StatewideCheck] = {}
    has_check = any(f.role == "check" for f in files)
    for pf, items in county_frames.items():
        if not has_check:
            continue  # the 2020 primary has no statewide file; the row totals are all we have
        frames = [rd.frame for _, _, rd in items]
        if pf not in breakdowns:
            if pf == "non":
                continue  # the SBE has not published that check file; recorded as n/a below
            raise ResultsParseError(f"no statewide check file for the {pf} county files")
        if e.check == "all_state":
            compared[pf] = _check_statewide_all_state(e, frames, breakdowns[pf])
        else:
            compared[pf] = _check_statewide(e, pf, frames, breakdowns[pf])

    targets = [
        (out_dir / f"{f.county_code}-{county_slug(f.county)}-{f.party_file}.csv", f, rec, rd)
        for items in county_frames.values()
        for f, rec, rd in items
    ]
    # a CSV is current when its sidecar was written from the newest archived revision
    if not force:
        targets = [t for t in targets if _revision_updated(t[0], t[2])]
    if not targets:
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for out_csv, f, record, (frame, row_total_exceptions) in targets:
        if not has_check:
            statewide = "no statewide file published"
        else:
            check_file = next(
                c.file_name for c in files if c.role == "check" and c.party_file == f.party_file
            )
            result = compared.get(f.party_file)
            if result is None:
                statewide = f"n/a: {check_file} not published"
            elif result.for_rows + result.against_rows:
                statewide = _statewide_check_text(e, check_file, result)
            else:
                statewide = f"n/a: {check_file} carries no statewide rows"
        checks: dict = {"rows": frame.height, "row_totals": "ok", "statewide_check": statewide}
        if row_total_exceptions:
            checks["row_totals"] = (
                f"{len(row_total_exceptions)} rows whose printed Total Votes is not the sum of "
                "the vote types (the board's own arithmetic in this file; the printed total is "
                "kept as total_votes, the rows are listed in row_total_exceptions)"
            )
            checks["row_total_exceptions"] = row_total_exceptions
        sidecar = ProvenanceSidecar.from_source(
            record,
            parser_version=PARSER_VERSION,
            extraction_method="csv",
            checks=checks,
            notes=f"{e.code} {f.county} county results ({f.party_file}), totals checked per row.",
        )
        sidecar.election, sidecar.family, sidecar.geography = e.code, "results", "county"
        # CSV first (temp file + replace), sidecar last: a missing sidecar means "re-parse" in
        # _revision_updated, so a crash between the two is repaired by the next parse
        tmp_csv = out_csv.with_name(out_csv.name + ".tmp")
        frame.write_csv(tmp_csv)
        os.replace(tmp_csv, out_csv)
        sidecar.write(out_csv)
    logger.info("%s: parsed %d files", e.code, len(targets))
    return len(targets)


# ---- precincts ------------------------------------------------------------------------------


class SumCheck(NamedTuple):
    contests: int  # contests (or questions) compared
    rows: int  # rows of the county file compared
    columns: list[str]
    # the listed exceptions applied, as "<row> <column>: precincts N, county file M"
    exceptions: list[str]


def _check_sums(
    where: str,
    keys: list[str],
    contest_keys: int,
    columns: list[str],
    parts: pl.DataFrame,
    whole: pl.DataFrame,
    listed: Callable[[tuple, str], tuple[int, int] | None],
) -> SumCheck:
    """``parts`` (precinct rows) summed by ``keys`` must equal ``whole`` (the county file) on
    every column, blank counting as 0. The first ``contest_keys`` keys name a contest (office and
    district; a question number): a contest in one file and not the other fails. Within a
    contest, a row missing on one side counts as zeros, so a county row of zeros (a write-in line
    nobody used) needs no precinct rows. A difference passes only when ``listed`` returns exactly
    (precinct sum, county figure) for that row and column."""

    def summed(frame: pl.DataFrame) -> dict[tuple, dict[str, int]]:
        out: dict[tuple, dict[str, int]] = {}
        for r in frame.select(*keys, *columns).iter_rows(named=True):
            key = tuple("" if r[k] is None else r[k] for k in keys)  # a missing cell is blank
            sums = out.setdefault(key, dict.fromkeys(columns, 0))
            for c in columns:
                sums[c] += r[c] or 0
        return out

    mine, board = summed(parts), summed(whole)
    contests_mine = {k[:contest_keys] for k in mine}
    contests_board = {k[:contest_keys] for k in board}
    if unmatched := sorted(contests_board - contests_mine):
        raise ResultsParseError(f"{where}: {' '.join(unmatched[0]).strip()} has no precinct rows")
    if unmatched := sorted(contests_mine - contests_board):
        raise ResultsParseError(
            f"{where}: {' '.join(unmatched[0]).strip()} is not in the county file"
        )
    zero = dict.fromkeys(columns, 0)
    exceptions = []
    for key in sorted(set(mine) | set(board)):
        ours, theirs = mine.get(key, zero), board.get(key, zero)
        label = " ".join(key[:contest_keys]).strip()
        if len(key) > contest_keys:
            label += f" / {key[contest_keys]}"
            if len(key) > contest_keys + 1:
                label += f" ({key[-1]})"
        for c in columns:
            if ours[c] == theirs[c]:
                continue
            if listed(key, c) != (ours[c], theirs[c]):
                raise ResultsParseError(
                    f"{where}: {label}: precincts sum to {ours[c]} {c}, the county file says "
                    f"{theirs[c]}"
                )
            exceptions.append(f"{label} {c}: precincts {ours[c]}, county file {theirs[c]}")
    return SumCheck(len(contests_board), whole.height, columns, exceptions)


def _precinct_check_columns(election: ResultsElection) -> list[str]:
    """The columns a precinct file can be checked on: its vote types, and the totals when it
    carries every vote type (from 2020; through 2018 only election-night votes)."""
    against = election.kind == "general"
    columns = []
    for _, stem in election.precinct_vote_types:
        columns.append(f"{stem}_votes")
        if against:
            columns.append(f"{stem}_against")
    if election.precinct_every_vote_type:
        columns += ["total_votes", "total_against"] if against else ["total_votes"]
    return columns


def _check_precinct_county(
    election: ResultsElection,
    county: str,
    party_file: str,
    precincts: pl.DataFrame,
    county_frame: pl.DataFrame,
) -> SumCheck:
    """One county and party file's precinct rows, summed by office, district, candidate and
    party, against its county file (PRECINCT_CHECK_EXCEPTIONS aside)."""
    return _check_sums(
        f"{election.code} {county} {party_file} precincts",
        ["office", "district", "candidate", "party"],
        2,
        _precinct_check_columns(election),
        precincts,
        county_frame,
        lambda key, c: PRECINCT_CHECK_EXCEPTIONS.get((election.code, county, party_file, *key, c)),
    )


def _precinct_csv(out_dir: Path, f: ResultsFile) -> Path:
    return out_dir / f"{f.county_code}-{county_slug(f.county)}-{f.party_file}.csv.gz"


def _gzip_csv(frame: pl.DataFrame) -> bytes:
    """The frame as gzipped CSV. mtime 0 (and no file name in the header) keeps the bytes the
    same from run to run, so an unchanged re-parse leaves git nothing to commit."""
    return gzip.compress(frame.write_csv().encode(), mtime=0)


def _precinct_check_text(e: ResultsElection, county_file: str, check: SumCheck) -> str:
    """The sidecar's county_check line: what was compared, on which columns, and any listed
    exception applied."""
    text = (
        f"ok: the precincts sum to {county_file}'s {check.rows} rows ({check.contests} contests) "
        f"on {', '.join(check.columns)}"
    )
    if not e.precinct_every_vote_type:
        text += " (the board's precinct files for 2010-2018 carry election-night votes only)"
    if check.exceptions:
        text += (
            "; the board's own files disagree on "
            + "; ".join(check.exceptions)
            + " (listed in PRECINCT_CHECK_EXCEPTIONS, recorded, not corrected)"
        )
    return text


def _county_file(f: ResultsFile) -> ResultsFile:
    """The county file a precinct file is checked against."""
    return ResultsFile(f.election, f.county_code, f.county, f.party_file, "county")


def _county_source_changed(csv_path: Path, county: ResultsFile) -> bool:
    """Whether the precinct CSV's sidecar records a county file revision other than the newest
    archived one (or none): a re-posted county file means its precincts are checked again."""
    sidecar = sidecar_path(csv_path)
    if not sidecar.exists():
        return True
    recorded = ProvenanceSidecar.read(sidecar).checks.get("county_source")
    newest = newest_source(MANIFEST_PATH, county.source_id)
    return newest is None or recorded != {"source_id": newest.source_id, "sha256": newest.sha256}


def _parse_precincts(e: ResultsElection, force: bool, data_dir: Path) -> int:
    """Check each county's precinct files against its county files and write them gzipped to
    data/results/<CODE>/precinct/.

    All-or-nothing per election. A CSV written from the newest archived revision is current
    without the archive (the precinct archives are gitignored), so a clone without them parses
    nothing and fails nothing unless forced."""
    files = precinct_files(e)
    out_dir = data_dir / "results" / e.code / "precinct"
    records = {f.source_id: newest_source(MANIFEST_PATH, f.source_id) for f in files}
    if files and all(r is None for r in records.values()):
        raise ResultsParseError(
            "precinct files not fetched (skipjack results-fetch --level precinct)"
        )
    for f in files:
        if records[f.source_id] is None and f.party_file != "non":
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
    present = [f for f in files if records[f.source_id] is not None]
    stale = {
        f.source_id
        for f in present
        if force
        or _revision_updated(_precinct_csv(out_dir, f), records[f.source_id])
        or _county_source_changed(_precinct_csv(out_dir, f), _county_file(f))
    }
    if not stale:
        return 0
    county_frames, _ = _read_county_files(e, with_checks=False)
    by_county = {
        (f.county, f.party_file): (rd.frame, rec)
        for items in county_frames.values()
        for f, rec, rd in items
    }
    checked = []
    for f in present:
        archived = _read_archived(f)
        if archived is None:
            raise ResultsParseError(
                f"{f.file_name} is in the manifest but not archived here; re-fetch with "
                "`skipjack results-fetch --level precinct`"
            )
        data, record = archived
        try:
            read = read_precinct(data, e, f.county, f.party_file)
            if (f.county, f.party_file) not in by_county:
                raise ResultsParseError("no county file to check it against")
            county_frame, county_record = by_county[(f.county, f.party_file)]
            check = _check_precinct_county(e, f.county, f.party_file, read.frame, county_frame)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        checked.append((f, record, read, check, county_record))

    out_dir.mkdir(parents=True, exist_ok=True)
    for f, record, read, check, county_record in checked:
        if f.source_id not in stale:
            continue
        county_file = _county_file(f).file_name
        checks: dict = {
            "rows": read.frame.height,
            "row_totals": "ok"
            if e.precinct_total
            else "computed: the precinct file prints no total, so total_votes is the sum of "
            "its vote types",
            "county_check": _precinct_check_text(e, county_file, check),
            # the county file revision checked against: a re-posted one re-checks this file
            "county_source": {
                "source_id": county_record.source_id,
                "sha256": county_record.sha256,
            },
        }
        if check.exceptions:
            checks["county_check_exceptions"] = check.exceptions
        sidecar = ProvenanceSidecar.from_source(
            record,
            parser_version=PARSER_VERSION,
            extraction_method="csv",
            checks=checks,
            notes=f"{e.code} {f.county} results by precinct ({f.party_file}), summed and "
            "checked against the county file.",
        )
        sidecar.election, sidecar.family, sidecar.geography = e.code, "results", "precinct"
        out = _precinct_csv(out_dir, f)
        _write_atomic(out, _gzip_csv(read.frame))  # CSV first, sidecar last (see _parse_election)
        sidecar.write(out)
    logger.info("%s: parsed %d precinct files", e.code, len(stale))
    return len(stale)


PARSERS = {"county": _parse_election, "precinct": _parse_precincts}
