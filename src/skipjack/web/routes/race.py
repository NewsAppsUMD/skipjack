"""Estimated race of registered voters."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.web.db import get_db
from skipjack.web.race import build_race

router = APIRouter(prefix="/voters/race")


@router.get("/")
async def race(request: Request):
    with get_db() as conn:
        data = build_race(conn)
    if data is None:
        raise HTTPException(404, "No voter file snapshot has estimated race")
    return request.app.state.templates.TemplateResponse(
        request, "race.html", {"r": data, "suppress_below": data["suppress_below"]}
    )
