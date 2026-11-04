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

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser

from skipjack.pipeline.results import COUNTY_CODES


class UnofficialParseError(RuntimeError):
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
        elif kind == "h3":
            text = event[1]
            if "Vote Yes or No" in text:
                state.update(kind="retention", candidate=text.split("\n")[0], vote_for=None)
            elif not vote_for(text):
                state.update(district=text, area="", vote_for=None, seat_rule="")
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
