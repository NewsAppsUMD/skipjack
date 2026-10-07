"""Young voters: new registrations and party choice, from stored voter file aggregates.

Everything here reads three metrics of the ``voter_file_counts`` table (see
pipeline/voterfile.py), so it sees no individual voter:

* ``new_registrants``: first-time Maryland registrants who were 18 or older when they
  registered, by county, party, age at registration, year, and whether they registered on or
  before the snapshot's month and day ("to_date") or later in the year ("rest").
* ``new_registrant_weeks``: the same, statewide, by week of the year.
* ``age_detail_by_party``: everyone registered today, by age today.

Cells of 1 to 9 voters arrive as ``None``. Sums that include one are ``None`` too, except in
the weekly running total, where a hidden week is drawn as 5 and the page says so.
"""

from __future__ import annotations

import datetime
import json
import math
import sqlite3

from skipjack.web.dates import month_label
from skipjack.web.elections import PRIMARIES

STATEWIDE = "Maryland"
ALL = "ALL"
YOUNG = ("18-22",)
OLDER = ("23-29", "30+")
EVERYONE = YOUNG + OLDER
SUPPRESS_BELOW = 10  # the pipeline hides counts from 1 to this minus 1
MIN_COUNTY_YOUNG = 250  # fewer new young registrants than this in either year: too noisy to compare
HIDDEN_WEEK = 5  # the middle of "1 to 9", used only to draw the running total
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
OLDER_AGE_BANDS = ("23-29", "30-44", "45-64", "65+")
AGE_TABLE_BANDS = ("18-22", "23-29", "30-44", "45-64", "65+")
PARTY_COLUMNS = [("DEM", "Democratic"), ("REP", "Republican"), ("UNA", "Unaffiliated")]
OTHER_GROUPS = ("GRN", "WCP", "OTH")


def _sum(values: list[int | None]) -> int | None:
    """Sum, or None if any cell is hidden."""
    return None if any(v is None for v in values) else sum(values)


def pct(part: int | None, whole: int | None) -> float | None:
    return None if part is None or not whole else 100 * part / whole


def pct_change(before: int | None, after: int | None) -> float | None:
    return (
        None if before is None or after is None or not before else 100 * (after - before) / before
    )


def points(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


def nonleap_week(month: int, day: int) -> int:
    """Week of the year (1 to 53) of a month and day, counting a non-leap year."""
    day = 28 if (month, day) == (2, 29) else day  # 2001 had no February 29
    return (datetime.date(2001, month, day).timetuple().tm_yday - 1) // 7 + 1


def week_start_label(week: int) -> str:
    day = datetime.date(2001, 1, 1) + datetime.timedelta(days=(week - 1) * 7)
    return f"{MONTHS[day.month - 1]} {day.day}"


def snapshots_with_young(conn: sqlite3.Connection) -> list[str]:
    """Snapshot dates whose metadata says the young voter metrics exist, oldest first."""
    found = []
    for r in conn.execute("SELECT snapshot_date, meta_json FROM voter_file_snapshots"):
        if "new_registrants" in json.loads(r["meta_json"]):
            found.append(r["snapshot_date"])
    return sorted(found)


class Cells:
    """The cells of one snapshot that this page uses."""

    def __init__(self, conn: sqlite3.Connection, date: str):
        self.date = date
        self.meta = json.loads(
            conn.execute(
                "SELECT meta_json FROM voter_file_snapshots WHERE snapshot_date = ?", (date,)
            ).fetchone()["meta_json"]
        )
        self.new: dict[tuple, int | None] = {}
        for r in conn.execute(
            """SELECT county, party_group, age_band, reg_year, reg_window, count FROM voter_file_counts
               WHERE snapshot_date = ? AND metric = 'new_registrants'""",
            (date,),
        ):
            self.new[
                (r["county"], r["party_group"], r["age_band"], r["reg_year"], r["reg_window"])
            ] = r["count"]
        self.weeks: dict[tuple, int | None] = {
            (r["reg_year"], r["reg_week"], r["age_band"]): r["count"]
            for r in conn.execute(
                """SELECT reg_year, reg_week, age_band, count FROM voter_file_counts
                   WHERE snapshot_date = ? AND metric = 'new_registrant_weeks'""",
                (date,),
            )
        }
        self.age: dict[tuple, int | None] = {
            (r["county"], r["party_group"], r["age_band"]): r["count"]
            for r in conn.execute(
                """SELECT county, party_group, age_band, count FROM voter_file_counts
                   WHERE snapshot_date = ? AND metric = 'age_detail_by_party'""",
                (date,),
            )
        }
        info = self.meta["new_registrants"]
        self.cutoff = info["cutoff"]  # "08-12"
        self.year = int(date[:4])
        self.first_year = info["first_year"]

    def registrants(
        self, county: str, group: str, bands: tuple[str, ...], year: int, window: str | None = None
    ) -> int | None:
        """First-time registrants in a year. ``window`` is "to_date", "rest" or None for the year."""
        windows = [window] if window else ["to_date", "rest"]
        # A missing cell means zero registrants; a None cell means 1 to 9, hidden.
        return _sum([self.new.get((county, group, b, year, w), 0) for b in bands for w in windows])

    def today(self, county: str, group: str, bands: tuple[str, ...]) -> int | None:
        return _sum([self.age.get((county, group, b), 0) for b in bands])

    def counties(self) -> list[str]:
        return list(self.meta["counties"])

    @property
    def cutoff_label(self) -> str:
        month, day = self.cutoff.split("-")
        return f"{datetime.date(2001, int(month), 1).strftime('%B')} {int(day)}"


def _party_shares(cells: Cells, county: str, bands: tuple[str, ...], year: int) -> dict:
    """Democratic, Republican, Unaffiliated and other shares of registrants in a window.

    "Other" is what is left after those three, so one tiny hidden party cannot blank it.
    """
    total = cells.registrants(county, ALL, bands, year, "to_date")
    out = {"total": total}
    counts = {}
    for group, _ in PARTY_COLUMNS:
        counts[group] = cells.registrants(county, group, bands, year, "to_date")
        out[group] = pct(counts[group], total)
    other = (
        None
        if total is None or any(v is None for v in counts.values())
        else total - sum(counts.values())
    )
    out["other"] = (
        pct(other, total) if other is None or other == 0 or other >= SUPPRESS_BELOW else None
    )
    return out


def pace(cells: Cells, years: list[int]) -> list[dict]:
    rows = []
    for year in years:
        window = cells.registrants(STATEWIDE, ALL, YOUNG, year, "to_date")
        everyone = cells.registrants(STATEWIDE, ALL, EVERYONE, year, "to_date")
        complete = year < cells.year
        full = cells.registrants(STATEWIDE, ALL, YOUNG, year) if complete else None
        rows.append(
            {
                "year": year,
                "window": window,
                "full": full,
                "after_share": pct(None if full is None or window is None else full - window, full),
                "young_share": pct(window, everyone),
            }
        )
    return rows


def running_total(cells: Cells, year: int, last_week: int | None, known_total: int | None) -> dict:
    """Cumulative 18-to-22 registrations by week of ``year``.

    A week with 1 to 9 registrants is hidden. Each hidden week is drawn as a value from 1 to 9
    chosen so the line ends at ``known_total`` (this year's count to the cutoff, or an earlier
    year's whole year), or as 5 when that is not possible. ``hidden`` counts those weeks.
    """
    final = last_week or 53
    counts = [cells.weeks.get((year, w, "18-22"), 0) for w in range(1, final + 1)]
    hidden = [i for i, c in enumerate(counts) if c is None]
    seen = sum(c for c in counts if c is not None)
    fill = [HIDDEN_WEEK] * len(hidden)
    if hidden and known_total is not None:
        residual = known_total - seen
        if len(hidden) <= residual <= 9 * len(hidden):
            base, extra = divmod(residual, len(hidden))
            fill = [base + (1 if i < extra else 0) for i in range(len(hidden))]
    for i, value in zip(hidden, fill, strict=True):
        counts[i] = value
    values: list[int | None] = []
    total = 0
    for week in range(1, 54):
        if week > final:
            values.append(None)
            continue
        total += counts[week - 1]
        values.append(total)
    return {"year": year, "values": values, "hidden": len(hidden)}


def weeks_through(cells: Cells, year: int, last_week: int) -> tuple[int, int]:
    """First-time 18-to-22 registrants in the first ``last_week`` weeks of ``year``, and how many of
    those weeks were hidden (1 to 9 voters) and counted as 5."""
    total, hidden = 0, 0
    for week in range(1, last_week + 1):
        cell = cells.weeks.get((year, week, "18-22"), 0)
        if cell is None:
            cell, hidden = HIDDEN_WEEK, hidden + 1
        total += cell
    return total, hidden


def estimate_full_year(cells: Cells, rows: list[dict]) -> tuple[int, int] | None:
    """A rough range for this year's total from the share earlier years had left after the cutoff."""
    now = next((r for r in rows if r["year"] == cells.year), None)
    shares = [r["after_share"] for r in rows if r["after_share"] is not None]
    if not now or not now["window"] or not shares or max(shares) >= 100:
        return None
    guesses = [now["window"] / (1 - s / 100) for s in shares]
    return round(min(guesses), -3), round(max(guesses), -3)


GAP_POINTS = 4  # a difference this big between the two lines counts as a real gap


def _trend_note(trend: dict) -> str:
    """One sentence on when the young and older unaffiliated shares pulled apart, from the data."""
    years = trend["years"]
    gaps = [
        None if a is None or b is None else a - b
        for a, b in zip(trend["young"], trend["older"], strict=True)
    ]
    start = None
    for i in range(len(years) - 1, -1, -1):  # the earliest year from which every gap is large
        if gaps[i] is None or gaps[i] < GAP_POINTS:
            break
        start = i
    if start is None or start == 0:
        return ""
    before = [abs(g) for g in gaps[:start] if g is not None]
    after = [g for g in gaps[start:] if g is not None]
    return (
        f"From {years[0]} through {years[start - 1]}, new registrants aged 18 to 22 and older ones chose "
        f"unaffiliated at similar rates, within {math.ceil(max(before))} points of each other in every year. "
        f"Since {years[start]} the young share has been higher every year, by roughly {min(after):.0f} to "
        f"{max(after):.0f} points."
    )


def build_young(conn: sqlite3.Connection) -> dict | None:
    """Everything the young voters page shows, or None if no snapshot has the metrics."""
    dates = snapshots_with_young(conn)
    if not dates:
        return None
    cells = Cells(conn, dates[-1])
    year = cells.year
    candidates = [year - 8, year - 4, year]
    compare = [y for y in candidates if cells.registrants(STATEWIDE, ALL, YOUNG, y, "to_date")]
    prior = compare[-2] if len(compare) > 1 else None  # the cycle before this one
    first = compare[0]

    rows = pace(cells, compare)
    by_year = {r["year"]: r for r in rows}
    changes = {
        y: pct_change(by_year[y]["window"], by_year[year]["window"]) for y in compare if y != year
    }

    # Running total by week.
    cutoff_month, cutoff_day = (int(x) for x in cells.cutoff.split("-"))
    cutoff_week = nonleap_week(cutoff_month, cutoff_day)
    series = [
        running_total(
            cells,
            y,
            cutoff_week if y == year else None,
            by_year[y]["window"] if y == year else by_year[y]["full"],
        )
        for y in compare
    ]
    primary_weeks = {}
    for election in PRIMARIES:
        if int(election[:4]) in compare:
            primary_weeks[int(election[:4])] = nonleap_week(int(election[5:7]), int(election[8:10]))

    # Share unaffiliated among new registrants, each year, January 1 to the cutoff.
    trend_years = list(range(cells.first_year, year + 1))
    trend = {"years": trend_years, "young": [], "older": [], "young_n": [], "older_n": []}
    for y in trend_years:
        for key, bands in (("young", YOUNG), ("older", OLDER)):
            total = cells.registrants(STATEWIDE, ALL, bands, y, "to_date")
            trend[key].append(pct(cells.registrants(STATEWIDE, "UNA", bands, y, "to_date"), total))
            trend[f"{key}_n"].append(total)

    trend_note = _trend_note(trend)
    parties = [
        {
            "year": y,
            "young": _party_shares(cells, STATEWIDE, YOUNG, y),
            "older": _party_shares(cells, STATEWIDE, OLDER, y),
        }
        for y in compare
    ]

    # Everyone registered today, by age today.
    age_rows = []
    for band in AGE_TABLE_BANDS:
        n = cells.today(STATEWIDE, ALL, (band,))
        age_rows.append(
            {
                "band": band,
                "n": n,
                **{g: pct(cells.today(STATEWIDE, g, (band,)), n) for g, _ in PARTY_COLUMNS},
            }
        )
    older_n = cells.today(STATEWIDE, ALL, OLDER_AGE_BANDS)
    age_rows.append(
        {
            "band": "All 23 and older",
            "n": older_n,
            **{
                g: pct(cells.today(STATEWIDE, g, OLDER_AGE_BANDS), older_n)
                for g, _ in PARTY_COLUMNS
            },
            "total": True,
        }
    )
    left_out = {
        "under_18": cells.today(STATEWIDE, ALL, ("Under 18",)),
        "unknown": cells.today(STATEWIDE, ALL, ("Unknown",)),
    }

    # Counties: this cycle against the one before it.
    counties = []
    if prior:
        for county in cells.counties():
            young_then = cells.registrants(county, ALL, YOUNG, prior, "to_date")
            young_now = cells.registrants(county, ALL, YOUNG, year, "to_date")
            everyone_then = cells.registrants(county, ALL, EVERYONE, prior, "to_date")
            everyone_now = cells.registrants(county, ALL, EVERYONE, year, "to_date")
            enough = all(n is not None and n >= MIN_COUNTY_YOUNG for n in (young_then, young_now))
            rep_then = pct(cells.registrants(county, "REP", YOUNG, prior, "to_date"), young_then)
            rep_now = pct(cells.registrants(county, "REP", YOUNG, year, "to_date"), young_now)
            una_young = pct(
                cells.today(county, "UNA", ("18-22",)), cells.today(county, ALL, ("18-22",))
            )
            una_older = pct(
                cells.today(county, "UNA", OLDER_AGE_BANDS),
                cells.today(county, ALL, OLDER_AGE_BANDS),
            )
            counties.append(
                {
                    "county": county,
                    "young_then": young_then,
                    "young_now": young_now,
                    "young_change": pct_change(young_then, young_now),
                    "all_change": pct_change(everyone_then, everyone_now),
                    "enough": enough,
                    "rep_then": rep_then if enough else None,
                    "rep_now": rep_now if enough else None,
                    "rep_points": points(rep_then, rep_now) if enough else None,
                    "gap_points": points(una_older, una_young),
                }
            )

    # People leave the list (they move away, die or are removed), so a year's registrants are
    # fewer in a later file than in an earlier one. Two snapshots show how many, for exactly the
    # same stretch of the year: this is the least the older years were undercounted by.
    shrink, adjusted = [], {}
    shrink_label = None
    if len(dates) > 1:
        before = Cells(conn, dates[-2])
        shrink_label = month_label(before.date[:7])
        for y in compare:
            if y >= before.year or y == year:
                continue
            was, hidden_was = weeks_through(before, y, cutoff_week)
            now, hidden_now = weeks_through(cells, y, cutoff_week)
            was_full = before.registrants(STATEWIDE, ALL, YOUNG, y)
            now_full = cells.registrants(STATEWIDE, ALL, YOUNG, y)
            if not was:
                continue
            lost = 100 * (was - now) / was
            shrink.append(
                {
                    "year": y,
                    "was": was,
                    "now": now,
                    "lost_pct": lost,
                    "was_full": was_full,
                    "now_full": now_full,
                    "lost_full_pct": pct(
                        None if was_full is None or now_full is None else was_full - now_full,
                        was_full,
                    ),
                }
            )
            # What this year's window count would be with the voters seen leaving put back.
            window_now = by_year[y]["window"]
            if window_now and lost < 100:
                restored = window_now / (1 - lost / 100)
                adjusted[y] = {
                    "restored": round(restored),
                    "change": pct_change(round(restored), by_year[year]["window"]),
                }

    return {
        "snapshot": cells.date,
        "snapshot_label": month_label(cells.date[:7]),
        "year": year,
        "prior": prior,
        "first": first,
        "compare": compare,
        "cutoff_label": cells.cutoff_label,
        "pace": rows,
        "changes": changes,
        "estimate": estimate_full_year(cells, rows),
        "series": series,
        "primary_weeks": primary_weeks,
        "cutoff_week": cutoff_week,
        "week_labels": [week_start_label(w) for w in range(1, 54)],
        "month_weeks": {MONTHS[m - 1]: nonleap_week(m, 1) for m in range(1, 13)},
        "trend": trend,
        "trend_note": trend_note,
        "parties": parties,
        "age_rows": age_rows,
        "left_out": left_out,
        "counties": counties,
        "min_county": MIN_COUNTY_YOUNG,
        "shrink": shrink,
        "shrink_label": shrink_label,
        "adjusted": adjusted,
    }
