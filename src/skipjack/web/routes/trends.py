"""Statewide trend pages: how people register, why they leave the rolls, who switches party."""

from __future__ import annotations

import math

from fastapi import APIRouter, Request

from skipjack.web import trends
from skipjack.web.dates import month_label, shift_month
from skipjack.web.db import get_db
from skipjack.web.elections import GENERALS, PRIMARIES, month_of

router = APIRouter(prefix="/voters")

# Groups are called election-year groups when even-numbered years hold at least this percent.
ELECTION_YEAR_SHARE = 75

# A primary month counts as busy when it has at least this many times a typical month's switches.
BUSY_RATIO = 1.25

PARTY_COLORS = {
    "Democratic": "--party-dem",
    "Republican": "--party-rep",
    "Unaffiliated": "--party-una",
    "Other": "--party-minor",
}


def _group_payload(breakdown: trends.Breakdown) -> list[dict]:
    """What the chart script needs for each group, largest in the last 12 months first."""
    ordered = sorted(breakdown.groups, key=lambda g: -g.last_12())
    return [
        {"name": g.name, "monthly": g.monthly, "rolling": g.rolling, "last_12": g.last_12()}
        for g in ordered
    ]


def _chart(breakdown: trends.Breakdown, total_title: str) -> dict:
    return {
        "totalTitle": total_title,
        "dates": breakdown.dates,
        "total": {"monthly": breakdown.total, "rolling": breakdown.total_rolling},
        "groups": _group_payload(breakdown),
        "extras": [],
    }


def _lead(groups: list[trends.Group], total: int, window: str, noun: str) -> str:
    """One sentence on the biggest groups over the last 12 months."""
    if not total:
        return ""
    top = [g for g in groups if g.last_12()][:3]
    named = [f"{g.name} ({100 * g.last_12() / total:.0f}%)" for g in top]
    if len(named) > 1:
        listing = ", ".join(named[:-1]) + f" and {named[-1]}"
    else:
        listing = named[0]
    return f"The reports count {total:,} {noun} from {window}. The biggest groups: {listing}."


def _election_year_notes(breakdown: trends.Breakdown, noun: str) -> list[str]:
    """A sentence about groups that are concentrated in even-numbered (election) years."""
    found = []
    for g in breakdown.groups:
        share = breakdown.election_year_share(g)
        if share is not None and share >= ELECTION_YEAR_SHARE:
            found.append((g.name, math.floor(share)))
    if not found:
        return []
    names = [f"{name} ({share}%)" for name, share in found]
    listing = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
    return [
        f"Some {noun} cluster in election years. Counting complete calendar years, even-numbered years "
        f"hold about this share of each group's {noun}: {listing}."
    ]


@router.get("/methods/")
async def methods(request: Request):
    with get_db() as conn:
        new = trends.new_registrations(conn)
        baseline = trends.party_baseline(conn)
    ordered = sorted(new.groups, key=lambda g: -g.last_12())
    last12 = sum(g.last_12() for g in new.groups)
    first = new.dates[-12] if len(new.dates) >= 12 else new.dates[0]
    window = f"{month_label(first)} to {month_label(new.dates[-1])}"
    return request.app.state.templates.TemplateResponse(
        request,
        "methods.html",
        {
            "latest": new.dates[-1],
            "window": window,
            "lead": _lead(ordered, last12, window, "new registrations"),
            "groups": ordered,
            "year_rows": new.year_rows(),
            "party_buckets": trends.PARTY_BUCKETS,
            "party_rows": trends.party_rows(new),
            "baseline": baseline,
            "notes": _election_year_notes(new, "registrations"),
            "chart": _chart(new, "All new registrations, 12-month total"),
        },
    )


@router.get("/removals/")
async def removals(request: Request):
    with get_db() as conn:
        gone = trends.removals(conn)
        new = trends.new_registrations(conn)
        baseline = trends.party_baseline(conn)
        inactive_dates, inactive = trends.statewide_activity(conn, "inactive")
        mail_dates, mailings = trends.statewide_activity(conn, "confirmation_mailings")
        rolls = _rolls_change(conn, gone.dates[-1])
    ordered = sorted(gone.groups, key=lambda g: -g.last_12())
    first = gone.dates[-12] if len(gone.dates) >= 12 else gone.dates[0]
    window = f"{month_label(first)} to {month_label(gone.dates[-1])}"
    moved = next((g for g in gone.groups if g.name == "Moved to another county"), None)
    chart = _chart(gone, "All removals, 12-month total")
    chart["extras"] = [
        {
            "title": "Inactive voters",
            "note": "registered voters the State Board has marked inactive",
            "values": trends.align(gone.dates, inactive_dates, inactive),
        },
        {
            "title": "Confirmation mailings",
            "note": "reported each month, in large batches some months and almost none in others",
            "values": trends.align(gone.dates, mail_dates, mailings),
        },
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "removals.html",
        {
            "latest": gone.dates[-1],
            "window": window,
            "lead": _lead(ordered, sum(g.last_12() for g in gone.groups), window, "removals"),
            "moved_last12": moved.last_12() if moved else 0,
            "net_last12": trends.total(new.total[-12:]) - trends.total(gone.total[-12:]),
            "rolls_change": rolls,
            "groups": ordered,
            "year_rows": gone.year_rows(),
            "party_buckets": trends.PARTY_BUCKETS,
            "party_rows": trends.party_rows(gone),
            "baseline": baseline,
            "notes": _election_year_notes(gone, "removals"),
            "chart": chart,
        },
    )


def _rolls_change(conn, latest: str) -> dict | None:
    """Change in everyone on the rolls (active plus inactive) over the 12 months to ``latest``."""

    def rolls(month: str) -> int | None:
        row = conn.execute(
            """SELECT (SELECT SUM(active_voters) FROM voter_registration WHERE report_date = ?)
                    + (SELECT SUM(value) FROM registration_activity
                       WHERE report_date = ? AND measure = 'inactive') AS n""",
            (month, month),
        ).fetchone()
        return row["n"]

    before, now = rolls(shift_month(latest, -12)), rolls(latest)
    if before is None or now is None:
        return None
    return {"change": now - before, "since": shift_month(latest, -12)}


@router.get("/party-switching/")
async def party_switching(request: Request):
    with get_db() as conn:
        sw = trends.party_switches(conn)
        peak = trends.other_peak(conn, sw)
    rows = trends.primary_rows(sw)
    quietest = [r for r in rows if r["rank"] == 1]
    busier = [r for r in rows if r["ratio"] and r["ratio"] >= BUSY_RATIO]
    markers = [
        {"index": sw.dates.index(month_of(d)), "label": f"’{d[2:4]}", "kind": "primary"}
        for d in PRIMARIES
        if month_of(d) in sw.dates
    ] + [
        {"index": sw.dates.index(month_of(d)), "kind": "general"}
        for d in GENERALS
        if month_of(d) in sw.dates
    ]
    latest_year = sw.dates[-1][:4]
    other_note = None
    if peak:
        other_note = (
            f"The biggest month is {month_label(peak['month'])}, with {peak['count']:,} changes, "
            f"about {peak['times_typical']:.0f} times a typical month"
        )
        if peak["top"] and peak["top_share"] and peak["top_share"] >= 50:
            other_note += (
                f"; {peak['top_share']:.0f}% of them were voters coded {peak['top']} in the report"
            )
        other_note += ". The reports do not say why."
    return request.app.state.templates.TemplateResponse(
        request,
        "party_switching.html",
        {
            "latest": sw.dates[-1],
            "rows": rows,
            "quietest": [month_label(r["month"]) for r in quietest],
            "busier": [month_label(r["month"]) for r in busier],
            "busy_ratio": BUSY_RATIO,
            "primaries_counted": len(rows),
            "year_totals": [
                {
                    "year": y,
                    "total": sum(
                        v or 0 for d, v in zip(sw.dates, sw.total, strict=True) if d.startswith(y)
                    ),
                    "months": sum(1 for d in sw.dates if d.startswith(y)),
                }
                for y in sorted({d[:4] for d in sw.dates}, reverse=True)
            ],
            "latest_year": latest_year,
            "chart": {
                "dates": sw.dates,
                "series": [
                    {"name": b, "values": sw.by_party[b], "token": PARTY_COLORS[b]}
                    for b in trends.PARTY_BUCKETS
                    if b != "Other"
                ],
                "other": {
                    "name": "Other parties",
                    "values": sw.by_party["Other"],
                    "token": PARTY_COLORS["Other"],
                    "note": other_note,
                },
                "markers": markers,
            },
        },
    )
