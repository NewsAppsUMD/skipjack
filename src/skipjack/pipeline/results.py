"""County-level election results: fetch the SBE's CSVs, check them, write one CSV per file.

Everything is keyed by the SBE election code (GP26, PG24, ...). A parse is all-or-nothing per
election: every county file must read, every row's total must equal the sum of its vote types,
and the county sums must equal the SBE's statewide rows in the congressional breakdown files.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from skipjack.pipeline import fetch
from skipjack.pipeline.fetch import ARCHIVES_DIR, MANIFEST_PATH, SOURCE_NAME, resolve_archived
from skipjack.pipeline.provenance import (
    ProvenanceSidecar,
    SourceRecord,
    append_to_manifest,
    find_source,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "skipjack 0.2.0"
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
    layout: str = "coded"  # "coded" or "named2020"
    vote_types: list[tuple[str, str]] = field(default_factory=lambda: list(VOTE_TYPES))
    # False until the SBE posts the files (it answers an HTML page meanwhile); flip it later
    published: bool = True

    @property
    def party_files(self) -> list[str]:
        return ["dem", "rep", "non"] if self.kind == "primary" else ["all"]


def _election(code, year, kind, name, day, live=False, published=True) -> ResultsElection:
    base = (LIVE_BASE if live else ARCHIVE_BASE).format(year=year)
    return ResultsElection(code, year, kind, name, day, base, published=published)


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
    files += [
        ResultsFile(election, "00", "State of Maryland", pf, "check") for pf in election.party_files
    ]
    return files


def county_slug(county: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", county.lower().replace("'", "")).strip("-")


# ---- reading --------------------------------------------------------------------------------


def _frame(data: bytes, where: str) -> pl.DataFrame:
    """The file as a frame of stripped strings (BOM, quotes, and the stray trailing space)."""
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ResultsParseError(f"{where}: empty file")
    header = [c.strip() for c in rows[0]]
    body = [[c.strip() for c in r] for r in rows[1:]]
    if any(len(r) != len(header) for r in body):
        raise ResultsParseError(f"{where}: rows do not all have {len(header)} columns")
    return pl.DataFrame(body, schema={c: pl.Utf8 for c in header}, orient="row")


def _int(value: str, where: str, column: str) -> int | None:
    value = value.replace(",", "").strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise ResultsParseError(f"{where}: {column!r} is not a number: {value!r}") from None


def _read_coded(
    data: bytes, election: ResultsElection, county: str, party_file: str
) -> pl.DataFrame:
    """One county file of the 2022-onward layout, as the wide output frame (checks row totals)."""
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
        raise ResultsParseError(f"{where}: missing columns {missing}")

    rows = []
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
            "winner": 1 if r["Winner"].upper() == "Y" else 0,
            "write_in": 1 if against and r["Write-In?"].upper() == "Y" else 0,
        }
        votes_sum, against_sum, against_seen = 0, 0, False
        for label, stem in election.vote_types:
            v = _int(r[f"{label} Votes"], who, f"{label} Votes")
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
        out["total_votes"] = total
        if total != votes_sum:
            raise ResultsParseError(f"{who}: total votes {total} != sum of vote types {votes_sum}")
        if against:
            total_against = _int(r["Total Votes Against"], who, "Total Votes Against")
            out["total_against"] = total_against
            if (total_against or 0) != against_sum or (total_against is None) == against_seen:
                raise ResultsParseError(
                    f"{who}: total against {total_against} != sum of vote types against "
                    f"{against_sum if against_seen else None}"
                )
        rows.append(out)

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
    return pl.DataFrame(rows, schema=schema)


def _check_statewide(
    election: ResultsElection, party_file: str, frames: list[pl.DataFrame], breakdown: bytes
) -> int:
    """Compare the county sums with the breakdown file's statewide (County 00) rows.

    The breakdown files carry the statewide, congressional and legislative contests, not county
    offices (sheriff, school board), and not every office on the ballot in all 24 counties. So an
    office the breakdown carries with a blank district must match candidate by candidate in both
    directions; district contests are compared when present; other offices are not checked.
    Generals use FOR rows only (Vote Type == 1). Returns how many contest rows were compared.
    """
    where = f"{election.code} {party_file} breakdown"
    sums: dict[tuple[str, str, str, str], int] = {}
    for frame in frames:
        for r in frame.select(
            "office", "district", "candidate", "party", "total_votes"
        ).iter_rows():
            key = r[:4]
            sums[key] = sums.get(key, 0) + r[4]

    raw = _frame(breakdown, where)
    if election.kind == "general":
        vt = "Vote Type (FOR=1,AGAINST=2)"
        if vt not in raw.columns:
            raise ResultsParseError(f"{where}: no Vote Type column")
        raw = raw.filter(pl.col(vt) == "1")
    state: dict[tuple[str, str, str, str], int] = {}
    district_cols = [c for c in raw.columns if c.startswith("Congressional District")]
    if not district_cols:
        raise ResultsParseError(f"{where}: no district columns")
    for r in raw.filter(pl.col("County") == "00").iter_rows(named=True):
        key = (r["Office Name"], r["Office District"], r["Candidate Name"], r["Party"])
        total = sum(_int(r[c], where, c) or 0 for c in district_cols)
        state[key] = state.get(key, 0) + total

    statewide = {k[0] for k in state if k[1] == ""}
    compared = 0
    for key, total in sorted(sums.items()):
        if key not in state:
            if key[1] == "" and key[0] in statewide:
                raise ResultsParseError(
                    f"{where}: no statewide row for {key[0]} / {key[2]} (counties sum to {total})"
                )
            continue
        if state[key] != total:
            raise ResultsParseError(
                f"{where}: {key[0]} {key[1]} / {key[2]}: counties sum to {total}, "
                f"statewide row is {state[key]}"
            )
        compared += 1
    for key in state:
        if key not in sums and key[1] == "" and key[0] in statewide:
            raise ResultsParseError(
                f"{where}: statewide row for {key[0]} / {key[2]} has no county rows"
            )
    return compared


# ---- fetch ----------------------------------------------------------------------------------


def _selected(election: str | None) -> list[ResultsElection]:
    if election is None:
        return list(RESULTS_ELECTIONS.values())
    return [RESULTS_ELECTIONS[election]]


def results_fetch(election: str | None = None, force: bool = False) -> list[SourceRecord]:
    """Archive every county and check file not yet archived; returns the new manifest rows."""
    fetched: list[SourceRecord] = []
    for e in _selected(election):
        if not e.published:
            logger.info("%s: not published", e.code)
            continue
        missing = 0
        for f in county_files(e):
            if not force:
                existing = find_source(MANIFEST_PATH, f.source_id)
                if existing and resolve_archived(existing).exists():
                    continue
            if not fetch.fetch_file(f.url, f.local_path, expect_csv=True):
                missing += 1
                if f.party_file == "non" and f.role == "county":
                    logger.info("%s: no non-partisan file for %s (normal)", e.code, f.county)
                else:
                    logger.warning("%s: not available: %s", e.code, f.url)
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
    it. An election whose outputs all exist is skipped unless force. Returns (files written,
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
    record = find_source(MANIFEST_PATH, f.source_id)
    if record is None:
        return None
    path = resolve_archived(record)
    return path if path.exists() else None


def _parse_election(e: ResultsElection, force: bool, data_dir: Path) -> int:
    out_dir = data_dir / "results" / e.code / "county"
    files = county_files(e)
    county_frames: dict[str, list[tuple[ResultsFile, SourceRecord, pl.DataFrame]]] = {}
    breakdowns: dict[str, bytes] = {}
    skipped_non = []
    for f in files:
        path = _archived(f)
        if path is None:
            if f.party_file == "non":
                # not every county has non-partisan contests; the breakdown may be absent too
                skipped_non.append(f.source_id)
                continue
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
        data = path.read_bytes()
        if f.role == "check":
            breakdowns[f.party_file] = data
            continue
        try:
            frame = _read_coded(data, e, f.county, f.party_file)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        record = find_source(MANIFEST_PATH, f.source_id)
        assert record is not None
        county_frames.setdefault(f.party_file, []).append((f, record, frame))

    compared: dict[str, int] = {}
    for pf, items in county_frames.items():
        if pf not in breakdowns:
            raise ResultsParseError(f"no breakdown file for the {pf} county files")
        try:
            compared[pf] = _check_statewide(e, pf, [fr for _, _, fr in items], breakdowns[pf])
        except ResultsParseError as err:
            raise ResultsParseError(str(err)) from None

    targets = [
        (out_dir / f"{f.county_code}-{county_slug(f.county)}-{f.party_file}.csv", f, rec, fr)
        for items in county_frames.values()
        for f, rec, fr in items
    ]
    if not force and all(t[0].exists() for t in targets):
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for out_csv, f, record, frame in targets:
        sidecar = ProvenanceSidecar.from_source(
            record,
            parser_version=PARSER_VERSION,
            extraction_method="csv",
            checks={
                "rows": frame.height,
                "row_totals": "ok",
                "statewide_check": f"ok: {compared[f.party_file]} statewide contest rows "
                f"match {f.election.code}_CongressionalBreakDown",
            },
            notes=f"{e.code} {f.county} county results ({f.party_file}), totals checked per row.",
        )
        sidecar.election, sidecar.family, sidecar.geography = e.code, "results", "county"
        sidecar.write(out_csv)  # provenance first, so a crash leaves no bare CSV
        frame.write_csv(out_csv)
    logger.info("%s: parsed %d files", e.code, len(targets))
    return len(targets)
