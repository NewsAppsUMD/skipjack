"""A county's results over time: every election since 2010 on one page's worth of data.

Design: docs/plans/2026-11-07-results-trends-design.md. Everything here is built from the
county pages of ``skipjack.web.results`` (certified) and ``skipjack.web.results_unofficial``
(the live count), with the same dispatch as the routes, so the double-count rule, write-ins,
retention contests and the unofficial election come free. Contests are keyed by the office
crosswalk (``skipjack.web.offices``), so an office keeps one series while the board's spelling
of it drifts.

Shapes: a county's ``county_trends`` carries the elections (oldest first), one SERIES per
(office key, district) the county has ever voted on, grouped by level, and a ``summary`` of
the top of the ticket, the primary electorate, the gap between county offices and the ticket,
the county's disagreements with the state and a wall of winners. ``counties_index`` is a row
per county for the index page.
"""

from __future__ import annotations

import re
import sqlite3

from skipjack.pipeline.results import COUNTY_CODES
from skipjack.web import offices, ticket
from skipjack.web import results as qr
from skipjack.web import results_unofficial as qu
from skipjack.web.results import _natural, _top
from skipjack.web.urls import slugify

# The board's unofficial pages spell a district "District 6" / "District 1A" / "District A"
# where the certified files have "6" / "01A" / "A" (a numbered-and-lettered district zero-padded
# to two digits; Harford's council and board seats are letters alone).
_UNOFFICIAL_DISTRICT = re.compile(r"District (?:(\d+)([A-Z]?)|([A-Z]))")
# County offices with numbered districts and no map table: a break is marked where the set of
# districts the county's file lists changes between generals.
DATA_DRIVEN_BREAKS = ("county_council", "county_commissioner", "board_of_education", "city_council")
_SHOWN = set(offices.LEVELS)
# Display order within a level is the crosswalk's declaration order.
_OFFICE_ORDER = {key: i for i, key in enumerate(offices.OFFICES)}


def _queries(code: str):
    return qu if qu.is_unofficial(code) else qr


def _office_key(office: str, scope: str) -> str | None:
    """The canonical office key for a board spelling (certified or unofficial) and the
    contest's scope, or None for a spelling the crosswalk does not know."""
    try:
        return offices.canonical(office, scope).key
    except KeyError:
        return None


def _district(district: str) -> str:
    """A district in the certified files' spelling, whichever track it came from."""
    m = _UNOFFICIAL_DISTRICT.fullmatch(district)
    if m is None:
        return district
    n, letter, lettered = m.groups()
    if lettered:
        return lettered
    return f"{int(n):02d}{letter}" if letter else n


def elections(conn: sqlite3.Connection) -> list[dict]:
    """Every election the results pages list, oldest first (a primary before its year's
    general), each with its ``cycle`` and whether it is the unofficial count."""
    found = [
        {
            **e,
            "cycle": "presidential" if e["year"] % 4 == 0 else "gubernatorial",
            "unofficial": bool(e.get("unofficial")),
        }
        for e in qu.site_elections(conn)
    ]
    return sorted(found, key=lambda e: (e["year"], e["kind"] == "general"))


def county_slugs(conn: sqlite3.Connection) -> list[str]:
    """Counties with any certified results row (all 24 have one; the unofficial count is not
    consulted), in the board's order."""
    have = {r[0] for r in conn.execute("SELECT DISTINCT county FROM results_county")}
    return [slugify(name) for name in COUNTY_CODES.values() if name in have]


# --- points ------------------------------------------------------------------------------


def _pct(n: int, total: int) -> float | None:
    # Every figure is rounded once here; margins, gaps and leans are differences of the rounded
    # shares, so the page's arithmetic adds up as printed.
    return round(100 * n / total, 1) if total else None


def _party_sum(named: list[dict], party: str) -> dict | None:
    mine = [c for c in named if c["party"] == party]
    if not mine:
        return None
    return {"name": ", ".join(c["name"] for c in mine), "votes": sum(c["total"] or 0 for c in mine)}


def _leader_party(contest: dict) -> str | None:
    """The party of the candidate with the most votes in the county, or None in a multi-seat
    contest, a tie or a contest with no votes."""
    if contest["has_against"] or contest["seats"] > 1:
        return None
    leader, _tied = _top([c["total"] for c in contest["candidates"]])
    return contest["candidates"][leader]["party"] if leader is not None else None


def _hrefs(election: dict, county_slug: str, contest: dict) -> tuple[str, str | None]:
    href = f"/results/{election['slug']}/county/{county_slug}/#{contest['anchor']}"
    page = contest["page_slug"]
    return href, f"/results/{election['slug']}/{page}/" if page else None


def _gpoint(election: dict, county_slug: str, contest: dict, key: str) -> dict:
    candidates = contest["candidates"]
    named = [c for c in candidates if not c["write_in"]]
    total = sum(c["total"] or 0 for c in candidates)
    dem, rep = _party_sum(named, "DEM"), _party_sum(named, "REP")
    for side in (dem, rep):
        if side:
            side["share"] = _pct(side["votes"], total)
    other = total - (dem["votes"] if dem else 0) - (rep["votes"] if rep else 0)
    contested = dem is not None and rep is not None
    margin = _pct(dem["votes"] - rep["votes"], total) if contested else None
    winners = [{"name": c["name"], "party": c["party"]} for c in candidates if c["winner"]]
    leader_party = _leader_party(contest)
    matched = None
    if contest["scope"] == "statewide" and winners and leader_party is not None:
        matched = leader_party == winners[0]["party"]
    href, contest_href = _hrefs(election, county_slug, contest)
    return {
        "election": election["code"],
        "year": election["year"],
        "unofficial": election["unofficial"],
        "era": offices.era(key, election["year"]),
        "seats": contest["seats"],
        "contested": contested,
        "total": total,
        "dem": dem,
        "rep": rep,
        "other_share": _pct(other, total),
        "margin": margin,
        "winners": winners,
        "leader_party": leader_party,
        "matched": matched,
        "href": href,
        "contest_href": contest_href,
    }


def _ppoint(election: dict, county_slug: str, contest: dict) -> dict:
    candidates = contest["candidates"]
    named = [c for c in candidates if not c["write_in"]]
    total = sum(c["total"] or 0 for c in candidates)
    flagged = sorted((c for c in candidates if c["winner"]), key=lambda c: -(c["total"] or 0))
    winner = None
    if flagged:
        w = flagged[0]
        winner = {"name": w["name"], "votes": w["total"], "share": _pct(w["total"] or 0, total)}
    href, _ = _hrefs(election, county_slug, contest)
    return {
        "election": election["code"],
        "year": election["year"],
        "unofficial": election["unofficial"],
        "party_file": contest["party_file"],
        "seats": contest["seats"],
        "candidates": len(named),
        "contested": len(named) > contest["seats"],
        "total": total,
        "winner": winner,
        "href": href,
    }


# --- series ------------------------------------------------------------------------------


def _shown_office(contest: dict) -> offices.Office | None:
    """The office of a contest the trends page tracks, or None (judges, municipal, party and
    convention offices; retention questions)."""
    if contest["has_against"]:
        return None
    key = _office_key(contest["office"], contest["scope"])
    if key is None:
        return None
    office = offices.OFFICES[key]
    return office if office.level in _SHOWN else None


def _series_title(office: offices.Office, district: str) -> str:
    title = office.title
    if district:
        title += f", District {district}" if len(district) <= 3 else f", {district}"
    return title


def _new_series(office: offices.Office, district: str, generals: list) -> dict:
    return {
        "key": office.key,
        "level": office.level,
        "title": _series_title(office, district),
        "district": district,
        "id": f"{office.key}-{slugify(district)}" if district else office.key,
        "districted": office.districted,
        "breaks": [],
        "general": [None] * len(generals),
        "primary": {},
    }


def _series_order(series: dict) -> tuple:
    return _OFFICE_ORDER[series["key"]], _natural(series["district"])


def _contests(page: dict, party_file: str | None = None) -> list[dict]:
    return [
        c
        for g in page["groups"]
        if party_file is None or g["party_file"] == party_file
        for c in g["contests"]
    ]


def _breaks(series: dict, generals: list[dict], by_key: dict[str, list[dict]]) -> list[int]:
    """Where a districted series' lines were redrawn: the office's map table where it has one,
    else the generals where the county's set of districts for the office differs from the
    previous general of the same cycle (a board elected in staggered classes lists different
    seats in presidential and gubernatorial years without any redrawing)."""
    key = series["key"]
    if key in offices.ERAS:
        years = [e["year"] for e, p in zip(generals, series["general"], strict=True) if p]
        if not years:
            return []
        return [s for s in offices.ERAS[key] if years[0] < s <= years[-1]]
    if key not in DATA_DRIVEN_BREAKS:
        return []
    out: list[int] = []
    previous: dict[str, set[str]] = {}
    for i, e in enumerate(generals):
        districts = {s["district"] for s in by_key[key] if s["general"][i]}
        if not districts:
            continue  # not on this general's ballot
        if e["cycle"] in previous and districts != previous[e["cycle"]]:
            out.append(e["year"])
        previous[e["cycle"]] = districts
    return out


# --- the summary -------------------------------------------------------------------------


def _statewide_margin(conn: sqlite3.Connection, code: str, slug: str, cache: dict) -> float | None:
    """Dem share minus Rep share of a contest's statewide totals, None when not contested."""
    if (code, slug) not in cache:
        c = _queries(code).contest(conn, code, slug)
        margin = None
        if c:
            named = [x for x in c["candidates"] if not x["write_in"]]
            dem, rep = _party_sum(named, "DEM"), _party_sum(named, "REP")
            if dem and rep:
                margin = _pct(dem["votes"] - rep["votes"], c["total"])
        cache[(code, slug)] = margin
    return cache[(code, slug)]


def _top_of_ticket(
    conn: sqlite3.Connection, election: dict, contests: list[dict], points: dict, cache: dict
) -> dict | None:
    """The first contest's GPOINT (from ``points``, which maps a contest's anchor to its
    (series, GPOINT)) with the statewide margin and the county's lean from it, or None when
    the ballot does not open with the president or the governor."""
    if not contests or contests[0]["office"] not in ticket.TOP_OF_TICKET:
        return None
    top = contests[0]
    series, point = points[top["anchor"]]  # the ticket's offices are always shown series
    statewide = None
    if top["page_slug"]:
        statewide = _statewide_margin(conn, election["code"], top["page_slug"], cache)
    lean = None
    if point["margin"] is not None and statewide is not None:
        lean = round(point["margin"] - statewide, 1)
    return {
        **point,
        "office": series["key"],
        "title": ticket.TOP_LABELS[top["office"]],
        "statewide_margin": statewide,
        "lean": lean,
    }


def _ticket_gap(top: dict | None, contests: list[dict], points: dict) -> dict | None:
    """How far each countywide, contested county office's Dem share ran from the top of the
    ticket's. ``points`` maps a contest's anchor to (series, GPOINT)."""
    if top is None or top["dem"] is None or top["dem"]["share"] is None:
        return None
    top_share = top["dem"]["share"]
    flags = ticket.countywide(contests, top["total"])
    rows = []
    for contest, whole in zip(contests[1:], flags[1:], strict=True):
        found = points.get(contest["anchor"])
        if not whole or found is None:
            continue
        series, point = found
        if series["level"] != "county" or not point["contested"]:
            continue
        share = point["dem"]["share"]
        if share is None:
            continue
        rows.append(
            {
                "id": series["id"],
                "title": series["title"],
                "dem_share": share,
                "gap": round(share - top_share, 1),
            }
        )
    mean = round(sum(r["gap"] for r in rows) / len(rows), 1) if rows else None
    return {
        "top_title": top["title"],
        "top_dem_share": top_share,
        "offices": rows,
        "mean_gap": mean,
        "compared": len(rows),
    }


PRIMARY_LABELS = {
    "dem": "Democratic primary",
    "rep": "Republican primary",
    "non": "Nonpartisan primary",
}


def _primary_disagreements(election: dict, county_slug: str, contests: list[dict]) -> list[dict]:
    """Statewide primary contests where the county's leader is not the flagged nominee, each
    titled by the crosswalk with the party's primary named separately."""
    out = []
    for contest in contests:
        office = _shown_office(contest)
        if contest["scope"] != "statewide" or office is None:
            continue
        leader = contest["leader"]
        winners = [c for c in contest["candidates"] if c["winner"]]
        if leader is None or not winners or contest["candidates"][leader]["winner"]:
            continue
        led = contest["candidates"][leader]
        href, _ = _hrefs(election, county_slug, contest)
        out.append(
            {
                "election": election["code"],
                "year": election["year"],
                "kind": "primary",
                "title": _series_title(office, _district(contest["district"])),
                "party_label": PRIMARY_LABELS.get(contest["party_file"], contest["party_file"]),
                "county_leader": {"name": led["name"], "party": led["party"]},
                "statewide_winner": {"name": winners[0]["name"], "party": winners[0]["party"]},
                "href": href,
            }
        )
    return out


def _general_disagreements(election: dict, contests: list[dict], points: dict) -> list[dict]:
    """Statewide general contests where the county's leader is not the flagged winner's
    party. ``points`` maps a contest's anchor to (series, GPOINT)."""
    out = []
    for contest in contests:
        found = points.get(contest["anchor"])
        if found is None or found[1]["matched"] is not False:
            continue
        series, point = found
        leader, _ = _top([c["total"] for c in contest["candidates"]])
        led = contest["candidates"][leader]
        out.append(
            {
                "election": election["code"],
                "year": election["year"],
                "kind": "general",
                "title": series["title"],
                "party_label": None,
                "county_leader": {"name": led["name"], "party": led["party"]},
                "statewide_winner": point["winners"][0],
                "href": point["href"],
            }
        )
    return out


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _name_key(name: str) -> tuple[str, str] | None:
    tokens = [t for t in re.sub(r"[^\w\s]", "", name.lower()).split() if t not in _NAME_SUFFIXES]
    return (tokens[0], tokens[-1]) if len(tokens) > 1 else None


def _same_person(a: str, b: str) -> bool:
    """Whether two spellings name one candidate: the first and last tokens match, case and
    punctuation aside, suffixes (Jr., Sr., II, III, IV) ignored, so a middle initial that
    comes and goes between elections does not split a run. A one-token name never matches."""
    ka, kb = _name_key(a), _name_key(b)
    return ka is not None and ka == kb


def _winners_order(w: dict) -> tuple:
    return (w["party"], _name_key(w["name"]) or ("", w["name"].lower()))


def _same_winners(a: list[dict], b: list[dict]) -> bool:
    """Whether two generals' winner sets match, whatever the ballot order (a multi-seat
    contest's winners come in the order the file lists them, which rotates)."""
    a, b = sorted(a, key=_winners_order), sorted(b, key=_winners_order)
    return len(a) == len(b) and all(
        x["party"] == y["party"] and _same_person(x["name"], y["name"])
        for x, y in zip(a, b, strict=True)
    )


def _winners_wall(all_series: list[dict], generals: list[dict]) -> list[dict]:
    """Per series, its general winners in order, consecutive generals with the same winner
    (or, for a multi-seat contest, the same set of winners) collapsed into one run, shown
    under the latest spelling of the name (``_same_person``)."""
    wall = []
    for s in all_series:
        runs: list[dict] = []
        for e, p in zip(generals, s["general"], strict=True):
            if not p or not p["winners"]:
                continue
            name = ", ".join(w["name"] for w in p["winners"])
            party = ", ".join(w["party"] for w in p["winners"])
            if runs and _same_winners(runs[-1]["winners"], p["winners"]):
                runs[-1]["years"].append(e["year"])
                runs[-1]["name"], runs[-1]["winners"] = name, p["winners"]
            else:
                runs.append(
                    {"name": name, "party": party, "years": [e["year"]], "winners": p["winners"]}
                )
        for run in runs:
            del run["winners"]
        if runs:
            wall.append({"id": s["id"], "title": s["title"], "level": s["level"], "runs": runs})
    return wall


def _primary_electorate(page: dict) -> dict:
    sides = {}
    for pf in ("dem", "rep"):
        first = next((c for c in _contests(page, pf)), None)
        sides[pf] = (
            {"title": first["title"], "total": sum(c["total"] or 0 for c in first["candidates"])}
            if first
            else None
        )
    both = sides["dem"] and sides["rep"]
    share = (
        _pct(sides["dem"]["total"], sides["dem"]["total"] + sides["rep"]["total"]) if both else None
    )
    return {"dem": sides["dem"], "rep": sides["rep"], "dem_share_of_two": share}


# --- the county --------------------------------------------------------------------------


def _general_points(
    conn: sqlite3.Connection,
    generals: list[dict],
    pages: dict[str, dict | None],
    county_slug: str,
    find,
    cache: dict,
) -> tuple[list, list, list]:
    """Fill each series' general points through ``find`` (a contest -> its series, or None)
    and return, per general, the top of the ticket and the ticket gap, plus the county's
    disagreements with the state in generals."""
    top, ticket_gap, disagreements = [], [], []
    for i, e in enumerate(generals):
        page = pages[e["code"]]
        if page is None:
            top.append(None)
            ticket_gap.append(None)
            continue
        contests = _contests(page, "all")
        points: dict[str, tuple] = {}
        for contest in contests:
            s = find(contest)
            if s is None:
                continue
            point = _gpoint(e, county_slug, contest, s["key"])
            s["general"][i] = point
            points[contest["anchor"]] = (s, point)
        t = _top_of_ticket(conn, e, contests, points, cache)
        top.append(t)
        ticket_gap.append(_ticket_gap(t, contests, points))
        disagreements += _general_disagreements(e, contests, points)
    return top, ticket_gap, disagreements


def _primary_points(
    primaries: list[dict], pages: dict[str, dict | None], county_slug: str, find
) -> tuple[list, list]:
    """Fill each series' primary points per party file through ``find`` and return, per
    primary, the electorate, plus the county's disagreements with the state in primaries."""
    electorate, disagreements = [], []
    for i, e in enumerate(primaries):
        page = pages[e["code"]]
        if page is None:
            electorate.append(None)
            continue
        contests = _contests(page)
        for contest in contests:
            s = find(contest)
            if s is None:
                continue
            pts = s["primary"].setdefault(contest["party_file"], [None] * len(primaries))
            pts[i] = _ppoint(e, county_slug, contest)
        electorate.append(_primary_electorate(page))
        disagreements += _primary_disagreements(e, county_slug, contests)
    return electorate, disagreements


def county_trends(
    conn: sqlite3.Connection, county_slug: str, cache: dict | None = None
) -> dict | None:
    """Everything the county's results-over-time page shows, or None for a county the results
    do not know. ``cache`` memoizes statewide margins by (election, contest slug) across calls:
    ``counties_index`` passes one dict so each statewide contest is summed once, not 24 times."""
    county = qr._COUNTY_SLUGS.get(county_slug)
    if county is None:
        return None
    cache = {} if cache is None else cache
    all_elections = elections(conn)
    pages = {
        e["code"]: _queries(e["code"]).county_page(conn, e["code"], county_slug)
        for e in all_elections
    }
    if not any(pages.values()):
        return None
    generals = [e for e in all_elections if e["kind"] == "general"]
    primaries = [e for e in all_elections if e["kind"] == "primary"]
    series: dict[tuple, dict] = {}

    def find(contest: dict) -> dict | None:
        office = _shown_office(contest)
        if office is None:
            return None
        k = (office.key, _district(contest["district"]))
        if k not in series:
            series[k] = _new_series(office, k[1], generals)
        return series[k]

    source_ids = [sid for p in pages.values() if p for sid in p["source_ids"]]
    top, ticket_gap, disagreements = _general_points(
        conn, generals, pages, county_slug, find, cache
    )
    primary_electorate, primary_disagreements = _primary_points(primaries, pages, county_slug, find)
    disagreements += primary_disagreements

    ordered = sorted(series.values(), key=_series_order)
    by_key: dict[str, list[dict]] = {}
    for s in ordered:
        by_key.setdefault(s["key"], []).append(s)
    for s in ordered:
        s["breaks"] = _breaks(s, generals, by_key)
    levels = [
        {
            "level": level,
            "label": offices.LEVEL_LABELS[level],
            "offices": [s for s in ordered if s["level"] == level],
        }
        for level in offices.LEVELS
    ]
    # the disagreements are collected oldest election first, generals after their primaries
    disagreements.sort(key=lambda d: (-d["year"], d["kind"] == "primary"))
    summary = {
        "top": top,
        "primary_electorate": primary_electorate,
        "ticket_gap": ticket_gap,
        "disagreements": disagreements,
        "winners_wall": _winners_wall(ordered, generals),
    }
    return {
        "county": county,
        "slug": county_slug,
        "elections": all_elections,
        "generals": generals,
        "primaries": primaries,
        "levels": levels,
        "summary": summary,
        "source_ids": list(dict.fromkeys(source_ids)),
    }


def counties_index(conn: sqlite3.Connection) -> list[dict]:
    """A row per county for /results/county/: its latest and first top-of-ticket points, the
    shift between their margins and a margin per general."""
    cache: dict = {}
    rows = []
    for slug in county_slugs(conn):
        t = county_trends(conn, slug, cache)
        if t is None:
            continue
        tops = [p for p in t["summary"]["top"] if p]
        latest = tops[-1] if tops else None
        first = tops[0] if tops else None
        shift = None
        if latest and first and latest["margin"] is not None and first["margin"] is not None:
            shift = round(latest["margin"] - first["margin"], 1)
        rows.append(
            {
                "county": t["county"],
                "slug": slug,
                "latest": latest,
                "first": first,
                "shift": shift,
                "spark": [p["margin"] if p else None for p in t["summary"]["top"]],
                "generals": len(t["generals"]),
                "primaries": len(t["primaries"]),
            }
        )
    return rows
