"""The /turnout/ section: each election's mail-in figures from the press-room reports."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.pipeline.turnout import ELECTIONS
from skipjack.web import turnout as qt
from skipjack.web.db import get_db

router = APIRouter(prefix="/turnout")


@router.get("/")
async def index(request: Request):
    with get_db() as conn:
        elections = qt.elections(conn)
    return request.app.state.templates.TemplateResponse(
        request, "turnout_index.html", {"elections": elections}
    )


@router.get("/{slug}/")
async def election(request: Request, slug: str):
    with get_db() as conn:
        available = {e["slug"]: e["code"] for e in qt.elections(conn)}
        code = available.get(slug)
        if code is None or code not in ELECTIONS:
            raise HTTPException(status_code=404)
        headline = qt.headline(conn, code)
        certified = ELECTIONS[code].certified
        dates: dict[str, str] = {}
        cert, reasons = {}, {}
        if certified:
            cert = qt.certified_headline(conn, code)
            dates = _certified_dates(cert)
            reasons = {
                family: qt.rejection_reason_rows(conn, code, family, as_of)
                for family, as_of in dates.items()
                if family in ("mail-voting", "provisional") and as_of
            }
            tiles = _certified_tiles(cert)
        else:
            tiles = _tiles(headline)
        # the page links only to the grains whose reports publish figures
        geographies = qt.publishable_geographies(conn, code)
        sources = qt.snapshot_sources(conn, code)
        chart = qt.chart_data(conn, code)
    return request.app.state.templates.TemplateResponse(
        request,
        "turnout_election.html",
        {
            "election": ELECTIONS[code],
            "headline": headline,
            "cert": cert,
            "tiles": tiles,
            "chart": chart,
            "sources": sources,
            "reasons": reasons,
            "dates": dates,
            "geographies": geographies,
        },
    )


_GEOGRAPHY_LABELS = {"county": "county", "congressional": "congressional district"}


@router.get("/{slug}/{geography}/")
async def election_geography(request: Request, slug: str, geography: str):
    """One election's table at one geography: the county page the election page used
    to carry, and the congressional-district page beside it."""
    if geography not in _GEOGRAPHY_LABELS:
        raise HTTPException(status_code=404)
    with get_db() as conn:
        available = {e["slug"]: e["code"] for e in qt.elections(conn)}
        code = available.get(slug)
        if code is None or code not in ELECTIONS:
            raise HTTPException(status_code=404)
        geographies = qt.publishable_geographies(conn, code)
        certified = ELECTIONS[code].certified
        if certified:
            cert = qt.certified_headline(conn, code)
            if geography == "county":
                dates = _certified_dates(cert)
                rows = qt.certified_county_rows(conn, code, dates)
            else:
                dates = {
                    f: a for f, a in cert["as_ofs"].items() if f in qt.CERTIFIED_PAGE_FAMILIES and a
                }
                rows = qt.certified_district_rows(conn, code, dates)
        else:
            headline = qt.headline(conn, code)
            sent_as_of = headline.get("sent-returned", {}).get("as_of")
            requests_as_of = headline.get("requests", {}).get("as_of")
            rows = (
                qt.county_rows(
                    conn, code, sent_as_of or requests_as_of, requests_as_of=requests_as_of
                )
                if geography == "county"
                else qt.district_rows(
                    conn, code, sent_as_of or requests_as_of, requests_as_of=requests_as_of
                )
            )
            dates: dict[str, str] = {}
            if sent_as_of:
                dates["sent-returned"] = sent_as_of
            if requests_as_of:
                dates["requests"] = requests_as_of
        sources = qt.snapshot_sources(conn, code)
        shown_dates = sorted({d for d in dates.values() if d})
    return request.app.state.templates.TemplateResponse(
        request,
        "turnout_geography.html",
        {
            "election": ELECTIONS[code],
            "geography": geography,
            "geography_label": _GEOGRAPHY_LABELS[geography],
            "certified": certified,
            "rows": rows,
            "sources": sources,
            # the page says which days it shows: distinct as-ofs, oldest last
            "shown_dates": shown_dates,
            # the election page links only to the grains with published figures and
            # this page's table drops the two request columns when no row has one
            "geographies": geographies,
            "show_requests": any(r.get("requests_total") is not None for r in rows),
        },
    )


def _certified_dates(cert: dict) -> dict[str, str]:
    """The as-of dates the certified page's tables read, per family."""
    return {
        family: as_of
        for family, as_of in cert["as_ofs"].items()
        if as_of
        and family in ("county-canvass", "votes-accepted", "mail-voting", "requests", "provisional")
    }


def _certified_tiles(cert: dict) -> list[dict]:
    """The certified headline's tiles: turnout (when the denominator files exist),
    ballots cast and counted, and the four modes."""
    tiles: list[dict] = []
    if cert.get("turnout"):
        tiles.append(
            {
                "label": (
                    "turnout — the board's own percentage, from its official turnout sheet"
                    if cert["turnout"].get("printed")
                    else "turnout — ballots cast over eligible voters, counted or not"
                ),
                "value_text": f"{cert['turnout']['value'] * 100:.1f}%",
                "value": None,
                "as_of": cert["turnout"]["as_of"],
            }
        )
    tiles.append(
        {
            "label": "ballots cast",
            "value": cert.get("cast"),
            "as_of": cert["as_ofs"].get("county-canvass", ""),
        }
    )
    if cert.get("counted") is not None:
        tiles.append(
            {
                "label": "votes counted in the canvass",
                "value": cert.get("counted"),
                "as_of": cert["as_ofs"].get("votes-accepted", ""),
            }
        )
    for mode, entry in cert.get("by_mode", {}).items():
        tiles.append(
            {
                "label": mode.replace("-", " "),
                "value": entry.get("cast"),
                "sub": f"{entry['share'] * 100:.1f}% of cast" if entry.get("share") else None,
                "as_of": cert["as_ofs"].get("county-canvass", ""),
            }
        )
    mail = cert.get("mail-voting")
    if mail and mail.get("sent"):
        tiles.append(
            {
                "label": "mail-in ballots received back",
                "value": mail.get("received"),
                "sub": (
                    f"{mail['accepted']:,} accepted, {mail['rejected']:,} rejected"
                    if mail.get("accepted") is not None and mail.get("rejected") is not None
                    else None
                ),
                "as_of": cert["as_ofs"].get("mail-voting", ""),
            }
        )
    if mail:
        tiles.append(
            {
                "label": "mail-in ballot requests",
                "value": cert.get("requests", {}).get("total"),
                "as_of": cert["as_ofs"].get("requests", ""),
            }
        )
    return tiles


def _tiles(headline: dict) -> list[dict]:
    """The headline figures, in the order the page shows them: each tile carries its
    family's own as-of day and the change from that family's previous snapshot."""
    tiles: list[dict] = []
    if "requests" in headline:
        entry = headline["requests"]
        tiles.append(
            {
                "label": "mail-in ballot requests",
                "value": entry.get("requests_total"),
                "change": entry.get("change", {}).get("total"),
                "as_of": entry["as_of"],
            }
        )
    if "sent-returned" in headline:
        entry = headline["sent-returned"]
        change = entry.get("change", {})
        tiles += [
            {
                "label": "ballots sent",
                "value": entry.get("sent"),
                "change": change.get("sent"),
                "as_of": entry["as_of"],
            },
            {
                "label": "received back",
                "value": entry.get("received"),
                "change": change.get("received"),
                "sub": (
                    f"{entry['return_rate'] * 100:.0f}% of sent"
                    if entry.get("return_rate") is not None
                    else None
                ),
                "as_of": entry["as_of"],
            },
            {
                "label": "accepted in the canvass",
                "value": entry.get("accepted"),
                "change": change.get("accepted"),
                "as_of": entry["as_of"],
            },
        ]
    return tiles
