"""A machine-readable index of the downloads, for the data CLI (skipjack.client).

Built from download_files(), so it lists exactly what the site offers: the publish gates in
skipjack.web.downloads decide, not this module. Paths are relative to downloads/.
"""

from __future__ import annotations

from pathlib import Path

from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS
from skipjack.pipeline.turnout import ELECTIONS
from skipjack.web.downloads import download_files
from skipjack.web.urls import slugify

CATALOG_VERSION = 1
REGISTRATION_TABLES = {
    "monthly": "combined/registration_monthly_all.csv",
    "activity": "combined/registration_activity_all.csv",
    "summary": "combined/registration_summary_all.csv",
}


def build_catalog(data_dir: Path) -> dict:
    months: list[str] = []
    results: dict[str, dict] = {}
    turnout: dict[str, dict] = {}
    for relative in download_files(data_dir):
        if relative.suffix != ".csv":
            continue
        parts = relative.parts
        path = relative.as_posix()
        if parts[:2] == ("voter_registration", "monthly") and len(parts) == 3:
            months.append(relative.stem)
        elif parts[0] == "results" and len(parts) == 4 and parts[2] == "county":
            _results_entry(results, parts[1], official=True, as_of=None)["files"].append(path)
        elif parts[0] == "results_unofficial" and len(parts) == 5 and parts[3] == "county":
            _results_entry(results, parts[1], official=False, as_of=parts[2])["files"].append(path)
        elif parts[0] == "turnout" and len(parts) == 5:
            code, family, geography = parts[1:4]
            entry = turnout.setdefault(code, _turnout_entry(code))
            entry["families"].setdefault(family, {}).setdefault(geography, []).append(relative.stem)
    return {
        "version": CATALOG_VERSION,
        "registration": {"tables": REGISTRATION_TABLES, "months": sorted(months)},
        "counties": [
            {"name": name, "slug": slugify(name), "code": code}
            for code, name in COUNTY_CODES.items()
        ],
        "results": results,
        "turnout": turnout,
        "county_history": "county/{slug}.json",
    }


def _results_entry(results: dict, code: str, official: bool, as_of: str | None) -> dict:
    # download_files offers results/ and results_unofficial/ only for registry elections
    # (results_published and is_live both read RESULTS_ELECTIONS), so the lookup holds
    if code not in results:
        e = RESULTS_ELECTIONS[code]
        results[code] = {
            "name": e.name,
            "election_day": e.election_day,
            "kind": e.kind,
            "official": official,
            "as_of": as_of,
            "files": [],
        }
    return results[code]


def _turnout_entry(code: str) -> dict:
    e = ELECTIONS.get(code)
    return {
        "name": e.name if e else code,
        "election_day": e.election_day if e else None,
        "certified": bool(e and e.certified),
        "families": {},
    }
