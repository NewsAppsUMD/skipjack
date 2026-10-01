"""Normalization for voter registration data across eras."""

from __future__ import annotations

import polars as pl

PARTY_NORMALIZE = {
    "DEM": "Democratic",
    "REP": "Republican",
    "GRN": "Green",
    "CON": "Constitution",
    "IND": "Independent",
    "LIB": "Libertarian",
    "NLM": "No Labels",
    "AME": "Americans Elect",
    "BAR": "Bread and Roses",
    "UNAF": "Unaffiliated",
    "UNA": "Unaffiliated",
    "OTH": "Other",
    "WCP": "Working Class",
}


def normalize_parties(df: pl.DataFrame) -> pl.DataFrame:
    """Add a party_name column with full names while keeping the original code."""
    return df.with_columns(
        pl.col("party").replace_strict(PARTY_NORMALIZE, default="Other").alias("party_name")
    )


# --- Voter file (statewide list) party codes -----------------------------------------
# The voter file spells minor parties with an "O" prefix; map them to the VRAR codes.
VOTERFILE_PARTY_CODES = {"OLB": "LIB", "ONLM": "NLM", "OBAR": "BAR", "OIN": "IND"}

# Coarse groups that match the party columns in the 2026 monthly reports.
PARTY_GROUPS = {"DEM": "DEM", "REP": "REP", "UNA": "UNA", "GRN": "GRN", "WCP": "WCP"}
PARTY_GROUP_ORDER = ["DEM", "REP", "UNA", "GRN", "WCP", "OTH"]


def voterfile_party_name(code: str) -> str:
    """Full party name for a voter-file party code; unknown codes are 'Other'."""
    return PARTY_NORMALIZE.get(VOTERFILE_PARTY_CODES.get(code, code), "Other")


def party_group_expr(column: str = "party") -> pl.Expr:
    return pl.col(column).replace_strict(PARTY_GROUPS, default="OTH")
