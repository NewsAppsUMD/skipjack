"""Data downloads page: the CSV and JSON files behind the charts."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Request

from skipjack.web.downloads import download_files
from skipjack.web.paths import DATA_DIR

router = APIRouter()

MONTHLY_DATASETS = [
    {
        "folder": "voter_registration/monthly",
        "title": "Active registration by county and party",
        "description": "One file per monthly report from the State Board of Elections, 2010 onward. "
        "Active voters in each of the 24 counties by party.",
        "columns": "report_date, county, party, active_voters, party_name",
    },
    {
        "folder": "voter_registration/activity",
        "title": "County activity",
        "description": "Address and name changes, party affiliation changes, confirmation "
        "mailings and inactive voters, by county.",
        "columns": "report_date, county, measure, party, value",
    },
    {
        "folder": "voter_registration/summary",
        "title": "Statewide new registrations and removals",
        "description": "New registrations by method and party, and removals by reason and party, "
        "statewide. A blank value means the report printed NA.",
        "columns": "report_date, section, category, party, value",
    },
]


def monthly_listing(data_dir: Path, folder: str) -> list[dict]:
    """Files in a monthly folder grouped by year, newest first."""
    by_year: dict[str, list[dict]] = defaultdict(list)
    for csv in sorted((data_dir / folder).glob("*.csv")):
        by_year[csv.stem[:4]].append({"month": csv.stem, "path": f"{folder}/{csv.name}"})
    return [{"year": y, "files": by_year[y]} for y in sorted(by_year, reverse=True)]


def voter_file_listing(data_dir: Path) -> list[dict]:
    snapshots = []
    root = data_dir / "voter_file"
    for snapshot in sorted((p for p in root.glob("*") if p.is_dir()), reverse=True):
        meta_path = snapshot / "snapshot.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        files = [
            {"name": p.name, "path": f"voter_file/{snapshot.name}/{p.name}"}
            for p in sorted(snapshot.glob("*.json"))
            if not p.name.startswith("snapshot")
        ]
        snapshots.append(
            {
                "date": snapshot.name,
                "files": files,
                "meta": f"voter_file/{snapshot.name}/snapshot.json",
                "provenance": f"voter_file/{snapshot.name}/snapshot_provenance.json",
                "suppress_below": meta.get("suppress_below"),
            }
        )
    return snapshots


@router.get("/data/")
async def data_page(request: Request):
    available = {p.as_posix() for p in download_files(DATA_DIR)}
    datasets = [
        {**d, "years": monthly_listing(DATA_DIR, d["folder"])}
        for d in MONTHLY_DATASETS
        if any(a.startswith(d["folder"]) for a in available)
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "data.html",
        {
            "datasets": datasets,
            "snapshots": voter_file_listing(DATA_DIR),
            "discrepancies": "voter_registration/source_discrepancies.csv" in available,
        },
    )
