"""Home page route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from skipjack.web.db import get_db
from skipjack.web.summary import highlights

router = APIRouter()


@router.get("/")
async def home(request: Request):
    with get_db() as conn:
        latest = conn.execute(
            "SELECT report_date FROM voter_registration ORDER BY report_date DESC LIMIT 1"
        ).fetchone()

        totals = conn.execute(
            """SELECT party, party_name, SUM(active_voters) as total
               FROM voter_registration
               WHERE report_date = ?
               GROUP BY party
               ORDER BY total DESC""",
            (latest["report_date"] if latest else "",),
        ).fetchall()

        total_voters = sum(r["total"] for r in totals) if totals else 0
        summary = highlights(conn)

    return request.app.state.templates.TemplateResponse(
        request,
        "home.html",
        {
            "latest_date": latest["report_date"] if latest else "N/A",
            "totals": totals,
            "total_voters": total_voters,
            "summary": summary,
        },
    )
