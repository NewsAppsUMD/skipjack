"""Estimated race of registered voters, from stored voter file aggregates.

Everything here reads the three race metrics of the ``voter_file_counts`` table (see
pipeline/voterfile.py), so it sees no individual voter. Race is estimated by BISG from surname,
first and middle names and ZIP code (scripts/race/predict_race.R): ``count`` is the expected
number of voters of a race in a cell, ``voters`` the registrants in it. Cells of 1 to 9 arrive
as ``None`` and make every share that uses them ``None``.
"""

from __future__ import annotations

import json
import re
import sqlite3

STATEWIDE = "Maryland"
ALL = "ALL"
RACES = ["white", "black", "hispanic", "asian", "other"]
RACE_LABELS = {
    "white": "White",
    "black": "Black",
    "hispanic": "Hispanic",
    "asian": "Asian",
    "other": "Other or multiracial",
}
PARTY_ROWS = [("DEM", "Democratic"), ("REP", "Republican"), ("UNA", "Unaffiliated")]
DISTRICT_TYPES = ("congressional", "legislative")


def pct(part: int | None, whole: int | None) -> float | None:
    return None if part is None or not whole else 100 * part / whole


def snapshots_with_race(conn: sqlite3.Connection) -> list[str]:
    """Snapshot dates whose metadata says the race estimates exist, oldest first."""
    found = []
    for r in conn.execute("SELECT snapshot_date, meta_json FROM voter_file_snapshots"):
        if "race" in json.loads(r["meta_json"]):
            found.append(r["snapshot_date"])
    return sorted(found)


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


class Cells:
    """The race cells of one snapshot: (group..., race) -> (expected count, registrants)."""

    def __init__(self, conn: sqlite3.Connection, date: str):
        self.date = date
        self.meta = json.loads(
            conn.execute(
                "SELECT meta_json FROM voter_file_snapshots WHERE snapshot_date = ?", (date,)
            ).fetchone()["meta_json"]
        )

        def load(metric: str, *dims: str) -> dict[tuple, tuple[int | None, int | None]]:
            cols = ", ".join(dims)
            return {
                tuple(r[d] for d in dims): (r["count"], r["voters"])
                for r in conn.execute(
                    f"""SELECT {cols}, count, voters FROM voter_file_counts
                        WHERE snapshot_date = ? AND metric = ?""",
                    (date, metric),
                )
            }

        self.party = load("race_by_party", "county", "party_group", "race")
        self.age = load("race_by_age", "county", "party_group", "age_band", "race")
        self.district = load("race_by_district", "district_type", "district", "race")

    def _shares(self, cells: dict, key: tuple) -> dict:
        """Percent of a group's registrants in each race, and the registrants."""
        found = {race: cells.get((*key, race)) for race in RACES}
        if not any(found.values()):
            return {}
        voters = next((v[1] for v in found.values() if v), None)
        return {
            "voters": voters,
            "counts": {race: (found[race][0] if found[race] else 0) for race in RACES},
            "shares": {race: pct(found[race][0] if found[race] else 0, voters) for race in RACES},
        }

    def by_party(self, county: str, group: str) -> dict:
        return self._shares(self.party, (county, group))

    def by_age(self, county: str, group: str, band: str) -> dict:
        return self._shares(self.age, (county, group, band))

    def by_district(self, kind: str, district: str) -> dict:
        return self._shares(self.district, (kind, district))

    def districts(self, kind: str) -> list[str]:
        return sorted({d for k, d, _ in self.district if k == kind}, key=_natural)


def build_race(conn: sqlite3.Connection) -> dict | None:
    """Everything the race page shows, or None if no snapshot has the estimates."""
    dates = snapshots_with_race(conn)
    if not dates:
        return None
    cells = Cells(conn, dates[-1])
    meta = cells.meta
    info = meta["race"]

    statewide = cells.by_party(STATEWIDE, ALL)
    calibration = []
    for race in RACES:
        row = info["calibration"][STATEWIDE][race]
        calibration.append(
            {
                "race": race,
                "label": RACE_LABELS[race],
                "estimated": row["estimated_pct"],
                "census": row["census_vap_pct"],
                "difference": round(row["estimated_pct"] - row["census_vap_pct"], 1),
            }
        )

    # Race makeup of each party's registrants, statewide.
    party_rows = []
    for group, label in [*PARTY_ROWS, (ALL, "All registered voters")]:
        row = cells.by_party(STATEWIDE, group)
        if row:
            party_rows.append({"group": group, "label": label, **row})

    # Party of each estimated race's registrants, statewide. "Other parties" is the remainder
    # after the three large ones, so one hidden minor-party cell cannot blank it.
    race_rows = []
    for race in RACES:
        total = (cells.party.get((STATEWIDE, ALL, race)) or (None, None))[0]
        counts = {g: (cells.party.get((STATEWIDE, g, race)) or (0, 0))[0] for g, _ in PARTY_ROWS}
        known = all(v is not None for v in counts.values()) and total is not None
        other = max(total - sum(counts.values()), 0) if known else None
        race_rows.append(
            {
                "race": race,
                "label": RACE_LABELS[race],
                "total": total,
                **{g: pct(counts[g], total) for g, _ in PARTY_ROWS},
                "other": pct(other, total) if other is None or other >= 10 or other == 0 else None,
            }
        )

    counties = [
        {"county": c, **cells.by_party(c, ALL)}
        for c in [*meta["counties"], STATEWIDE]
        if cells.by_party(c, ALL)
    ]
    counties.sort(key=lambda r: (r["county"] == STATEWIDE, r["county"]))

    ages = []
    for band in meta["age_bands"]:
        row = cells.by_age(STATEWIDE, ALL, band)
        if row and row["voters"]:
            ages.append({"band": band, **row})

    districts = {
        kind: [
            {"district": d, **cells.by_district(kind, d)}
            for d in cells.districts(kind)
            if cells.by_district(kind, d)
        ]
        for kind in DISTRICT_TYPES
    }

    return {
        "snapshot": cells.date,
        "meta": meta,
        "info": info,
        "statewide": statewide,
        "calibration": calibration,
        "party_rows": party_rows,
        "race_rows": race_rows,
        "counties": counties,
        "ages": ages,
        "districts": districts,
        "races": RACES,
        "race_labels": RACE_LABELS,
        "suppress_below": meta["suppress_below"],
    }
