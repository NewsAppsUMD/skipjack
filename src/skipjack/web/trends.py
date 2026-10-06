"""Statewide trends built from the registration summary and activity tables.

The State Board's monthly report breaks new registrations into 13 to 18 methods and removals
into 11 reasons, and renames some of them over the years. This module folds those categories
into a few groups that stay comparable across 2010 to today, and computes the series, yearly
totals and shares the trend pages show. Nothing here touches the web framework, so it can be
tested on its own.

Categories that no group names land in ``OTHER_GROUP`` instead of vanishing, and
``unmapped_categories`` lets a test notice when the State Board adds one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from statistics import median

from skipjack.web.dates import month_label
from skipjack.web.elections import PRIMARIES, month_of

OTHER_GROUP = "Other"

# Group name -> the State Board's category names, including old spellings.
METHOD_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Motor Vehicle Administration", ("MOTOR VEHICLE ADMINISTRATION",)),
    ("Online", ("ONLINE REGISTRATION",)),
    ("Same-day registration", ("SAME DAY REGISTRATION", "SAME DAY PROVISIONAL")),
    (
        "Paper forms and in person",
        ("BY MAIL", "NVRA BY MAIL", "IN PERSON", "VOLUNTEER", "RECRUITING", "OTHER MEANS", "OTHER"),
    ),
    (
        "State agencies",
        (
            "DESIGNATED STATE AGENCIES",
            "SOCIAL SERVICES",
            "ELDERLY / DISABLED",
            "SOCSEC/ELDERLY / DISABLED",
            "ELDERLY / DISABLED / SOC SEC",
        ),
    ),
    ("ERIC matches", ("ERIC REPORT",)),
    ("Provisional ballot", ("PROVISIONAL BALLOT",)),
    ("Military and overseas", ("ABSENTEE BALLOT (FPCA)", "MAIL-IN BALLOT (FPCA)")),
    ("From another board", ("FROM ANOTHER BOARD",)),
]

REMOVAL_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Moved to another county", ("COUNTY TRANSFER - OUT", "COUNTY TRANSFER-OUT")),
    ("Address confirmation mailings", ("MAIL VERIFICATION (NVRA)", "CONFIRMATION MAIL")),
    ("Death", ("DEATH NOTICE",)),
    ("Moved out of state", ("MOVED OUT OF STATE",)),
    ("Voter request", ("VOTER REQUEST",)),
    ("Duplicate registration", ("DUPLICATE",)),
    ("Conviction or incompetency", ("CRIMINAL CONVICTION", "MENTAL INCOMPETENCY")),
    ("Board action and other", ("BOARD ACTION", "OTHER")),
]

PARTY_BUCKETS = ["Democratic", "Republican", "Unaffiliated", "Other"]
_BUCKET_OF = {
    "DEM": "Democratic",
    "REP": "Republican",
    "UNAF": "Unaffiliated",
    "UNA": "Unaffiliated",
}


def bucket(party: str) -> str:
    """Democratic, Republican, Unaffiliated (UNAF and UNA are one party), or Other."""
    return _BUCKET_OF.get(party, "Other")


Series = list[int | None]


def add(values: list[Series]) -> Series:
    """Month-by-month sum; a month is None only when every input is None."""
    out: Series = []
    for column in zip(*values, strict=True):
        present = [v for v in column if v is not None]
        out.append(sum(present) if present else None)
    return out


def rolling(values: Series, window: int = 12) -> Series:
    """Sum of the ``window`` months ending at each month; None until a full window of known
    months is available."""
    out: Series = []
    for i in range(len(values)):
        chunk = values[i - window + 1 : i + 1] if i >= window - 1 else None
        out.append(None if chunk is None or any(v is None for v in chunk) else sum(chunk))
    return out


def total(values: Series) -> int:
    return sum(v for v in values if v is not None)


@dataclass
class Group:
    name: str
    categories: list[str]  # the State Board's names that fed this group, as observed
    monthly: Series
    by_party: dict[str, Series]  # party bucket -> monthly
    rolling: Series = field(default_factory=list)

    def last_12(self) -> int:
        return total(self.monthly[-12:])

    def party_last_12(self) -> dict[str, int]:
        return {b: total(self.by_party[b][-12:]) for b in PARTY_BUCKETS}


@dataclass
class Breakdown:
    """One section of the summary table (new registrations or removals), grouped."""

    dates: list[str]
    groups: list[Group]
    total: Series
    total_rolling: Series
    unmapped: list[str]  # categories no group names

    def years(self) -> list[str]:
        return sorted({d[:4] for d in self.dates})

    def months_in(self, year: str) -> int:
        return sum(1 for d in self.dates if d.startswith(year))

    def complete_years(self) -> list[str]:
        return [y for y in self.years() if self.months_in(y) == 12]

    def year_total(self, series: Series, year: str) -> int | None:
        values = [v for d, v in zip(self.dates, series, strict=True) if d.startswith(year)]
        return (
            sum(v for v in values if v is not None) if any(v is not None for v in values) else None
        )

    def year_rows(self) -> list[dict]:
        """Calendar-year totals for every group, newest year first."""
        rows = []
        for year in sorted(self.years(), reverse=True):
            rows.append(
                {
                    "year": year,
                    "months": self.months_in(year),
                    "cells": [self.year_total(g.monthly, year) for g in self.groups],
                    "total": self.year_total(self.total, year),
                }
            )
        return rows

    def election_year_share(self, group: Group, minimum: int = 5000) -> float | None:
        """The percent of a group's yearly average that falls in even-numbered (election) years:
        the average even-year total over the sum of the even-year and odd-year averages, across
        complete calendar years. None if either kind of year has no data or the group is small
        (fewer than ``minimum`` in all those years), since a share of a handful means little."""
        even, odd = [], []
        for year in self.complete_years():
            value = self.year_total(group.monthly, year)
            if value is None:
                continue
            (even if int(year) % 2 == 0 else odd).append(value)
        if not even or not odd or sum(even) + sum(odd) < minimum:
            return None
        even_avg, odd_avg = sum(even) / len(even), sum(odd) / len(odd)
        return 100 * even_avg / (even_avg + odd_avg) if even_avg + odd_avg else None


def _load(conn: sqlite3.Connection, section: str):
    """dates, and {(category, party bucket): series} for a summary section.

    The reports print a category's row only in months when it has something to report (the
    same-day registration row is missing in months without early voting, for instance). So a
    category with no row in a month counts as zero from the month it first appears onward, and
    as unknown (None) before that. A row printed as NA stays unknown.
    """
    rows = conn.execute(
        """SELECT report_date, category, party, SUM(value) AS value
           FROM registration_summary
           WHERE section = ? AND party <> 'DUPS'
           GROUP BY report_date, category, party
           ORDER BY report_date""",
        (section,),
    ).fetchall()
    dates = sorted({r["report_date"] for r in rows})
    index = {d: i for i, d in enumerate(dates)}
    cells: dict[tuple[str, str], Series] = {}
    printed: dict[str, set[int]] = {}  # category -> months with a row, even one printed as NA
    for r in rows:
        i = index[r["report_date"]]
        printed.setdefault(r["category"], set()).add(i)
        series = cells.setdefault((r["category"], bucket(r["party"])), [None] * len(dates))
        if r["value"] is not None:
            series[i] = (series[i] or 0) + r["value"]
    for (category, _), series in cells.items():
        first = min(printed[category])
        for i in range(first, len(dates)):
            if i not in printed[category]:
                series[i] = 0
    return dates, cells


def breakdown(
    conn: sqlite3.Connection, section: str, groups: list[tuple[str, tuple[str, ...]]]
) -> Breakdown:
    dates, cells = _load(conn, section)
    observed = sorted({category for category, _ in cells})
    named = {c for _, names in groups for c in names}
    unmapped = [c for c in observed if c not in named]
    spec = list(groups) + ([(OTHER_GROUP, tuple(unmapped))] if unmapped else [])

    blank: Series = [None] * len(dates)
    result = []
    for name, names in spec:
        used = [c for c in names if c in observed]
        if not used:
            continue
        by_party = {b: add([cells.get((c, b), blank) for c in used]) for b in PARTY_BUCKETS}
        monthly = add(list(by_party.values()))
        result.append(Group(name, used, monthly, by_party, rolling(monthly)))
    grand = add([g.monthly for g in result])
    return Breakdown(dates, result, grand, rolling(grand), unmapped)


def new_registrations(conn: sqlite3.Connection) -> Breakdown:
    return breakdown(conn, "new_registration", METHOD_GROUPS)


def removals(conn: sqlite3.Connection) -> Breakdown:
    return breakdown(conn, "removal", REMOVAL_GROUPS)


def unmapped_categories(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Summary categories that METHOD_GROUPS and REMOVAL_GROUPS do not name."""
    return {
        "new_registration": new_registrations(conn).unmapped,
        "removal": removals(conn).unmapped,
    }


def statewide_activity(conn: sqlite3.Connection, measure: str) -> tuple[list[str], Series]:
    """A county activity measure summed over all counties and parties, by month."""
    rows = conn.execute(
        """SELECT report_date, SUM(value) AS value FROM registration_activity
           WHERE measure = ? GROUP BY report_date ORDER BY report_date""",
        (measure,),
    ).fetchall()
    return [r["report_date"] for r in rows], [r["value"] for r in rows]


@dataclass
class Switches:
    dates: list[str]
    by_party: dict[str, Series]  # the party the voter left
    total: Series


def party_switches(conn: sqlite3.Connection) -> Switches:
    """Voters who changed party, by month and the party they left, statewide."""
    rows = conn.execute(
        """SELECT report_date, party, SUM(value) AS value FROM registration_activity
           WHERE measure = 'party_affiliation_changes_from'
           GROUP BY report_date, party ORDER BY report_date"""
    ).fetchall()
    dates = sorted({r["report_date"] for r in rows})
    index = {d: i for i, d in enumerate(dates)}
    by_party: dict[str, Series] = {b: [None] * len(dates) for b in PARTY_BUCKETS}
    for r in rows:
        if r["value"] is None:
            continue
        series = by_party[bucket(r["party"])]
        i = index[r["report_date"]]
        series[i] = (series[i] or 0) + r["value"]
    return Switches(dates, by_party, add(list(by_party.values())))


def primary_rows(switches: Switches) -> list[dict]:
    """For each primary in the data: switches the month before, in the primary's month, and in
    a typical other month of that year (the median), so any dip around a primary is visible.

    Maryland closes party changes 21 days before an election, so a primary's own month could be
    expected to be quiet. The data shows that only in some years. ``ratio`` is the primary's
    month divided by the typical month; ``rank`` is its place among the year's reported months,
    1 being the fewest switches."""
    rows = []
    for election in PRIMARIES:
        month = month_of(election)
        if month not in switches.dates:
            continue
        i = switches.dates.index(month)
        year = month[:4]
        others = [
            v
            for d, v in zip(switches.dates, switches.total, strict=True)
            if d.startswith(year) and d != month and v is not None
        ]
        during = switches.total[i]
        if during is None or len(others) < 6:
            continue  # a partial year, or an unreported month
        typical = median(others)
        rank = 1 + sum(1 for v in others if v < during)
        rows.append(
            {
                "date": election,
                "month": month,
                "label": month_label(month),
                "before": switches.total[i - 1] if i else None,
                "during": during,
                "typical": round(typical),
                "ratio": during / typical if typical else None,
                "rank": rank,
                "months": len(others) + 1,
            }
        )
    return rows


def party_baseline(conn: sqlite3.Connection) -> dict[str, float]:
    """Each party bucket's share of active voters in the latest report, for comparison."""
    latest = conn.execute("SELECT MAX(report_date) AS d FROM voter_registration").fetchone()["d"]
    counts = dict.fromkeys(PARTY_BUCKETS, 0)
    for r in conn.execute(
        "SELECT party, SUM(active_voters) AS n FROM voter_registration WHERE report_date = ? GROUP BY party",
        (latest,),
    ):
        counts[bucket(r["party"])] += r["n"]
    everyone = sum(counts.values())
    return {b: 100 * n / everyone for b, n in counts.items()} if everyone else {}


def party_rows(breakdown: Breakdown) -> list[dict]:
    """For each group, who is in it: the share of the last 12 months by party bucket."""
    rows = []
    for g in sorted(breakdown.groups, key=lambda g: -g.last_12()):
        mix = g.party_last_12()
        everyone = sum(mix.values())
        if everyone:
            rows.append(
                {
                    "name": g.name,
                    "total": everyone,
                    "shares": {b: 100 * n / everyone for b, n in mix.items()},
                }
            )
    return rows


def align(dates: list[str], source_dates: list[str], values: Series) -> Series:
    """``values`` (reported on ``source_dates``) placed on ``dates``; None where missing."""
    known = dict(zip(source_dates, values, strict=True))
    return [known.get(d) for d in dates]


def other_peak(conn: sqlite3.Connection, switches: Switches) -> dict | None:
    """The biggest month for switches away from "other" parties, if it dwarfs a typical month.

    Returns the month, its count, how many times a typical month (the median) it is, and the
    party codes that make up most of it, so the page can point at it without guessing why.
    """
    values = switches.by_party["Other"]
    known = [(v, d) for d, v in zip(switches.dates, values, strict=True) if v is not None]
    if len(known) < 24:
        return None
    peak, month = max(known)
    typical = median(v for v, _ in known)
    if not typical or peak < 5 * typical:
        return None
    codes = conn.execute(
        """SELECT party, SUM(value) AS n FROM registration_activity
           WHERE measure = 'party_affiliation_changes_from' AND report_date = ?
             AND party NOT IN ('DEM', 'REP', 'UNAF', 'UNA')
           GROUP BY party ORDER BY n DESC""",
        (month,),
    ).fetchall()
    return {
        "month": month,
        "count": peak,
        "times_typical": peak / typical,
        "top": codes[0]["party"] if codes else None,
        "top_share": 100 * codes[0]["n"] / peak if codes and peak else None,
    }
