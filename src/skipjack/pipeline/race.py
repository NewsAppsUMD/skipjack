"""Prepare the voter file for race estimation (BISG), outside the repository.

``skipjack race-input`` reads the raw voter file and writes one slim CSV of the fields the
estimate needs: the voter's names and ZIP code. Like the raw file, that CSV is row-level
personal data, so it is written to a directory outside this repository and never committed.

    <out>/<snapshot_date>/input.csv     vtr_id, surname, first, middle, state, county, zcta

``scripts/race/predict_race.R`` turns it into ``predictions.csv`` and ``run.json`` next to it,
and ``skipjack voterfile --race-predictions <that directory>`` aggregates the predictions.

The rows are the same voters ``skipjack voterfile`` keeps (same county, status, party and ID
checks, first of any duplicate ID), so the two runs join on ``vtr_id`` with nothing left over.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import polars as pl

from skipjack.pipeline.voterfile import (
    COUNTIES,
    COUNTY_FIPS,
    COUNTY_FIXES,
    PROJECT_ROOT,
    STATUSES,
    VoterFileError,
    columns_from_header,
    discover_parts,
    read_readme,
)

logger = logging.getLogger(__name__)

STATE = "MD"
INPUT_COLUMNS = ["vtr_id", "surname", "first", "middle", "state", "county", "zcta"]


def _scan(part, names: list[str]) -> pl.LazyFrame:
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

    def clean(column: str) -> pl.Expr:
        return pl.col(column).fill_null("").str.strip_chars()

    zip5 = clean("ResidentialZip").str.extract(r"^(\d{5})", 1)
    return lf.select(
        pl.col("VTR_ID").alias("vtr_id"),
        clean("LastName").alias("surname"),
        clean("FirstName").alias("first"),
        clean("MiddleName").alias("middle"),
        pl.col("County").replace(COUNTY_FIXES).alias("county_name"),
        pl.col("StatusCode").alias("status"),
        pl.col("Party").alias("party"),
        zip5.fill_null("").alias("zcta"),
    )


def build_input(path: Path, out_root: Path, snapshot_date: str | None = None) -> dict:
    """Write ``out_root/<snapshot>/input.csv`` from the voter file at ``path``."""
    directory = Path(path).expanduser().resolve()
    out_root = Path(out_root).expanduser().resolve()
    if out_root == directory or directory in out_root.parents:
        raise VoterFileError("Refusing to write output inside the voter file directory")
    if out_root == PROJECT_ROOT or PROJECT_ROOT in out_root.parents:
        raise VoterFileError(
            "Refusing to write row-level names inside the repository; choose a directory outside it"
        )

    if directory.is_file():
        if not snapshot_date:
            raise VoterFileError(
                "A single voter file has no readme to give its date; pass --snapshot-date"
            )
        snapshot = snapshot_date
    else:
        snapshot = snapshot_date or read_readme(directory).snapshot_date
    parts, _ = discover_parts(directory)
    names, _ = columns_from_header(parts)

    frames = [_scan(part, names).collect() for part in parts]
    df = pl.concat(frames)
    rows_read = df.height
    valid = (
        pl.col("county_name").is_in(COUNTIES)
        & pl.col("status").is_in(STATUSES)
        & pl.col("party").is_not_null()
        & (pl.col("party") != "")
        & pl.col("vtr_id").str.contains(r"^\d+$")
    ).fill_null(False)
    df = df.filter(valid).unique(subset="vtr_id", keep="first", maintain_order=True)
    df = df.select(
        "vtr_id",
        "surname",
        "first",
        "middle",
        pl.lit(STATE).alias("state"),
        pl.col("county_name").replace(COUNTY_FIPS).alias("county"),
        "zcta",
    )
    assert df.columns == INPUT_COLUMNS

    out_dir = out_root / snapshot
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = out_dir / "input.csv"
    df.write_csv(input_path)
    summary = {
        "snapshot_date": snapshot,
        "rows_read": rows_read,
        "rows_written": df.height,
        "blank_surname": int((df["surname"] == "").sum()),
        "blank_zcta": int((df["zcta"] == "").sum()),
    }
    (out_dir / "input_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    summary["input_path"] = str(input_path)
    return summary
