"""Aggregate a statewide Maryland voter file into small, committed JSON files.

The raw file is row-level personal data. It stays outside the repository; only
aggregates are written, with small cells suppressed. Output layout::

    data/voter_file/<snapshot_date>/
        snapshot.json                 metadata, election list, limitations
        snapshot_provenance.json      checks and per-part hashes
        <metric>.json                 one file per metric, long "rows" format

Each metric file looks like::

    {"metric": "turnout_by_election", "snapshot_date": "2026-08-12",
     "dims": ["county", "party_group", "election"], "values": ["count", "eligible"],
     "suppress_below": 10, "rows": [{"county": "Allegany", ..., "count": 8123}, ...]}

A ``count`` of null means the cell was suppressed (1 to ``suppress_below - 1``).
Statewide figures use ``county = "Maryland"`` and are computed before suppression.

The file is a tab-delimited export split into parts; only the first part has a
header row. The header names the vote-history columns (one per election, e.g.
``11/05/2024-PG``), so later snapshots with more elections need no code change.

An older export may be a single file with no readme. Point ``--path`` at it and give
``--snapshot-date``; there is then no record count to check, and any election column dated
after the snapshot (the export is made before the election) is ignored.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path

import polars as pl

from skipjack.pipeline.clean import PARTY_GROUP_ORDER, party_group_expr
from skipjack.pipeline.fetch import MANIFEST_PATH
from skipjack.pipeline.provenance import (
    ProvenanceSidecar,
    SourceRecord,
    append_to_manifest,
    compute_sha256,
    find_source,
)
from skipjack.pipeline.vrar import COUNTY_NORMALIZE

logger = logging.getLogger(__name__)

SOURCE_NAME = "Maryland State Board of Elections"
SOURCE_URL = "https://elections.maryland.gov/voter_registration/data.html"
PARSER_VERSION = "skipjack 0.3.0"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
ARCHIVES_DIR = PROJECT_ROOT / "archives"
CENSUS_NAME = "U.S. Census Bureau"
CENSUS_URL = "https://api.census.gov/data/2020/dec/pl"

# wru's five categories, by the column each is predicted in. "Other" holds every voter the
# model does not place in one of the four: multiracial, Native American, Pacific Islander.
RACES = {
    "pred_whi": "white",
    "pred_bla": "black",
    "pred_his": "hispanic",
    "pred_asi": "asian",
    "pred_oth": "other",
}
MAX_UNMATCHED_RACE = 0.005  # share of voters without a prediction before the run stops

STATEWIDE = "Maryland"
ALL_PARTIES = "ALL"
COUNTIES = sorted(set(COUNTY_NORMALIZE.values()))
# The voter file spells some counties differently from the monthly reports.
COUNTY_FIXES = {"Saint Mary's": "St. Mary's"}
STATUSES = ["A", "I"]

# Census county FIPS codes (the three digits after the state's 24).
COUNTY_FIPS = {
    "Allegany": "001", "Anne Arundel": "003", "Baltimore County": "005", "Calvert": "009",
    "Caroline": "011", "Carroll": "013", "Cecil": "015", "Charles": "017", "Dorchester": "019",
    "Frederick": "021", "Garrett": "023", "Harford": "025", "Howard": "027", "Kent": "029",
    "Montgomery": "031", "Prince George's": "033", "Queen Anne's": "035", "St. Mary's": "037",
    "Somerset": "039", "Talbot": "041", "Washington": "043", "Wicomico": "045",
    "Worcester": "047", "Baltimore City": "510",
}  # fmt: skip

FIXED_COLUMNS = [
    "VTR_ID", "LastName", "FirstName", "MiddleName", "Suffix", "HouseNumber", "HouseSuffix",
    "StreetPreDirection", "StreetName", "StreetType", "StreetPostDirection", "UnitType",
    "UnitNumber", "NonStandardAddress", "ResidentialCity", "ResidentialState", "ResidentialZip",
    "ResidentialZipPlus", "MAILINGADDRESS", "MAILINGCITY", "MAILINGSTATE", "MAILINGZIP",
    "MAILINGZIPPLUS", "StatusCode", "Party", "Gender", "Congressional", "Legislative",
    "Councilmanic", "Commissioner", "Ward", "Municipal", "School", "Precinct", "Split",
    "BirthDate", "CountyRegistrationDate", "StateRegistrationDate",
]  # fmt: skip
ELECTION_COLUMN_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})-([A-Z]{2})$")
ELECTION_NAMES = {
    "GP": "Gubernatorial Primary",
    "GG": "Gubernatorial General",
    "PP": "Presidential Primary",
    "PG": "Presidential General",
}

MAX_AGE = 115
AGE_BANDS = [
    ("Under 18", 0, 17), ("18-24", 18, 24), ("25-34", 25, 34), ("35-44", 35, 44),
    ("45-54", 45, 54), ("55-64", 55, 64), ("65-74", 65, 74), ("75+", 75, MAX_AGE),
]  # fmt: skip
# Age today, in the bands the young voters page uses.
AGE_DETAIL_BANDS = [
    ("Under 18", 0, 17), ("18-22", 18, 22), ("23-29", 23, 29), ("30-44", 30, 44),
    ("45-64", 45, 64), ("65+", 65, MAX_AGE),
]  # fmt: skip
# Age when a first-time registrant signed up.
NEW_REGISTRANT_BANDS = [("18-22", 18, 22), ("23-29", 23, 29), ("30+", 30, MAX_AGE)]
NEW_REGISTRANT_FIRST_YEAR = 2010
GENERATIONS = [
    ("Gen Z", 1997, 2100), ("Millennial", 1981, 1996), ("Gen X", 1965, 1980),
    ("Baby Boomer", 1946, 1964), ("Silent/Greatest", 1900, 1945),
]  # fmt: skip
VOTER_TYPES = [
    "every_election", "primary_and_general", "primary_only", "general_only", "none",
]  # fmt: skip

MAX_REJECTED = 50  # malformed fragments tolerated before the load is refused

LIMITATIONS = [
    "Participation figures describe the people on the voter list today, not the electorate on "
    "election day. Voters who have since moved, died or been removed are missing from the file, "
    "both as voters and as registered voters. Past elections are therefore undercounted, the "
    "older the election the more is missing, and these are not official turnout figures. The "
    "State Board of Elections publishes official turnout for each election.",
    "A voter counts as eligible for an election if they were registered by election day and "
    "turned 18 by it (by that year's general election, for a primary), or if the file records "
    "a vote for it. Age bands use age today.",
    "Party switching, removals, and net registration change cannot be measured from a single "
    "snapshot. They need two snapshots compared voter by voter.",
    "The file records whether someone voted, not how (early, mail, or election day).",
    "Cells with 1 to 9 voters are hidden. Hidden cells are not rebalanced, so rows may not sum "
    "to their totals.",
    "Active counts in the file are about 1% below the State Board's monthly registration report "
    "for the same month. The monthly report remains the official count; shares and participation "
    "rates here are computed within the file.",
    "Registration cohorts show survivors: current voters by the year they first registered "
    "in Maryland, not everyone who ever registered.",
    "New registrants are voters whose county and state registration dates match, which marks a "
    "first registration in Maryland, and who were 18 or older when they registered. Voters who "
    "pre-registered at 16 or 17 are left out, because the youngest of them are not yet in the "
    "file. Party is the voter's party today, not the party they chose when they registered.",
]


class VoterFileError(ValueError):
    """The voter file could not be read or does not match its readme."""


# --------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------


def general_election_day(year: int) -> date:
    """Maryland's general election: the Tuesday after the first Monday in November."""
    first = date(year, 11, 1)
    first_monday = first.toordinal() + (-first.weekday()) % 7
    return date.fromordinal(first_monday + 1)


@dataclass
class Election:
    column: str  # as named in the file header, e.g. "11/05/2024-PG"
    key: str  # "2024-PG"
    date: str  # ISO "2024-11-05"
    kind: str  # "primary" or "general"
    label: str  # "2024 Presidential General"
    short: str  # "2024 General"
    adult_by: str  # a voter must be 18 by this date: election day, or that year's general

    @property
    def voted(self) -> str:
        return "voted_" + self.key.replace("-", "_")

    @property
    def eligible(self) -> str:
        return "elig_" + self.key.replace("-", "_")


def parse_election(column: str) -> Election:
    m = ELECTION_COLUMN_RE.match(column)
    if not m:
        raise VoterFileError(f"Unrecognized election column: {column!r}")
    month, day, year, suffix = m.groups()
    kind = "primary" if suffix.endswith("P") else "general"
    # Marylanders who turn 18 by the general election may vote in that year's primary.
    adult_by = f"{year}-{month}-{day}" if kind == "general" else general_election_day(int(year))
    return Election(
        column=column,
        key=f"{year}-{suffix}",
        date=f"{year}-{month}-{day}",
        kind=kind,
        label=f"{year} {ELECTION_NAMES.get(suffix, suffix)}",
        short=f"{year} {kind.title()}",
        adult_by=str(adult_by),
    )


@dataclass
class Readme:
    total_records: int | None  # None for a single file that came without a readme
    snapshot_date: str  # ISO


def read_readme(directory: Path) -> Readme:
    candidates = sorted(p for p in directory.glob("*.txt") if "readme" in p.name.lower())
    if not candidates:
        raise VoterFileError(f"No readme found in {directory}")
    text = candidates[0].read_text(errors="replace")
    total = re.search(r"Total Records\s*:\s*([\d,]+)", text)
    when = re.search(r"Date\s*:\s*(\d{2})/(\d{2})/(\d{4})", text)
    if not total or not when:
        raise VoterFileError(f"{candidates[0].name}: missing Total Records or Date")
    month, day, year = when.groups()
    return Readme(int(total.group(1).replace(",", "")), f"{year}-{month}-{day}")


@dataclass
class Part:
    path: Path
    number: int
    sha256: str
    bytes: int
    has_header: bool
    rows: int = 0


def _part_number(path: Path) -> int:
    m = re.search(r"Part_(\d+)", path.name)
    return int(m.group(1)) if m else 10**6


def discover_parts(directory: Path) -> tuple[list[Part], list[Path]]:
    """Return (unique parts in order, byte-identical duplicates that were skipped).

    ``directory`` may also be a single voter file, which becomes part 1.
    """
    if directory.is_file():
        with open(directory, "rb") as f:
            header = f.read(7) == b"VTR_ID\t"
        return [Part(directory, 1, compute_sha256(directory), directory.stat().st_size, header)], []
    files = [p for p in directory.glob("*Part_*.txt") if "readme" not in p.name.lower()]

    files.sort(key=lambda p: (_part_number(p), p.name))
    seen: dict[str, Part] = {}
    duplicates: list[Path] = []
    for p in files:
        digest = compute_sha256(p)
        if digest in seen:
            duplicates.append(p)
            continue
        with open(p, "rb") as f:
            header = f.read(7) == b"VTR_ID\t"
        seen[digest] = Part(p, _part_number(p), digest, p.stat().st_size, header)
    if not seen:
        raise VoterFileError(f"No *Part_*.txt files in {directory}")
    return list(seen.values()), duplicates


def columns_from_header(parts: list[Part]) -> tuple[list[str], list[Election]]:
    """Column names (including a trailing placeholder if rows end in a tab) and elections."""
    header_part = next((p for p in parts if p.has_header), None)
    if header_part is None:
        raise VoterFileError("None of the parts has a header row")
    with open(header_part.path, encoding="utf-8", errors="replace") as f:
        line = f.readline().rstrip("\r\n")
    fields = line.split("\t")
    trailing = fields[-1] == ""
    if trailing:
        fields.pop()
    if fields[: len(FIXED_COLUMNS)] != FIXED_COLUMNS or fields[-1] != "County":
        raise VoterFileError("Header does not match the expected voter file layout")
    elections = sorted(
        (parse_election(c) for c in fields[len(FIXED_COLUMNS) : -1]), key=lambda e: e.date
    )
    if not elections:
        raise VoterFileError("Header lists no election columns")
    return fields + (["_trailing"] if trailing else []), elections


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def _band_expr(value: pl.Expr, bands: list[tuple[str, int, int]]) -> pl.Expr:
    expr = pl.lit("Unknown")
    for label, lo, hi in reversed(bands):
        expr = pl.when(value.is_between(lo, hi)).then(pl.lit(label)).otherwise(expr)
    return expr


def scan_part(part: Part, names: list[str], elections: list[Election], snapshot: date):
    """Lazily scan one part and derive the compact columns used by the aggregates."""
    lf = pl.scan_csv(
        part.path,
        separator="\t",
        has_header=False,
        schema={n: pl.String for n in names},
        quote_char=None,
        truncate_ragged_lines=True,
        ignore_errors=True,
        encoding="utf8-lossy",
        skip_rows=1 if part.has_header else 0,
    )
    birth = pl.col("BirthDate").str.to_date("%m/%d/%Y", strict=False)
    registered = pl.col("StateRegistrationDate").str.to_date("%m/%d/%Y", strict=False)
    had_birthday = (birth.dt.month() < snapshot.month) | (
        (birth.dt.month() == snapshot.month) & (birth.dt.day() <= snapshot.day)
    )
    age = snapshot.year - birth.dt.year() - pl.when(had_birthday).then(0).otherwise(1)
    age = pl.when(age.is_between(0, MAX_AGE)).then(age)  # implausible ages become null

    county_registered = pl.col("CountyRegistrationDate").str.to_date("%m/%d/%Y", strict=False)

    def completed_years(on: pl.Expr) -> pl.Expr:
        """Whole years between the birth date and ``on``."""
        before_birthday = (on.dt.month() < birth.dt.month()) | (
            (on.dt.month() == birth.dt.month()) & (on.dt.day() < birth.dt.day())
        )
        return on.dt.year() - birth.dt.year() - pl.when(before_birthday).then(1).otherwise(0)

    # A first registration in Maryland: the county and state dates match. Voters who moved
    # between counties or re-registered have a county date later than the state date.
    age_at_registration = completed_years(registered)
    first_time = (
        (county_registered == registered)
        & birth.is_not_null()
        & registered.dt.year().is_between(NEW_REGISTRANT_FIRST_YEAR, snapshot.year)
        & (registered <= snapshot)
        & (age_at_registration >= NEW_REGISTRANT_BANDS[0][1])
    ).fill_null(False)
    # Registered on or before the snapshot's month and day, in any year: lets each year be
    # compared with the same stretch of the others, e.g. January 1 to August 12.
    to_date = (registered.dt.month() < snapshot.month) | (
        (registered.dt.month() == snapshot.month) & (registered.dt.day() <= snapshot.day)
    )
    # Day of a non-leap year, so a week means the same thing in leap years.
    day_of_year = registered.dt.ordinal_day() - pl.when(
        registered.dt.is_leap_year() & (registered.dt.month() > 2)
    ).then(1).otherwise(0)  # fmt: skip

    def district(column: str) -> pl.Expr:
        value = pl.col(column).fill_null("").str.strip_chars()
        return pl.when(value == "").then(pl.lit("Unknown")).otherwise(value).alias(column.lower())

    voted = {e.key: pl.col(e.column).fill_null("").str.strip_chars() != "" for e in elections}

    def eligible(e: Election) -> pl.Expr:
        """Registered by election day and old enough, or the file records a vote."""
        adult = date.fromisoformat(e.adult_by)
        old_enough = birth.is_null() | (birth <= date(adult.year - 18, adult.month, adult.day))
        registered_in_time = registered.is_null() | (registered <= date.fromisoformat(e.date))
        return (registered_in_time & old_enough) | voted[e.key]

    return lf.select(
        pl.col("VTR_ID").alias("vtr_id"),
        pl.col("County").replace(COUNTY_FIXES).alias("county"),
        pl.col("StatusCode").alias("status"),
        pl.col("Party").alias("party"),
        party_group_expr("Party").alias("party_group"),
        pl.col("Gender").fill_null("").str.strip_chars().replace("", "Unknown").alias("gender"),
        district("Congressional"),
        district("Legislative"),
        _band_expr(age, AGE_BANDS).alias("age_band"),
        _band_expr(birth.dt.year(), GENERATIONS).alias("generation"),
        _band_expr(age, AGE_DETAIL_BANDS).alias("age_detail"),
        pl.when(first_time)
        .then(_band_expr(age_at_registration, NEW_REGISTRANT_BANDS))
        .alias("new_age"),
        pl.when(first_time)
        .then(pl.when(to_date).then(pl.lit("to_date")).otherwise(pl.lit("rest")))
        .alias("reg_window"),
        pl.when(first_time).then(((day_of_year - 1) // 7 + 1).cast(pl.Int64)).alias("reg_week"),
        pl.when(registered.dt.year().is_between(1900, snapshot.year))
        .then(registered.dt.year())
        .alias("reg_year"),
        registered.alias("reg_date"),
        *[voted[e.key].alias(e.voted) for e in elections],
        *[eligible(e).alias(e.eligible) for e in elections],
    )


def read_race_predictions(directory: Path) -> tuple[pl.DataFrame, dict]:
    """Read ``predictions.csv`` and ``run.json`` from a ``predict_race.R`` run."""
    directory = Path(directory).expanduser().resolve()
    predictions, run_json = directory / "predictions.csv", directory / "run.json"
    for p in (predictions, run_json):
        if not p.exists():
            raise VoterFileError(f"No {p.name} in {directory}; run scripts/race/predict_race.R")
    schema = {"vtr_id": pl.String, "geo_level": pl.String, **{c: pl.Float64 for c in RACES}}
    df = pl.read_csv(predictions, schema_overrides=schema).select(["vtr_id", *RACES, "geo_level"])
    if df["vtr_id"].n_unique() != df.height:
        raise VoterFileError("predictions.csv has duplicate vtr_id values")
    total = df.select(pl.sum_horizontal(list(RACES))).to_series()
    if df.select(pl.any_horizontal(pl.col(c).is_null() for c in RACES)).to_series().any():
        raise VoterFileError("predictions.csv has blank probabilities")
    if (total - 1).abs().max() > 1e-4:
        raise VoterFileError("predictions.csv has rows whose probabilities do not sum to 1")
    return df, {
        "run": json.loads(run_json.read_text()),
        "predictions_sha256": compute_sha256(predictions),
    }


def load_voterfile(
    parts: list[Part],
    names: list[str],
    elections: list[Election],
    readme: Readme,
    race: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict]:
    snapshot = date.fromisoformat(readme.snapshot_date)
    frames = []
    for part in parts:
        df = scan_part(part, names, elections, snapshot).collect()
        part.rows = df.height
        logger.info("Part %d: %d rows", part.number, df.height)
        frames.append(df)
    df = pl.concat(frames)
    rows_read = df.height

    valid = (
        pl.col("county").is_in(COUNTIES)
        & pl.col("status").is_in(STATUSES)
        & pl.col("party").is_not_null()
        & (pl.col("party") != "")
        & pl.col("vtr_id").str.contains(r"^\d+$")
    ).fill_null(False)
    rejected = df.filter(~valid)
    df = df.filter(valid)
    for vtr_id in rejected["vtr_id"].head(5).to_list():
        logger.warning("Rejected malformed row (first field %r)", (vtr_id or "")[:20])
    unrecognized = (
        rejected.filter(pl.col("county").is_not_null() & (pl.col("county") != ""))["county"]
        .value_counts()
        .head(5)
        .to_dicts()
    )

    duplicate_ids = df.height - df["vtr_id"].n_unique()
    if duplicate_ids:
        logger.warning("%d duplicate voter IDs; keeping the first of each", duplicate_ids)
        df = df.unique(subset="vtr_id", keep="first", maintain_order=True)
    race_unmatched = None
    if race is not None:
        # Predictions are joined by ID and only the probabilities are kept; the ID is dropped
        # right after, like every other identifier.
        df = df.join(race.drop("geo_level"), on="vtr_id", how="left")
        race_unmatched = df["pred_whi"].null_count() / max(df.height, 1)
        if race_unmatched > MAX_UNMATCHED_RACE:
            raise VoterFileError(
                f"{race_unmatched:.2%} of voters have no race prediction; the predictions do "
                "not come from this voter file (rebuild with skipjack race-input)"
            )
    df = df.drop("vtr_id")

    # Categoricals are cast after concatenation so the parts share one set of categories.
    df = df.with_columns(
        pl.col(
            ["county", "status", "party", "party_group", "gender", "congressional", "legislative"]
        ).cast(pl.Categorical)
    )
    checks = {
        "readme_total_records": readme.total_records,
        "rows_read": rows_read,
        "rows_valid": df.height,
        "rows_rejected": rejected.height,
        "duplicate_voter_ids": duplicate_ids,
        "unrecognized_counties": unrecognized,
        "status_counts": dict(df["status"].cast(pl.String).value_counts().iter_rows()),
    }
    if race_unmatched is not None:
        checks["race_unmatched_share"] = race_unmatched
    checks["latest_registration_date"] = (
        str(df["reg_date"].max()) if df["reg_date"].null_count() < df.height else None
    )
    # Without a readme there is no record count to compare, only the malformed-row limit.
    checks["reconciled"] = rejected.height <= MAX_REJECTED and (
        readme.total_records is None or abs(df.height - readme.total_records) <= MAX_REJECTED
    )
    return df, checks


# --------------------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------------------


@dataclass
class Metric:
    name: str
    dims: list[str]
    values: list[str]
    df: pl.DataFrame = field(repr=False)


NUMERIC_DIMS = {"reg_year", "eligible_elections", "reg_week"}


def _strings(df: pl.DataFrame, dims: list[str]) -> pl.DataFrame:
    """Cast categorical dimensions to strings; numeric dimensions stay numbers."""
    return df.with_columns(
        [pl.col(d).cast(pl.String) for d in dims if d in df.columns and d not in NUMERIC_DIMS]
    )


def with_statewide(df: pl.DataFrame, dims: list[str], values: list[str]) -> pl.DataFrame:
    """Append county = 'Maryland' rows that sum the county rows (before suppression)."""
    others = [d for d in dims if d != "county"]
    total = (
        df.group_by(others)
        .agg([pl.col(v).sum() for v in values])
        .with_columns(pl.lit(STATEWIDE).alias("county"))
    )
    return pl.concat([df, total.select(df.columns)])


def with_all_parties(df: pl.DataFrame, dims: list[str], values: list[str]) -> pl.DataFrame:
    """Append party_group = 'ALL' rows that sum the party groups (before suppression)."""
    others = [d for d in dims if d != "party_group"]
    total = (
        df.group_by(others)
        .agg([pl.col(v).sum() for v in values])
        .with_columns(pl.lit(ALL_PARTIES).alias("party_group"))
    )
    return pl.concat([df, total.select(df.columns)])


def count_by(df: pl.DataFrame, dims: list[str]) -> pl.DataFrame:
    return _strings(df.group_by(dims).agg(pl.len().alias("count")), dims)


def election_long(df: pl.DataFrame, elections: list[Election], by: list[str]) -> pl.DataFrame:
    """Voters and eligible registrants per election, grouped by ``by``."""
    return pl.concat(
        [
            _strings(
                df.group_by(by).agg(
                    pl.col(e.voted).sum().cast(pl.Int64).alias("count"),
                    pl.col(e.eligible).sum().cast(pl.Int64).alias("eligible"),
                ),
                by,
            ).with_columns(pl.lit(e.key).alias("election"))
            for e in elections
        ]
    )


def _count_cols(df: pl.DataFrame, dims: list[str], values: list[str]) -> pl.DataFrame:
    return df.select([*dims, *values]).with_columns([pl.col(v).cast(pl.Int64) for v in values])


def build_metrics(df: pl.DataFrame, elections: list[Election]) -> list[Metric]:
    metrics: list[Metric] = []

    def add(name: str, dims: list[str], values: list[str], frame: pl.DataFrame, county=True):
        if "party_group" in dims:
            frame = with_all_parties(frame, dims, values)
        if county and "county" in dims:
            frame = with_statewide(frame, dims, values)
        metrics.append(Metric(name, dims, values, _count_cols(frame, dims, values)))

    # Active and inactive registrants.
    dims = ["county", "party_group", "status"]
    add("party_by_county", dims, ["count"], count_by(df, dims))

    # Participation in each election among registrants.
    dims = ["county", "party_group", "election"]
    add("turnout_by_election", dims, ["count", "eligible"],
        election_long(df, elections, ["county", "party_group"]))  # fmt: skip

    dims = ["county", "age_band", "election"]
    add("turnout_by_age", dims, ["count", "eligible"],
        election_long(df, elections, ["county", "age_band"]))  # fmt: skip

    dims = ["party_group", "age_band", "election"]
    add("turnout_by_age_party", dims, ["count", "eligible"],
        election_long(df, elections, ["party_group", "age_band"]))  # fmt: skip

    # Voter types, judged only over the elections each voter was eligible for.
    def total(cols: list[str]) -> pl.Expr:
        return pl.sum_horizontal([pl.col(c).cast(pl.UInt8) for c in cols] or [pl.lit(0)])

    primaries = [e for e in elections if e.kind == "primary"]
    generals = [e for e in elections if e.kind == "general"]
    n_elig = total([e.eligible for e in elections])
    n_voted = total([e.voted for e in elections])
    n_primary = total([e.voted for e in primaries])
    n_general = total([e.voted for e in generals])
    voter_type = (
        pl.when(n_voted == 0).then(pl.lit("none"))
        .when(n_voted == n_elig).then(pl.lit("every_election"))
        .when((n_primary > 0) & (n_general > 0)).then(pl.lit("primary_and_general"))
        .when(n_primary > 0).then(pl.lit("primary_only"))
        .otherwise(pl.lit("general_only"))
    )  # fmt: skip
    typed = df.select(
        "county", "party_group",
        n_elig.cast(pl.Int64).alias("eligible_elections"),
        voter_type.alias("voter_type"),
    )  # fmt: skip
    dims = ["county", "party_group", "eligible_elections", "voter_type"]
    add("voter_types", dims, ["count"], _strings(count_by(typed, dims), dims))

    # Districts.
    district_frames, district_turnout = [], []
    for kind, column in (("congressional", "congressional"), ("legislative", "legislative")):
        g = count_by(df, [column, "party_group", "status"]).rename({column: "district"})
        district_frames.append(g.with_columns(pl.lit(kind).alias("district_type")))
        t = election_long(df, elections, [column]).rename({column: "district"})
        district_turnout.append(t.with_columns(pl.lit(kind).alias("district_type")))
    dims = ["district_type", "district", "party_group", "status"]
    add("districts", dims, ["count"], pl.concat(district_frames).select(dims + ["count"]))
    dims = ["district_type", "district", "election"]
    add("district_turnout", dims, ["count", "eligible"],
        pl.concat(district_turnout).select(dims + ["count", "eligible"]))  # fmt: skip

    # Demographics and registration history.
    for name, extra in (
        ("age_by_party", "age_band"),
        ("generation_by_party", "generation"),
        ("gender", "gender"),
    ):
        dims = ["county", "party_group", extra]
        add(name, dims, ["count"], count_by(df, dims))

    dims = ["county", "party_group", "reg_year"]
    cohorts = df.filter(pl.col("reg_year").is_not_null())
    add("registration_cohorts", dims, ["count"], count_by(cohorts, dims))

    dims = ["county", "party"]
    minor = df.filter(pl.col("party_group") == "OTH")
    add("minor_parties", dims, ["count"], count_by(minor, dims))

    # First-time registrants (age 18 or older when they registered), by the year they
    # registered and whether that was on or before the snapshot's month and day.
    new = df.filter(pl.col("new_age").is_not_null()).with_columns(
        pl.col("new_age").alias("age_band")
    )
    dims = ["county", "party_group", "age_band", "reg_year", "reg_window"]
    add("new_registrants", dims, ["count"], count_by(new, dims))
    # The same, week by week, statewide: enough to draw a running total.
    dims = ["reg_year", "reg_week", "age_band"]
    add("new_registrant_weeks", dims, ["count"], count_by(new, dims))

    # Everyone registered today, by age in the bands the young voters page uses.
    detail = df.with_columns(pl.col("age_detail").alias("age_band"))
    dims = ["county", "party_group", "age_band"]
    add("age_detail_by_party", dims, ["count"], count_by(detail, dims))

    # Registered since the previous general election, and how many voted in the latest one.
    latest = elections[-1]
    previous = [e for e in generals if e.date < latest.date]
    if previous:
        since = date.fromisoformat(previous[-1].date)
        new = df.filter(pl.col("reg_date") > since).filter(pl.col(latest.eligible))
        dims = ["county", "party_group"]
        frame = _strings(
            new.group_by(dims).agg(
                pl.col(latest.voted).sum().cast(pl.Int64).alias("count"),
                pl.len().cast(pl.Int64).alias("eligible"),
            ),
            dims,
        )
        add("new_voters_voted", dims, ["count", "eligible"], frame)

    if "pred_whi" in df.columns:
        metrics.extend(build_race_metrics(df))

    return metrics


def race_long(df: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Expected voters of each estimated race per group: the sum of their probabilities.

    ``count`` stays fractional here so statewide and all-party rows add up before rounding;
    ``voters`` is the number of registrants in the group, the denominator.
    """
    g = df.filter(pl.col("pred_whi").is_not_null()).group_by(by).agg(
        *[pl.col(c).sum() for c in RACES], pl.len().cast(pl.Int64).alias("voters")
    )  # fmt: skip
    long = pl.concat(
        [
            g.select(*by, pl.lit(race).alias("race"), pl.col(col).alias("count"), "voters")
            for col, race in RACES.items()
        ]
    )
    return _strings(long, by)


def build_race_metrics(df: pl.DataFrame) -> list[Metric]:
    """Estimated race (see scripts/race/predict_race.R) by party, age and district."""
    out: list[Metric] = []
    values = ["count", "voters"]

    def add(name: str, dims: list[str], frame: pl.DataFrame):
        if "party_group" in dims:
            frame = with_all_parties(frame, dims, values)
        if "county" in dims:
            frame = with_statewide(frame, dims, values)
        frame = frame.with_columns(pl.col("count").round().cast(pl.Int64))
        out.append(Metric(name, dims, values, _count_cols(frame, dims, values)))

    dims = ["county", "party_group", "race"]
    add("race_by_party", dims, race_long(df, ["county", "party_group"]).select(dims + values))

    dims = ["county", "party_group", "age_band", "race"]
    add("race_by_age", dims, race_long(df, ["county", "party_group", "age_band"]).select(dims + values))  # fmt: skip

    district_frames = []
    for kind in ("congressional", "legislative"):
        g = race_long(df, [kind]).rename({kind: "district"})
        district_frames.append(g.with_columns(pl.lit(kind).alias("district_type")))
    dims = ["district_type", "district", "race"]
    add("race_by_district", dims, pl.concat(district_frames).select(dims + values))
    return out


CENSUS_SOURCE_ID = "census-pl94171-2020-md-county-vap"
CENSUS_COUNTY_CSV = ARCHIVES_DIR / "census" / "md_county_vap_2020.csv"

RACE_LIMITATIONS = [
    "Race is estimated, not recorded. Maryland's voter file has no race or ethnicity field. "
    "Each voter's race is estimated from their surname, first and middle names and ZIP code "
    "with Bayesian Improved Surname Geocoding (BISG, the wru package). The estimate for one "
    "voter is a set of probabilities, never a fact about that person.",
    "Counts are expected values: the sum of the probabilities for each group, rounded. They "
    "describe groups of voters and are most reliable for large groups. A cell of 1 to 9 is "
    "hidden like any other.",
    "BISG is known to err in a pattern. In integrated areas it tends to place Black voters in "
    "the white group, and it identifies multiracial voters, Native Americans and Pacific "
    "Islanders poorly: all of them land in 'other' or in another group. Hispanic and Asian "
    "estimates rest heavily on surname.",
    "ZIP code is coarser than a census block, so the geographic part of the estimate is weaker "
    "than in studies that geocode addresses. Voters with no usable ZIP code are placed using "
    "their county.",
    "The estimate does not use party, age or sex, so the race by party and race by age tables "
    "do not repeat what the model was given. Active and inactive registrants are both counted.",
    "The comparison with the Census is rough: it sets the estimated race of registered voters "
    "against the 2020 voting-age population, and registration rates differ by group.",
]


def _repo_relative(path: Path) -> str:
    """The path from the repository root, so published records carry no home directory."""
    path = Path(path).resolve()
    return str(path.relative_to(PROJECT_ROOT)) if PROJECT_ROOT in path.parents else path.name


def race_calibration(df: pl.DataFrame, census_csv: Path = CENSUS_COUNTY_CSV) -> dict:
    """Estimated share of registrants by race next to the 2020 Census voting-age population."""
    census = pl.read_csv(census_csv, schema_overrides={"county_fips": pl.String})
    fips = {code: county for county, code in COUNTY_FIPS.items()}
    cols = {"white": "vap_white", "black": "vap_black", "hispanic": "vap_hispanic",
            "asian": "vap_asian", "other": "vap_other"}  # fmt: skip
    census = census.with_columns(pl.col("county_fips").replace(fips).alias("county"))
    if census["county"].n_unique() != 24 or not set(census["county"]) <= set(COUNTIES):
        raise VoterFileError(f"{census_csv} does not hold the 24 Maryland counties")

    estimated = (
        df.filter(pl.col("pred_whi").is_not_null())
        .group_by(pl.col("county").cast(pl.String))
        .agg(*[pl.col(c).sum() for c in RACES], pl.len().alias("voters"))
    )
    est = {r["county"]: r for r in estimated.iter_rows(named=True)}
    cen = {r["county"]: r for r in census.iter_rows(named=True)}

    def shares(est_row: dict, cen_row: dict) -> dict:
        return {
            race: {
                "estimated_pct": round(100 * est_row[col] / est_row["voters"], 1),
                "census_vap_pct": round(100 * cen_row[cols[race]] / cen_row["vap_total"], 1),
            }
            for col, race in RACES.items()
        }

    def combine(rows: list[dict], keys: list[str]) -> dict:
        return {k: sum(r[k] for r in rows) for k in keys}

    est_total = combine(list(est.values()), [*RACES, "voters"])
    cen_total = combine(list(cen.values()), ["vap_total", *cols.values()])
    return {
        STATEWIDE: shares(est_total, cen_total),
        "counties": {c: shares(est[c], cen[c]) for c in COUNTIES if c in est and c in cen},
    }


def vrar_cross_check(df: pl.DataFrame, snapshot: str, data_dir: Path | None = None) -> dict | None:
    """Compare active registrants with the same month's monthly registration report."""
    path = (data_dir or DATA_DIR) / "voter_registration" / "monthly" / f"{snapshot[:7]}.csv"
    if not path.exists():
        return None
    reported = dict(
        pl.read_csv(path).group_by("county").agg(pl.col("active_voters").sum()).iter_rows()
    )
    ours = dict(
        df.filter(pl.col("status") == "A")
        .group_by(pl.col("county").cast(pl.String))
        .len()
        .iter_rows()
    )
    file_total, vrar_total = sum(ours.values()), sum(reported.values())
    return {
        "vrar_month": snapshot[:7],
        "file_active": file_total,
        "vrar_active": vrar_total,
        "difference_pct": round(100 * (file_total - vrar_total) / vrar_total, 2),
        "by_county_pct": {
            c: round(100 * (ours.get(c, 0) - n) / n, 2) for c, n in sorted(reported.items())
        },
    }


def electorate_coverage(
    df: pl.DataFrame, elections: list[Election], data_dir: Path | None = None
) -> dict:
    """How much of each election's electorate is still in this file.

    For each election month, compares the registered voters the file counts as eligible with
    the active plus inactive voters on that month's registration report. The difference is
    mostly voters removed since (moved, died, cancelled). It is approximate: the report also
    counts 16 and 17 year-olds who registered early, and it is taken at month end.
    """
    root = (data_dir or DATA_DIR) / "voter_registration"
    out = {}
    for e in elections:
        month = e.date[:7]
        monthly, activity = root / "monthly" / f"{month}.csv", root / "activity" / f"{month}.csv"
        if not (monthly.exists() and activity.exists()):
            continue
        active = dict(
            pl.read_csv(monthly).group_by("county").agg(pl.col("active_voters").sum()).iter_rows()
        )
        inactive = dict(
            pl.read_csv(activity)
            .filter(pl.col("measure") == "inactive")
            .group_by("county")
            .agg(pl.col("value").sum())
            .iter_rows()
        )
        reported = {c: n + inactive.get(c, 0) for c, n in active.items()}
        in_file = dict(
            df.group_by(pl.col("county").cast(pl.String))
            .agg(pl.col(e.eligible).sum().cast(pl.Int64))
            .iter_rows()
        )

        def entry(reported_n: int, file_n: int) -> dict:
            return {
                "reported": reported_n,
                "in_file": file_n,
                "pct": round(100 * file_n / reported_n, 1) if reported_n else None,
            }

        out[e.key] = {
            "month": month,
            STATEWIDE: entry(sum(reported.values()), sum(in_file.get(c, 0) for c in reported)),
            "counties": {c: entry(n, in_file.get(c, 0)) for c, n in sorted(reported.items())},
        }
    return out


def suppress(df: pl.DataFrame, values: list[str], below: int) -> pl.DataFrame:
    """Null out counts from 1 to below - 1. Zeros are kept: they identify nobody."""
    return df.with_columns(
        [
            pl.when((pl.col(v) > 0) & (pl.col(v) < below)).then(None).otherwise(pl.col(v)).alias(v)
            for v in values
        ]
    )


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


def _dump_metric(path: Path, metric: Metric, snapshot: str, below: int) -> None:
    rows = suppress(metric.df, metric.values, below).sort(metric.dims, nulls_last=True)
    head = json.dumps(
        {
            "metric": metric.name,
            "snapshot_date": snapshot,
            "dims": metric.dims,
            "values": metric.values,
            "suppress_below": below,
        },
        separators=(",", ":"),
    )
    body = ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows.iter_rows(named=True))
    path.write_text(f'{head[:-1]},"rows":[\n{body}\n]}}\n')


def _snapshot_limitations(checks: dict, snapshot: str) -> list[str]:
    notes = []
    if checks.get("readme_total_records") is None:
        notes.append(
            f"This file came without a readme, so its date ({snapshot}) was supplied by hand and "
            "its record count could not be checked against the State Board's. The latest "
            f"registration in it is dated {checks.get('latest_registration_date')}."
        )
    held = checks.get("elections_not_yet_held")
    if held:
        notes.append(
            f"The file was made before the {', '.join(held)} election, so that election is not in it."
        )
    return notes


def write_outputs(
    out_dir: Path,
    metrics: list[Metric],
    elections: list[Election],
    parts: list[Part],
    duplicates: list[Path],
    checks: dict,
    readme: Readme,
    snapshot: str,
    below: int,
    manifest_path: Path | None = None,
    census_csv: Path | None = None,
) -> list[Path]:
    manifest_path = manifest_path or MANIFEST_PATH
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for m in metrics:
        path = out_dir / f"{m.name}.json"
        _dump_metric(path, m, snapshot, below)
        written.append(path)

    meta = {
        "snapshot_date": snapshot,
        "elections": [
            {k: getattr(e, k) for k in ("key", "date", "kind", "label", "short")} for e in elections
        ],
        "counties": COUNTIES,
        "party_groups": PARTY_GROUP_ORDER,
        "all_parties": ALL_PARTIES,
        "statewide": STATEWIDE,
        "age_bands": [b[0] for b in AGE_BANDS] + ["Unknown"],
        "age_detail_bands": [b[0] for b in AGE_DETAIL_BANDS] + ["Unknown"],
        "new_registrants": {
            "first_year": NEW_REGISTRANT_FIRST_YEAR,
            "age_bands": [b[0] for b in NEW_REGISTRANT_BANDS],
            "cutoff": snapshot[5:],  # "08-12": registered on or before this month and day
        },
        "generations": [g[0] for g in GENERATIONS] + ["Unknown"],
        "voter_types": VOTER_TYPES,
        "records": {
            **{k: checks[k] for k in ("readme_total_records", "rows_valid", "status_counts")},
            "latest_registration_date": checks.get("latest_registration_date"),
        },
        "vrar_cross_check": checks.get("vrar_cross_check"),
        "electorate_coverage": checks.get("electorate_coverage", {}),
        **({"race": checks["race"]} if "race" in checks else {}),
        "metrics": {
            m.name: {"dims": m.dims, "values": m.values, "rows": m.df.height} for m in metrics
        },  # fmt: skip
        "suppress_below": below,
        "limitations": LIMITATIONS + _snapshot_limitations(checks, snapshot),
    }
    snapshot_path = out_dir / "snapshot.json"
    snapshot_path.write_text(json.dumps(meta, indent=2) + "\n")
    written.append(snapshot_path)

    combined = sha256("".join(sorted(p.sha256 for p in parts)).encode()).hexdigest()
    fetched = datetime.fromtimestamp(
        min(p.path.stat().st_mtime for p in parts), tz=timezone.utc
    ).isoformat(timespec="seconds")
    source_id = f"sbe-voterfile-{snapshot}"
    record = SourceRecord(
        source_id=source_id,
        source_url=SOURCE_URL,
        source_name=SOURCE_NAME,
        fetch_date=fetched,
        local_path=str(parts[0].path.parent),
        sha256=combined,
        document_title=f"Statewide voter registration list with voter history ({snapshot})",
        notes=f"Aggregates only. Counts of {1}-{below - 1} are suppressed.",
    )
    sidecar_checks = {
        **checks,
        "snapshot_date": snapshot,
        "parts": [
            {
                # Parts are identified by number and hash; file names can carry the
                # requesting agency's staff username.
                "part": p.number,
                "sha256": p.sha256,
                "bytes": p.bytes,
                "rows": p.rows,
                "has_header": p.has_header,
            }
            for p in parts
        ],  # fmt: skip
        "duplicate_parts_skipped": [f"part {_part_number(d)}" for d in duplicates],
        "elections": [e.key for e in elections],
        "suppress_below": below,
        "suppression_rule": f"Counts from 1 to {below - 1} are null; zeros are kept.",
    }
    written.append(
        ProvenanceSidecar.from_source(
            record,
            parser_version=PARSER_VERSION,
            notes="Aggregated with polars; no row-level data is written.",
            extraction_method="polars",
            checks=sidecar_checks,
        ).write(snapshot_path)
    )

    race = checks.get("race")
    if race and not find_source(manifest_path, CENSUS_SOURCE_ID):
        census_path = census_csv or CENSUS_COUNTY_CSV
        append_to_manifest(
            manifest_path,
            SourceRecord.create(
                source_id=CENSUS_SOURCE_ID,
                source_url=CENSUS_URL,
                source_name=CENSUS_NAME,
                local_path=census_path,
                document_title="2020 Census P.L. 94-171 table P4: voting-age population by race, Maryland counties",
                notes="Written by scripts/race/predict_race.R for the race estimate calibration.",
            ),
        )
    for p in parts:
        part_id = f"{source_id}-part-{p.number:02d}"
        if find_source(manifest_path, part_id):
            continue
        append_to_manifest(
            manifest_path,
            SourceRecord.create(
                source_id=part_id,
                source_url=SOURCE_URL,
                source_name=SOURCE_NAME,
                local_path=p.path,
                document_title=f"Voter file part {p.number} ({snapshot})",
                notes="Raw voter file, restricted use. Not stored in the repository.",
            ),
        )
    return written


def run(
    path: Path,
    out_root: Path,
    snapshot_date: str | None = None,
    suppress_below: int = 10,
    force: bool = False,
    manifest_path: Path | None = None,
    data_dir: Path | None = None,
    race_predictions: Path | None = None,
    census_csv: Path | None = None,
) -> dict:
    """Aggregate the voter file in ``path`` into ``out_root/<snapshot_date>/``."""
    directory = Path(path).expanduser().resolve()
    out_root = Path(out_root).expanduser().resolve()
    if out_root == directory or directory in out_root.parents:
        raise VoterFileError("Refusing to write output inside the voter file directory")

    if directory.is_file():
        if not snapshot_date:
            raise VoterFileError(
                "A single voter file has no readme to give its date; pass --snapshot-date"
            )
        readme = Readme(total_records=None, snapshot_date=snapshot_date)
    else:
        readme = read_readme(directory)
    snapshot = snapshot_date or readme.snapshot_date
    out_dir = out_root / snapshot
    if (out_dir / "snapshot.json").exists() and not force:
        raise VoterFileError(f"{out_dir} already exists; pass --force to rebuild it")

    parts, duplicates = discover_parts(directory)
    for d in duplicates:
        logger.info("Skipping duplicate part: %s", d.name)
    names, elections = columns_from_header(parts)
    # An export made before an election still has that election's (empty) column.
    not_yet_held = [e for e in elections if e.date > snapshot]
    elections = [e for e in elections if e.date <= snapshot]
    if not elections:
        raise VoterFileError(f"No election in the header took place on or before {snapshot}")
    race = race_info = None
    if race_predictions is not None:
        race, race_info = read_race_predictions(race_predictions)
    df, checks = load_voterfile(parts, names, elections, readme, race)
    checks["elections_not_yet_held"] = [e.key for e in not_yet_held]
    if not checks["reconciled"]:
        raise VoterFileError(
            f"Voter file does not match its readme: {json.dumps(checks, default=str)}"
        )

    if race_info is not None:
        census_csv = census_csv or CENSUS_COUNTY_CSV
        run_record = race_info["run"]
        checks["race"] = {
            "method": run_record.get("method"),
            "names_used": run_record.get("names_used"),
            "conditioned_on": run_record.get("conditioned_on"),
            "census_year": run_record.get("census_year"),
            "wru_version": run_record.get("wru_version"),
            "share_zcta": run_record.get("share_zcta"),
            "share_county_fallback": run_record.get("share_county_fallback"),
            "share_surname_on_2010_list": run_record.get("share_surname_on_2010_list"),
            "predictions_sha256": race_info["predictions_sha256"],
            "census_source_id": CENSUS_SOURCE_ID,
            "census_county_csv": _repo_relative(census_csv),
            "unmatched_share": checks.get("race_unmatched_share"),
            "calibration": race_calibration(df, census_csv),
            "limitations": RACE_LIMITATIONS,
        }

    checks["vrar_cross_check"] = vrar_cross_check(df, snapshot, data_dir)
    checks["electorate_coverage"] = electorate_coverage(df, elections, data_dir)
    metrics = build_metrics(df, elections)
    write_outputs(
        out_dir,
        metrics,
        elections,
        parts,
        duplicates,
        checks,
        readme,
        snapshot,
        suppress_below,
        manifest_path,
        census_csv,
    )
    checks["out_dir"] = str(out_dir)
    checks["parts"] = len(parts)
    checks["duplicates_skipped"] = len(duplicates)
    checks["metrics"] = len(metrics)
    return checks
