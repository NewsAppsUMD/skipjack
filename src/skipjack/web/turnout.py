"""Turnout data for the site, read from the turnout tables.

The queries all read one grain at a time. County-level rows in the county geography
carry a county and no district. The congressional-district rows in the congressional
geography carry a district and no county, and the two printed spellings of a district
join on district_label. The statewide figures the site shows are those county
rows summed, per family and snapshot; the totals the State Board prints were checked
against these rows at parse time and are not stored.
"""

from __future__ import annotations

import re

import sqlite3

from skipjack.pipeline.turnout import ELECTIONS, may_publish

_SENT_MEASURES = ("sent", "received", "accepted")

# The four cast-ballot modes, in the order the certified pages show them.
_MODES = ("early-voting", "election-day", "vote-by-mail", "provisional")

_DISTRICT_RE = re.compile(r"(?:Congressional District Code )?(\d{1,2})")


def district_label(raw: str) -> str:
    """A congressional-district row's printed code, as the page shows it: the xlsx
    sheets spell 'Congressional District Code 01' where the PDF tables print '01' —
    both read as 'District 1'. Anything the reports do not spell as a numbered
    district (Undetermined) passes through as its own label."""
    if m := _DISTRICT_RE.fullmatch(raw.strip()):
        return f"District {int(m.group(1))}"
    return raw.strip()


def district_sort(raw: str) -> tuple:
    """Sort key for the printed district codes: districts 1-8 by number whatever the
    spelling, everything else (Undetermined) after them."""
    if m := _DISTRICT_RE.fullmatch(raw.strip()):
        return (0, int(m.group(1)), "")
    return (1, 0, raw.strip())


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


# The area column and its match filter, per geography: county rows carry a county and
# no district; congressional rows the reverse.
_GEOGRAPHY_FILTERS = {
    "county": ("county", "county = :area AND district = ''"),
    "congressional": ("district", "county = '' AND district = :area"),
}


def _area_cells(
    conn: sqlite3.Connection,
    election: str,
    family: str,
    geography: str,
    area: str,
    as_of: str,
) -> dict[tuple[str, str], int]:
    """One area's (a county's or a district's) party x measure counts for one family at
    one snapshot (ALL category).

    ``area`` is the county's or district's value as the report prints it — for
    districts that may be either printed spelling (see district_label).
    """
    area_filter = _GEOGRAPHY_FILTERS[geography][1]
    return {
        (r["party"], r["measure"]): r["value"]
        for r in conn.execute(
            f"""SELECT party, measure, value FROM turnout_counts
               WHERE election = :election AND family = :family AND geography = :geography
                 AND {area_filter} AND category = 'ALL' AND as_of = :as_of""",
            {
                "election": election,
                "family": family,
                "geography": geography,
                "area": area,
                "as_of": as_of,
            },
        )
    }


def _area_measures(
    conn: sqlite3.Connection,
    election: str,
    family: str,
    geography: str,
    area: str,
    as_of: str,
) -> dict[str, int]:
    """One area's measure values for one family, at the row the area's Total printed
    (party and category ALL)."""
    return {
        r["measure"]: r["value"]
        for r in conn.execute(
            f"""SELECT measure, value FROM turnout_counts
               WHERE election = :election AND family = :family AND geography = :geography
                 AND {_GEOGRAPHY_FILTERS[geography][1]} AND category = 'ALL'
                 AND party = 'ALL' AND as_of = :as_of""",
            {
                "election": election,
                "family": family,
                "geography": geography,
                "area": area,
                "as_of": as_of,
            },
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
            before = _statewide(conn, election, family, dates[-2])
            if before:  # a previous snapshot with no rows reads as no previous at all
                entry["previous"] = dates[-2]
                entry["change"] = {
                    m: entry["totals"][m] - before.get(m, 0) for m in entry["totals"]
                }
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


def _sent_row(label_key: str, label: str, sent: dict, method_cells: dict) -> dict:
    """The two families' counts as one table row: mail-in sent/received/accepted with
    the party splits, and the request totals — carrying the area's label under
    ``label_key``."""
    row: dict = {
        label_key: label,
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
            sent[("ALL", "received")] / sent[("ALL", "sent")] if sent.get(("ALL", "sent")) else None
        ),
        "requests_total": method_cells.get(("ALL", "total")),
        "mailed": method_cells.get(("ALL", "mailed")),
        "print_at_home": method_cells.get(("ALL", "print-at-home")),
    }
    return row


def county_rows(
    conn: sqlite3.Connection, election: str, as_of: str, requests_as_of: str | None = None
) -> list[dict]:
    """One sortable row per county, joining both families at each family's own date.

    The two press-room files refresh on their own schedules, so the mail-in columns and
    the request columns can be from different snapshot days; each is read at its own
    family's latest date (requests_as_of defaults to the same day). A county absent
    from a family's snapshot is absent from the row (unknown, not zero); counties only
    in the requests file read with empty mail-in columns.
    """
    requests_as_of = requests_as_of or as_of
    counties = sorted(
        {
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT county FROM turnout_counts
                   WHERE election = ? AND geography = 'county' AND county != ''
                     AND district = '' AND category = 'ALL'
                     AND as_of IN (?, ?)
                   ORDER BY county""",
                (election, as_of, requests_as_of),
            )
        }
    )
    out: list[dict] = []
    for county in counties:
        sent = _area_cells(conn, election, "sent-returned", "county", county, as_of)
        method_cells = _area_cells(conn, election, "requests", "county", county, requests_as_of)
        if not sent and not method_cells:
            continue  # the county reports on the other day only
        out.append(_sent_row("county", county, sent, method_cells))
    return out


def _district_areas(
    conn: sqlite3.Connection, election: str, family_dates: dict[str, str]
) -> dict[str, dict[str, str]]:
    """The districts found in the given families' snapshots at congressional grain,
    keyed by the normalized label, each mapping family → the spelling that family
    prints. One election's files spell the districts two ways (the xlsx sheets print
    'Congressional District Code 01' where the PDF tables print '01'), so the families
    join on the normalized label, not on the raw code."""
    out: dict[str, dict[str, str]] = {}
    for family, as_of in family_dates.items():
        for (district,) in conn.execute(
            """SELECT DISTINCT district FROM turnout_counts
               WHERE election = ? AND family = ? AND geography = 'congressional'
                 AND county = '' AND district != '' AND category = 'ALL'
                 AND as_of = ?""",
            (election, family, as_of),
        ):
            out.setdefault(district_label(district), {})[family] = district
    return out


def district_rows(
    conn: sqlite3.Connection, election: str, as_of: str, requests_as_of: str | None = None
) -> list[dict]:
    """One sortable row per congressional district, joining both families at each
    family's own date — the district-grain mirror of county_rows.

    The two printed spellings of a district join on the normalized label (see
    _district_areas); Undetermined sorts last. A district present in one family's
    snapshot but not the other's reads unknown there, not zero; a district in neither
    family's snapshot is absent from the rows.
    """
    requests_as_of = requests_as_of or as_of
    if not (
        areas := _district_areas(
            conn, election, {"sent-returned": as_of, "requests": requests_as_of}
        )
    ):
        return []
    out: list[dict] = []
    # both spellings of a label sort identically: district_sort keys on the district number
    for label, spellings in sorted(
        areas.items(), key=lambda kv: district_sort(next(iter(kv[1].values())))
    ):
        sent = (
            _area_cells(
                conn,
                election,
                "sent-returned",
                "congressional",
                spellings["sent-returned"],
                as_of,
            )
            if "sent-returned" in spellings
            else {}
        )
        method_cells = (
            _area_cells(
                conn,
                election,
                "requests",
                "congressional",
                spellings["requests"],
                requests_as_of,
            )
            if "requests" in spellings
            else {}
        )
        if not sent and not method_cells:
            continue
        out.append(_sent_row("district", label, sent, method_cells))
    return out


def snapshot_sources(
    conn: sqlite3.Connection, election: str, as_of: str | None = None
) -> list[dict]:
    """The manifest records behind a snapshot — for the page's source strip, with its
    download links. With no as-of, the latest of each family (and geography) wins:
    a file re-posted later the same day leaves more than one record here.
    """
    if as_of is None:
        rows = conn.execute(
            """SELECT * FROM turnout_sources
               WHERE election = ?
               ORDER BY family, geography, as_of""",
            (election,),
        ).fetchall()
        latest: dict[tuple[str, str], dict] = {}
        for r in rows:
            if r["as_of"] and r["as_of"] >= latest.get((r["family"], r["geography"]), {}).get(
                "as_of", ""
            ):
                latest[(r["family"], r["geography"])] = dict(r)
        rows = list(latest.values())
    else:
        rows = conn.execute(
            """SELECT * FROM turnout_sources
               WHERE election = ? AND as_of = ?
               ORDER BY family, geography""",
            (election, as_of),
        ).fetchall()
    out = []
    for r in sorted(rows, key=lambda r: (r["family"], r["geography"])):
        family_as_of = as_of or r["as_of"]
        out.append(
            {
                "family": r["family"],
                "geography": r["geography"],
                "as_of": family_as_of,
                "url": r["source_url"],
                "title": r["document_title"],
                "fetched": r["fetch_date"][:10],
                "method": r["extraction_method"],
                # a certified election does not publish its unofficial tally families:
                # the strip still documents the read, but links to nothing unpublished
                "csv": (
                    f"turnout/{election}/{r['family']}/{r['geography']}/{family_as_of}.csv"
                    if may_publish(election, r["family"])
                    else None
                ),
                "provenance": (
                    f"turnout/{election}/{r['family']}/{r['geography']}/{family_as_of}_provenance.json"
                    if may_publish(election, r["family"])
                    else None
                ),
            }
        )
    return out


def chart_data(conn: sqlite3.Connection, election: str) -> dict | None:
    """Chart data for the election page: both families' trends on one day axis.

    The axis is every snapshot day with any figures; each series stops where its
    family has no figures that day. A series that is zero everywhere (accepted counts
    before the canvass) is left off the chart on purpose — the tiles carry that number;
    a line that starts at zero and later moves does appear.
    """
    trends = {family: series(conn, election, family) for family in ("requests", "sent-returned")}
    all_dates = sorted({d for trend in trends.values() for d in trend["dates"]})
    if not all_dates:
        return None

    def aligned(family: str, measure: str):
        trend = trends[family]
        if not trend["dates"]:
            return None
        date_index = {d: i for i, d in enumerate(trend["dates"])}
        values = trend["series"].get(measure, [None] * len(trend["dates"]))
        return [values[date_index[d]] if d in date_index else None for d in all_dates]

    wanted = [
        ("requests", "total", "Requests"),
        ("sent-returned", "sent", "Sent"),
        ("sent-returned", "received", "Received"),
        ("sent-returned", "accepted", "Accepted"),
    ]
    out = []
    for family, measure, name in wanted:
        values = aligned(family, measure)
        if values is not None and any(values):
            out.append({"name": name, "values": values})
    return {"dates": all_dates, "series": out} if out else None


# --- the certified (final) election figures ----------------------------------------------------

CERTIFIED_CANVASS_FAMILIES = ("county-canvass", "votes-accepted")

# The certified record families the page tables read. A certified election's pages
# show its figures of record — the canvass, the accepted-ballot count, the certified
# mail-in and request reports, and the official turnout sheets — while the
# campaign-time tracking files (sent-returned, early-voting, same-day,
# left-to-count) stay in the archive and off the pages.
# Downloads gate differently: the pipeline's may_publish (UNOFFICIAL_TALLY_FAMILIES)
# still lets a certified election publish its certified sent-returned CSVs. The two
# lists differ on purpose — page tables read figures of record; the archive keeps the
# campaign-time files.
CERTIFIED_PAGE_FAMILIES = (
    "county-canvass",
    "votes-accepted",
    "mail-voting",
    "requests",
    "official-turnout",
)


def _family_state(
    conn: sqlite3.Connection, election: str, family: str, as_of: str
) -> dict[str, int]:
    """The state's counts for one certified family at one snapshot: summed over the
    counties, at the row the county's own Total printed (the eligible workbooks publish
    a row per party plus the Total column; the certified reports' single rows carry
    'ALL' already) — the printed state rows are checked at parse time.
    """
    return {
        r["measure"]: r["v"]
        for r in conn.execute(
            """SELECT measure, SUM(value) AS v FROM turnout_counts
               WHERE election = ? AND family = ? AND geography = 'county'
                 AND county != '' AND district = '' AND category = 'ALL'
                 AND party = 'ALL'
                 AND as_of = ?
               GROUP BY measure""",
            (election, family, as_of),
        )
    }


def _family_county(
    conn: sqlite3.Connection, election: str, family: str, county: str, as_of: str
) -> dict[str, int]:
    """One county's counts for one certified family at one snapshot, at the row the
    county's Total printed (see _family_state)."""
    return _area_measures(conn, election, family, "county", county, as_of)


def certified_headline(conn: sqlite3.Connection, election: str) -> dict:
    """A certified election's statewide finals: ballots cast and counted, the four
    modes with their shares, and the eligible-voter totals where the SBE's denominator
    files have republished.

    Every figure is summed from the county rows at each family's latest snapshot;
    the printed state rows were checked against these at parse time and are not stored.
    """
    dates = {
        r[0]: _snapshot_dates(conn, election, r[0])[-1]
        for r in conn.execute(
            "SELECT DISTINCT family FROM turnout_counts WHERE election = ?", (election,)
        )
    }
    out: dict = {"as_ofs": dates}
    canvass = (
        _family_state(conn, election, "county-canvass", dates["county-canvass"])
        if "county-canvass" in dates
        else {}
    )
    accepted = (
        _family_state(conn, election, "votes-accepted", dates["votes-accepted"])
        if "votes-accepted" in dates
        else {}
    )
    out["cast"] = canvass.get("total")
    out["counted"] = accepted.get("total")
    for family, measures in (
        ("mail-voting", ("sent", "received", "accepted", "rejected")),
        ("requests", ("total",)),
    ):
        if family in dates:
            state = _family_state(conn, election, family, dates[family])
            out[family] = {m: state.get(m) for m in measures}
    by_mode = {
        m: {
            "cast": canvass.get(m),
            "counted": accepted.get(m),
            "share": (
                canvass[m] / canvass["total"]
                if canvass.get("total") and canvass.get(m) is not None
                else None
            ),
        }
        for m in _MODES
        if m in canvass or m in accepted
    }
    out["by_mode"] = by_mode
    # the board's own printed percentage: its official turnout sheet's ballots cast
    # over the eligible voters the sheet itself prints (both summed from the county
    # rows the parse checked against its printed state rows). The sheet's register is
    # what the board computed its percentage over — for the 2024-era sheets, active
    # registration; the guide's construction below is the fallback while the sheet has
    # not republished.
    if sheet_dates := dates.get("official-turnout"):
        sheet = _family_state(conn, election, "official-turnout", sheet_dates)
        sheet_modes = sum(sheet.get(m) or 0 for m in _MODES)
        if sheet.get("eligible") and sheet_modes:
            out["turnout"] = {
                "value": sheet_modes / sheet["eligible"],
                "as_of": sheet_dates,
                "printed": True,
            }
    if "eligible-active" in dates:
        active = _family_state(conn, election, "eligible-active", dates["eligible-active"])
        inactive = (
            _family_state(conn, election, "eligible-inactive", dates["eligible-inactive"])
            if "eligible-inactive" in dates
            else {}
        )
        # a turnout percentage claims an "active and inactive together" denominator;
        # show it only once both parts are published
        if active.get("active") is not None and inactive.get("inactive") is not None:
            eligible = active["active"] + inactive["inactive"]
            eligible_dates = max(
                dates["eligible-active"],
                dates.get("eligible-inactive", ""),
            )
            out["eligible"] = {
                "active": active.get("active"),
                "inactive": inactive.get("inactive"),
                "total": eligible,
                "as_of": eligible_dates,
            }
            if out["cast"] and "turnout" not in out:
                out["turnout"] = {
                    "value": out["cast"] / eligible,
                    "as_of": eligible_dates,
                    "printed": False,
                }
    return out


def certified_county_turnout(
    conn: sqlite3.Connection, election: str, as_of: str | None
) -> dict[str, float]:
    """Turnout by county from the State Board's official turnout sheet: ballots in the four
    modes as a percent of eligible voters. Empty when the election has no such sheet."""
    if not as_of:
        return {}
    counties = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT county FROM turnout_counts
               WHERE election = ? AND family = 'official-turnout' AND geography = 'county'
                 AND county != '' AND as_of = ?""",
            (election, as_of),
        )
    ]
    out = {}
    for county in counties:
        sheet = _family_county(conn, election, "official-turnout", county, as_of)
        modes = sum(sheet.get(m) or 0 for m in _MODES)
        if sheet.get("eligible") and modes:
            out[county] = 100 * modes / sheet["eligible"]
    return out


def certified_county_rows(
    conn: sqlite3.Connection, election: str, dates: dict[str, str]
) -> list[dict]:
    """One sortable row per county for a certified election, joining every published
    family at that family's own snapshot: ballots by mode, the mail-in figures, and the
    early-voting/same-day totals."""
    counties = sorted(
        {
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT county FROM turnout_counts
                   WHERE election = ? AND geography = 'county' AND county != ''
                     AND district = '' AND category = 'ALL'
                     AND as_of IN (?,?,?,?)""",
                (
                    election,
                    dates.get("county-canvass", ""),
                    dates.get("votes-accepted", ""),
                    dates.get("mail-voting", ""),
                    dates.get("eligible-active", ""),
                ),
            )
        }
    )
    out: list[dict] = []
    for county in counties:
        canvass = _family_county(
            conn, election, "county-canvass", county, dates.get("county-canvass", "")
        )
        accepted = _family_county(
            conn, election, "votes-accepted", county, dates.get("votes-accepted", "")
        )
        mail_voting = (
            _family_county(
                conn, election, "mail-voting", county, dates.get("mail-voting", "") or ""
            )
            if "mail-voting" in dates
            else {}
        )
        requests = _family_county(conn, election, "requests", county, dates.get("requests", ""))
        if not canvass and not accepted and not mail_voting:
            continue
        out.append(
            {
                "county": county,
                "cast": canvass.get("total"),
                "counted": accepted.get("total"),
                "by_mode": {m: canvass.get(m) for m in _MODES if m in canvass},
                "counted_by_mode": {m: accepted.get(m) for m in _MODES if m in accepted},
                "mail_voting": {
                    "requests": requests.get("total"),
                    "sent": mail_voting.get("sent"),
                    "received": mail_voting.get("received"),
                    "accepted": mail_voting.get("accepted"),
                    "rejected": mail_voting.get("rejected"),
                },
            }
        )
    return out


def certified_district_rows(
    conn: sqlite3.Connection, election: str, dates: dict[str, str]
) -> list[dict]:
    """One sortable row per congressional district for a certified election: the
    official turnout sheet's ballots by mode and eligible figure, plus the certified
    envelope counts and counted ballots where a certified report carries the CD grain.

    Each sheet's modes over its own printed eligible are that district's turnout
    share — the board computes the statewide percentage the same way. The campaign-
    time tallies at this grain (sent-returned, early-voting, same-day) stay off the
    page (see CERTIFIED_PAGE_FAMILIES); Undetermined sorts last and, its sheet having
    no eligible voters, prints no percentage.
    """
    wanted = {f: dates[f] for f in CERTIFIED_PAGE_FAMILIES if dates.get(f)}
    if not (areas := _district_areas(conn, election, wanted)):
        return []
    out: list[dict] = []
    for label, spellings in sorted(
        areas.items(), key=lambda kv: district_sort(next(iter(kv[1].values())))
    ):
        sheet = (
            _area_measures(
                conn,
                election,
                "official-turnout",
                "congressional",
                spellings["official-turnout"],
                wanted["official-turnout"],
            )
            if "official-turnout" in spellings
            else {}
        )
        counted = (
            _area_measures(
                conn,
                election,
                "votes-accepted",
                "congressional",
                spellings["votes-accepted"],
                wanted["votes-accepted"],
            )
            if "votes-accepted" in spellings
            else {}
        )
        envelopes = (
            _area_cells(
                conn,
                election,
                "mail-voting",
                "congressional",
                spellings["mail-voting"],
                wanted["mail-voting"],
            )
            if "mail-voting" in spellings
            else {}
        )
        if not sheet and not counted and not envelopes:
            continue
        cast = sum(sheet.get(m) or 0 for m in _MODES) if sheet else None
        out.append(
            {
                "district": label,
                "cast": cast,
                "by_mode": {m: sheet[m] for m in _MODES if m in sheet},
                "eligible": sheet.get("eligible"),
                "turnout": cast / sheet["eligible"] if sheet.get("eligible") else None,
                "counted": counted.get("total"),
                "mail_voting": {
                    "accepted": envelopes.get(("ALL", "accepted")),
                    "rejected": envelopes.get(("ALL", "rejected")),
                },
            }
        )
    return out


def publishable_geographies(conn: sqlite3.Connection, election: str) -> list[str]:
    """The geography subpages to publish for an election, in page order: some report
    family has rows at that grain and, for a certified election, the family is one of
    its figures of record — the campaign-time tallies stay in the archive."""
    certified = election in ELECTIONS and ELECTIONS[election].certified
    out: list[str] = []
    for geography in ("county", "congressional"):
        column = _GEOGRAPHY_FILTERS[geography][0]
        families = {
            r[0]
            for r in conn.execute(
                f"""SELECT DISTINCT family FROM turnout_counts
                    WHERE election = ? AND geography = ?
                      AND {column} != '' AND category = 'ALL'""",
                (election, geography),
            )
        }
        if certified:
            families &= set(CERTIFIED_PAGE_FAMILIES)
        if families:
            out.append(geography)
    return out


def rejection_reason_rows(
    conn: sqlite3.Connection, election: str, family: str, as_of: str
) -> list[dict]:
    """A certified family's printed rejection breakdown, one row per reason."""
    return [
        {
            "reason": r["category"],
            "value": r["v"],
        }
        for r in conn.execute(
            """SELECT category, SUM(value) AS v FROM turnout_counts
               WHERE election = ? AND family = ? AND geography = 'county'
                 AND county = '' AND district = '' AND category != 'ALL'
                 AND measure = 'rejected' AND as_of = ?
               GROUP BY category ORDER BY v DESC, category""",
            (election, family, as_of),
        )
    ]
