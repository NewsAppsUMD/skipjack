"""Compare two voter file snapshots using only their stored, suppressed aggregates.

Everything here reads the ``voter_file_counts`` table, so it sees no individual voter. What it
can show is how the *totals* moved between two dates: how many were registered, in which party,
county and age band, and how many of each registration-year cohort were still on the list.
It cannot say who changed party or who left, because that needs the two files compared voter by
voter. The page says so.
"""

from __future__ import annotations

import json
import sqlite3

from skipjack.web.dates import month_label

STATEWIDE = "Maryland"
ALL = "ALL"
PARTY_GROUPS = ["DEM", "REP", "UNA", "GRN", "WCP", "OTH"]
PARTY_LABELS = {
    "DEM": "Democratic", "REP": "Republican", "UNA": "Unaffiliated", "GRN": "Green",
    "WCP": "Working Class", "OTH": "Other", ALL: "All voters",
}  # fmt: skip
SHARE_GROUPS = ["DEM", "REP", "UNA"]  # the parties big enough for shares to mean something
COHORT_FIRST_YEAR = 1980


def snapshot_pairs(dates: list[str]) -> list[tuple[str, str]]:
    """Consecutive (earlier, later) pairs of snapshot dates, oldest first."""
    ordered = sorted(dates)
    return list(zip(ordered, ordered[1:], strict=False))


def pct_change(before: int | None, after: int | None) -> float | None:
    if before is None or after is None or not before:
        return None
    return 100 * (after - before) / before


def share(part: int | None, whole: int | None) -> float | None:
    if part is None or not whole:
        return None
    return 100 * part / whole


def _diff(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else b - a


def _plus(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a + b


class Snapshot:
    """The cells of one snapshot that the comparison uses."""

    def __init__(self, conn: sqlite3.Connection, date: str):
        self.date = date
        row = conn.execute(
            "SELECT meta_json FROM voter_file_snapshots WHERE snapshot_date = ?", (date,)
        ).fetchone()
        if row is None:
            raise KeyError(date)
        self.meta = json.loads(row["meta_json"])
        self.status: dict[tuple[str, str, str], int | None] = {}
        for r in conn.execute(
            """SELECT county, party_group, status, count FROM voter_file_counts
               WHERE snapshot_date = ? AND metric = 'party_by_county'""",
            (date,),
        ):
            self.status[(r["county"], r["party_group"], r["status"])] = r["count"]
        self.age: dict[tuple[str, str], int | None] = {}
        for r in conn.execute(
            """SELECT party_group, age_band, count FROM voter_file_counts
               WHERE snapshot_date = ? AND metric = 'age_by_party' AND county = ?""",
            (date, STATEWIDE),
        ):
            self.age[(r["party_group"], r["age_band"])] = r["count"]
        self.cohorts: dict[int, int | None] = {
            r["reg_year"]: r["count"]
            for r in conn.execute(
                """SELECT reg_year, count FROM voter_file_counts
                   WHERE snapshot_date = ? AND metric = 'registration_cohorts'
                     AND county = ? AND party_group = ?""",
                (date, STATEWIDE, ALL),
            )
        }

    def active(self, county: str, group: str) -> int | None:
        return self.status.get((county, group, "A"))

    def inactive(self, county: str, group: str) -> int | None:
        return self.status.get((county, group, "I"))

    def registered(self, county: str, group: str) -> int | None:
        """Active plus inactive."""
        return _plus(self.active(county, group), self.inactive(county, group))

    @property
    def year(self) -> int:
        return int(self.date[:4])


def _change(a: int | None, b: int | None) -> dict:
    return {"a": a, "b": b, "change": _diff(a, b), "pct": pct_change(a, b)}


def _shares(a: Snapshot, b: Snapshot, county: str) -> dict[str, dict]:
    """Each major party's share of registered voters at both dates, and the move in points."""
    out = {}
    for g in SHARE_GROUPS:
        before = share(a.registered(county, g), a.registered(county, ALL))
        after = share(b.registered(county, g), b.registered(county, ALL))
        out[g] = {
            "a": before,
            "b": after,
            "points": None if before is None or after is None else after - before,
        }
    return out


def official_check(a: Snapshot, b: Snapshot) -> dict | None:
    """Active voters in each file next to the State Board's monthly reports for those months."""
    ca, cb = a.meta.get("vrar_cross_check"), b.meta.get("vrar_cross_check")
    if not ca or not cb:
        return None
    file_change, official_change = (
        _change(ca["file_active"], cb["file_active"]),
        _change(ca["vrar_active"], cb["vrar_active"]),
    )
    return {
        "file": file_change,
        "official": official_change,
        "agree": (file_change["change"] > 0) == (official_change["change"] > 0),
        "a": {"month": ca["vrar_month"], "gap_pct": ca["difference_pct"]},
        "b": {"month": cb["vrar_month"], "gap_pct": cb["difference_pct"]},
    }


def cohort_survival(a: Snapshot, b: Snapshot) -> dict:
    """For voters who first registered before the earlier snapshot's year, how many of each
    year's cohort are on the later list. A cohort can only shrink (people move away, die or are
    removed), so the ratio reads as the share still on the list. The earlier snapshot's own year
    is left out because it was only part-way through."""
    last = a.year - 1
    years = [
        y
        for y in sorted(a.cohorts)
        if COHORT_FIRST_YEAR <= y <= last and a.cohorts.get(y) and b.cohorts.get(y) is not None
    ]
    rows = [
        {"year": y, "a": a.cohorts[y], "b": b.cohorts[y], "kept": share(b.cohorts[y], a.cohorts[y])}
        for y in years
    ]
    total_a = sum(r["a"] for r in rows)
    total_b = sum(r["b"] for r in rows)
    return {
        "rows": rows,
        "first_year": years[0] if years else None,
        "last_year": last,
        "total_a": total_a,
        "total_b": total_b,
        "kept": share(total_b, total_a),
    }


def build_comparison(conn: sqlite3.Connection, earlier: str, later: str) -> dict:
    """Everything the comparison page shows. Raises KeyError for an unknown snapshot."""
    a, b = Snapshot(conn, earlier), Snapshot(conn, later)
    counties = [c for c in b.meta["counties"] if c in a.meta["counties"]]

    parties = [
        {
            "group": g,
            "label": PARTY_LABELS[g],
            "registered": _change(a.registered(STATEWIDE, g), b.registered(STATEWIDE, g)),
            "active": _change(a.active(STATEWIDE, g), b.active(STATEWIDE, g)),
            "share": {
                "a": share(a.registered(STATEWIDE, g), a.registered(STATEWIDE, ALL)),
                "b": share(b.registered(STATEWIDE, g), b.registered(STATEWIDE, ALL)),
            },
        }
        for g in [ALL, *PARTY_GROUPS]
    ]
    for p in parties:
        s = p["share"]
        s["points"] = None if s["a"] is None or s["b"] is None else s["b"] - s["a"]

    county_rows = [
        {
            "county": c,
            "registered": _change(a.registered(c, ALL), b.registered(c, ALL)),
            "active": _change(a.active(c, ALL), b.active(c, ALL)),
            "shares": _shares(a, b, c),
        }
        for c in counties
    ]

    bands = [x for x in b.meta["age_bands"] if x in a.meta["age_bands"] and x != "Unknown"]

    def age_registered(snap: Snapshot, group: str, band: str) -> int | None:
        return snap.age.get((group, band))

    age_rows = []
    for band in bands:
        shares = {}
        for g in SHARE_GROUPS:
            before = share(age_registered(a, g, band), age_registered(a, ALL, band))
            after = share(age_registered(b, g, band), age_registered(b, ALL, band))
            shares[g] = {
                "a": before,
                "b": after,
                "points": None if before is None or after is None else after - before,
            }
        age_rows.append(
            {
                "band": band,
                "registered": _change(age_registered(a, ALL, band), age_registered(b, ALL, band)),
                "shares": shares,
            }
        )

    return {
        "earlier": earlier,
        "later": later,
        "earlier_label": month_label(earlier[:7]),
        "later_label": month_label(later[:7]),
        "earlier_meta": a.meta,
        "later_meta": b.meta,
        "tiles": {
            "registered": _change(a.registered(STATEWIDE, ALL), b.registered(STATEWIDE, ALL)),
            "active": _change(a.active(STATEWIDE, ALL), b.active(STATEWIDE, ALL)),
            "inactive": _change(a.inactive(STATEWIDE, ALL), b.inactive(STATEWIDE, ALL)),
        },
        "official": official_check(a, b),
        "parties": parties,
        "counties": county_rows,
        "ages": age_rows,
        "cohorts": cohort_survival(a, b),
        "elections": {
            "earlier": [e["short"] for e in a.meta["elections"]],
            "later": [e["short"] for e in b.meta["elections"]],
            "shared": sorted(
                {e["key"] for e in a.meta["elections"]} & {e["key"] for e in b.meta["elections"]}
            ),
        },
    }
