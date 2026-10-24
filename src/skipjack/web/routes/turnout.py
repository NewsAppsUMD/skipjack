"""The /turnout/ section: each election's mail-in figures from the press-room reports."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.pipeline.turnout import ELECTIONS
from skipjack.web import turnout as qt
from skipjack.web.dates import day_label
from skipjack.web.db import get_db
from skipjack.web.maps import turnout_certified_maps, turnout_live_maps

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
        early = qt.early_voting(conn, code)
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
            "early": early,
        },
    )


_GEOGRAPHY_LABELS = {
    "county": "county",
    "congressional": "congressional district",
    "legislative": "legislative district",
}


@router.get("/{slug}/{geography}/")
async def election_geography(request: Request, slug: str, geography: str):
    """One election's table at one geography: the county page the election page used
    to carry, and the congressional- and legislative-district pages beside it (the
    legislative one a running election's only)."""
    if geography not in _GEOGRAPHY_LABELS:
        raise HTTPException(status_code=404)
    with get_db() as conn:
        available = {e["slug"]: e["code"] for e in qt.elections(conn)}
        code = available.get(slug)
        if code is None or code not in ELECTIONS:
            raise HTTPException(status_code=404)
        geographies = qt.publishable_geographies(conn, code)
        certified = ELECTIONS[code].certified
        if certified and geography == "legislative":
            raise HTTPException(status_code=404)
        county_maps: list[dict] = []
        if certified:
            cert = qt.certified_headline(conn, code)
            if geography == "county":
                # the county table keeps its fixed columns: its three record families
                # publish for every certified election, so the page names those days
                dates = {
                    f: a
                    for f, a in cert["as_ofs"].items()
                    if f in ("county-canvass", "votes-accepted", "mail-voting") and a
                }
                rows = qt.certified_county_rows(conn, code, dates)
                county_maps = turnout_certified_maps(
                    rows,
                    qt.certified_county_turnout(conn, code, cert["as_ofs"].get("official-turnout")),
                )
            else:
                dates = {
                    f: a for f, a in cert["as_ofs"].items() if f in qt.CERTIFIED_PAGE_FAMILIES and a
                }
                rows = qt.certified_district_rows(conn, code, dates)
        else:
            headline = qt.headline(conn, code)
            sent_as_of = headline.get("sent-returned", {}).get("as_of")
            requests_as_of = headline.get("requests", {}).get("as_of")
            early_as_of = headline.get("early-voting", {}).get("as_of")
            # the two files refresh on their own schedules: read each family at its own
            # latest snapshot so a day with only one new file keeps the other family's columns
            rows = (
                qt.county_rows(
                    conn,
                    code,
                    sent_as_of or requests_as_of,
                    requests_as_of=requests_as_of,
                    early_as_of=early_as_of,
                )
                if geography == "county"
                else qt.district_rows(
                    conn,
                    code,
                    sent_as_of or requests_as_of,
                    requests_as_of=requests_as_of,
                    geography=geography,
                    early_as_of=early_as_of,
                )
            )
            if geography == "county" and sent_as_of:
                county_maps = turnout_live_maps(rows, day_label(sent_as_of))
            dates: dict[str, str] = {}
            if sent_as_of:
                dates["sent-returned"] = sent_as_of
            if requests_as_of:
                dates["requests"] = requests_as_of
            if early_as_of:
                dates["early-voting"] = early_as_of
        # the flags read the rows, not the dates: each optional column renders only
        # where a row carries its figure, and the page names a family's day on that
        # same condition (below). The certified county table keeps its fixed columns
        # and therefore its three families' days whatever the rows hold.
        show_requests = any(r.get("requests_total") is not None for r in rows)
        show_counted = any(r.get("counted") is not None for r in rows)
        show_early = any(r.get("early_voting") is not None for r in rows)
        show_mail = any(
            r.get("mail_voting", {}).get("accepted") is not None
            or r.get("mail_voting", {}).get("rejected") is not None
            for r in rows
        )
        # the sentence names only the days the table's columns read
        if not certified:
            if not show_requests:
                dates.pop("requests", None)
            if not show_early:
                dates.pop("early-voting", None)
        elif geography == "congressional":
            dates = {
                f: a
                for f, a in dates.items()
                if f == "official-turnout"
                or (f == "votes-accepted" and show_counted)
                or (f == "mail-voting" and show_mail)
            }
        sources = qt.snapshot_sources(conn, code)
        # the page says which days it shows: its table's families' distinct as-ofs,
        # oldest first
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
            # the election page links only to the grains with published figures and
            # this page's table drops the columns whose rows carry no figure
            "geographies": geographies,
            "shown_dates": shown_dates,
            "show_requests": show_requests,
            "show_counted": show_counted,
            "show_early": show_early,
            "show_mail": show_mail,
            "county_maps": county_maps,
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
