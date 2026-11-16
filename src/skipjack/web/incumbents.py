"""Incumbents on the 2026 ballots: who sought re-election, who lost, who ran for something else.

Neither the board's results files nor its candidate lists say who is an incumbent. The
officeholders come from the Maryland Manual's roster (``skipjack.pipeline.officials``), a
snapshot of who held each office in 2025-2026, so this covers the 2026 primary and general only
(``ELECTIONS``); earlier elections would need the officeholders of their own time.

An officeholder is *up* in 2026 when the roster's term ends in 2026 or 2027 (legislators' and
statewide officers' terms end in January 2027, county officers' in December 2026) and the office
is one the board's ballots carry (``FAMILIES``). The roster has no members of Congress, so the
U.S. House is not covered. An officeholder is matched to a ballot candidate by name: last name
and first name (or nickname, or the middle name of someone who goes by it), within the
officeholder's county for a county office. Where that finds nothing, a candidate with the same
last name and a compatible first name ("Sam" and "Samuel", "Bill" and "William") in a contest
for the officeholder's own office and place counts too. ``official_overrides`` removes pairs a
person checked and found to be two people, or could not confirm.

An incumbent *lost* when they were a candidate for their own office (an office in the same
``SAME_OFFICE`` group) and won none of those contests: the board's winner flag for certified
results, the leaders in the count so far (as many as the contest elects) for an unofficial one.
A council member who ran for county executive *ran for another office* and is listed apart.
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata

from skipjack.pipeline.results import COUNTY_CODES
from skipjack.web import results as qr
from skipjack.web import results_unofficial as qu

ELECTIONS = ("GP26", "GG26")
TERM_ENDS = ("2026", "2027")

# office families, with the label the pages print
FAMILIES = {
    "governor": "Governor",
    "comptroller": "Comptroller",
    "attorney_general": "Attorney General",
    "state_senator": "State Senate",
    "delegate": "House of Delegates",
    "county_executive": "County executive",
    "county_council": "County council",
    "council_president": "County council president",
    "county_commissioner": "County commissioners",
    "commission_president": "County commission president",
    "sheriff": "Sheriff",
    "states_attorney": "State's attorney",
    "register_of_wills": "Register of wills",
    "clerk": "Clerk of the Circuit Court",
    "orphans_court": "Orphans' Court",
    "treasurer": "County treasurer",
    "board_of_education": "Board of Education",
    # ballot offices no roster row is matched to: they only appear as "another office"
    "congress": "U.S. House",
    "circuit_judge": "Circuit Court judge",
}
STATE_FAMILIES = ("governor", "comptroller", "attorney_general", "state_senator", "delegate")
LEGISLATIVE = ("state_senator", "delegate")
# a council member running for council president, or a commissioner for commission president,
# is running for a seat on the same body
SAME_OFFICE = {
    "county_council": {"county_council", "council_president"},
    "council_president": {"county_council", "council_president"},
    "county_commissioner": {"county_commissioner", "commission_president"},
    "commission_president": {"county_commissioner", "commission_president"},
}

# the board's office names, certified files and unofficial pages both
BALLOT_FAMILIES = {
    "Governor / Lt. Governor": "governor",
    "Comptroller": "comptroller",
    "Attorney General": "attorney_general",
    "State Senator": "state_senator",
    "House of Delegates": "delegate",
    "U.S. Congress": "congress",
    "Representative in Congress": "congress",
    "County Executive": "county_executive",
    "County Council": "county_council",
    "County Council At Large": "county_council",
    "Pres County Council": "council_president",
    "President of the County Council": "council_president",
    "County Commissioner": "county_commissioner",
    "County Commissioner At Large": "county_commissioner",
    "Pres County Commission": "commission_president",
    "County Commissioner President": "commission_president",
    "Sheriff": "sheriff",
    "State's Attorney": "states_attorney",
    "Register of Wills": "register_of_wills",
    "Clerk Circuit Court": "clerk",
    "Clerk of the Circuit Court": "clerk",
    "Judge Orphans' Court": "orphans_court",
    "Judge of the Orphans' Court": "orphans_court",
    "Treasurer": "treasurer",
    "Board of Education": "board_of_education",
    "Board of Ed at Large": "board_of_education",
    "Board of Education At Large": "board_of_education",
    "Judge Circuit Court": "circuit_judge",
    "Judge of the Circuit Court": "circuit_judge",
}

# the roster's bodies for the elected county commissions (its other "Commissioners" bodies,
# liquor boards and the like, are appointed)
COMMISSION_BODIES = (
    "Board of County Commissioners",
    "Commissioners of St. Mary's County",
    "County Commissioners of Caroline County",
    "County Commissioners of Charles County, Maryland",
)

# given names and the short forms candidates file under; both directions are checked
NICKNAMES = {
    "alexander": {"alex"}, "andrew": {"andy", "drew"}, "anthony": {"tony"},
    "benjamin": {"ben"}, "charles": {"charlie", "chuck"}, "christopher": {"chris"},
    "daniel": {"dan", "danny"}, "david": {"dave"}, "deborah": {"deb", "debbie"},
    "donald": {"don"}, "edward": {"ed", "eddie", "ted"}, "elizabeth": {"liz", "beth"},
    "gerald": {"jerry"}, "gregory": {"greg"}, "james": {"jim", "jimmy"},
    "jeffrey": {"jeff"}, "john": {"jack", "johnny"}, "jonathan": {"jon"},
    "joseph": {"joe"}, "katherine": {"kathy", "kate", "katie"}, "kenneth": {"ken"},
    "lawrence": {"larry"}, "matthew": {"matt"}, "michael": {"mike"},
    "nicholas": {"nick"}, "patricia": {"pat", "patty"}, "patrick": {"pat"},
    "richard": {"rick", "ricky", "dick", "rich"}, "robert": {"bob", "rob", "bobby"},
    "ronald": {"ron"}, "samuel": {"sam"}, "stephen": {"steve"}, "steven": {"steve"},
    "susan": {"sue"}, "theodore": {"ted"}, "thomas": {"tom", "tommy"},
    "timothy": {"tim"}, "william": {"bill", "will", "billy"},
}  # fmt: skip
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq", "md", "phd", "dc"}
_COUNTY_NAMES = set(COUNTY_CODES.values())


# --- names ----------------------------------------------------------------------------------


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _words(text: str) -> list[str]:
    out = []
    for w in re.split(r"[^a-z'\-.]+", _fold(text)):
        w = w.strip("'.-")
        if w and w.replace(".", "") not in _SUFFIXES:
            out.append(w)
    return out


def ballot_name(name: str) -> tuple[set[str], str, list[str]]:
    """(given names and nicknames, last name, every word) of a ballot name. A ticket ("Wes
    Moore and Aruna Miller") is its first name's."""
    name = name.split(" and ")[0]
    nicks = {w for n in re.findall(r'["(]([^")]+)[")]', name) for w in _words(n)}
    words = _words(re.sub(r'["(][^")]*[")]', " ", name))
    if not words:
        return set(), "", []
    given = {words[0]} | nicks | ({words[1]} if len(words) > 2 else set())
    return given, words[-1], words


def roster_name(row: dict) -> tuple[set[str], set[str]]:
    """(given names and nicknames, last names) of a roster row. A compound or hyphenated last
    name also matches on its last part ("Lanman Guzzone", "Boyle-Tsottles")."""
    last = " ".join(_words(row["name_last"]))
    lasts = {last, last.split()[-1], last.split("-")[-1]} if last else set()
    given = {w for w in _words(row["name_first"])[:1]}
    given |= set(_words(row["nickname"]))
    middle = _words(row["name_middle"])
    if middle and len(row["name_first"].strip(". ")) <= 1:  # "H. Kevin Anderson"
        given.add(middle[0])
    return given, lasts


def _compatible(a: str, b: str) -> bool:
    """Whether two given names can be one person's: equal, one a nickname of the other, or one
    the start of the other ("Sam", "Samuel")."""
    if a == b or a.startswith(b) or b.startswith(a):
        return len(min(a, b, key=len)) >= 3 or a == b
    return b in NICKNAMES.get(a, ()) or a in NICKNAMES.get(b, ())


# --- the roster -----------------------------------------------------------------------------


def roster_family(row: dict) -> str | None:
    """The ballot office of a roster row, or None for anything not elected on these ballots."""
    if row["record_type"] != "office" or row["is_vacancy"] == "1" or not row["name_last"]:
        return None
    body = row["body_name"].replace("’", "'")
    title = row["office_title"]
    state = {
        ("Governor", "Governor"): "governor",
        ("Comptroller of Maryland", "Comptroller of Maryland"): "comptroller",
        ("Attorney General", "Attorney General"): "attorney_general",
        ("Senate", "State Senator"): "state_senator",
        ("House of Delegates", "Delegate"): "delegate",
    }
    if row["jurisdiction_level"] == "state":
        return state.get((body, title))
    if row["jurisdiction_level"] != "county":
        return None
    # a council or commission's own president (the roster's role) is chosen by the members in
    # most counties; where voters elect one, it is a seat on the same body (SAME_OFFICE)
    if body == "County Council" and (title == "Member" or title.startswith("Dist")):
        return "county_council"
    if body in COMMISSION_BODIES and title in ("Member", "Commissioner"):
        return "county_commissioner"
    if body == "Board of Education" and row["selection_method"] == "elected":
        return "board_of_education" if title == "Member" or title.startswith("District") else None
    local = {
        ("County Executive", "County Executive"): "county_executive",
        ("Sheriff", "Sheriff"): "sheriff",
        ("State's Attorney", "State's Attorney"): "states_attorney",
        ("Register of Wills", "Register of Wills"): "register_of_wills",
        ("Clerk", "Clerk"): "clerk",
        ("Clerk's Office", "Clerk of Circuit Court"): "clerk",
        ("Orphans' Court", "Judge"): "orphans_court",
        ("Orphans' Court", "Chief Judge"): "orphans_court",
        ("County Treasurer", "County Treasurer"): "treasurer",
    }
    return local.get((body, title))


def _county(jurisdiction: str) -> str | None:
    name = (
        jurisdiction if jurisdiction == "Baltimore County" else jurisdiction.removesuffix(" County")
    )
    return name if name in _COUNTY_NAMES else None


def _legislative_district(district: str) -> str:
    """The board's form of a legislative district: "1A" -> "01A", "10" -> "10"."""
    m = re.match(r"(\d+)([A-Z]?)$", district.strip())
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else district


def roster(conn: sqlite3.Connection) -> list[dict]:
    """The officeholders up in 2026, one per person and office, in roster order."""
    try:
        cur = conn.execute("SELECT * FROM officials ORDER BY rowid")
    except sqlite3.OperationalError:  # a database built before the roster
        return []
    names = [d[0] for d in cur.description]
    out: dict[tuple, dict] = {}
    for values in cur:
        row = dict(zip(names, values, strict=True))
        family = roster_family(row)
        if family is None or row["term_end_year"] not in TERM_ENDS:
            continue
        county = _county(row["jurisdiction"])
        if family not in STATE_FAMILIES and county is None:
            continue
        key = (row["person_id"] or row["name_full"], family, county, row["district"])
        if key in out:
            continue
        given, lasts = roster_name(row)
        out[key] = {
            "person_id": row["person_id"] or row["name_full"],
            "name": row["name_full"],
            "family": family,
            "office": FAMILIES[family],
            "county": county,
            "district": row["district"],
            "counties": row["counties"],
            "party": row["party"],
            "appointed": "appointed" in _fold(row["note"]),
            "given": given,
            "lasts": lasts,
            "bio_url": row["bio_url"],
            "source_id": row["source_id"],
        }
    return list(out.values())


def roster_source(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """SELECT s.source_url, s.document_title, s.notes FROM sources s
           WHERE s.source_id = (SELECT source_id FROM officials LIMIT 1)"""
    ).fetchone()
    return {"url": row[0], "title": row[1], "notes": row[2]} if row else None


# --- the ballots ----------------------------------------------------------------------------


def _ballots(conn: sqlite3.Connection, code: str) -> list[dict]:
    """Every candidate contest of the election, each once: the contests with a page from the
    hub (district-wide or statewide totals), every other one from its county's page. Each
    candidate carries ``won``: the board's flag, or in an unofficial count, being among the
    contest's leaders so far."""
    unofficial = qu.is_unofficial(code)
    q = qu if unofficial else qr
    hub = q.hub(conn, code)
    if hub is None:
        return []
    slug = hub["election"]["slug"]
    contests: dict[tuple, dict] = {}
    for group in hub["groups"]:
        for c in group["contests"]:
            if c["has_against"]:
                continue
            ahead = {(a["name"], a["party"]) for a in c.get("ahead", [])}
            contests[(c["office"], c["district"], c["party_file"], None)] = {
                "office": c["office"],
                "district": c["district"],
                "party_file": c["party_file"],
                "county": None,
                "counties": set(),
                "title": c["title"],
                "url": f"/results/{slug}/{c['slug']}/",
                "candidates": [
                    {**x, "won": (x["name"], x["party"]) in ahead if unofficial else x["winner"]}
                    for x in c["candidates"]
                ],
            }
    for county_slug in q.county_slugs(conn, code):
        page = q.county_page(conn, code, county_slug)
        if page is None:
            continue
        for group in page["groups"]:
            for c in group["contests"]:
                key = (c["office"], c["district"], c["party_file"], None)
                if key in contests:
                    contests[key]["counties"].add(page["county"])
                    continue
                if c["has_against"]:
                    continue
                seats = c["seats"]
                contests[(c["office"], c["district"], c["party_file"], page["county"])] = {
                    "office": c["office"],
                    "district": c["district"],
                    "party_file": c["party_file"],
                    "county": page["county"],
                    "counties": {page["county"]},
                    "title": f"{c['title']}, {page['county']}",
                    "url": f"/results/{slug}/county/{county_slug}/#{c['anchor']}",
                    "candidates": [
                        {**x, "won": i < seats if unofficial else x["winner"]}
                        for i, x in enumerate(c["candidates"])
                    ],
                }
    out = []
    for c in contests.values():
        family = BALLOT_FAMILIES.get(c["office"])
        if family is None:
            continue
        c["family"] = family
        c["candidates"] = [x for x in c["candidates"] if not x["write_in"]]
        for x in c["candidates"]:
            x["given"], x["last"], x["words"] = ballot_name(x["name"])
        out.append(c)
    return out


def _place_ok(person: dict, contest: dict) -> bool:
    """Whether a contest is on a ballot the officeholder's own voters see: a county officer's
    county, a legislator's counties (statewide officers run anywhere)."""
    if person["county"] is not None:
        return person["county"] in contest["counties"]
    if person["family"] in LEGISLATIVE:
        return contest["county"] is None or contest["county"] in person["counties"]
    return True


def _same_seat(person: dict, contest: dict) -> bool:
    """Whether a contest is for the officeholder's own office (and, for a legislator, their own
    district): where a looser name match is allowed."""
    if contest["family"] not in SAME_OFFICE.get(person["family"], {person["family"]}):
        return False
    if person["family"] in LEGISLATIVE:
        return contest["district"] == _legislative_district(person["district"])
    return True


def _matches(person: dict, contests: list[dict], skip: set[str]) -> list[tuple[dict, dict]]:
    """(contest, candidate) pairs that are this officeholder."""
    found = []
    for contest in contests:
        if not _place_ok(person, contest):
            continue
        for cand in contest["candidates"]:
            if cand["name"] in skip or not cand["last"]:
                continue
            last_ok = cand["last"] in person["lasts"] or any(
                " ".join(cand["words"][i:]) in person["lasts"] for i in range(1, len(cand["words"]))
            )
            if not last_ok:
                continue
            if cand["given"] & person["given"]:
                found.append((contest, cand))
    if found:
        return found
    for contest in contests:  # the looser match, in the officeholder's own contest only
        if not (_place_ok(person, contest) and _same_seat(person, contest)):
            continue
        for cand in contest["candidates"]:
            if cand["name"] in skip or cand["last"] not in person["lasts"]:
                continue
            if any(_compatible(a, b) for a in cand["given"] for b in person["given"]):
                found.append((contest, cand))
    return found


def _overrides(conn: sqlite3.Connection, code: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    try:
        rows = conn.execute(
            "SELECT person_id, candidate FROM official_overrides WHERE election = ?", (code,)
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for person_id, candidate in rows:
        out.setdefault(person_id, set()).add(candidate)
    return out


def _entry(person: dict, contest: dict, cand: dict) -> dict:
    return {
        "name": person["name"],
        "office": person["office"],
        "place": person["county"]
        or (f"District {person['district']}" if person["family"] in LEGISLATIVE else "Statewide"),
        "party": person["party"],
        "appointed": person["appointed"],
        "bio_url": person["bio_url"],
        "candidate": cand["name"],
        "contest": contest["title"],
        "ran_for": FAMILIES[contest["family"]],
        "url": contest["url"],
        "total": cand["total"],
        "share": cand["share"],
        "won": cand["won"],
        # where the candidate sits in the results pages' data: see ``mark``
        "key": (
            contest["office"],
            contest["district"],
            contest["party_file"],
            contest["county"],
            cand["name"],
            cand["party"],
        ),
    }


_CACHE: dict[tuple, dict | None] = {}


def _cache_key(conn: sqlite3.Connection, code: str) -> tuple | None:
    """The database file and its modification time: every contest and county page reads the
    summary, and a rebuilt database is a new file. None for an in-memory database."""
    path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not path:
        return None
    return (path, os.stat(path).st_mtime_ns, code)


def summary(conn: sqlite3.Connection, code: str) -> dict | None:
    """The incumbents on an election's ballots, or None outside the 2026 elections or without
    the roster."""
    if code not in ELECTIONS:
        return None
    key = _cache_key(conn, code)
    if key is not None and key in _CACHE:
        return _CACHE[key]
    found = _summary(conn, code)
    if key is not None:
        _CACHE[key] = found
    return found


def _summary(conn: sqlite3.Connection, code: str) -> dict | None:
    people = roster(conn)
    contests = _ballots(conn, code)
    if not people or not contests:
        return None
    overrides = _overrides(conn, code)
    families: dict[str, dict] = {
        f: {"family": f, "label": FAMILIES[f], "up": 0, "ran": 0, "lost": 0}
        for f in FAMILIES
        if f not in ("congress", "circuit_judge")
    }
    lost, held, other, absent = [], [], [], []
    for person in people:
        row = families[person["family"]]
        row["up"] += 1
        found = _matches(person, contests, overrides.get(person["person_id"], set()))
        group = SAME_OFFICE.get(person["family"], {person["family"]})
        own = [(c, x) for c, x in found if c["family"] in group]
        if own:
            row["ran"] += 1
            best = max(own, key=lambda p: (p[1]["won"], p[1]["total"]))
            entry = _entry(person, *best)
            if best[1]["won"]:
                held.append(entry)
            else:
                row["lost"] += 1
                lost.append(entry)
        elif found:
            for c, x in found:
                other.append(_entry(person, c, x))
        else:
            absent.append(person)
    order = list(FAMILIES)

    def key(e: dict) -> tuple:
        family = next(f for f, label in FAMILIES.items() if label == e["office"])
        return (order.index(family), e["place"], e["name"])

    return {
        "code": code,
        "unofficial": qu.is_unofficial(code),
        "up": len(people),
        "ran": len(held) + len(lost),
        "held": sorted(held, key=key),
        "lost": sorted(lost, key=key),
        "other": sorted(other, key=key),
        "absent": len(absent),
        "families": [f for f in families.values() if f["up"]],
        "source": roster_source(conn),
    }


def mark(
    conn: sqlite3.Connection, code: str, contests: list[dict], county: str | None = None
) -> bool:
    """Set ``incumbent`` on each candidate of ``contests`` (results-page contest dicts) who is an
    incumbent running for their own office. ``county`` is a county page's county: its local
    contests are that county's, its other contests the district-wide or statewide ones. A
    candidate who holds a different office (a delegate running for the Senate) is not marked.
    Whether any candidate was marked."""
    found = summary(conn, code)
    keys = {e["key"] for e in found["held"] + found["lost"]} if found else set()
    for c in contests:
        where = county if c.get("scope", "local") == "local" and county else None
        for cand in c["candidates"]:
            k = (c["office"], c["district"], c["party_file"], where, cand["name"], cand["party"])
            cand["incumbent"] = k in keys
    return any(cand["incumbent"] for c in contests for cand in c["candidates"])
