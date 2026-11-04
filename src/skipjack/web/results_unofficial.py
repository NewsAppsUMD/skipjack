"""The board's unofficial running count for the site, read from results_unofficial.

Design: docs/plans/2026-11-04-unofficial-results-design.md. Every function here returns the
same shapes as ``skipjack.web.results`` (the certified results), so the results templates
render either. The differences:

- only the newest snapshot is read, and only while the election is live (``is_live``: its
  certified results are not published);
- a vote type the board has not reported is None ("not yet counted"), never 0. A statewide or
  per-candidate sum is None only when every county's figure is None;
- no candidate carries a winner flag: the board marks none while it counts, and we compute none;
- a judge's continuance (Yes / No rows on the board's pages) becomes one candidate with the
  Yes votes as its total and the No votes as ``against``, the shape of a certified retention;
- ballot questions are not on these pages yet (Task 5 of the plan).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS
from skipjack.pipeline.results_unofficial import (
    CANVASSES,
    UNOFFICIAL_RESULTS,
    VOTE_TYPES,
    is_live,
)
from skipjack.web import results as certified
from skipjack.web.results import (
    PARTY_LABELS,
    SCOPE_LABELS,
    SCOPES,
    _county_row,
    _election_record,
    _natural,
    _top,
    contest_slug,
    contest_title,
)
from skipjack.web.urls import slugify

# The board's office names on the unofficial pages, by the hub's scopes. Anything not listed is
# local: it appears on its county's page only. A "statewide" office that is not on all 24
# counties' ballots is demoted to local, as for certified results.
STATEWIDE_OFFICES = (
    "Governor / Lt. Governor",
    "Comptroller",
    "Attorney General",
    "Judge, Appellate Court of Maryland At Large",
)
OFFICE_SCOPES = (
    {o: "statewide" for o in STATEWIDE_OFFICES}
    | {"Representative in Congress": "congress"}
    | {"State Senator": "senate"}
    | {"House of Delegates": "delegates"}
)
OFFICE_TITLES = {
    "Judge, Appellate Court of Maryland At Large": "Appellate Court judges, at large",
}
# The board spells parties out on these pages; the certified files (and the maps) use codes.
PARTY_CODES = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Green": "GRN",
    "Working Class Party": "WCP",
    "Unaffiliated": "UNA",
    "Libertarian": "LIB",
}
STEMS = [stem for _, stem in VOTE_TYPES]
CANVASS_LABELS = {
    "EV": "Early voting",
    "EN": "Election night",
    "MIB 1": "Mail-in, first count",
    "Prov": "Provisional",
    "MIB 2": "Mail-in, second count",
}
_STATUS_COLUMNS = ("ev", "en", "mib1", "prov", "mib2")
N_COUNTIES = len(COUNTY_CODES)
_COUNTY_SLUGS = {slugify(name): name for name in COUNTY_CODES.values()}


def _title(office: str, district: str) -> str:
    title = OFFICE_TITLES.get(office, office)
    return contest_title(title, district, "all") if district else title


def latest_snapshot(conn: sqlite3.Connection, code: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot) FROM results_unofficial WHERE election = ?", (code,)
    ).fetchone()
    return row[0] if row else None


def _as_of(refreshed: str) -> str:
    """'11/04/2026 04:02:22 PM' -> '4:02 p.m. on Nov. 4, 2026' (AP style; the year last, so a
    sentence never ends on "p.m.")."""
    t = datetime.strptime(refreshed, "%m/%d/%Y %I:%M:%S %p")
    month = t.strftime("%B")
    month = month if len(month) <= 5 else month[:3] + "."
    hour = t.strftime("%I").lstrip("0")
    return f"{hour}:{t:%M} {'a.m.' if t.hour < 12 else 'p.m.'} on {month} {t.day}, {t.year}"


def live_elections(conn: sqlite3.Connection) -> list[dict]:
    """The live elections that have a parsed snapshot, as ``results._election_record`` dicts
    marked unofficial."""
    try:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT election FROM results_unofficial")]
    except sqlite3.OperationalError:  # an older database without the table
        return []
    return [{**_election_record(c), "unofficial": True} for c in codes if is_live(c)]


def site_elections(conn: sqlite3.Connection) -> list[dict]:
    """Every election the results pages list, newest first: the certified ones, and an
    election being counted from its unofficial count (marked ``unofficial``) while its certified
    results are unpublished."""
    live = live_elections(conn)
    codes = {e["code"] for e in live}
    found = [e for e in certified.elections(conn) if e["code"] not in codes] + live
    return sorted(found, key=lambda e: (-e["year"], e["kind"] != "general"))


def contest_slugs_for(conn: sqlite3.Connection, code: str) -> list[str]:
    """The contest page slugs of any election the pages list (the static site reads this)."""
    return contest_slugs(conn, code) if is_unofficial(code) else certified.contest_slugs(conn, code)


def county_slugs_for(conn: sqlite3.Connection, code: str) -> list[str]:
    return county_slugs(conn, code) if is_unofficial(code) else certified.county_slugs(conn, code)


def unofficial_context(conn: sqlite3.Connection, code: str) -> dict | None:
    """What every unofficial page shows above its figures: the board's refreshed time, each
    county's canvasses and the board's results page."""
    snap = latest_snapshot(conn, code)
    if snap is None or not is_live(code):
        return None
    refreshed = conn.execute(
        "SELECT last_refreshed FROM results_unofficial WHERE election = ? AND snapshot = ? LIMIT 1",
        (code, snap),
    ).fetchone()[0]
    status = conn.execute(
        f"""SELECT county, last_updated, precincts_reported, precincts_total,
                   {", ".join(_STATUS_COLUMNS)}
            FROM results_unofficial_status WHERE election = ? AND snapshot = ?""",
        (code, snap),
    ).fetchall()
    by_county = {r[0]: r for r in status}
    canvass = [
        {
            "county": county,
            "slug": slugify(county),
            "last_updated": by_county[county][1],
            "precincts_reported": by_county[county][2],
            "precincts_total": by_county[county][3],
            "states": list(by_county[county][4:]),
        }
        for county in COUNTY_CODES.values()
        if county in by_county
    ]
    reported = {name: sum(1 for c in canvass if c["states"][i] != "not_reported")
                for i, name in enumerate(CANVASSES)}  # fmt: skip
    return {
        "snapshot": snap,
        "refreshed": refreshed,
        "as_of": _as_of(refreshed),
        "canvasses": [
            {"key": k, "label": CANVASS_LABELS[k], "counties": reported[k]} for k in CANVASSES
        ],  # fmt: skip
        "canvass": canvass,
        "board_url": f"{UNOFFICIAL_RESULTS[code].base_url}/index.html",
    }


def _vote_types() -> list[dict]:
    return [{"stem": stem, "label": label} for label, stem in VOTE_TYPES]


# --- reading rows ---------------------------------------------------------------------------


def _fetch(
    conn: sqlite3.Connection,
    code: str,
    *,
    office: str | None = None,
    district: str | None = None,
    county: str | None = None,
) -> list[dict]:
    """The newest snapshot's candidate rows, in the board's order, in the certified rows'
    shape; a judge's Yes and No rows become one row with ``against``. Questions are left out."""
    snap = latest_snapshot(conn, code)
    if snap is None:
        return []
    where = "r.election = ? AND r.snapshot = ? AND r.kind <> 'question'"
    args: list = [code, snap]
    for column, value in (("office", office), ("district", district), ("county", county)):
        if value is not None:
            where += f" AND r.{column} = ?"
            args.append(value)
    rows = conn.execute(
        f"""SELECT r.id, r.source_id, r.county, r.office, r.district, r.kind,
                   r.candidate_heading, r.candidate, r.choice, r.party, r.write_in,
                   r.total_votes, r.vote_for
            FROM results_unofficial r WHERE {where} ORDER BY r.id""",
        args,
    ).fetchall()
    votes: dict[int, dict] = {}
    for result_id, stem, n in conn.execute(
        f"""SELECT v.result_id, v.vote_type, v.votes FROM results_unofficial_votes v
            JOIN results_unofficial r ON r.id = v.result_id WHERE {where}""",
        args,
    ):
        votes.setdefault(result_id, {})[stem] = n
    out: list[dict] = []
    retention: dict[tuple, dict] = {}
    for rid, sid, cty, off, dist, kind, judge, cand, choice, party, wi, total, vote_for in rows:
        if kind == "retention":
            key = (cty, off, dist, judge)
            row = retention.get(key)
            if row is None:
                row = retention[key] = {
                    "id": rid, "source_id": sid, "county": cty, "party_file": "all",
                    "office": off, "district": dist, "candidate": judge, "party": "",
                    "winner": False, "write_in": False, "total": 0, "against": 0,
                    "votes": dict.fromkeys(STEMS), "votes_against": dict.fromkeys(STEMS),
                    "vote_for": None,
                }  # fmt: skip
                out.append(row)
            which = "votes" if choice == "Yes" else "votes_against"
            row["total" if choice == "Yes" else "against"] += total
            row[which] = votes.get(rid, dict.fromkeys(STEMS))
            continue
        out.append(
            {
                "id": rid, "source_id": sid, "county": cty, "party_file": "all", "office": off,
                "district": dist, "candidate": cand, "party": PARTY_CODES.get(party, party),
                "winner": False, "write_in": bool(wi), "total": total, "against": None,
                "votes": votes.get(rid, dict.fromkeys(STEMS)), "votes_against": {},
                "vote_for": vote_for,
            }
        )  # fmt: skip
    return out


def _add(a: int | None, b: int | None) -> int | None:
    return a if b is None else (b if a is None else a + b)


def _candidates(rows: list[dict]) -> list[dict]:
    """One row per candidate summed over ``rows``, most votes first. A vote type stays None
    only where no row has a figure for it."""
    by_name: dict[tuple, dict] = {}
    for r in rows:
        c = by_name.setdefault(
            (r["candidate"], r["party"]),
            {"name": r["candidate"], "party": r["party"], "write_in": r["write_in"],
             "winner": False, "total": 0, "against": None, "votes": dict.fromkeys(STEMS),
             "first": r["id"]},
        )  # fmt: skip
        c["total"] += r["total"]
        if r["against"] is not None:
            c["against"] = (c["against"] or 0) + r["against"]
        for stem in STEMS:
            c["votes"][stem] = _add(c["votes"][stem], r["votes"].get(stem))
    out = sorted(by_name.values(), key=lambda c: (-c["total"], c["first"]))
    cast = sum(c["total"] for c in out)
    for c in out:
        base = c["total"] + c["against"] if c["against"] is not None else cast
        c["share"] = 100 * c["total"] / base if base else None
        del c["first"]
    return out


# --- contests -------------------------------------------------------------------------------


def _scope(office: str, n_counties: int) -> str:
    scope = OFFICE_SCOPES.get(office, "local")
    return "local" if scope == "statewide" and n_counties < N_COUNTIES else scope


def _pages(conn: sqlite3.Connection, code: str) -> list[dict]:
    """The contests that get a page, in hub order: scope, the office's place in its scope,
    then district."""
    snap = latest_snapshot(conn, code)
    if snap is None:
        return []
    contests = []
    for office, district, n, vote_for in conn.execute(
        """SELECT office, district, COUNT(DISTINCT county), MAX(vote_for)
           FROM results_unofficial WHERE election = ? AND snapshot = ? AND kind <> 'question'
           GROUP BY office, district ORDER BY MIN(id)""",
        (code, snap),
    ):
        scope = _scope(office, n)
        if scope == "local":
            continue
        contests.append(
            {
                "office": office,
                "district": district,
                "party_file": "all",
                "counties": n,
                "scope": scope,
                "vote_for": vote_for,
                "slug": contest_slug(office, district, "all"),
                "title": _title(office, district),
                "party_label": PARTY_LABELS["all"],
            }
        )

    def key(c: dict) -> tuple:
        offices = STATEWIDE_OFFICES if c["scope"] == "statewide" else (c["office"],)
        return (SCOPES.index(c["scope"]), offices.index(c["office"]), _natural(c["district"]))

    return sorted(contests, key=key)


def contest_slugs(conn: sqlite3.Connection, code: str) -> list[str]:
    if not is_live(code):
        return []
    return [c["slug"] for c in _pages(conn, code)]


def _record(code: str) -> dict:
    return {**_election_record(code), "unofficial": True}


def hub(conn: sqlite3.Connection, code: str) -> dict | None:
    if not is_live(code):
        return None
    pages = _pages(conn, code)
    if not pages:
        return None
    wanted = {(c["office"], c["district"]): c for c in pages}
    rows_by_contest: dict[tuple, list[dict]] = {k: [] for k in wanted}
    for r in _fetch(conn, code):
        if (k := (r["office"], r["district"])) in rows_by_contest:
            rows_by_contest[k].append(r)
    groups = []
    for scope in SCOPES:
        summaries = []
        for k, c in wanted.items():
            if c["scope"] != scope:
                continue
            rows = rows_by_contest[k]
            candidates = _candidates(rows)
            has_against = any(r["against"] is not None for r in rows)
            summaries.append(
                {
                    **{x: c[x] for x in ("slug", "title", "office", "district", "party_file")},
                    "party_label": c["party_label"],
                    "counties_reporting": c["counties"],
                    "candidates": candidates,
                    "has_against": has_against,
                    # ahead in the count so far: as many as the contest elects, never a winner
                    "ahead": [] if has_against else candidates[: c["vote_for"] or 1],
                }
            )
        if summaries:
            groups.append({"scope": scope, "label": SCOPE_LABELS[scope], "contests": summaries})
    return {"election": _record(code), "groups": groups}


def contest(conn: sqlite3.Connection, code: str, slug: str) -> dict | None:
    if not is_live(code):
        return None
    found = next((c for c in _pages(conn, code) if c["slug"] == slug), None)
    if found is None:
        return None
    rows = _fetch(conn, code, office=found["office"], district=found["district"])
    candidates = _candidates(rows)
    has_against = any(r["against"] is not None for r in rows)
    by_county: dict[str, list[dict]] = {}
    for r in rows:
        by_county.setdefault(r["county"], []).append(r)
    multi_winner = (found["vote_for"] or 1) > 1
    county_rows = [
        _county_row(county, by_county[county], candidates, has_against, not multi_winner)
        for county in COUNTY_CODES.values()
        if county in by_county
    ]
    sources = {r["county"]: r["source_id"] for r in rows}
    return {
        "election": _record(code),
        **{k: found[k] for k in ("slug", "title", "office", "district", "party_file", "scope")},
        "party_label": found["party_label"],
        "scope_label": SCOPE_LABELS[found["scope"]],
        "vote_types": _vote_types(),
        "candidates": candidates,
        "total": sum(c["total"] for c in candidates),
        "counties_reporting": len(county_rows),
        "has_against": has_against,
        "multi_winner": multi_winner,
        "county_rows": county_rows,
        "source_ids": [sources[c["county"]] for c in county_rows],
    }


# --- counties -------------------------------------------------------------------------------


def _counties_with_rows(conn: sqlite3.Connection, code: str) -> list[str]:
    snap = latest_snapshot(conn, code)
    have = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT county FROM results_unofficial WHERE election = ? AND snapshot = ?",
            (code, snap),
        )
    }
    return [name for name in COUNTY_CODES.values() if name in have]


def county_slugs(conn: sqlite3.Connection, code: str) -> list[str]:
    if not is_live(code):
        return []
    return [slugify(c) for c in _counties_with_rows(conn, code)]


def county_list(conn: sqlite3.Connection, code: str) -> dict | None:
    if not is_live(code):
        return None
    counties = _counties_with_rows(conn, code)
    if not counties:
        return None
    snap = latest_snapshot(conn, code)
    counts = dict(
        conn.execute(
            """SELECT county, COUNT(DISTINCT office || char(31) || district || char(31)
                                    || candidate_heading)
               FROM results_unofficial WHERE election = ? AND snapshot = ? AND kind <> 'question'
               GROUP BY county""",
            (code, snap),
        ).fetchall()
    )
    out = [
        {"county": c, "slug": slugify(c), "contests": counts.get(c, 0), "party_files": ["all"]}
        for c in counties
    ]
    return {"election": _record(code), "counties": out}


def county_page(conn: sqlite3.Connection, code: str, county_slug: str) -> dict | None:
    county = _COUNTY_SLUGS.get(county_slug)
    if not is_live(code) or county is None:
        return None
    rows = _fetch(conn, code, county=county)
    if not rows:
        return None
    page_info = {(c["office"], c["district"]): c for c in _pages(conn, code)}
    grouped: dict[tuple, list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["office"], r["district"]), []).append(r)
    contests = []
    used: set[str] = set()
    for (office, district), contest_rows in grouped.items():
        slug = contest_slug(office, district, "all")
        anchor, n = slug, 1
        while anchor in used:
            n += 1
            anchor = f"{slug}-{n}"
        used.add(anchor)
        page = page_info.get((office, district))
        candidates = _candidates(contest_rows)
        has_against = any(r["against"] is not None for r in contest_rows)
        vote_for = max((r["vote_for"] or 1) for r in contest_rows)
        leader, tied = (None, [])
        if not has_against and vote_for <= 1:
            leader, tied = _top([c["total"] for c in candidates])
        contests.append(
            {
                "title": _title(office, district),
                "office": office,
                "district": district,
                "party_file": "all",
                "slug": slug,
                "anchor": anchor,
                "page_slug": page["slug"] if page else None,
                "scope": page["scope"] if page else "local",
                "candidates": candidates,
                "has_against": has_against,
                "leader": leader,
                "tied": tied,
            }
        )
    return {
        "election": _record(code),
        "county": county,
        "slug": county_slug,
        "vote_types": _vote_types(),
        "groups": [{"party_file": "all", "label": "", "contests": contests}],
        "source_ids": sorted({r["source_id"] for r in rows}),
    }


def election_source_ids(conn: sqlite3.Connection, code: str) -> list[str]:
    snap = latest_snapshot(conn, code)
    return [
        r[0]
        for r in conn.execute(
            "SELECT source_id FROM results_unofficial WHERE election = ? AND snapshot = ?"
            " GROUP BY source_id ORDER BY MIN(id)",
            (code, snap),
        )
    ]


def is_unofficial(code: str) -> bool:
    """Whether the site shows this election from the unofficial count."""
    return code in RESULTS_ELECTIONS and is_live(code)
