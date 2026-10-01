"""About page route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from skipjack.web.db import get_db

router = APIRouter()


@router.get("/about")
async def about(request: Request):
    with get_db() as conn:
        sources = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()

    return request.app.state.templates.TemplateResponse(
        request,
        "about.html",
        {"sources": sources},
    )
