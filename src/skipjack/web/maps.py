"""County choropleth maps, drawn on the server as SVG.

The outlines are in geo/md_counties.json (built once by scripts/geo/build_county_shapes.R from
the Census Bureau's cartographic boundary file). A page emits them once, in a hidden <defs>
block (``map_defs`` in templates/_maps.html), and each map draws ``<use href="#md-<fips>">``
with its own fill, so several maps on a page cost one set of paths.

A map is a dict built by :func:`county_map`; templates/_maps.html renders it. Values are
classed by a :class:`Scale` into five colour classes. A value of ``None`` (suppressed, missing,
or too few voters to compare) is drawn hatched and named in the legend; it is never drawn as
zero.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Collection
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from skipjack.web.urls import slugify

SHAPES_PATH = Path(__file__).parent / "geo" / "md_counties.json"
CLASSES = 5
NICE_STEPS = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)


@lru_cache(maxsize=1)
def load_shapes() -> dict:
    return json.loads(SHAPES_PATH.read_text())


def nice_step(raw: float) -> float:
    """The smallest round step (1, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 times a power of ten) that is at
    least ``raw``."""
    if raw <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(raw))
    return next(m * power for m in NICE_STEPS if m * power >= raw - 1e-12)


def _fmt_break(v: float) -> str:
    return f"{v:g}"


@dataclass
class Scale:
    """Five colour classes. ``kind`` is "seq" (low to high), "div" (below and above a centre)
    or "party" (Republican to Democratic margin). ``breaks`` are the four inner boundaries,
    ascending; a value equal to a boundary goes in the class above it."""

    kind: str
    breaks: list[float]
    unit: str = ""
    labels: tuple[str, str] | None = None  # words for the two ends of a diverging scale

    def index(self, value: float | None) -> int | None:
        if value is None:
            return None
        return sum(1 for b in self.breaks if value >= b)

    def css(self, value: float | None) -> str:
        i = self.index(value)
        return "map-none" if i is None else f"map-{self.kind}-{i}"

    def legend(self) -> list[dict]:
        b, u = [_fmt_break(x) for x in self.breaks], self.unit
        texts = [f"under {b[0]}{u}"]
        texts += [f"{b[i]} to {b[i + 1]}{u}" for i in range(len(b) - 1)]
        texts.append(f"{b[-1]}{u} or more")
        if self.kind == "party":
            # Breaks are signed margins, symmetric around zero.
            far, near = abs(self.breaks[0]), abs(self.breaks[1])
            texts = [
                f"Republican by {far:g}+ points",
                f"Republican by {near:g} to {far:g}",
                f"within {near:g} points",
                f"Democratic by {near:g} to {far:g}",
                f"Democratic by {far:g}+ points",
            ]
        elif self.kind == "div" and self.labels:
            low, high = self.labels
            texts[0] = f"{low}: {texts[0]}"
            texts[-1] = f"{high}: {texts[-1]}"
        return [{"css": f"map-{self.kind}-{i}", "text": t} for i, t in enumerate(texts)]


def sequential(values: list[float | None], unit: str = "") -> Scale:
    """Equal-width classes from the lowest to the highest value, on round numbers."""
    present = [v for v in values if v is not None]
    if not present:
        return Scale("seq", [1, 2, 3, 4], unit)
    lo, hi = min(present), max(present)
    step = nice_step((hi - lo) / CLASSES) if hi > lo else nice_step(abs(hi) / CLASSES or 1)
    start = math.floor(lo / step) * step
    while start + CLASSES * step < hi:  # rounding the start down can leave the top uncovered
        step = nice_step(step * 1.01)
        start = math.floor(lo / step) * step
    return Scale("seq", [round(start + step * i, 10) for i in range(1, CLASSES)], unit)


def diverging(
    values: list[float | None],
    unit: str = "",
    kind: str = "div",
    labels: tuple[str, str] | None = None,
) -> Scale:
    """Symmetric classes around zero: two below, a middle band near zero, two above."""
    present = [abs(v) for v in values if v is not None]
    reach = max(present) if present else 1.0
    s = nice_step(reach / 2.5 if reach else 1.0)
    breaks = [-1.5 * s, -0.5 * s, 0.5 * s, 1.5 * s]
    if kind == "party":
        # Wider outer classes: margins run to 60+ points, and the darkest colours should
        # still mark more than the two or three most lopsided counties.
        s = nice_step(reach / 4 if reach else 1.0)
        breaks = [-2 * s, -s / 2, s / 2, 2 * s]
    return Scale(kind, [round(b, 10) for b in breaks], unit, labels)


def party_margin(values: list[float | None]) -> Scale:
    """Democratic minus Republican, in points: red below zero, blue above."""
    return diverging(values, " pts", kind="party")


def county_map(
    map_id: str,
    title: str,
    values: dict[str, float | None],
    scale: Scale,
    fmt: Callable[[float], str],
    link: str = "row",
    caption: str = "",
    none_text: str = "not shown",
    linked_counties: Collection[str] | None = None,
) -> dict:
    """Everything templates/_maps.html needs to draw one map.

    ``link`` is "county_page" (each county links to /voters/county/<slug>/), "row" (to the
    ``#county-<slug>`` row of the table on the same page) or "" (no links). With
    ``linked_counties`` only those counties are linked and the rest are drawn without a link
    (a "row" link to a row the table does not have would be dead); without it the ``linked``
    key is absent from the county dicts, so the other pages' markup is unchanged.
    """
    shapes = load_shapes()["counties"]
    counties = []
    for name, shape in shapes.items():
        value = values.get(name)
        county = {
            "name": name,
            "fips": shape["fips"],
            "slug": slugify(name),
            "css": scale.css(value),
            "label": f"{name}: {none_text if value is None else fmt(value)}",
        }
        if linked_counties is not None:
            county["linked"] = name in linked_counties
        counties.append(county)
    return {
        "id": map_id,
        "title": title,
        "caption": caption,
        "link": link,
        "view_box": load_shapes()["viewBox"],
        "counties": counties,
        "legend": scale.legend(),
        "none": none_text if any(c["css"] == "map-none" for c in counties) else None,
    }


# Formats shared by the pages.


def fmt_pct(digits: int = 0) -> Callable[[float], str]:
    return lambda v: f"{v:.{digits}f}%"


def fmt_signed_pct(digits: int = 1) -> Callable[[float], str]:
    return lambda v: f"{v:+.{digits}f}%"


def fmt_points(digits: int = 1) -> Callable[[float], str]:
    return lambda v: f"{v:+.{digits}f} points"


def fmt_margin(v: float) -> str:
    if round(v) == 0:
        return "even"
    return f"{'Democrats' if v > 0 else 'Republicans'} +{abs(v):.0f} points"


# --------------------------------------------------------------------------------------
# The maps each page shows. Inputs are the rows the pages already build for their tables.
# --------------------------------------------------------------------------------------

UNAFFILIATED_CODES = ("UNA", "UNAF")  # the State Board renamed UNAF to UNA


def _margin(row: dict) -> float | None:
    total = row["total"]
    if not total:
        return None
    parties = row["parties"]
    return 100 * (parties.get("DEM", 0) - parties.get("REP", 0)) / total


def locator_map(county_list: list[dict], as_of: str) -> dict:
    """Home page: each county shaded by the Democratic minus Republican margin, linking to its
    registration trends."""
    values = {r["county"]: _margin(r) for r in county_list}
    return county_map(
        "home-margin",
        "Party margin among active voters",
        values,
        party_margin(list(values.values())),
        fmt_margin,
        link="county_page",
        caption=f"as of {as_of}. Select a county for its registration trends.",
    )


def registration_maps(
    county_list: list[dict], year_counties: dict | None, as_of: str, since: str | None
) -> list[dict]:
    """Monthly registration page: party margin, unaffiliated share, and change over a year."""
    margin = {r["county"]: _margin(r) for r in county_list}
    unaffiliated = {
        r["county"]: 100 * sum(r["parties"].get(p, 0) for p in UNAFFILIATED_CODES) / r["total"]
        if r["total"]
        else None
        for r in county_list
    }
    change = {
        r["county"]: ((year_counties or {}).get(r["county"]) or {}).get("pct") for r in county_list
    }
    maps = [
        county_map(
            "reg-margin", "Democratic minus Republican", margin,
            party_margin(list(margin.values())), fmt_margin, link="county_page",
            caption=f"share of active voters, as of {as_of}",
        ),
        county_map(
            "reg-unaffiliated", "Unaffiliated share", unaffiliated,
            sequential(list(unaffiliated.values()), "%"), fmt_pct(1), link="county_page",
            caption=f"of active voters, as of {as_of}",
        ),
    ]  # fmt: skip
    if year_counties:
        maps.append(
            county_map(
                "reg-change", "Change over a year", change,
                diverging(list(change.values()), "%", labels=("fewer", "more")),
                fmt_signed_pct(1), link="county_page",
                caption=f"in active voters, {since} to {as_of}", none_text="no earlier report",
            )
        )  # fmt: skip
    return maps


def _ratio(part: float | None, whole: float | None) -> float | None:
    return None if part is None or not whole else 100 * part / whole


def turnout_live_maps(rows: list[dict], as_of: str) -> list[dict]:
    """An election in progress: how many mail ballots sent have come back."""
    values = {r["county"]: _ratio(r.get("received"), r.get("sent")) for r in rows}
    return [
        county_map(
            "turnout-return", "Mail ballots returned", values,
            sequential(list(values.values()), "%"), fmt_pct(1),
            caption=f"share of mail ballots sent that have been received, as of {as_of}",
            none_text="no ballots sent yet",
        )
    ]  # fmt: skip


def turnout_certified_maps(rows: list[dict], turnout: dict[str, float | None]) -> list[dict]:
    """A certified election: how people voted, mail rejections, and turnout where reported."""
    mail = {
        r["county"]: _ratio((r.get("counted_by_mode") or {}).get("vote-by-mail"), r.get("counted"))
        for r in rows
    }
    rejected = {
        r["county"]: _ratio(
            (r.get("mail_voting") or {}).get("rejected"),
            (r.get("mail_voting") or {}).get("received"),
        )
        for r in rows
    }
    maps = []
    if turnout:
        maps.append(
            county_map(
                "turnout-rate", "Turnout", turnout, sequential(list(turnout.values()), "%"),
                fmt_pct(1), caption="ballots counted as a share of eligible voters",
            )
        )  # fmt: skip
    maps += [
        county_map(
            "turnout-mail", "Voted by mail", mail, sequential(list(mail.values()), "%"),
            fmt_pct(1), caption="share of counted ballots cast by mail",
        ),
        county_map(
            "turnout-rejected", "Mail ballots rejected", rejected,
            sequential(list(rejected.values()), "%"), fmt_pct(2),
            caption="share of mail ballots received that were rejected",
        ),
    ]  # fmt: skip
    return [m for m in maps if any(c["css"] != "map-none" for c in m["counties"])]


def young_maps(counties: list[dict], year: int, prior: int | None) -> list[dict]:
    """Young voters: change in first-time 18-to-22 registrants, and in their Republican share."""
    change = {c["county"]: c.get("young_change") for c in counties}
    rep = {c["county"]: c.get("rep_points") for c in counties}
    then = f", {prior} to {year}" if prior else ""
    return [
        county_map(
            "young-change", "Change in new young registrants", change,
            diverging(list(change.values()), "%", labels=("fewer", "more")), fmt_signed_pct(0),
            caption=f"first-time registrants aged 18 to 22, same stretch of the year, {year} vs {prior}",
            none_text="no comparison",
        ),
        county_map(
            "young-rep", "Change in Republican share", rep,
            diverging(list(rep.values()), " pts", labels=("less Republican", "more Republican")),
            fmt_points(1),
            caption=f"among new registrants aged 18 to 22{then}",
            none_text="too few to compare",
        ),
    ]  # fmt: skip


def race_maps(counties: list[dict], calibration: dict, labels: dict[str, str]) -> dict:
    """Estimated race: one small map per group, and the estimate's gap from the Census."""
    rows = [c for c in counties if c["county"] in load_shapes()["counties"]]
    per_race = []
    for race, label in labels.items():
        values = {c["county"]: c["shares"][race] for c in rows}
        per_race.append(
            county_map(
                f"race-{race}", label, values, sequential(list(values.values()), "%"),
                fmt_pct(1), link="row", none_text="hidden (fewer than 10)",
            )
        )  # fmt: skip
    gap = {
        county: round(rows_["black"]["estimated_pct"] - rows_["black"]["census_vap_pct"], 1)
        for county, rows_ in calibration.items()
    }
    gap_map = county_map(
        "race-gap", "Estimated Black share minus the Census", gap,
        diverging(list(gap.values()), " pts", labels=("estimate lower", "estimate higher")),
        fmt_points(1), link="row",
        caption="estimated share of registered voters minus the 2020 Census share of adults",
    )  # fmt: skip
    return {"per_race": per_race, "gap": gap_map}


def results_map(contest: dict) -> list[dict]:
    """The map on a results contest page (``results.contest()``'s dict), as a list of one.

    A primary shows the winner's share of the vote in each county (the SBE's winner, else the
    leader). A general shows the Democratic minus Republican margin when the contest has both
    parties, else the leader's share. Counties outside the contest are None (hatched). A
    retention question (for / against) has no meaningful map and returns []. Rows link to
    ``#county-<slug>`` on the same page; counties with no row are hatched and not linked.
    """
    candidates, rows = contest["candidates"], contest["county_rows"]
    if contest.get("has_against") or not candidates or not rows:
        return []
    kind = contest["election"]["kind"]
    parties = {c["party"] for c in candidates}
    cells = {r["county"]: r for r in rows}

    if kind == "general" and {"DEM", "REP"} <= parties:

        def margin(r: dict) -> float | None:
            by = {"DEM": 0, "REP": 0}
            for c, n in zip(candidates, r["cells"]):
                if c["party"] in by and n:
                    by[c["party"]] += n
            return 100 * (by["DEM"] - by["REP"]) / r["total"] if r["total"] else None

        values = {county: margin(r) for county, r in cells.items()}
        return [
            county_map(
                "results-margin", "Democratic minus Republican", values,
                party_margin(list(values.values())), fmt_margin, link="row",
                caption="share of the county's votes in this contest, in points",
                none_text="not on the ballot, or no votes cast", linked_counties=cells,
            )
        ]  # fmt: skip
    top = next((i for i, c in enumerate(candidates) if c["winner"]), 0)
    name = candidates[top]["name"]
    values = {county: _ratio(r["cells"][top], r["total"]) for county, r in cells.items()}
    label = (
        "Winner's share" if kind == "primary" and candidates[top]["winner"] else "Leader's share"
    )
    return [
        county_map(
            "results-share", f"{label}: {name}", values,
            sequential(list(values.values()), "%"), fmt_pct(1), link="row",
            caption="share of the votes cast in the county",
            # a county with no votes cast at all is hatched with the counties off the ballot
            none_text="not on the ballot, or no votes cast", linked_counties=cells,
        )
    ]  # fmt: skip
