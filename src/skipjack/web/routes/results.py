"""The /results/ section: county-level results from the State Board's results CSVs.

Route order matters: ``/{slug}/county/...`` is registered before ``/{slug}/{contest_slug}/``
so the county pages are never read as a contest. No contest slug can be "county" either (they
all end in a party-file suffix, see ``results.contest_slug``).
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS
from skipjack.web import results as qr
from skipjack.web.db import get_db
from skipjack.web.maps import results_map

router = APIRouter(prefix="/results")

_SOURCE_ID = re.compile(r"-county-(\d\d)-(\w+)$")


def _code(conn: sqlite3.Connection, slug: str) -> str:
    """The election code for a slug, or a 404 when it has no results rows."""
    code = next((e["code"] for e in qr.elections(conn) if e["slug"] == slug), None)
    if code is None:
        raise HTTPException(status_code=404)
    return code


def source_links(conn: sqlite3.Connection, source_ids: list[str]) -> list[dict]:
    """The SBE CSV behind each source id, labelled by county and party file, in input order."""
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    urls = dict(
        conn.execute(
            f"SELECT source_id, source_url FROM sources WHERE source_id IN ({marks})", ids
        ).fetchall()
    )
    links = []
    for sid in ids:
        if sid not in urls:
            continue
        label = sid
        if m := _SOURCE_ID.search(sid):
            county = COUNTY_CODES.get(m.group(1), m.group(1))
            party = qr.PARTY_LABELS.get(m.group(2), "")
            label = f"{county}, {party}" if party else county
        links.append({"label": label, "url": urls[sid]})
    return links


def _election_sources(conn: sqlite3.Connection, code: str) -> list[dict]:
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_id FROM results_county WHERE election = ? ORDER BY source_id",
            (code,),
        )
    ]
    return source_links(conn, ids)


def mail_pair(vote_types: list[dict]) -> tuple[str, str] | None:
    """Labels of the two mail columns when an election's files have two (Mail-In Ballot 1 and
    2, By Mail and By Mail 2), else None. The one place the pages decide to explain them."""
    by_stem = {v["stem"]: v["label"] for v in vote_types}
    for first, second in (("mail_in_1", "mail_in_2"), ("by_mail", "by_mail_2")):
        if first in by_stem and second in by_stem:
            return by_stem[first], by_stem[second]
    return None


def _render(request: Request, template: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/")
async def index(request: Request):
    with get_db() as conn:
        elections = qr.elections(conn)
    years: dict[int, dict] = {}
    for e in elections:  # newest first already
        years.setdefault(e["year"], {"year": e["year"], "primary": None, "general": None})
        years[e["year"]][e["kind"]] = e
    data_page = RESULTS_ELECTIONS[elections[0]["code"]].base_url if elections else ""
    return _render(
        request, "results_index.html", {"years": list(years.values()), "data_page": data_page}
    )


@router.get("/{slug}/")
async def election(request: Request, slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = qr.hub(conn, code)
        if data is None:
            raise HTTPException(status_code=404)
        sources = _election_sources(conn, code)
    return _render(
        request,
        "results_election.html",
        {
            "election": data["election"],
            "groups": data["groups"],
            "sources": sources,
            "data_page": RESULTS_ELECTIONS[code].base_url,
        },
    )


@router.get("/{slug}/county/")
async def county_index(request: Request, slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = qr.county_list(conn, code)
        if data is None:
            raise HTTPException(status_code=404)
        sources = _election_sources(conn, code)
    return _render(
        request,
        "results_county_index.html",
        {
            "election": data["election"],
            "counties": data["counties"],
            "party_labels": qr.PARTY_LABELS,
            "sources": sources,
            "data_page": RESULTS_ELECTIONS[code].base_url,
        },
    )


@router.get("/{slug}/county/{county_slug}/")
async def county(request: Request, slug: str, county_slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = qr.county_page(conn, code, county_slug)
        if data is None:
            raise HTTPException(status_code=404)
        sources = source_links(conn, data["source_ids"])
    return _render(
        request,
        "results_county.html",
        {
            "election": data["election"],
            "county": data["county"],
            "vote_types": data["vote_types"],
            "mail_pair": mail_pair(data["vote_types"]),
            "groups": data["groups"],
            "sources": sources,
            "data_page": RESULTS_ELECTIONS[code].base_url,
        },
    )


@router.get("/{slug}/{contest_slug}/")
async def contest(request: Request, slug: str, contest_slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = qr.contest(conn, code, contest_slug) if contest_slug != "county" else None
        if data is None:
            raise HTTPException(status_code=404)
        sources = source_links(conn, data["source_ids"])
    return _render(
        request,
        "results_contest.html",
        {
            "contest": data,
            "election": data["election"],
            "county_maps": results_map(data),
            "sources": sources,
            "mail_pair": mail_pair(data["vote_types"]),
            "data_page": RESULTS_ELECTIONS[code].base_url,
        },
    )
