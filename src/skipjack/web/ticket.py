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

TOP_OF_TICKET = ("President - Vice Pres", "Governor / Lt. Governor")
TOP_LABELS = {"President - Vice Pres": "President", "Governor / Lt. Governor": "Governor"}
# party ballot lines; unaffiliated, "other" and nonpartisan candidates have no ticket to outrun
PARTIES = ("DEM", "REP", "LIB", "GRN", "WCP")
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
