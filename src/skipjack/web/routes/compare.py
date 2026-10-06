"""Compare two voter file snapshots."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.web.compare import build_comparison, snapshot_pairs
from skipjack.web.db import get_db

router = APIRouter(prefix="/voters/file/compare")


@router.get("/")
async def compare_latest(request: Request):
    """The two most recent snapshots."""
    with get_db() as conn:
        dates = [r[0] for r in conn.execute("SELECT snapshot_date FROM voter_file_snapshots")]
    pairs = snapshot_pairs(dates)
    if not pairs:
        raise HTTPException(404, "Comparing needs at least two voter file snapshots")
    return _render(request, *pairs[-1])


@router.get("/{earlier}/{later}/")
async def compare_pair(request: Request, earlier: str, later: str):
    if earlier >= later:
        raise HTTPException(404, "The earlier snapshot comes first")
    return _render(request, earlier, later)


def _render(request: Request, earlier: str, later: str):
    with get_db() as conn:
        try:
            comparison = build_comparison(conn, earlier, later)
        except KeyError as missing:
            raise HTTPException(404, f"No voter file snapshot for {missing.args[0]}") from None
    cohorts = comparison["cohorts"]
    chart = {
        "years": [r["year"] for r in cohorts["rows"]],
        "left": [None if r["kept"] is None else 100 - r["kept"] for r in cohorts["rows"]],
        "before": [r["a"] for r in cohorts["rows"]],
        "after": [r["b"] for r in cohorts["rows"]],
        "earlier": comparison["earlier_label"],
        "later": comparison["later_label"],
    }
    return request.app.state.templates.TemplateResponse(
        request,
        "voterfile_compare.html",
        {"c": comparison, "chart": chart},
    )
