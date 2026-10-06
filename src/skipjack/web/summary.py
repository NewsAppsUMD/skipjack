"""Plain-language highlights for the home page, computed from the latest report.

Every sentence is built from numbers in the database, so none of them can go stale: when a
new monthly report is loaded the home page describes that report.
"""

from __future__ import annotations

import sqlite3

from skipjack.web import trends
from skipjack.web.dates import month_label, shift_month

MINUS = "−"
MAJOR = ["Democratic", "Republican", "Unaffiliated"]


def signed(n: int | float, digits: int = 0, suffix: str = "") -> str:
    """ "+1,234" or "\u22121,234" (a real minus sign, which reads better than a hyphen).

    A value that rounds to zero carries no sign, so -0.02 at one digit reads "0.0%", not "\u22120.0%".
    """
    rounded = round(n, digits)
    body = f"{abs(rounded):,.{digits}f}{suffix}"
    return f"+{body}" if rounded > 0 else f"{MINUS}{body}" if rounded < 0 else body


def direction(n: int | float) -> str:
    return "up" if n > 0 else "down" if n < 0 else "unchanged"


def _party_series(conn: sqlite3.Connection) -> tuple[list[str], dict[str, list[int | None]]]:
    """Statewide active voters by party name and month (UNAF and UNA are both Unaffiliated)."""
    rows = conn.execute(
        """SELECT report_date, party_name, SUM(active_voters) AS voters
           FROM voter_registration GROUP BY report_date, party_name ORDER BY report_date"""
    ).fetchall()
    dates = sorted({r["report_date"] for r in rows})
    index = {d: i for i, d in enumerate(dates)}
    by_party: dict[str, list[int | None]] = {}
    for r in rows:
        by_party.setdefault(r["party_name"], [None] * len(dates))[index[r["report_date"]]] = r[
            "voters"
        ]
    return dates, by_party


def _county_totals(conn: sqlite3.Connection, report_date: str) -> dict[str, int]:
    return {
        r["county"]: r["voters"]
        for r in conn.execute(
            """SELECT county, SUM(active_voters) AS voters FROM voter_registration
               WHERE report_date = ? GROUP BY county""",
            (report_date,),
        )
    }


def highlights(conn: sqlite3.Connection) -> dict:
    """The latest month, a few sentences about it, and the series for the home page chart."""
    dates, by_party = _party_series(conn)
    if not dates:
        return {"latest": None, "total": 0, "sentences": [], "chart": None}
    latest = dates[-1]
    i = len(dates) - 1
    totals = [sum(v[k] or 0 for v in by_party.values()) for k in range(len(dates))]
    items: list[str] = []

    def change(back: int) -> tuple[str, int, float] | None:
        month = shift_month(latest, -back)
        if month not in dates:
            return None
        before = totals[dates.index(month)]
        return month, totals[i] - before, 100 * (totals[i] - before) / before

    parts = []
    for back, name in ((1, "the month before"), (12, "a year earlier")):
        c = change(back)
        if c:
            parts.append(
                f"{direction(c[1])} {abs(c[1]):,} ({signed(c[2], 1, '%')}) from "
                f"{month_label(c[0]) if back == 1 else name}"
            )
    items.append(
        f"{totals[i]:,} active registered voters in {month_label(latest)}"
        + (": " + ", and ".join(parts) if parts else "")
        + "."
    )

    shares = {
        p: [
            100 * v / t if v is not None and t else None
            for v, t in zip(by_party[p], totals, strict=True)
        ]
        for p in MAJOR
        if p in by_party
    }
    now = {p: s[i] for p, s in shares.items()}
    year_ago = shift_month(latest, -12)
    sentence = (
        "Democrats are {Democratic:.1f}% of active voters, Republicans {Republican:.1f}% "
        "and unaffiliated voters {Unaffiliated:.1f}%"
    )
    if all(p in now and now[p] is not None for p in MAJOR):
        sentence = sentence.format(**now)
        if year_ago in dates:
            j = dates.index(year_ago)
            then = {p: shares[p][j] for p in MAJOR}
            if all(v is not None for v in then.values()):
                sentence += (
                    f", against {then['Democratic']:.1f}%, {then['Republican']:.1f}% and "
                    f"{then['Unaffiliated']:.1f}% a year earlier"
                )
        items.append(sentence + ".")
        for p in MAJOR:
            known = [v for v in shares[p] if v is not None]
            if len(known) > 24 and now[p] == max(known):
                items.append(
                    f"{p} voters' share is the highest in any report since {month_label(dates[0])}."
                )
            elif len(known) > 24 and now[p] == min(known):
                items.append(
                    f"{p} voters' share is the lowest in any report since {month_label(dates[0])}."
                )

    new, gone = trends.new_registrations(conn), trends.removals(conn)
    if latest in new.dates and latest in gone.dates:
        n, r = new.total[new.dates.index(latest)], gone.total[gone.dates.index(latest)]
        text = f"The {month_label(latest).split()[0]} report counts {n:,} new registrations and {r:,} removals"
        top = max(new.groups, key=lambda g: g.monthly[new.dates.index(latest)] or 0)
        top_n = top.monthly[new.dates.index(latest)] or 0
        if n and top_n:
            text += f"; {100 * top_n / n:.0f}% of the new registrations came through {top.name}"
        moved = next((g for g in gone.groups if g.name == "Moved to another county"), None)
        moved_n = moved.monthly[gone.dates.index(latest)] if moved else None
        if moved_n:
            text += f", and {moved_n:,} of the removals were moves to another county"
        items.append(text + ".")

    inactive = conn.execute(
        "SELECT SUM(value) AS v FROM registration_activity WHERE measure = 'inactive' AND report_date = ?",
        (latest,),
    ).fetchone()["v"]
    if inactive:
        items.append(
            f"{inactive:,} registered voters are inactive, {100 * inactive / (inactive + totals[i]):.1f}% "
            "of everyone on the rolls."
        )

    if year_ago in dates:
        now_c, then_c = _county_totals(conn, latest), _county_totals(conn, year_ago)
        growth = {c: 100 * (now_c[c] - then_c[c]) / then_c[c] for c in now_c if then_c.get(c)}
        if growth:
            fast = max(growth, key=growth.get)
            slow = min(growth, key=growth.get)
            text = f"Over the past year {fast} grew fastest ({signed(growth[fast], 1, '%')})"
            if growth[slow] < 0:
                text += f" and {slow} lost the most ({signed(growth[slow], 1, '%')})"
            items.append(text + ".")

    return {
        "latest": latest,
        "total": totals[i],
        "sentences": items,
        "chart": {
            "dates": dates,
            "series": [{"name": p, "values": by_party[p]} for p in MAJOR if p in by_party],
        },
    }
