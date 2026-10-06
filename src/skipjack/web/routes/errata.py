"""Errata: arithmetic errors in the State Board's own reports, and bulk downloads."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from skipjack.web import errata as errata_data
from skipjack.web.bulk import BULK_FILES, bulk_csv
from skipjack.web.db import get_db

router = APIRouter()


@router.get("/errata/")
async def errata_page(request: Request):
    rows = errata_data.load_discrepancies()
    with get_db() as conn:
        urls = {
            r["source_id"]: r["source_url"]
            for r in conn.execute("SELECT source_id, source_url FROM sources")
        }
    for row in rows:
        row["pdf"] = urls.get(f"sbe-vrar-{row['report_date']}")
    return request.app.state.templates.TemplateResponse(
        request,
        "errata.html",
        {
            "rows": rows,
            "months": len({r["report_date"] for r in rows}),
            "file": "voter_registration/source_discrepancies.csv",
        },
    )


@router.get("/downloads/combined/{name}")
async def combined_download(name: str):
    if name not in BULK_FILES:
        raise HTTPException(404, f"No combined file called {name}")
    with get_db() as conn:
        text = bulk_csv(conn, name)
    return Response(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
