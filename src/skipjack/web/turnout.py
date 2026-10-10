"""Turnout data for the site, read from the turnout tables.

The queries all read one grain at a time. County-level rows in the county geography
carry a county and no district. The statewide figures the site shows are those county
rows summed, per family and snapshot; the totals the State Board prints were checked
against these rows at parse time and are not stored.
"""

from __future__ import annotations

import sqlite3

from skipjack.pipeline.turnout import ELECTIONS

_SENT_MEASURES = ("sent", "received", "accepted")
_REQUEST_METHODS = ("agent", "faxed", "in-person", "mailed", "others", "print-at-home")


def _snapshot_dates(conn: sqlite3.Connection, election: str, family: str) -> list[str]:
    """Every snapshot of one family of one election, oldest first."""
    return [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT as_of FROM turnout_counts
               WHERE election = ? AND family = ? ORDER BY as_of""",
            (election, family),
        )
    ]


def _statewide(conn: sqlite3.Connection, election: str, family: str, as_of: str) -> dict[str, int]:
    """The statewide sum of every measure of one family at one snapshot (county grain)."""
    return {
        r["measure"]: r["v"]
        for r in conn.execute(
            """SELECT measure, SUM(value) AS v FROM turnout_counts
               WHERE election = ? AND family = ? AND geography = 'county'
                 AND county != '' AND district = '' AND category = 'ALL' AND party = 'ALL'
                 AND as_of = ?
               GROUP BY measure""",
            (election, family, as_of),
        )
    }


def _county_cells(
    conn: sqlite3.Connection, election: str, family: str, county: str, as_of: str
) -> dict[tuple[str, str], int]:
    """One county's party x measure counts for one family at one snapshot (ALL category)."""
    return {
        (r["party"], r["measure"]): r["value"]
        for r in conn.execute(
            """SELECT party, measure, value FROM turnout_counts
               WHERE election = ? AND family = ? AND geography = 'county'
                 AND county = ? AND district = '' AND category = 'ALL' AND as_of = ?""",
            (election, family, county, as_of),
        )
    }


def elections(conn: sqlite3.Connection) -> list[dict]:
    """Every election that has turnout data, newest election day first.

    A row pairs the pipeline's election record (name, election day) with the set of
    snapshot dates it covers, per report family; an election without a config entry
    reads under its code.
    """
    codes = [r[0] for r in conn.execute("SELECT DISTINCT election FROM turnout_counts")]
    out = []
    for code in sorted(
        codes,
        key=lambda code: ELECTIONS[code].election_day if code in ELECTIONS else "",
        reverse=True,
    ):
        config = ELECTIONS.get(code)
        snapshots = {
            family["family"]: _snapshot_dates(conn, code, family["family"])
            for family in conn.execute(
                "SELECT DISTINCT family FROM turnout_counts WHERE election = ?", (code,)
            )
        }
        latest = max(
            (dates[-1] for dates in snapshots.values() if dates),
            default=None,
        )
        out.append(
            {
                "code": code,
                "slug": code.lower(),
                "name": config.name if config else code,
                "election_day": config.election_day if config else None,
                "snapshots": snapshots,
                "latest": latest,
            }
        )
    return out


def headline(conn: sqlite3.Connection, election: str) -> dict:
    """An election's latest statewide figures by family, with the change since the
    previous snapshot where a previous snapshot exists.
    """
    out: dict = {"latest": None, "previous": None}
    for r in conn.execute(
        "SELECT DISTINCT family FROM turnout_counts WHERE election = ? ORDER BY family",
        (election,),
    ):
        family = r[0]
        dates = _snapshot_dates(conn, election, family)
        if not dates:
            continue
        entry: dict = {"as_of": dates[-1]}
        entry["totals"] = _statewide(conn, election, family, dates[-1])
        out[family] = entry
        if len(dates) > 1:
            entry["previous"] = dates[-2]
            before = _statewide(conn, election, family, dates[-2])
            entry["change"] = {m: entry["totals"][m] - before.get(m, 0) for m in entry["totals"]}
        if family == "requests":
            entry["requests_total"] = entry["totals"].get("total")
            entry["mailed"] = entry["totals"].get("mailed")
            entry["print_at_home"] = entry["totals"].get("print-at-home")
        if family == "sent-returned":
            totals = entry["totals"]
            sent, received, accepted = (totals.get(m) for m in _SENT_MEASURES)
            entry.update({"sent": sent, "received": received, "accepted": accepted})
            if sent:
                entry["return_rate"] = received / sent
        if out["latest"] is None or entry["as_of"] > out["latest"]:
            out["latest"] = entry["as_of"]
            out["previous"] = entry["previous"] if "previous" in entry else None
    return out


def previous_as_of(conn: sqlite3.Connection, election: str, family: str, as_of: str) -> str | None:
    """The snapshot of this family immediately before `as_of`, if one exists."""
    dates = _snapshot_dates(conn, election, family)
    try:
        i = dates.index(as_of)
    except ValueError:
        return None
    return dates[i - 1] if i > 0 else None


def series(conn: sqlite3.Connection, election: str, family: str) -> dict:
    """The statewide trend of one family, by snapshot date (chart data).

    The date axis is every snapshot the family has; a measure with no numbers on a
    given date gets None, so the line simply stops there.
    """
    dates = _snapshot_dates(conn, election, family)
    measures = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT measure FROM turnout_counts
               WHERE election = ? AND family = ? ORDER BY measure""",
            (election, family),
        )
    ]
    series: dict[str, list[int | None]] = {measure: [] for measure in measures}
    for date in dates:
        totals = _statewide(conn, election, family, date)
        for measure in measures:
            series[measure].append(totals.get(measure))
    return {"dates": dates, "series": series}


def county_rows(conn: sqlite3.Connection, election: str, as_of: str) -> list[dict]:
    """One sortable row per county at a snapshot, joining the families that reported it.

    Counties absent from a family's snapshot are absent here (unknown, not zero);
    counties only in the requests file read with empty mail-in columns.
    """
    counties = sorted(
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT county FROM turnout_counts
               WHERE election = ? AND geography = 'county' AND county != ''
                 AND district = '' AND category = 'ALL' AND as_of = ?
               ORDER BY county""",
            (election, as_of),
        )
    )
    out: list[dict] = []
    for county in counties:
        sent = _county_cells(conn, election, "sent-returned", county, as_of)
        method_cells = _county_cells(conn, election, "requests", county, as_of)
        row: dict = {
            "county": county,
            "sent": sent.get(("ALL", "sent")),
            "received": sent.get(("ALL", "received")),
            "accepted": sent.get(("ALL", "accepted")),
            "dem_sent": sent.get(("DEM", "sent")),
            "rep_sent": sent.get(("REP", "sent")),
            "oth_sent": sent.get(("OTH", "sent")),
            "dem_received": sent.get(("DEM", "received")),
            "rep_received": sent.get(("REP", "received")),
            "oth_received": sent.get(("OTH", "received")),
            "return_rate": (
                sent[("ALL", "received")] / sent[("ALL", "sent")]
                if sent.get(("ALL", "sent"))
                else None
            ),
            "requests_total": method_cells.get(("ALL", "total")),
            "mailed": method_cells.get(("ALL", "mailed")),
            "print_at_home": method_cells.get(("ALL", "print-at-home")),
        }
        out.append(row)
    return out


def snapshot_sources(conn: sqlite3.Connection, election: str, as_of: str) -> list[dict]:
    """The manifest records behind one snapshot, one per report family — for the page's
    source strip, with its download links."""
    return [
        {
            "family": r["family"],
            "geography": r["geography"],
            "url": r["source_url"],
            "title": r["document_title"],
            "sha256": r["sha256"],
            "fetched": r["fetch_date"][:10],
            "method": r["extraction_method"],
            "csv": f"turnout/{election}/{r['family']}/{r['geography']}/{as_of}.csv",
            "provenance": f"turnout/{election}/{r['family']}/{r['geography']}/{as_of}_provenance.json",
        }
        for r in conn.execute(
            """SELECT * FROM turnout_sources
               WHERE election = ? AND as_of = ?
               ORDER BY family, geography""",
            (election, as_of),
        )
    ]
