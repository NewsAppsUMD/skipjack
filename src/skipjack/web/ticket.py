"""Who outran the top of the ticket on a county's general-election ballot.

Design: docs/plans/2026-11-05-outran-ticket-design.md. On a county results page for a general
election, a down-ballot candidate *outran the top of the ticket* when they got more votes in
the county than their party's candidate in the ballot's first contest (President in a
presidential year, Governor in a gubernatorial one).

The comparison is raw votes in one county, so it is only fair where every voter in the county
had the down-ballot contest on their ballot. The results files have no precinct or district
split of the top-of-ticket vote, so a contest that covers only part of the county is left out
rather than compared with a countywide figure. Which contests cover the whole county:

- statewide offices: always;
- Congress, State Senate, House of Delegates: when the county has a single district of the
  office (every voter is in some district, so one district is the whole county);
- county offices: by the votes. Several counties elect commissioners or council members
  countywide from residency districts, and the files do not say which, so a county office is
  countywide when its votes per seat reach ``LOCAL_MIN_RATIO`` of the top-of-ticket votes and,
  for an office with several numbered districts in the county, those districts' ratios add to
  at least ``PARTITION_MIN`` (districts that split the county add to about 1; districts each
  elected countywide add to about one per district). Measured on PG20, GG22, PG24 and GG26
  (2026-11-05): district seats 0.29 or less, countywide contests 0.52 or more; Dorchester's
  two-seat Orphans' Court District 2 (0.49) is caught by the partition sum (0.76).

A candidate without a real opponent is left out: one with no named (non-write-in) candidate of
another party in the contest, or in a contest with no more named candidates than seats. Voters
of every party can only vote for them, so their lead over the ticket says nothing.
"""

from __future__ import annotations

from skipjack.web.urls import slugify

TOP_OF_TICKET = ("President - Vice Pres", "Governor / Lt. Governor")
TOP_LABELS = {"President - Vice Pres": "President", "Governor / Lt. Governor": "Governor"}
# party ballot lines; unaffiliated, "other" and nonpartisan candidates have no ticket to outrun
PARTIES = ("DEM", "REP", "LIB", "GRN", "WCP")
PARTY_NAMES = {
    "DEM": "Democratic",
    "REP": "Republican",
    "LIB": "Libertarian",
    "GRN": "Green",
    "WCP": "Working Class",
}
STATE_SCOPES = ("congress", "senate", "delegates")
LOCAL_MIN_RATIO = 0.4
PARTITION_MIN = 1.2


def _ratio(contest: dict, top_total: int) -> float:
    cast = sum(c["total"] or 0 for c in contest["candidates"])
    return cast / max(1, contest["seats"]) / top_total


def countywide(contests: list[dict], top_total: int) -> list[bool]:
    """For each of ``contests`` (a county's general-election contests, top of the ticket
    first), whether every voter in the county had it on their ballot."""
    districts: dict[str, set[str]] = {}
    for c in contests:
        districts.setdefault(c["office"], set()).add(c["district"])
    ratios = [_ratio(c, top_total) if top_total else 0.0 for c in contests]
    partition: dict[str, float] = {}
    for c, r in zip(contests, ratios, strict=True):
        if c["scope"] == "local" and c["district"]:
            partition[c["office"]] = partition.get(c["office"], 0.0) + r
    out = []
    for c, r in zip(contests, ratios, strict=True):
        if c["scope"] == "statewide":
            out.append(True)
        elif c["scope"] in STATE_SCOPES:
            out.append(len(districts[c["office"]]) == 1)
        else:
            numbered = len(districts[c["office"]] - {""}) > 1
            out.append(
                r >= LOCAL_MIN_RATIO
                and (not c["district"] or not numbered or partition[c["office"]] >= PARTITION_MIN)
            )
    return out


def _unopposed(candidate: dict, contest: dict) -> bool:
    named = [c for c in contest["candidates"] if not c["write_in"]]
    if len(named) <= contest["seats"]:
        return True
    return not any(c["party"] != candidate["party"] for c in named)


def outran(page: dict) -> dict | None:
    """The outran-the-ticket table for a county page (``results.county_page`` or
    ``results_unofficial.county_page``), or None when the page is not a general election's or
    its first contest is not a top-of-ticket office."""
    if page["election"]["kind"] != "general":
        return None
    contests = [c for g in page["groups"] if g["party_file"] == "all" for c in g["contests"]]
    if not contests or contests[0]["office"] not in TOP_OF_TICKET:
        return None
    top = contests[0]
    top_total = sum(c["total"] or 0 for c in top["candidates"])
    ticket: dict[str, dict] = {}
    for c in top["candidates"]:
        if c["party"] in PARTIES and not c["write_in"]:
            ticket.setdefault(c["party"], c)
    rows = []
    compared = 0
    flags = countywide(contests, top_total)
    for contest, whole in zip(contests[1:], flags[1:], strict=True):
        if not whole or contest["has_against"]:
            continue
        for cand in contest["candidates"]:
            head = ticket.get(cand["party"])
            if head is None or cand["write_in"] or _unopposed(cand, contest):
                continue
            compared += 1
            if cand["total"] > head["total"]:
                diff = cand["total"] - head["total"]
                rows.append(
                    {
                        "contest": contest["title"],
                        "anchor": contest["anchor"],
                        "name": cand["name"],
                        "party": cand["party"],
                        "votes": cand["total"],
                        "top_name": head["name"],
                        "top_votes": head["total"],
                        "diff": diff,
                        "diff_pct": 100 * diff / head["total"] if head["total"] else None,
                    }
                )
    rows.sort(key=lambda r: -r["diff"])
    return {
        "top_title": TOP_LABELS[top["office"]],
        "top_anchor": top["anchor"],
        "ticket": [ticket[p] for p in PARTIES if p in ticket],
        "rows": rows,
        "compared": compared,
    }


# --- the election hub: the other statewide candidates against the top of the ticket, by county


# the hubs that draw the dot plot: only the 2026 general, by decision (2026-11-06)
HUB_PLOT_ELECTIONS = ("GG26",)
PLOT_WIDTH = 680
PLOT_LABEL_WIDTH = 150
PLOT_ROW = 19
PLOT_TOP = 34
SERIES_COLORS = ("#0f6e56", "#7f77dd", "#c27f17", "#993556")


def _nice_ceiling(x: float) -> float:
    """The smallest multiple of 5 at or above ``x`` (at least 5)."""
    return max(5.0, 5.0 * -(-x // 5))


def statewide(pages: list[dict]) -> dict | None:
    """For an election hub: how far the other statewide candidates of the party leading the top
    of the ticket ran ahead of (or behind) their ticket-mate in each county, with the layout of
    a sorted dot plot.

    ``pages`` are the election's county pages (``county_page`` of either results module), one
    per county. The party compared is that of the top-of-ticket candidate with the most votes
    statewide. A statewide contest is a series when that party's candidate has a real opponent
    (same rule as ``outran``). None when the election is not a general one, its first contest
    is not President or Governor, or no statewide contest can be compared.
    """
    counties = []
    for page in pages:
        if page["election"]["kind"] != "general":
            return None
        contests = [c for g in page["groups"] if g["party_file"] == "all" for c in g["contests"]]
        if not contests or contests[0]["office"] not in TOP_OF_TICKET:
            return None
        counties.append((page["county"], contests))
    if not counties:
        return None
    top_office = counties[0][1][0]["office"]
    # the party leading the top of the ticket statewide
    totals: dict[str, int] = {}
    for _, contests in counties:
        for c in contests[0]["candidates"]:
            if c["party"] in PARTIES and not c["write_in"]:
                totals[c["party"]] = totals.get(c["party"], 0) + (c["total"] or 0)
    if not totals:
        return None
    party = max(totals, key=totals.get)
    # the series: each statewide contest's candidate of that party, by contest title
    series: dict[str, dict] = {}
    for _, contests in counties:
        for contest in contests[1:]:
            if contest["scope"] != "statewide" or contest["has_against"]:
                continue
            for cand in contest["candidates"]:
                if (
                    cand["party"] == party
                    and not cand["write_in"]
                    and not _unopposed(cand, contest)
                ):
                    s = series.setdefault(
                        contest["title"],
                        {"contest": contest["title"], "name": cand["name"], "total": 0, "ahead": 0},
                    )
                    s["total"] += cand["total"] or 0
    if not series:
        return None
    head = {"name": "", "party": party, "total": 0}
    rows = []
    for county, contests in counties:
        top = next(
            (c for c in contests[0]["candidates"] if c["party"] == party and not c["write_in"]),
            None,
        )
        if top is None:
            continue
        head["name"] = head["name"] or top["name"]
        head["total"] += top["total"] or 0
        values = []
        for title, s in series.items():
            cand = next(
                (
                    cd
                    for c in contests[1:]
                    if c["title"] == title
                    for cd in c["candidates"]
                    if cd["party"] == party and not cd["write_in"]
                ),
                None,
            )
            if cand is None or not top["total"]:
                values.append(None)
                continue
            diff = (cand["total"] or 0) - top["total"]
            if diff > 0:
                s["ahead"] += 1
            values.append(
                {"votes": cand["total"] or 0, "diff": diff, "diff_pct": 100 * diff / top["total"]}
            )
        rows.append(
            {"county": county, "slug": slugify(county), "top_votes": top["total"], "values": values}
        )
    rows.sort(key=lambda r: -(r["values"][0]["diff_pct"] if r["values"][0] else float("-inf")))
    for s in series.values():
        s["lead"] = s["total"] - head["total"]
    out = {
        "top_title": TOP_LABELS[top_office],
        "head": head,
        "party_label": PARTY_NAMES.get(party, party),
        "series": [
            {**s, "color": SERIES_COLORS[i % len(SERIES_COLORS)]}
            for i, s in enumerate(series.values())
        ],
        "rows": rows,
        "counties": len(rows),
    }
    out["plot"] = _plot(out)
    return out


def _plot(st: dict) -> dict:
    """Pixel positions for the dot plot in templates/_ticket.html: a row per county, a dot per
    series, the gap as a share of the ticket-mate's vote on the x axis."""
    pcts = [v["diff_pct"] for r in st["rows"] for v in r["values"] if v]
    hi = _nice_ceiling(max(pcts, default=0))
    lo = -_nice_ceiling(-min(pcts)) if pcts and min(pcts) < 0 else 0.0
    span = PLOT_WIDTH - PLOT_LABEL_WIDTH - 20
    px = span / (hi - lo)

    def x(p: float) -> float:
        return round(PLOT_LABEL_WIDTH + (p - lo) * px, 1)

    step = 5 if hi - lo <= 40 else 10
    ticks = [
        {"x": x(t), "label": f"{t:+d}%" if t else "0"} for t in range(int(lo), int(hi) + 1, step)
    ]
    rows = []
    for i, r in enumerate(st["rows"]):
        y = PLOT_TOP + PLOT_ROW * i + 6
        dots = [
            {**v, "x": x(v["diff_pct"]), "series": s["name"], "color": s["color"]}
            for v, s in zip(r["values"], st["series"], strict=True)
            if v
        ]
        xs = [d["x"] for d in dots] + [x(0)]
        rows.append({**r, "y": y, "x_min": min(xs), "x_max": max(xs), "dots": dots})
    return {
        "width": PLOT_WIDTH,
        "height": PLOT_TOP + PLOT_ROW * len(rows) + 4,
        "top": PLOT_TOP,
        "x0": x(0),
        "label_x": PLOT_LABEL_WIDTH - 8,
        "ticks": ticks,
        "rows": rows,
    }
