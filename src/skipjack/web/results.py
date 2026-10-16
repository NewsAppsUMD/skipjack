"""County-level election results for the site, read from results_county / results_votes.

A *contest* is (office, district, party_file): a primary has one contest per party file (the
Democratic and Republican Governor races are two contests), a general has party_file "all".
Statewide totals are the county rows summed; the SBE's winner flag is passed through
(``MAX(winner)`` over the rows), never computed. Hub and contest pages exist only for the
statewide, congressional, State Senate and House of Delegates contests; every other contest
appears only on its county's page.
"""

from __future__ import annotations

import re
import sqlite3

from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS
from skipjack.web.urls import slugify

# --- which contests get pages -----------------------------------------------------------

# Offices elected statewide. Comptroller is only statewide in gubernatorial years: in the
# presidential years' files it is a single county's city comptroller, so `_scope` demotes any
# "statewide" office that is not on all 24 counties' ballots to local. The judges are the
# at-large retention questions (one per county file); the district appellate seats, which only
# some counties vote on, are local.
STATEWIDE_OFFICES = (
    "President - Vice Pres",
    "U.S. Senator",
    "Governor / Lt. Governor",
    "Comptroller",
    "Attorney General",
    "Appeals Judge MD at Lrg",
    "Judge Spec Appeal at Lg",
    "Judge Special Appeals At Large",
)
CONGRESS_OFFICES = ("U.S. Congress", "Representative in Congress")
SENATE_OFFICES = ("State Senator",)
DELEGATES_OFFICES = ("House of Delegates",)
# Everything else the SBE files carry. Listed explicitly so that a new office in a new
# election fails tests/test_results_web.py until someone decides where it belongs.
LOCAL_OFFICES = (
    "Appeals Judge MD",  # district appellate retention, a few counties each
    "Appellate Judge",
    "Judge Court of Appeals",
    "Judge Spec Appeals",
    "Judge Special Appeals",
    "MD Supreme Court",
    "Board of Ed at Large",
    "Board of Education",
    "Board of Education At Large",
    "Clerk Circuit Court",
    "Council - City of Cumberland",
    "County Commissioner",
    "County Commissioner At Large",
    "County Council",
    "County Council At Large",
    "County Executive",
    "Cumberland Council",
    "Hagerstown Council",
    "Judge Circuit Court",
    "Judge of the Circuit Court",
    "Judge Orphans' Court",
    "Mayor",
    "Mayor of Cumberland",
    "Mayor of Hagerstown",
    "Member City Council",
    "Pres County Commission",
    "Pres County Council",
    "President City Council",
    "Register of Wills",
    "Sheriff",
    "State's Attorney",
    "Treasurer",
    # party central committees
    "DEM Cent Comm",
    "DEM Cent Comm at Large",
    "DEM Cent Comm at Lg",
    "DEM Cent Committee",
    "Dem Cent Comm at Large",
    "Dem Cent Committee",
    "REP Cent Comm at Large",
    "REP Cent Committee",
    # presidential-primary convention delegates and alternates (by congressional district,
    # not the legislature's House of Delegates)
    "Democratic Delegates",
    "Female Delegates",
    "Male Delegates",
    "REP Alternates",
    "REP Delegates",
    "Republican Alternates",
    "Republican Delegates",
)

SCOPES = ("statewide", "congress", "senate", "delegates")
SCOPE_LABELS = {
    "statewide": "Statewide",
    "congress": "U.S. House of Representatives",
    "senate": "State Senate",
    "delegates": "House of Delegates",
    "local": "Local",
}
_OFFICE_SCOPE = (
    {o: "statewide" for o in STATEWIDE_OFFICES}
    | {o: "congress" for o in CONGRESS_OFFICES}
    | {o: "senate" for o in SENATE_OFFICES}
    | {o: "delegates" for o in DELEGATES_OFFICES}
    | {o: "local" for o in LOCAL_OFFICES}
)
PARTY_FILE_ORDER = ("dem", "rep", "non", "all")
PARTY_LABELS = {"dem": "Democratic", "rep": "Republican", "non": "Nonpartisan", "all": ""}
OFFICE_TITLES = {
    "President - Vice Pres": "President / Vice President",
    "Appeals Judge MD at Lrg": "Supreme Court of Maryland judges, at large",
    "Judge Spec Appeal at Lg": "Appellate Court judges, at large",
    "Judge Special Appeals At Large": "Court of Special Appeals judges, at large",
}
N_COUNTIES = len(COUNTY_CODES)
_COUNTY_SLUGS = {slugify(name): name for name in COUNTY_CODES.values()}


def contest_slug(office: str, district: str, party_file: str) -> str:
    return slugify(f"{office} {district} {party_file}")


def _scope(office: str, n_counties: int) -> str:
    scope = _OFFICE_SCOPE.get(office, "local")
    if scope == "statewide" and n_counties < N_COUNTIES:
        return "local"
    return scope


def _natural(text: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text))


def contest_title(office: str, district: str, party_file: str) -> str:
    title = OFFICE_TITLES.get(office, office)
    if district:
        title += f", District {district}" if len(district) <= 3 else f", {district}"
    if PARTY_LABELS[party_file]:
        title += f" ({PARTY_LABELS[party_file]})"
    return title


# --- reading rows -----------------------------------------------------------------------

# THE PP20 / PP24 DOUBLE-COUNT RULE, in one place. In the presidential primaries' President
# files each county appears twice: once with a blank district (the county's total) and again
# split by congressional district (one row per district the county touches, and a one-district
# county repeats the same numbers under its district). Summing both would count every vote
# twice (PP20 Democratic Biden: 1,759,506 against the real 879,753). So a row with a district
# is dropped when the same county's file carries a blank-district row for the same office and
# party file: the blank row is the county total, and the split is the same votes. Every query
# below reads through this filter. It applies to the statewide offices only: no other office has
# a blank row and district rows in one county file in these elections except local ones (e.g.
# GG22 Board of Education), where a blank at-large row and numbered districts are different
# contests and are shown as published, never summed. Convention delegates (PP20) are
# district-only, one row set per county per district, so they sum without duplication.
_STATEWIDE_SQL = ", ".join("'" + o.replace("'", "''") + "'" for o in STATEWIDE_OFFICES)  # constants
_DEDUP = f"""NOT (r.office IN ({_STATEWIDE_SQL}) AND r.district <> '' AND EXISTS (
    SELECT 1 FROM results_county b
    WHERE b.election = r.election AND b.county = r.county AND b.party_file = r.party_file
      AND b.office = r.office AND b.district = ''))"""


def _fetch(
    conn: sqlite3.Connection,
    code: str,
    *,
    office: str | None = None,
    district: str | None = None,
    party_file: str | None = None,
    county: str | None = None,
    votes: bool = True,
) -> list[dict]:
    """The election's rows (after the double-count rule), SBE order, optionally narrowed."""
    where = f"r.election = ? AND {_DEDUP}"
    args: list = [code]
    for column, value in (
        ("office", office),
        ("district", district),
        ("party_file", party_file),
        ("county", county),
    ):
        if value is not None:
            where += f" AND r.{column} = ?"
            args.append(value)
    sql = f"""SELECT r.id, r.source_id, r.county, r.party_file, r.office, r.district, r.candidate,
                     r.party, r.winner, r.write_in, r.total_votes, r.total_against
              FROM results_county r WHERE {where} ORDER BY r.id"""
    rows = [
        {
            "id": r[0],
            "source_id": r[1],
            "county": r[2],
            "party_file": r[3],
            "office": r[4],
            "district": r[5],
            "candidate": r[6],
            "party": r[7] or "",
            "winner": bool(r[8]),
            "write_in": bool(r[9]),
            "total": r[10],
            "against": r[11],
            "votes": {},
            "votes_against": {},
        }
        for r in conn.execute(sql, args)
    ]
    if votes and rows:
        by_id = {r["id"]: r for r in rows}
        for result_id, vote_type, n, against in conn.execute(
            f"""SELECT v.result_id, v.vote_type, v.votes, v.votes_against
                FROM results_votes v JOIN results_county r ON r.id = v.result_id
                WHERE {where} ORDER BY v.rowid""",
            args,
        ):
            if row := by_id.get(result_id):
                row["votes"][vote_type] = n
                row["votes_against"][vote_type] = against
    return rows


def _election_record(code: str) -> dict:
    e = RESULTS_ELECTIONS[code]
    return {
        "code": e.code,
        "slug": e.code.lower(),
        "year": e.year,
        "kind": e.kind,
        "name": e.name,
        "election_day": e.election_day,
        "published": e.published,
    }


def elections(conn: sqlite3.Connection) -> list[dict]:
    """Elections with results rows, newest first (a general before its year's primary)."""
    codes = [r[0] for r in conn.execute("SELECT DISTINCT election FROM results_county")]
    found = [_election_record(c) for c in codes if c in RESULTS_ELECTIONS]
    return sorted(found, key=lambda e: (-e["year"], e["kind"] != "general"))


def _vote_types(code: str) -> list[dict]:
    return [{"stem": stem, "label": label} for label, stem in RESULTS_ELECTIONS[code].vote_types]


# --- contests ---------------------------------------------------------------------------


def _candidates(rows: list[dict], vote_stems: list[str]) -> list[dict]:
    """One row per candidate, summed over ``rows``, most votes first (SBE order breaks ties)."""
    # keyed by (name, party): a candidate listed under two parties in one contest (a
    # cross-filed judge, say) is two ballot lines and is never merged
    by_name: dict[tuple, dict] = {}
    for r in rows:
        c = by_name.setdefault(
            (r["candidate"], r["party"]),
            {
                "name": r["candidate"],
                "party": r["party"],
                "write_in": r["write_in"],
                "winner": False,
                "total": 0,
                "against": None,
                "votes": dict.fromkeys(vote_stems, 0),
                "first": r["id"],
            },
        )
        c["winner"] = c["winner"] or r["winner"]
        c["total"] += r["total"]
        if r["against"] is not None:
            c["against"] = (c["against"] or 0) + r["against"]
        for stem, n in r["votes"].items():
            c["votes"][stem] = c["votes"].get(stem, 0) + n
    out = sorted(by_name.values(), key=lambda c: (-c["total"], c["first"]))
    cast = sum(c["total"] for c in out)
    for c in out:
        # a retention question's share is of its own for + against; everything else, of the
        # votes cast in the contest
        base = c["total"] + c["against"] if c["against"] is not None else cast
        c["share"] = 100 * c["total"] / base if base else None
        del c["first"]
    return out


def _contest_index(conn: sqlite3.Connection, code: str) -> list[dict]:
    """Every contest in the election, with its scope, in the SBE's order of appearance."""
    contests = []
    for office, district, party_file, n, first in conn.execute(
        f"""SELECT r.office, r.district, r.party_file, COUNT(DISTINCT r.county), MIN(r.id)
            FROM results_county r WHERE r.election = ? AND {_DEDUP}
            GROUP BY r.office, r.district, r.party_file ORDER BY MIN(r.id)""",
        (code,),
    ):
        contests.append(
            {
                "office": office,
                "district": district,
                "party_file": party_file,
                "counties": n,
                "first": first,
                "scope": _scope(office, n),
                "slug": contest_slug(office, district, party_file),
                "title": contest_title(office, district, party_file),
                "party_label": PARTY_LABELS[party_file],
            }
        )
    return contests


def _pages(conn: sqlite3.Connection, code: str) -> list[dict]:
    """The contests that get a page, in hub order: scope, then the office's place in its
    scope's list, then district (numerically), then party file."""
    contests = [c for c in _contest_index(conn, code) if c["scope"] != "local"]

    def key(c: dict) -> tuple:
        offices = STATEWIDE_OFFICES if c["scope"] == "statewide" else (c["office"],)
        return (
            SCOPES.index(c["scope"]),
            offices.index(c["office"]),
            _natural(c["district"]),
            PARTY_FILE_ORDER.index(c["party_file"]),
        )

    return sorted(contests, key=key)


def contest_slugs(conn: sqlite3.Connection, code: str) -> list[str]:
    """Slugs of every contest page of an election (the hub, the pages and the static site all
    read this one list)."""
    return [c["slug"] for c in _pages(conn, code)]


def hub(conn: sqlite3.Connection, code: str) -> dict | None:
    """An election's contests grouped by scope, each with statewide candidate totals."""
    if code not in RESULTS_ELECTIONS:
        return None
    pages = _pages(conn, code)
    if not pages:
        return None
    wanted = {(c["office"], c["district"], c["party_file"]): c for c in pages}
    rows_by_contest: dict[tuple, list[dict]] = {k: [] for k in wanted}
    for r in _fetch(conn, code, votes=False):
        if (key := (r["office"], r["district"], r["party_file"])) in rows_by_contest:
            rows_by_contest[key].append(r)
    groups = []
    for scope in SCOPES:
        summaries = []
        for key, c in wanted.items():
            if c["scope"] != scope:
                continue
            rows = rows_by_contest[key]
            summaries.append(
                {
                    **{k: c[k] for k in ("slug", "title", "office", "district", "party_file")},
                    "party_label": c["party_label"],
                    "counties_reporting": c["counties"],
                    "candidates": _candidates(rows, []),
                    "has_against": any(r["against"] is not None for r in rows),
                }
            )
        if summaries:
            groups.append({"scope": scope, "label": SCOPE_LABELS[scope], "contests": summaries})
    return {"election": _election_record(code), "groups": groups}


def _top(cells: list[int | None]) -> tuple[int | None, list[int]]:
    """(leader index, tied indices) of the votes in one county: the most votes among the
    contest's candidates, not the SBE's flag. A county with no votes cast has no leader, and
    candidates level on the top count are ``tied`` with no single leader."""
    present = [n for n in cells if n is not None]
    if not present or max(present) == 0:
        return None, []
    top = [i for i, n in enumerate(cells) if n == max(present)]
    return (top[0], []) if len(top) == 1 else (None, top)


def _county_row(county: str, rows: list[dict], candidates: list[dict], has_against: bool) -> dict:
    by_name = {(r["candidate"], r["party"]): r for r in rows}
    keys = [(c["name"], c["party"]) for c in candidates]
    cells = [by_name[k]["total"] if k in by_name else None for k in keys]
    leader, tied = (None, []) if has_against else _top(cells)
    row = {
        "county": county,
        "slug": slugify(county),
        "cells": cells,
        "total": sum(n for n in cells if n is not None),
        "leader": leader,
        "tied": tied,
    }
    if has_against:
        row["against"] = [by_name[k]["against"] if k in by_name else None for k in keys]
    return row


def contest(conn: sqlite3.Connection, code: str, slug: str) -> dict | None:
    """One contest page's data, or None for an unknown or local contest."""
    if code not in RESULTS_ELECTIONS:
        return None
    found = next((c for c in _pages(conn, code) if c["slug"] == slug), None)
    if found is None:
        return None
    rows = _fetch(
        conn, code, office=found["office"], district=found["district"],
        party_file=found["party_file"],
    )  # fmt: skip
    vote_types = _vote_types(code)
    stems = [v["stem"] for v in vote_types]
    candidates = _candidates(rows, stems)
    has_against = any(r["against"] is not None for r in rows)
    by_county: dict[str, list[dict]] = {}
    for r in rows:
        by_county.setdefault(r["county"], []).append(r)
    county_rows = [
        _county_row(county, by_county[county], candidates, has_against)
        for county in COUNTY_CODES.values()
        if county in by_county
    ]
    sources = {r["county"]: r["source_id"] for r in rows}
    return {
        "election": _election_record(code),
        **{k: found[k] for k in ("slug", "title", "office", "district", "party_file", "scope")},
        "party_label": found["party_label"],
        "scope_label": SCOPE_LABELS[found["scope"]],
        "vote_types": vote_types,
        "candidates": candidates,
        "total": sum(c["total"] for c in candidates),
        "counties_reporting": len(county_rows),
        "has_against": has_against,
        "county_rows": county_rows,
        "source_ids": [sources[c["county"]] for c in county_rows],
    }


# --- counties ---------------------------------------------------------------------------


def _counties_with_rows(conn: sqlite3.Connection, code: str) -> list[str]:
    have = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT county FROM results_county WHERE election = ?", (code,)
        )
    }
    return [name for name in COUNTY_CODES.values() if name in have]


def county_slugs(conn: sqlite3.Connection, code: str) -> list[str]:
    return [slugify(c) for c in _counties_with_rows(conn, code)]


def county_list(conn: sqlite3.Connection, code: str) -> dict | None:
    """The county index: each county with its contest count and the party files it has."""
    if code not in RESULTS_ELECTIONS:
        return None
    counties = _counties_with_rows(conn, code)
    if not counties:
        return None
    index = conn.execute(
        f"""SELECT r.county, r.party_file, COUNT(DISTINCT r.office || char(31) || r.district)
            FROM results_county r WHERE r.election = ? AND {_DEDUP}
            GROUP BY r.county, r.party_file""",
        (code,),
    ).fetchall()
    out = []
    for county in counties:
        mine = {pf: n for c, pf, n in index if c == county}
        out.append(
            {
                "county": county,
                "slug": slugify(county),
                "contests": sum(mine.values()),
                "party_files": [pf for pf in PARTY_FILE_ORDER if pf in mine],
            }
        )
    return {"election": _election_record(code), "counties": out}


def county_page(conn: sqlite3.Connection, code: str, county_slug: str) -> dict | None:
    """Every contest on one county's ballot in the SBE's order, grouped by party file."""
    county = _COUNTY_SLUGS.get(county_slug)
    if code not in RESULTS_ELECTIONS or county is None:
        return None
    rows = _fetch(conn, code, county=county)
    if not rows:
        return None
    vote_types = _vote_types(code)
    stems = [v["stem"] for v in vote_types]
    page_info = {(c["office"], c["district"], c["party_file"]): c for c in _pages(conn, code)}
    grouped: dict[tuple, list[dict]] = {}
    for r in rows:  # dicts keep the SBE's order of first appearance
        grouped.setdefault((r["party_file"], r["office"], r["district"]), []).append(r)
    by_party: dict[str, list[dict]] = {}
    used: set[str] = set()
    for (party_file, office, district), contest_rows in grouped.items():
        slug = contest_slug(office, district, party_file)
        anchor, n = slug, 1
        while anchor in used:  # two local contests can slug alike ("DEM Cent Comm at Large")
            n += 1
            anchor = f"{slug}-{n}"
        used.add(anchor)
        page = page_info.get((office, district, party_file))
        candidates = _candidates(contest_rows, stems)
        has_against = any(r["against"] is not None for r in contest_rows)
        leader, tied = (None, []) if has_against else _top([c["total"] for c in candidates])
        by_party.setdefault(party_file, []).append(
            {
                "title": contest_title(office, district, party_file),
                "office": office,
                "district": district,
                "party_file": party_file,
                "slug": slug,
                "anchor": anchor,
                "page_slug": page["slug"] if page else None,
                # the SBE's winner flag is the contest-wide result: for a contest with a page
                # it is the statewide / district winner, for a local one it is this county's
                "scope": page["scope"] if page else "local",
                "candidates": candidates,
                "has_against": has_against,
                "leader": leader,
                "tied": tied,
            }
        )
    groups = [
        {"party_file": pf, "label": PARTY_LABELS[pf], "contests": by_party[pf]}
        for pf in PARTY_FILE_ORDER
        if pf in by_party
    ]
    return {
        "election": _election_record(code),
        "county": county,
        "slug": county_slug,
        "vote_types": vote_types,
        "groups": groups,
        "source_ids": sorted({r["source_id"] for r in rows}),
    }
