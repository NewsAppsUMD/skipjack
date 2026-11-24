"""A miniature downloads/ tree for the data CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

MONTHLY = """report_date,county,party,party_name,active_voters,source_id
2026-09-30,Allegany,DEM,Democratic,100,sbe-vrar-2026-09-30
2026-09-30,Allegany,REP,Republican,200,sbe-vrar-2026-09-30
2026-09-30,Howard,DEM,Democratic,900,sbe-vrar-2026-09-30
2026-10-31,Allegany,DEM,Democratic,110,sbe-vrar-2026-10-31
"""

RESULTS_HEADER = (
    "election,county,party_file,office,district,candidate,party,winner,write_in,"
    "early_votes,early_against,total_votes,total_against\n"
)
ALLEGANY = RESULTS_HEADER + (
    'GG22,Allegany,all,Governor / Lt. Governor,"",Wes Moore and Aruna Miller,DEM,1,0,783,,6796,\n'
    "GG22,Allegany,all,County Commissioner,06,Some Person,REP,1,0,10,,50,\n"
)
HOWARD = RESULTS_HEADER + (
    'GG22,Howard,all,Governor / Lt. Governor,"",Wes Moore and Aruna Miller,DEM,1,0,9000,,90000,\n'
)

TURNOUT_HEADER = "as_of,election,family,geography,county,district,category,party,measure,value\n"


def write_downloads(root: Path) -> Path:
    files = {
        "combined/registration_monthly_all.csv": MONTHLY,
        "results/GG22/county/01-allegany-all.csv": ALLEGANY,
        "results/GG22/county/14-howard-all.csv": HOWARD,
        "turnout/GG26/requests/county/2026-10-14.csv": TURNOUT_HEADER
        + '2026-10-14,GG26,requests,county,Allegany,"",ALL,DEM,requested,5\n',
        "turnout/GG26/requests/county/2026-10-15.csv": TURNOUT_HEADER
        + '2026-10-15,GG26,requests,county,Allegany,"",ALL,DEM,requested,7\n'
        + '2026-10-15,GG26,requests,county,Howard,"",ALL,DEM,requested,70\n',
        "turnout/GG26/sent-returned/county/2026-10-15.csv": TURNOUT_HEADER
        + '2026-10-15,GG26,sent-returned,county,Allegany,"",ALL,DEM,sent,4\n',
        "county/allegany.json": json.dumps(
            {
                "county": "Allegany",
                "slug": "allegany",
                "registration": [
                    {"report_date": "2026-10-31", "county": "Allegany", "party": "DEM",
                     "party_name": "Democratic", "active_voters": 110}
                ],
                "turnout": [
                    {"as_of": "2026-10-15", "election": "GG26", "family": "requests",
                     "geography": "county", "county": "Allegany", "district": "",
                     "category": "ALL", "party": "DEM", "measure": "requested", "value": 7}
                ],
                "results": [
                    {"election": "GG22", "county": "Allegany", "party_file": "all",
                     "office": "Governor / Lt. Governor", "district": "",
                     "candidate": "Wes Moore and Aruna Miller", "party": "DEM", "winner": 1,
                     "write_in": 0, "total_votes": 6796, "total_against": None}
                ],
                "results_over_time": {"county": "Allegany", "levels": []},
            }
        ),
    }  # fmt: skip
    catalog = {
        "version": 1,
        "registration": {
            "tables": {"monthly": "combined/registration_monthly_all.csv"},
            "months": ["2026-09", "2026-10"],
        },
        "counties": [
            {"name": "Allegany", "slug": "allegany", "code": "01"},
            {"name": "Howard", "slug": "howard", "code": "14"},
            {"name": "St. Mary's", "slug": "st-marys", "code": "19"},
        ],
        "results": {
            "GG22": {
                "name": "2022 gubernatorial general election", "election_day": "2022-11-08",
                "kind": "general", "official": True, "as_of": None,
                "files": [
                    "results/GG22/county/01-allegany-all.csv",
                    "results/GG22/county/14-howard-all.csv",
                ],
            }
        },
        "turnout": {
            "GG26": {
                "name": "2026 general election", "election_day": "2026-11-03",
                "certified": False,
                "families": {
                    "requests": {"county": ["2026-10-14", "2026-10-15"]},
                    "sent-returned": {"county": ["2026-10-15"]},
                },
            }
        },
        "county_history": "county/{slug}.json",
    }  # fmt: skip
    files["index.json"] = json.dumps(catalog)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
