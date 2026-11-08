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
import hashlib
import io
import logging
import os
import re
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

    @property
    def party_files(self) -> list[str]:
        return ["dem", "rep", "non"] if self.kind == "primary" else ["all"]


def _election(code, year, kind, name, day, live=False, published=True) -> ResultsElection:
    base = (LIVE_BASE if live else ARCHIVE_BASE).format(year=year)
    e = ResultsElection(code, year, kind, name, day, base, published=published)
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
        _election("PP20", 2020, "primary", "2020 presidential primary", "2020-06-02"),
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


@dataclass
class ResultsFile:
    election: ResultsElection
    county_code: str  # "00" for the statewide check files
    county: str
    party_file: str  # "dem" | "rep" | "non" | "all"
    role: str  # "county" | "check"

    @property
    def file_name(self) -> str:
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

    @property
    def url(self) -> str:
        return f"{self.election.base_url}/{self.file_name}"

    @property
    def source_id(self) -> str:
        where = "check" if self.role == "check" else f"county-{self.county_code}"
        return f"sbe-results-{self.election.code}-{where}-{self.party_file}"

    @property
    def local_path(self) -> Path:
        return ARCHIVES_DIR / "sbe" / "results" / self.election.code / self.file_name


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


def _frame(data: bytes, where: str) -> pl.DataFrame:
    """The file as a frame of stripped strings (BOM, quotes, and the stray trailing space)."""
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
    body = [[c.strip() for c in r] for r in rows[1:]]
    if any(len(r) != len(header) for r in body):
        raise ResultsParseError(f"{where}: rows do not all have {len(header)} columns")
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


def _read_wide(
    data: bytes, election: ResultsElection, county: str, party_file: str, blank_is_zero: bool
) -> ReadFile:
    where = f"{election.code} {county} {party_file}"
    raw = _frame(data, where)
    against = election.kind == "general"
    need = ["Office Name", "Office District", "Candidate Name", "Party", "Winner", "Total Votes"]
    for label, _ in election.vote_types:
        need.append(f"{label} Votes")
        if against:
            need.append(f"{label} Votes Against")
    if against:
        need += ["Write-In?", "Total Votes Against"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        code = next((c for c, n in COUNTY_CODES.items() if n == county), "00")
        expected = ResultsFile(election, code, county, party_file, "county").file_name
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
    allowed_total_mismatches = ROW_TOTAL_EXCEPTIONS.get((election.code, county, party_file), 0)
    row_total_exceptions: list[dict] = []
    for r in raw.iter_rows(named=True):
        who = f"{where}: {r['Office Name']} / {r['Candidate Name']}"
        out = {
            "election": election.code,
            "county": county,
            "party_file": party_file,
            "office": r["Office Name"],
            "district": r["Office District"],
            "candidate": r["Candidate Name"],
            "party": r["Party"],
            "winner": 1 if r["Winner"].upper() in ("Y", "YES") else 0,
            "write_in": 1 if against and r["Write-In?"].upper() == "Y" else 0,
        }
        votes_sum, against_sum, against_seen = 0, 0, False
        for label, stem in election.vote_types:
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
        total = _int(r["Total Votes"], who, "Total Votes")
        if total is None and blank_is_zero:
            total = 0
        out["total_votes"] = total
        if total is None:
            raise ResultsParseError(f"{who}: blank Total Votes")
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
        if against:
            total_against = _int(r["Total Votes Against"], who, "Total Votes Against")
            if blank_is_zero and total_against is None and against_seen and against_sum == 0:
                # a lone 0 in one Against cell with no total: no Against figures on this row
                for _, stem in election.vote_types:
                    out[f"{stem}_against"] = None
                against_seen = False
            out["total_against"] = total_against
            if (total_against or 0) != against_sum or (total_against is None) == against_seen:
                raise ResultsParseError(
                    f"{who}: total against {total_against} != sum of vote types against "
                    f"{against_sum if against_seen else None}"
                )
        rows.append(out)
    if len(row_total_exceptions) != allowed_total_mismatches:
        raise ResultsParseError(
            f"{where}: {len(row_total_exceptions)} rows whose total is not the sum of the vote "
            f"types, but {allowed_total_mismatches} are listed for this file"
        )

    schema = {c: pl.Utf8 for c in OUTPUT_ID_COLUMNS[:7]}
    schema |= {"winner": pl.Int64, "write_in": pl.Int64}
    cols = []
    for _, stem in election.vote_types:
        cols.append(f"{stem}_votes")
        if against:
            cols.append(f"{stem}_against")
    cols.append("total_votes")
    if against:
        cols.append("total_against")
    schema |= {c: pl.Int64 for c in cols}
    return ReadFile(pl.DataFrame(rows, schema=schema), row_total_exceptions)


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


def _download(url: str, timeout: float = 30.0) -> tuple[str, bytes, str]:
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
    election: str | None = None, force: bool = False, failures: list[str] | None = None
) -> list[SourceRecord]:
    """Archive every county and check file not yet archived; returns the new manifest rows.

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
        for f in county_files(e):
            newest = newest_source(MANIFEST_PATH, f.source_id)
            if not force and newest and resolve_archived(newest).exists():
                continue
            status, body, detail = _download(f.url)
            if status == "error":
                failures.append(f"{e.code}: could not download {f.url} ({detail})")
                logger.warning("%s: download failed: %s (%s)", e.code, f.url, detail)
                continue
            if status == "unpublished":
                if f.party_file == "non" and f.role == "county":
                    logger.info("%s: no non-partisan file for %s (normal)", e.code, f.county)
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
                document_title=f"{e.code} {f.county} results ({f.party_file})"
                if f.role == "county"
                else f"{e.code} congressional breakdown ({f.party_file})",
            )
            append_to_manifest(MANIFEST_PATH, record)
            fetched.append(record)
    return fetched


# ---- parse ----------------------------------------------------------------------------------


def results_parse(
    election: str | None = None, force: bool = False, data_dir: Path | None = None
) -> tuple[int, list[str]]:
    """Parse each election's archived files into data/results/<CODE>/county/.

    All-or-nothing per election: any failed check or missing required file writes nothing for
    it. A file whose CSV exists and was parsed from the newest archived revision is skipped
    unless force. Returns (files written,
    failures).
    """
    data_dir = data_dir or RESULTS_DATA_DIR
    written, failures = 0, []
    for e in _selected(election):
        if not e.published:
            logger.info("%s: not published", e.code)
            continue
        try:
            n = _parse_election(e, force, data_dir)
        except ResultsParseError as err:
            failures.append(f"{e.code}: {err}")
            continue
        written += n
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
    sidecar_path = csv_path.with_name(csv_path.stem + "_provenance.json")
    if not csv_path.exists() or not sidecar_path.exists():
        return True
    return ProvenanceSidecar.read(sidecar_path).sha256_original != record.sha256


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


def _parse_election(e: ResultsElection, force: bool, data_dir: Path) -> int:
    out_dir = data_dir / "results" / e.code / "county"
    files = county_files(e)
    county_frames: dict[str, list[tuple[ResultsFile, SourceRecord, ReadFile]]] = {}
    breakdowns: dict[str, bytes] = {}
    for f in files:
        path = _archived(f)
        if path is None:
            if f.party_file == "non":
                # not every county has non-partisan contests; the breakdown may be absent too
                continue
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
        data = path.read_bytes()
        record = newest_source(MANIFEST_PATH, f.source_id)
        assert record is not None
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise ResultsParseError(
                f"{f.file_name}: archived bytes do not match the newest manifest sha256 "
                f"({f.source_id}); the archive and manifest are out of step, so re-fetch with "
                f"`skipjack results-fetch --force`"
            )
        if f.role == "check":
            breakdowns[f.party_file] = data
            continue
        try:
            read = READERS[e.layout](data, e, f.county, f.party_file)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        county_frames.setdefault(f.party_file, []).append((f, record, read))

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
