"""The board's unofficial running count for the election being canvassed (GG26).

Design: docs/plans/2026-11-04-unofficial-results-design.md. This first part reads the board's
results pages, which are plain server-rendered HTML under
https://elections.maryland.gov/elections/2026/General_results/:

- one page per county (``gen_results_2026_by_county_<n>.html``) with every contest on its
  ballot, judges' continuance votes and ballot questions included;
- one page per statewide office group (``gen_results_2026_<n>.html``) and one for the statewide
  questions (``gen_qresults_2026_1_00_ALL.html``), used to cross-check the county sums;
- the home page's canvass-status table: per county, which of the five canvasses (early voting,
  election night, the two mail-in canvasses, provisional) have reported.

The readers record what is printed and nothing else: ``NR`` ("not reported") is None, never 0,
and no figure is computed. The checks on the arithmetic belong to the parse.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from skipjack.pipeline.fetch import ARCHIVES_DIR, MANIFEST_PATH, SOURCE_NAME, resolve_archived
from skipjack.pipeline.provenance import (
    ProvenanceSidecar,
    SourceRecord,
    append_to_manifest,
    read_manifest,
)
from skipjack.pipeline.results import (
    COUNTY_CODES,
    RESULTS_DATA_DIR,
    RESULTS_ELECTIONS,
    county_slug,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "skipjack 0.2.0"
DATA_DIR = RESULTS_DATA_DIR


class UnofficialParseError(RuntimeError):
    pass


class UnofficialFetchError(RuntimeError):
    pass


# The vote types the unofficial pages print, in order: (the board's column label, stem). One
# Mail-In column covers both mail-in canvasses (the certified CSVs split them).
VOTE_TYPES = [
    ("Early Voting", "early"),
    ("Election Day", "election_day"),
    ("Mail-In Ballot", "mail_in"),
    ("Provisional", "provisional"),
]
VOTE_STEMS = [stem for _, stem in VOTE_TYPES]

# The canvass-status table's columns, and which of them feed each vote type. A vote type is
# unreported in a county only when every canvass feeding it is.
CANVASSES = ("EV", "EN", "MIB 1", "Prov", "MIB 2")
VOTE_TYPE_CANVASSES = {
    "early": ("EV",),
    "election_day": ("EN",),
    "mail_in": ("MIB 1", "MIB 2"),
    "provisional": ("Prov",),
}
CANVASS_STATES = {
    "Canvass Complete": "complete",
    "Canvass Partially Complete": "partial",
    # The check with a superscript w: "Results have been reported and write-in votes are
    # assigned" (the page's key). Reported in full, with named write-ins on top. The alt text
    # printed "ate" for "are" on 2026-11-05; both spellings are read.
    "Results have been reported and write-in votes ate assigned.": "complete",
    "Results have been reported and write-in votes are assigned.": "complete",
}

# Every office heading the 2026 general's pages print (2026-11-04). Listed explicitly so that a
# heading not seen before fails the parse until someone decides where it belongs.
KNOWN_OFFICES = frozenset(
    {
        "Attorney General",
        "Board of Education",
        "Board of Education At Large",
        "Clerk of the Circuit Court",
        "Comptroller",
        "Council - City of Cumberland",
        "County Commissioner",
        "County Commissioner At Large",
        "County Commissioner President",
        "County Council",
        "County Council At Large",
        "County Executive",
        "Governor / Lt. Governor",
        "House of Delegates",
        "Judge of the Circuit Court",
        "Judge of the Orphans' Court",
        "Judge, Appellate Court of Maryland At Large",
        "Justice, Supreme Court of Maryland",
        "Mayor - City of Cumberland",
        "President of the County Council",
        "Register of Wills",
        "Representative in Congress",
        "Sheriff",
        "State Senator",
        "State's Attorney",
        "Treasurer",
    }
)
# Statewide questions are numbered, county questions lettered: "Question 1: Constitutional
# Amendment", "Question F: County Ordinance".
QUESTION_RE = re.compile(r"^Question (?:\d+|[A-Z]): \S")

REFRESHED_RE = re.compile(
    r"Last (?:refreshed:|updated)\s*(\d{2}/\d{2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)"
)
PRECINCTS_RE = re.compile(r"\((\d+) of (\d+) election day precincts reported\)")
VOTE_FOR_RE = re.compile(r"^Vote for (?:up to )?(\d+)(?:\s*-\s*(.*))?$")
SUBJECT_RE = re.compile(r"Results for (.+)$")
WRITE_IN_SUFFIX = " (Write-In)"
OTHER_WRITE_INS = "Other Write-Ins"
COUNTY_NAMES = frozenset(COUNTY_CODES.values())


# --- the refreshed time --------------------------------------------------------------------


def last_refreshed(html: str) -> str:
    """The page's "Last refreshed" time as printed ("11/04/2026 09:21:22 AM"). The home page
    prints the same time as "Last updated"."""
    m = REFRESHED_RE.search(html)
    if not m:
        raise UnofficialParseError("no Last refreshed time on the page")
    return m.group(1)


def snapshot_id(refreshed: str) -> str:
    """A snapshot's name from the board's refreshed time: "2026-11-04T092122"."""
    return datetime.strptime(refreshed, "%m/%d/%Y %I:%M:%S %p").strftime("%Y-%m-%dT%H%M%S")


def county_from_subject(subject: str) -> str | None:
    """The repo's county name for a county page's subject ("Allegany County" -> "Allegany",
    "Saint Mary's County" -> "St. Mary's"), or None for a statewide page."""
    name = subject.strip()
    if name.endswith(" County") and name != "Baltimore County":
        name = name.removesuffix(" County")
    name = name.replace("Saint Mary's", "St. Mary's")
    return name if name in COUNTY_NAMES else None


# --- the page as a stream of events --------------------------------------------------------


@dataclass
class _Cell:
    tag: str  # "td" | "th"
    text: str
    alts: list[str]


class _Events(HTMLParser):
    """The body of a board page, flattened to the events the readers need, in order:
    ("h1"|"h2"|"h3"|"h4"|"p", text), ("a", href), ("text", text) and ("table", rows).

    Only the main column (``<section id="bodyMain">``) is read; the menus, the sidebar and the
    footer are not. Inside a heading a ``<br>`` is a newline."""

    CAPTURE = ("h1", "h2", "h3", "h4", "p")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple] = []
        self._on = False
        self._done = False
        self._capture: str | None = None
        self._buf: list[str] = []
        self._rows: list[list[_Cell]] | None = None
        self._cell: _Cell | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and a.get("id") == "bodyMain" and not self._done:
            self._on = True
            return
        if not self._on:
            return
        if tag == "footer" or (tag == "section" and a.get("id") == "bodySide"):
            self._on, self._done = False, True
            return
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._rows.append([])
        elif tag in ("td", "th") and self._rows is not None:
            self._cell = _Cell(tag, "", [])
        elif tag == "img" and self._cell is not None:
            if a.get("alt"):
                self._cell.alts.append(a["alt"])
        elif tag == "br":
            if self._capture:
                self._buf.append("\n")
        elif tag in self.CAPTURE and self._rows is None:
            self._capture, self._buf = tag, []
        elif tag == "a" and self._rows is None and a.get("href"):
            self.events.append(("a", a["href"]))

    def handle_endtag(self, tag):
        if not self._on:
            return
        if tag in ("td", "th") and self._cell is not None and self._rows is not None:
            self._cell.text = " ".join(self._cell.text.split())
            if not self._rows:
                self._rows.append([])
            self._rows[-1].append(self._cell)
            self._cell = None
        elif tag == "table" and self._rows is not None:
            self.events.append(("table", [r for r in self._rows if r]))
            self._rows = None
        elif tag == self._capture:
            lines = ["".join(part).strip() for part in "".join(self._buf).split("\n")]
            text = "\n".join(" ".join(line.split()) for line in lines if line.strip())
            self.events.append((self._capture, text))
            self._capture = None

    def handle_data(self, data):
        if not self._on:
            return
        if self._cell is not None:
            self._cell.text += data
        elif self._capture:
            self._buf.append(data)
        elif self._rows is None and data.strip():
            self.events.append(("text", " ".join(data.split())))


def _events(html: str) -> list[tuple]:
    parser = _Events()
    parser.feed(html)
    parser.close()
    return parser.events


# --- results pages -------------------------------------------------------------------------


@dataclass(frozen=True)
class Votes:
    """One row's figures as printed; None where the board prints NR."""

    early: int | None
    election_day: int | None
    mail_in: int | None
    provisional: int | None
    total: int | None

    def get(self, stem: str) -> int | None:
        return getattr(self, stem)


@dataclass(frozen=True)
class Row(Votes):
    """A candidate's, a retention choice's or a question choice's line in one table."""

    candidate: str = ""  # the candidate, ticket or judge; empty for a question
    choice: str = ""  # "Yes"/"No" for a retention, "For the …"/"Against the …" for a question
    party: str = ""
    write_in: bool = False


@dataclass(frozen=True)
class Contest:
    """One table on a page and the headings above it."""

    kind: str  # "contest" | "retention" | "question"
    office: str
    district: str  # "District 9A", "Circuit 2", or ""
    area: str  # a sub-heading that is not a vote-for line (statewide judges: the county)
    vote_for: int | None
    seat_rule: str  # "No more than 1 per county", or ""
    candidate: str  # the judge, for a retention; else ""
    detail: str  # a question's chapter and short title, joined by " — "
    text_url: str  # a question's "View Question Text" link
    precincts_reported: int | None
    precincts_total: int | None
    rows: tuple[Row, ...]
    totals: Votes


@dataclass(frozen=True)
class Page:
    subject: str  # what the <h1> says the page is for: "Allegany County", "Governor / …"
    county: str | None  # the repo's county name for a county page, None for a statewide page
    last_refreshed: str
    contests: tuple[Contest, ...] = field(default_factory=tuple)


def _number(text: str, where: str) -> int | None:
    t = text.strip()
    if t == "NR":
        return None
    t = t.replace(",", "")
    if not t.isdigit():
        raise UnofficialParseError(f"{where}: not a vote count: {text!r}")
    return int(t)


def _votes(cells: list[_Cell], where: str) -> Votes:
    """The last six cells are the four vote types, the total and the percentage."""
    if len(cells) < 7:
        raise UnofficialParseError(f"{where}: a row of {len(cells)} cells")
    nums = [_number(c.text, where) for c in cells[-6:-1]]
    return Votes(*nums)


def _table(rows: list[list[_Cell]], state: dict, where: str) -> Contest:
    header = next((r for r in rows if all(c.tag == "th" for c in r)), None)
    if header is None:
        raise UnofficialParseError(f"{where}: a table with no header row")
    labels = [c.text for c in header]
    if labels[-6:] != [label for label, _ in VOTE_TYPES] + ["Total", "Percentage"]:
        raise UnofficialParseError(f"{where}: unexpected columns {labels}")
    has_party = len(labels) > 1 and labels[1] == "Party"
    body = [r for r in rows if r is not header]
    out: list[Row] = []
    totals: Votes | None = None
    for cells in body:
        label = cells[0].text
        votes = _votes(cells, where)
        if label == "Totals":
            totals = votes
            continue
        party = cells[1].text if has_party else ""
        if state["kind"] == "contest":
            write_in = label == OTHER_WRITE_INS or label.endswith(WRITE_IN_SUFFIX)
            name = label.removesuffix(WRITE_IN_SUFFIX)
            out.append(Row(**vars(votes), candidate=name, party=party, write_in=write_in))
        elif state["kind"] == "retention":
            out.append(Row(**vars(votes), candidate=state["candidate"], choice=label))
        else:
            out.append(Row(**vars(votes), choice=label))
    if totals is None:
        raise UnofficialParseError(f"{where}: a table with no Totals row")
    return Contest(
        kind=state["kind"],
        office=state["office"],
        district=state["district"],
        area=state["area"],
        vote_for=state["vote_for"],
        seat_rule=state["seat_rule"],
        candidate=state["candidate"],
        detail=" — ".join(state["detail"]),
        text_url=state["text_url"],
        precincts_reported=state["precincts"][0],
        precincts_total=state["precincts"][1],
        rows=tuple(out),
        totals=totals,
    )


def read_results_page(html: str) -> Page:
    """Every table on a county or statewide results page, with the headings above it."""
    refreshed = last_refreshed(html)
    subject = ""
    contests: list[Contest] = []
    state: dict = {}

    def vote_for(text: str) -> bool:
        m = VOTE_FOR_RE.match(text)
        if m:
            state["vote_for"] = int(m.group(1))
            state["seat_rule"] = (m.group(2) or "").strip()
        return bool(m)

    for event in _events(html):
        kind = event[0]
        if kind == "h1":
            m = SUBJECT_RE.search(event[1])
            if not m:
                raise UnofficialParseError(f"an unexpected page heading: {event[1]!r}")
            subject = m.group(1).strip()
        elif kind == "h2":
            office = event[1]
            question = bool(QUESTION_RE.match(office))
            if not question and office not in KNOWN_OFFICES:
                raise UnofficialParseError(f"{subject}: an office not seen before: {office!r}")
            state = {
                "kind": "question" if question else "contest",
                "office": office,
                "district": "",
                "area": "",
                "vote_for": None,
                "seat_rule": "",
                "candidate": "",
                "detail": [],
                "text_url": "",
                "precincts": (None, None),
            }
        elif not state:
            continue  # anything above the first office heading
        elif kind in ("h3", "h4") and "Vote Yes or No" in event[1]:
            # a judge's continuance: an <h3> on county pages, an <h4> under the district on
            # the statewide Supreme Court page
            state.update(kind="retention", candidate=event[1].split("\n")[0], vote_for=None)
        elif kind == "h3":
            if not vote_for(event[1]):
                state.update(district=event[1], area="", vote_for=None, seat_rule="")
        elif kind == "h4":
            if not vote_for(event[1]):
                state["area"] = event[1]
        elif kind == "p":
            if state["kind"] == "question" and event[1]:
                state["detail"].append(event[1])
        elif kind == "a":
            if "qtext" in event[1]:
                state["text_url"] = event[1]
        elif kind == "text":
            m = PRECINCTS_RE.search(event[1])
            if m:
                state["precincts"] = (int(m.group(1)), int(m.group(2)))
        elif kind == "table":
            where = f"{subject}, {state['office']} {state['district']}".strip()
            contests.append(_table(event[1], state, where))
            state["precincts"] = (None, None)
    if not subject:
        raise UnofficialParseError("no results page heading")
    return Page(subject, county_from_subject(subject), refreshed, tuple(contests))


# --- the canvass-status table --------------------------------------------------------------


@dataclass(frozen=True)
class CountyStatus:
    county: str
    last_updated: str  # as printed: "11/04/26 01:24 AM"
    precincts_reported: int
    precincts_total: int
    canvass: dict[str, str]  # CANVASSES -> "complete" | "partial" | "not_reported"


def _canvass_state(cell: _Cell, where: str) -> str:
    if cell.alts:
        state = CANVASS_STATES.get(cell.alts[0])
        if state is None:
            raise UnofficialParseError(f"{where}: an unexpected canvass mark {cell.alts[0]!r}")
        return state
    if cell.text in ("", "NR"):
        return "not_reported"
    raise UnofficialParseError(f"{where}: an unexpected canvass cell {cell.text!r}")


def read_canvass_status(html: str) -> list[CountyStatus]:
    """The home page's table of each county's canvasses, in the board's county order."""
    for event in _events(html):
        if event[0] != "table":
            continue
        rows = event[1]
        header = [c.text for c in rows[0]]
        if "# Precincts Reporting" not in header:
            continue
        expected = ["County", "Last Updated", "# Precincts Reporting", *CANVASSES]
        if header != expected:
            raise UnofficialParseError(f"canvass table: unexpected columns {header}")
        out = []
        for cells in rows[1:]:
            county = county_from_subject(cells[0].text)
            if county is None:
                raise UnofficialParseError(f"canvass table: unknown county {cells[0].text!r}")
            m = re.match(r"(\d+) of (\d+)", cells[2].text)
            if not m:
                raise UnofficialParseError(f"canvass table: {county}: {cells[2].text!r}")
            out.append(
                CountyStatus(
                    county=county,
                    last_updated=cells[1].text,
                    precincts_reported=int(m.group(1)),
                    precincts_total=int(m.group(2)),
                    canvass={
                        name: _canvass_state(cell, f"canvass table: {county} {name}")
                        for name, cell in zip(CANVASSES, cells[3:], strict=True)
                    },
                )
            )
        if len(out) != len(COUNTY_CODES):
            raise UnofficialParseError(f"canvass table: {len(out)} counties, expected 24")
        return out
    raise UnofficialParseError("no canvass-status table on the page")


# --- a printed 0 in a canvass that has not reported ----------------------------------------


def unreported(status: CountyStatus, stem: str) -> bool:
    """Whether no canvass feeding this vote type has reported in the county."""
    return all(status.canvass[c] == "not_reported" for c in VOTE_TYPE_CANVASSES[stem])


def _blank_zeros(votes, blank: list[str]):
    return replace(votes, **{s: None for s in blank if votes.get(s) == 0})


def apply_not_reported(page: Page, statuses: dict[str, CountyStatus]) -> Page:
    """The county page with a printed 0 turned to None in every vote type none of whose
    canvasses has reported in that county. The ballot-question tables print 0 where the
    candidate tables (and the statewide pages) print NR. A 0 in a canvass that has reported,
    even partly, is kept: it can be a real count. Printed totals are left as printed."""
    if page.county is None or page.county not in statuses:
        raise UnofficialParseError(f"no canvass status for {page.county or page.subject}")
    blank = [s for s in VOTE_STEMS if unreported(statuses[page.county], s)]
    contests = tuple(
        replace(
            c,
            rows=tuple(_blank_zeros(r, blank) for r in c.rows),
            totals=_blank_zeros(c.totals, blank),
        )
        for c in page.contests
    )
    return replace(page, contests=contests)


# --- the registry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnofficialElection:
    code: str
    base_url: str
    county_pages: int = 24
    statewide_pages: int = 9
    questions_page: str = ""
    status_url: str = "https://elections.maryland.gov/"

    @property
    def year(self) -> int:
        return RESULTS_ELECTIONS[self.code].year

    def pages(self) -> list[tuple[str, str]]:
        """(page name, url) for every page a snapshot keeps: the county pages, the statewide
        office pages, the statewide questions page, then the home page as status.html."""
        y = self.year
        names = [f"gen_results_{y}_by_county_{n}.html" for n in range(1, self.county_pages + 1)]
        names += [f"gen_results_{y}_{n}.html" for n in range(1, self.statewide_pages + 1)]
        names.append(self.questions_page)
        out = [(name, f"{self.base_url}/{name}") for name in names]
        out.append((STATUS_PAGE, self.status_url))
        return out


STATUS_PAGE = "status.html"

UNOFFICIAL_RESULTS: dict[str, UnofficialElection] = {
    "GG26": UnofficialElection(
        code="GG26",
        base_url="https://elections.maryland.gov/elections/2026/General_results",
        questions_page="gen_qresults_2026_1_00_ALL.html",
    ),
}


def is_live(code: str) -> bool:
    """Whether the election's unofficial count may be published: the board runs one and the
    certified results are not published yet. This is the only publish rule for the track."""
    return code in UNOFFICIAL_RESULTS and not RESULTS_ELECTIONS[code].published


def _archive_dir(code: str) -> Path:
    return ARCHIVES_DIR / "sbe" / "results-unofficial" / code


def _data_dir(code: str, data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / "results_unofficial" / code


def snapshots(code: str) -> list[str]:
    """The archived snapshots, oldest first."""
    root = _archive_dir(code)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def source_id(code: str, snapshot: str, page: str) -> str:
    return f"sbe-results-unofficial-{code}-{snapshot}-{page.removesuffix('.html')}"


# --- fetch ----------------------------------------------------------------------------------


def _get(client: httpx.Client, url: str) -> bytes:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise UnofficialFetchError(f"could not download {url} ({type(e).__name__}: {e})") from None
    return resp.content


def _download_all(e: UnofficialElection) -> dict[str, bytes]:
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        return {name: _get(client, url) for name, url in e.pages()}


def _refreshed(pages: dict[str, bytes]) -> dict[str, str]:
    out = {}
    for name, body in pages.items():
        try:
            out[name] = last_refreshed(body.decode("utf-8", errors="replace"))
        except UnofficialParseError:
            raise UnofficialFetchError(f"{name}: no Last refreshed time") from None
    return out


def unofficial_fetch(election: str = "GG26") -> str | None:
    """Archive one snapshot of the board's results pages; returns its name, or None when there
    is nothing new (the board has not refreshed since the newest archived snapshot) or the
    election is not live.

    Every results page must carry the same Last refreshed time. When they differ the fetch
    straddled a refresh, so it tries once more and then gives up without writing anything.
    The home page (the canvass-status table) is kept beside them; a different time on it is
    logged, not fatal."""
    if not is_live(election):
        logger.info("%s: no unofficial count to fetch (certified results are published)", election)
        return None
    e = UNOFFICIAL_RESULTS[election]
    for attempt in (1, 2):
        pages = _download_all(e)
        times = _refreshed(pages)
        results_times = {t for name, t in times.items() if name != STATUS_PAGE}
        if len(results_times) == 1:
            break
        logger.warning("%s: pages refreshed at different times %s", election, sorted(results_times))
    else:
        raise UnofficialFetchError(
            f"{election}: the board's pages carry different refreshed times twice running: "
            f"{sorted(results_times)}"
        )
    refreshed = results_times.pop()
    if times[STATUS_PAGE] != refreshed:
        logger.warning(
            "%s: the home page says %s, the results pages %s",
            election,
            times[STATUS_PAGE],
            refreshed,
        )
    snap = snapshot_id(refreshed)
    if snap in snapshots(election):
        logger.info("%s: snapshot %s already archived", election, snap)
        return None
    final = _archive_dir(election) / snap
    tmp = final.with_name(f".{snap}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name, body in pages.items():
        (tmp / name).write_bytes(body)
    os.replace(tmp, final)
    urls = dict(e.pages())
    for name in pages:
        append_to_manifest(
            MANIFEST_PATH,
            SourceRecord.create(
                source_id=source_id(election, snap, name),
                source_url=urls[name],
                source_name=SOURCE_NAME,
                local_path=final / name,
                document_title=f"{election} unofficial results, {name} (refreshed {refreshed})",
                notes="unofficial and incomplete: the board's running count",
            ),
        )
    logger.info("%s: archived snapshot %s (%d pages)", election, snap, len(pages))
    return snap


# --- checks ---------------------------------------------------------------------------------


def _sum(values) -> int:
    return sum(v or 0 for v in values)


def check_tables(page: Page) -> int:
    """Every row's reported vote types add up to its printed total, and each table's Totals
    row is its column sums (NR in a Totals cell only where the whole column is NR). Returns
    the number of rows checked."""
    rows = 0
    for c in page.contests:
        where = f"{page.subject}: {c.office} {c.district} {c.area} {c.candidate}".rstrip()
        for r in c.rows:
            rows += 1
            if _sum(r.get(s) for s in VOTE_STEMS) != r.total:
                raise UnofficialParseError(
                    f"{where}: {r.candidate or r.choice}'s vote types do not add up to {r.total}"
                )
        for s in [*VOTE_STEMS, "total"]:
            column = [r.get(s) for r in c.rows]
            printed = c.totals.get(s)
            if printed is None:
                if any(v for v in column):
                    raise UnofficialParseError(f"{where}: Totals prints NR for {s} over votes")
            elif _sum(column) != printed:
                raise UnofficialParseError(
                    f"{where}: the {s} column sums to {_sum(column)}, Totals prints {printed}"
                )
    return rows


def _group(c: Contest) -> tuple:
    return (c.office, c.district, c.candidate)


def _figures(c: Contest) -> dict[tuple[str, str], int]:
    return {(r.candidate, r.choice): r.total or 0 for r in c.rows}


def check_statewide(counties: list[Page], statewide: list[Page]) -> int:
    """Every table on the statewide pages equals the sum of the same table over the county
    pages, row for row (and no county carries a row the statewide table lacks). On the
    circuit-court page a table can stand for one county (its sub-heading names it); that one is
    compared with that county's table alone. Returns the number of tables compared."""
    summed: dict[tuple, Counter] = {}
    per_county: dict[tuple, dict] = {}
    for p in counties:
        for c in p.contests:
            summed.setdefault(_group(c), Counter()).update(_figures(c))
            per_county[(p.county, *_group(c))] = _figures(c)
    compared = 0
    for p in statewide:
        for c in p.contests:
            area = county_from_subject(c.area) if c.area else None
            if c.area and area is None:
                raise UnofficialParseError(f"{p.subject}: an unexpected sub-heading {c.area!r}")
            want = per_county.get((area, *_group(c))) if area else summed.get(_group(c))
            if want is None:
                raise UnofficialParseError(
                    f"{p.subject}: {c.office} {c.district} {c.area} is on no county page"
                )
            got = _figures(c)
            if dict(want) != got:
                diff = sorted(k for k in set(want) | set(got) if want.get(k, 0) != got.get(k, 0))[
                    :3
                ]
                raise UnofficialParseError(
                    f"{p.subject}: {c.office} {c.district} {c.area}: the county pages do not "
                    f"sum to the statewide figures ({diff})"
                )
            compared += 1
    return compared


# --- parse ----------------------------------------------------------------------------------

CSV_COLUMNS = [
    "election",
    "snapshot",
    "last_refreshed",
    "county",
    "kind",
    "office",
    "district",
    "area",
    "vote_for",
    "seat_rule",
    "candidate_heading",
    "detail",
    "text_url",
    "precincts_reported",
    "precincts_total",
    "candidate",
    "choice",
    "party",
    "write_in",
    *VOTE_STEMS,
    "total",
]
STATUS_COLUMNS = [
    "election",
    "snapshot",
    "county",
    "last_updated",
    "precincts_reported",
    "precincts_total",
    "ev",
    "en",
    "mib1",
    "prov",
    "mib2",
]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows([[_cell(v) for v in row] for row in rows])
    return buf.getvalue().encode("utf-8")


def _county_rows(code: str, snap: str, page: Page, base_url: str) -> list[list]:
    out = []
    for c in page.contests:
        url = f"{base_url}/{c.text_url}" if c.text_url and "://" not in c.text_url else c.text_url
        for r in c.rows:
            out.append(
                [code, snap, page.last_refreshed, page.county, c.kind, c.office, c.district,
                 c.area, c.vote_for, c.seat_rule, c.candidate, c.detail, url,
                 c.precincts_reported, c.precincts_total, r.candidate, r.choice, r.party,
                 r.write_in, *(r.get(s) for s in VOTE_STEMS), r.total]
            )  # fmt: skip
    return out


def _records(code: str, snap: str) -> dict[str, SourceRecord]:
    """The manifest rows of a snapshot's pages, by page name; the archived bytes must match."""
    found: dict[str, SourceRecord] = {}
    prefix = f"sbe-results-unofficial-{code}-{snap}-"
    for record in read_manifest(MANIFEST_PATH):
        if record.source_id.startswith(prefix):
            found[record.source_id.removeprefix(prefix) + ".html"] = record
    return found


def _read_snapshot(code: str, snap: str) -> tuple[dict[str, str], dict[str, SourceRecord]]:
    e = UNOFFICIAL_RESULTS[code]
    records = _records(code, snap)
    pages: dict[str, str] = {}
    for name, _ in e.pages():
        record = records.get(name)
        if record is None:
            raise UnofficialParseError(f"{snap}: no manifest row for {name}")
        path = resolve_archived(record)
        if not path.exists():
            raise UnofficialParseError(f"{snap}: {name} is not archived")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise UnofficialParseError(f"{snap}: {name} does not match its manifest sha256")
        pages[name] = data.decode("utf-8")
    return pages, records


def parse_snapshot(code: str, snap: str, data_dir: Path | None = None) -> int:
    """Check one archived snapshot and write its county CSVs and canvass-status CSV, each with
    a provenance sidecar, to data/results_unofficial/<CODE>/<snapshot>/. All or nothing: any
    failed check writes nothing. Returns the number of files written."""
    e = UNOFFICIAL_RESULTS[code]
    pages, records = _read_snapshot(code, snap)
    statuses = {s.county: s for s in read_canvass_status(pages[STATUS_PAGE])}
    county_names = [n for n, _ in e.pages() if "_by_county_" in n]
    counties: list[tuple[str, Page, Page]] = []  # (page name, as printed, zeros resolved)
    for name in county_names:
        printed = read_results_page(pages[name])
        counties.append((name, printed, apply_not_reported(printed, statuses)))
    seen = [p.county for _, p, _ in counties]
    if sorted(seen) != sorted(COUNTY_CODES.values()):
        raise UnofficialParseError(f"{snap}: the county pages are not the 24 counties: {seen}")
    statewide = [
        read_results_page(pages[n])
        for n, _ in e.pages()
        if n != STATUS_PAGE and "_by_county_" not in n
    ]
    times = {p.last_refreshed for _, p, _ in counties} | {p.last_refreshed for p in statewide}
    if len(times) != 1:
        raise UnofficialParseError(f"{snap}: the pages carry different refreshed times {times}")
    rows_checked = sum(check_tables(p) for _, p, _ in counties)
    rows_checked += sum(check_tables(p) for p in statewide)
    # the cross-check is on what the board printed; resolving zeros changes no total
    compared = check_statewide([p for _, p, _ in counties], statewide)

    final = _data_dir(code, data_dir) / snap
    tmp = final.with_name(f".{snap}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "county").mkdir(parents=True)
    code_of = {name: c for c, name in COUNTY_CODES.items()}
    written = 0
    for name, printed, resolved in counties:
        nn = code_of[resolved.county]
        out_csv = tmp / "county" / f"{nn}-{county_slug(resolved.county)}.csv"
        out_csv.write_bytes(_csv(CSV_COLUMNS, _county_rows(code, snap, resolved, e.base_url)))
        blanked = sum(
            1
            for c0, c1 in zip(printed.contests, resolved.contests, strict=True)
            for r0, r1 in zip(c0.rows, c1.rows, strict=True)
            for s in VOTE_STEMS
            if r0.get(s) == 0 and r1.get(s) is None
        )
        sidecar = ProvenanceSidecar.from_source(
            records[name],
            parser_version=PARSER_VERSION,
            extraction_method="html",
            checks={
                "unofficial": True,
                "last_refreshed": resolved.last_refreshed,
                "tables": len(resolved.contests),
                "rows": sum(len(c.rows) for c in resolved.contests),
                "row_totals": "ok",
                "table_totals": "ok",
                "statewide_check": (
                    f"ok: {compared} statewide tables match the county pages "
                    f"({rows_checked} rows checked across the snapshot)"
                ),
                "zeros_not_reported": blanked,
                "canvass_status_source": records[STATUS_PAGE].source_id,
            },
            notes=(
                f"{code} {resolved.county}, the board's unofficial and incomplete count as of "
                f"{resolved.last_refreshed}. Empty vote cells are not reported (NR), or a printed "
                f"0 in a canvass the county had not reported."
            ),
        )
        sidecar.election, sidecar.family, sidecar.geography = code, "results-unofficial", "county"
        sidecar.write(out_csv)
        written += 1
    status_csv = tmp / "canvass-status.csv"
    status_csv.write_bytes(
        _csv(
            STATUS_COLUMNS,
            [
                [
                    code,
                    snap,
                    s.county,
                    s.last_updated,
                    s.precincts_reported,
                    s.precincts_total,
                    *(s.canvass[c] for c in CANVASSES),
                ]
                for s in statuses.values()
            ],  # fmt: skip
        )
    )
    sidecar = ProvenanceSidecar.from_source(
        records[STATUS_PAGE],
        parser_version=PARSER_VERSION,
        extraction_method="html",
        checks={"unofficial": True, "counties": len(statuses)},
        notes=f"{code} canvass status per county from the board's home page, snapshot {snap}.",
    )
    sidecar.election, sidecar.family, sidecar.geography = code, "results-unofficial", "status"
    sidecar.write(status_csv)
    written += 1
    if final.exists():
        shutil.rmtree(final)
    os.replace(tmp, final)
    logger.info("%s: parsed snapshot %s (%d files)", code, snap, written)
    return written


def unofficial_parse(
    election: str = "GG26",
    snapshot: str | None = None,
    force: bool = False,
    data_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Parse archived snapshots not yet parsed (or the named one, or all with force). Returns
    (snapshots written, failures). Nothing is parsed once the election is not live."""
    if not is_live(election):
        logger.info("%s: no unofficial count to parse (certified results are published)", election)
        return [], []
    done = _data_dir(election, data_dir)
    todo = [snapshot] if snapshot else snapshots(election)
    if not snapshot and not force:
        todo = [s for s in todo if not (done / s).exists()]
    written, failures = [], []
    for snap in todo:
        try:
            parse_snapshot(election, snap, data_dir)
        except UnofficialParseError as err:
            failures.append(f"{election} {snap}: {err}")
            continue
        written.append(snap)
    return written, failures
