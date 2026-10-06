"""Voter file routes: aggregates from a statewide voter file snapshot.

Everything here reads the ``voter_file_counts`` table (see pipeline/voterfile.py).
Cells with 1 to 9 voters were suppressed in the pipeline and arrive as ``None``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from skipjack.pipeline.clean import voterfile_party_name
from skipjack.web.db import get_db
from skipjack.web.urls import resolve_slug, slugify

router = APIRouter(prefix="/voters/file")

STATEWIDE = "Maryland"
ALL = "ALL"
SHOWN_PARTIES = ["DEM", "REP", "UNA", ALL]
PARTY_LABELS = {
    "DEM": "Democratic",
    "REP": "Republican",
    "UNA": "Unaffiliated",
    "GRN": "Green",
    "WCP": "Working Class",
    "OTH": "Other",
    ALL: "All voters",
}
VOTER_TYPE_LABELS = {
    "every_election": "Voted in every election they could",
    "primary_and_general": "Voted in primaries and generals",
    "primary_only": "Primaries only",
    "general_only": "Generals only",
    "none": "Did not vote",
}
COHORT_FIRST_YEAR = 1980
DISTRICT_TYPES = ("congressional", "legislative")


def minor_party_name(code: str) -> str:
    """Name for a minor-party code. Codes the State Board does not document stay unnamed."""
    name = voterfile_party_name(code)
    return "Other (unlabeled)" if name == "Other" and code != "OTH" else name


def pct(n: int | None, d: int | None) -> float | None:
    """n as a percent of d; None if either is missing (suppressed) or d is zero."""
    return None if n is None or not d else 100.0 * n / d


def snapshot_dates(conn: sqlite3.Connection) -> list[str]:
    return [
        r["snapshot_date"]
        for r in conn.execute(
            "SELECT snapshot_date FROM voter_file_snapshots ORDER BY snapshot_date DESC"
        )
    ]


def load_snapshot(conn: sqlite3.Connection, requested: str | None) -> tuple[str, dict]:
    dates = snapshot_dates(conn)
    if not dates:
        raise HTTPException(404, "No voter file data has been loaded. Run `skipjack voterfile`.")
    snapshot = requested or dates[0]
    if snapshot not in dates:
        raise HTTPException(404, f"No voter file snapshot for {snapshot}")
    row = conn.execute(
        "SELECT meta_json FROM voter_file_snapshots WHERE snapshot_date = ?", (snapshot,)
    ).fetchone()
    return snapshot, json.loads(row["meta_json"])


def previous_general(elections: list[dict]) -> dict | None:
    """The general election before the latest election, or None."""
    latest = elections[-1]
    earlier = [e for e in elections if e["kind"] == "general" and e["date"] < latest["date"]]
    return earlier[-1] if earlier else None


def party_shares(
    cells: dict[tuple[str, str], int | None], registered: int | None
) -> dict[str, float | None]:
    """Each party group's share of registrants (active plus inactive)."""
    return {
        g: pct(sum(cells.get((g, s)) or 0 for s in "AI"), registered)
        for g in PARTY_LABELS
        if g != ALL
    }


def build_county_context(conn: sqlite3.Connection, snapshot: str, meta: dict, county: str) -> dict:
    """Everything the county (and statewide) page shows, computed from stored aggregates."""
    elections = meta["elections"]
    n_elections = len(elections)
    by_metric: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        """SELECT metric, party_group, party, status, election, age_band, generation, reg_year,
                  gender, voter_type, eligible_elections, count, eligible
           FROM voter_file_counts WHERE snapshot_date = ? AND county = ?""",
        (snapshot, county),
    ):
        by_metric[r["metric"]].append(r)

    # Tiles.
    cells = {(r["party_group"], r["status"]): r["count"] for r in by_metric["party_by_county"]}
    active, inactive = cells.get((ALL, "A")), cells.get((ALL, "I"))
    registered = (active or 0) + (inactive or 0)
    tiles = {
        "registered": registered,
        "active": active,
        "inactive": inactive,
        "inactive_pct": pct(inactive, registered),
    }
    shares = party_shares(cells, registered)

    # How much of each election's electorate this file still contains.
    coverage_rows = []
    for e in elections:
        by_place = meta.get("electorate_coverage", {}).get(e["key"])
        entry = None
        if by_place:
            entry = by_place[STATEWIDE] if county == STATEWIDE else by_place["counties"].get(county)
        if entry:
            coverage_rows.append({"election": e, **entry})

    # Participation by party and election.
    turnout = {(r["party_group"], r["election"]): (r["count"], r["eligible"])
               for r in by_metric["turnout_by_election"]}  # fmt: skip
    turnout_rows = []
    for e in elections:
        row = {"election": e, "cells": []}
        for g in SHOWN_PARTIES:
            count, eligible = turnout.get((g, e["key"]), (None, None))
            row["cells"].append({"pct": pct(count, eligible), "count": count, "eligible": eligible})
        turnout_rows.append(row)
    charts = {}
    for kind in ("primary", "general"):
        chosen = [e for e in elections if e["kind"] == kind]
        charts[kind] = {
            "labels": [e["short"] for e in chosen],
            "series": [
                {
                    "party": g,
                    "name": PARTY_LABELS[g],
                    "values": [pct(*turnout.get((g, e["key"]), (None, None))) for e in chosen],
                    "counts": [turnout.get((g, e["key"]), (None, None))[0] for e in chosen],
                    "eligible": [turnout.get((g, e["key"]), (None, None))[1] for e in chosen],
                }
                for g in SHOWN_PARTIES
            ],
        }

    # Voter types among registrants eligible for every election in the file.
    types: dict[str, dict[str, int | None]] = defaultdict(dict)
    for r in by_metric["voter_types"]:
        if r["eligible_elections"] == n_elections:
            types[r["party_group"]][r["voter_type"]] = r["count"]
    long_term_total = sum(v or 0 for v in types[ALL].values())
    voter_types = []
    for key, label in VOTER_TYPE_LABELS.items():
        cells_pct = []
        for g in SHOWN_PARTIES:
            total = sum(v or 0 for v in types[g].values())
            cells_pct.append(pct(types[g].get(key), total))
        voter_types.append({"key": key, "label": label, "pcts": cells_pct})

    # Participation by age.
    age_cells = {(r["age_band"], r["election"]): (r["count"], r["eligible"])
                 for r in by_metric["turnout_by_age"]}  # fmt: skip
    bands = [b for b in meta["age_bands"] if b not in ("Under 18", "Unknown")]
    age_turnout = [
        {"band": b, "pcts": [pct(*age_cells.get((b, e["key"]), (None, None))) for e in elections]}
        for b in bands
    ]

    # Age profile by party.
    band_counts: dict[str, dict[str, int | None]] = defaultdict(dict)
    for r in by_metric["age_by_party"]:
        band_counts[r["party_group"]][r["age_band"]] = r["count"]
    age_profile = []
    for b in bands:
        pcts = []
        for g in SHOWN_PARTIES:
            total = sum(v or 0 for v in band_counts[g].values())
            pcts.append(pct(band_counts[g].get(b), total))
        age_profile.append({"band": b, "pcts": pcts})

    # Gender.
    gender_counts: dict[str, dict[str, int | None]] = defaultdict(dict)
    for r in by_metric["gender"]:
        gender_counts[r["party_group"]][r["gender"]] = r["count"]
    genders = ["Female", "Male", "Unknown"]
    gender_rows = []
    for g in SHOWN_PARTIES:
        total = sum(v or 0 for v in gender_counts[g].values())
        gender_rows.append(
            {"party": g, "name": PARTY_LABELS[g],
             "pcts": [pct(gender_counts[g].get(x), total) for x in genders]}
        )  # fmt: skip

    # Registration cohorts: survivors by the year they first registered.
    cohort: dict[str, dict[int, int | None]] = defaultdict(dict)
    for r in by_metric["registration_cohorts"]:
        cohort[r["party_group"]][r["reg_year"]] = r["count"]
    last_year = int(snapshot[:4])
    years = list(range(COHORT_FIRST_YEAR, last_year + 1))
    cohorts = {
        "years": years,
        "series": [
            {"party": g, "name": PARTY_LABELS[g], "values": [cohort[g].get(y) for y in years]}
            for g in ("DEM", "REP", "UNA")
        ],
    }

    # Minor parties.
    minor = sorted(
        (
            (r["party"], minor_party_name(r["party"]), r["count"])
            for r in by_metric["minor_parties"]
        ),
        key=lambda t: (t[2] is None, -(t[2] or 0), t[0]),
    )
    shown_minor = minor[:10]
    other_total = sum(cells.get(("OTH", s)) or 0 for s in "AI")
    remainder = other_total - sum(c or 0 for _, _, c in shown_minor)
    minor_rows = {
        "shown": [{"code": c, "name": n, "count": v} for c, n, v in shown_minor],
        "remainder": remainder if len(minor) > 10 and remainder > 0 else None,
        "remainder_codes": len(minor) - 10,
    }

    # Newly registered voters.
    new_voters = None
    prev = previous_general(elections)
    new_cells = {
        r["party_group"]: (r["count"], r["eligible"]) for r in by_metric["new_voters_voted"]
    }
    if prev and new_cells:
        new_voters = {
            "since": prev,
            "latest": elections[-1],
            "rows": [
                {"name": PARTY_LABELS[g], "count": new_cells.get(g, (None, None))[0],
                 "eligible": new_cells.get(g, (None, None))[1],
                 "pct": pct(*new_cells.get(g, (None, None)))}
                for g in SHOWN_PARTIES
            ],
        }  # fmt: skip

    return {
        "tiles": tiles,
        "shares": shares,
        "turnout_rows": turnout_rows,
        "coverage_rows": coverage_rows,
        "charts": charts,
        "voter_types": voter_types,
        "long_term_pct": pct(long_term_total, registered),
        "age_turnout": age_turnout,
        "age_profile": age_profile,
        "gender_rows": gender_rows,
        "genders": genders,
        "cohorts": cohorts,
        "minor": minor_rows,
        "new_voters": new_voters,
    }


def _place_rows(
    registrants: dict[str, dict[tuple[str, str], int | None]],
    turnout: dict[tuple[str, str], tuple[int | None, int | None]],
    elections: list[dict],
    name_key: str,
) -> list[dict]:
    generals = [e for e in elections if e["kind"] == "general"]
    latest_general, latest = (generals[-1] if generals else elections[-1]), elections[-1]
    out = []
    for place, cells in registrants.items():
        active, inactive = cells.get((ALL, "A")), cells.get((ALL, "I"))
        registered = (active or 0) + (inactive or 0)
        shares = party_shares(cells, registered)
        out.append(
            {
                name_key: place,
                "registered": registered,
                "dem": shares["DEM"],
                "rep": shares["REP"],
                "una": shares["UNA"],
                "inactive": pct(inactive, registered),
                "general": pct(*turnout.get((place, latest_general["key"]), (None, None))),
                "latest": pct(*turnout.get((place, latest["key"]), (None, None))),
            }
        )
    return out


def county_table(conn: sqlite3.Connection, snapshot: str, elections: list[dict]) -> list[dict]:
    registrants: dict[str, dict] = defaultdict(dict)
    for r in conn.execute(
        """SELECT county, party_group, status, count FROM voter_file_counts
           WHERE snapshot_date = ? AND metric = 'party_by_county' AND county <> ?""",
        (snapshot, STATEWIDE),
    ):
        registrants[r["county"]][(r["party_group"], r["status"])] = r["count"]
    turnout = {
        (r["county"], r["election"]): (r["count"], r["eligible"])
        for r in conn.execute(
            """SELECT county, election, count, eligible FROM voter_file_counts
               WHERE snapshot_date = ? AND metric = 'turnout_by_election'
                 AND party_group = ? AND county <> ?""",
            (snapshot, ALL, STATEWIDE),
        )
    }
    return sorted(_place_rows(registrants, turnout, elections, "county"), key=lambda r: r["county"])


def district_table(
    conn: sqlite3.Connection, snapshot: str, elections: list[dict], district_type: str
) -> list[dict]:
    registrants: dict[str, dict] = defaultdict(dict)
    for r in conn.execute(
        """SELECT district, party_group, status, count FROM voter_file_counts
           WHERE snapshot_date = ? AND metric = 'districts' AND district_type = ?""",
        (snapshot, district_type),
    ):
        registrants[r["district"]][(r["party_group"], r["status"])] = r["count"]
    turnout = {}
    for r in conn.execute(
        """SELECT district, election, count, eligible FROM voter_file_counts
           WHERE snapshot_date = ? AND metric = 'district_turnout' AND district_type = ?""",
        (snapshot, district_type),
    ):
        turnout[(r["district"], r["election"])] = (r["count"], r["eligible"])
    # Turnout rows are per district across all parties.
    return sorted(
        _place_rows(registrants, turnout, elections, "district"), key=lambda r: r["district"]
    )


def _render(request: Request, template: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, template, context)


def _snapshot_query(snapshot: str, latest: str) -> str:
    """'?snapshot=...' when viewing an older snapshot, otherwise nothing."""
    return "" if snapshot == latest else f"?snapshot={snapshot}"


def _generals(meta: dict) -> list[dict]:
    return [e for e in meta["elections"] if e["kind"] == "general"]


@router.get("/")
async def voterfile_index(request: Request, snapshot: str | None = Query(None)):
    return _county_page(request, snapshot, None)


@router.get("/county/")
async def voterfile_county_root(
    request: Request, snapshot: str | None = Query(None), county: str | None = Query(None)
):
    suffix = f"?snapshot={snapshot}" if snapshot else ""
    if county and county != STATEWIDE:  # an old query-string link
        return RedirectResponse(f"/voters/file/county/{slugify(county)}/{suffix}", status_code=307)
    return RedirectResponse(f"/voters/file/{suffix}", status_code=307)


@router.get("/county/{slug}/")
async def voterfile_county(request: Request, slug: str, snapshot: str | None = Query(None)):
    return _county_page(request, snapshot, slug)


def _county_page(request: Request, snapshot: str | None, slug: str | None):
    with get_db() as conn:
        snapshot, meta = load_snapshot(conn, snapshot)
        county = STATEWIDE if slug is None else resolve_slug(slug, meta["counties"])
        if county is None:
            raise HTTPException(404, f"No county called {slug}")
        context = build_county_context(conn, snapshot, meta, county)
        county_rows = county_table(conn, snapshot, meta["elections"]) if slug is None else None
        dates = snapshot_dates(conn)
    generals = _generals(meta)
    return _render(
        request,
        "voterfile_county.html",
        {
            **context,
            "snapshot": snapshot,
            "snapshot_q": _snapshot_query(snapshot, dates[0]),
            "snapshots": dates,
            "compare_with": dates[1] if len(dates) > 1 and snapshot == dates[0] else None,
            "meta": meta,
            "county": county,
            "statewide": STATEWIDE,
            "counties": meta["counties"],
            "county_rows": county_rows,
            "latest_general": generals[-1] if generals else meta["elections"][-1],
            "party_labels": PARTY_LABELS,
            "shown_parties": SHOWN_PARTIES,
            "data": {"charts": context["charts"], "cohorts": context["cohorts"]},
        },
    )


@router.get("/districts/")
async def voterfile_districts_root(
    request: Request, snapshot: str | None = Query(None), type: str | None = Query(None)
):
    kind = type if type in DISTRICT_TYPES else DISTRICT_TYPES[0]
    suffix = f"?snapshot={snapshot}" if snapshot else ""
    return RedirectResponse(f"/voters/file/districts/{kind}/{suffix}", status_code=307)


@router.get("/districts/{kind}/")
async def voterfile_districts(request: Request, kind: str, snapshot: str | None = Query(None)):
    if kind not in DISTRICT_TYPES:
        raise HTTPException(404, f"No district type called {kind}")
    with get_db() as conn:
        snapshot, meta = load_snapshot(conn, snapshot)
        rows = district_table(conn, snapshot, meta["elections"], kind)
        dates = snapshot_dates(conn)
    generals = _generals(meta)
    return _render(
        request,
        "voterfile_districts.html",
        {
            "snapshot": snapshot,
            "snapshot_q": _snapshot_query(snapshot, dates[0]),
            "meta": meta,
            "district_type": kind,
            "rows": rows,
            "latest_general": generals[-1] if generals else meta["elections"][-1],
            "latest": meta["elections"][-1],
        },
    )
