# Turnout county and congressional-district pages — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the per-election turnout page into a hub plus `/turnout/{slug}/county/` and `/turnout/{slug}/congressional/` subpages, with a CD table built from the congressional-grain rows already in `turnout_counts`.

**Architecture:** One parameterized geography route and template (design: docs/plans/2026-10-13-turnout-geo-pages-design.md). `web/turnout.py` gains district-grain row builders mirroring the county ones, joined on a normalized district label; the certified CD table reads only figures-of-record families; the static build publishes subpage URLs only where that grain has publishable rows.

**Tech Stack:** FastAPI + Jinja2 (templates in `src/skipjack/web/templates/`), SQLite, pytest. Run tests with `uv run pytest`. The live app and static build share the same routes/templates; the static build runs each page URL through a TestClient.

**Design doc:** `docs/plans/2026-10-13-turnout-geo-pages-design.md` (committed).

**Data facts you must know (from skipjack.db):**
- Elections: GG26 2026 general (uncertified, in progress), GP26 2026 primary (`certified=True`), PG24 2024 general (`certified=True`).
- `geography='congressional'` rows exist for all three, but: GG26 has only `sent-returned` (the only publishable/unofficial-tracked family it shows); GP26 has only unofficial families (`early-voting`, `same-day`, `sent-returned`, `left-to-count`) — for a certified election nothing publishable → **no CD page**; PG24 has `official-turnout` by CD (per-CD ballots by mode + an `eligible` denominator) → **CD page with a turnout % column**.
- The stored district value carries **two spellings within one election**: xlsx sheets print `Congressional District Code 01`, PDF tables print `01`. There is also `Undetermined` (PG24 official-turnout only; its `eligible` is 0 there).
- Certified pages read **figures of record only** (`county-canvass`, `votes-accepted`, `mail-voting`, `requests`, `official-turnout`) — campaign-time tracking files (`sent-returned`, `early-voting`, `same-day`, `left-to-count`) stay off certified page tables. `may_publish(election, family)` (pipeline/turnout.py:288) gates downloads, not page tables; page shape follows the certified/uncertified branch.
- Current certified behavior to preserve: the GP26 county page shows only certified families (see tests/test_turnout_web.py test_real_gp26_page_renders_the_certified_data).

**Note on the working tree:** three templates (`about.html`, `base.html`, `home.html`) carry the separately approved tagline/credit/contact changes (verified: 469 tests pass, index.html reproduced byte-for-byte). Task 1 commits them so feature commits stay clean.

---

### Task 1: Commit the outstanding verified template changes

**Files:**
- Modify: `src/skipjack/web/templates/base.html`, `home.html`, `about.html` (already in the working tree)

**Step 1: Sanity-check the tree**

Run: `git status --short && uv run pytest tests/test_web.py -q`
Expected: the three templates modified; tests pass.

**Step 2: Commit**

```bash
git add src/skipjack/web/templates/base.html src/skipjack/web/templates/home.html src/skipjack/web/templates/about.html
git commit -m "the site tagline, footer credit line, and about contact

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: District label normalization (unit tests first)

**Files:**
- Test: `tests/test_turnout_web.py`
- Modify: `src/skipjack/web/turnout.py`

**Step 1: Extend the test fixture helper with an election parameter**

In `tests/test_turnout_web.py`, the `add()` helper (around line 27) hardcodes `'GG26'` in both inserts. Add a parameter so later tasks can build PG24/GP26 fixtures:

```python
def add(conn, as_of, rows, source_id="s", family="sent-returned", election="GG26"):
    """Rows into the turnout tables; rows carry (geography, county, district, category,
    party, measure, value)."""
    for geography, county, district, category, party, measure, value in rows:
        conn.execute(
            """INSERT INTO turnout_sources
               (source_id, election, family, geography, as_of, source_url, source_name,
                document_title, fetch_date, sha256, extraction_method, checks_json, notes)
               VALUES (?, ?, ?, ?, ?, 'https://elections.maryland.gov/x.xlsx',
                'Maryland State Board of Elections', 'test', '2026-10-10T17:00:00+00:00',
                '', 'xlsx (calamine)', '{}', '')
               ON CONFLICT(source_id) DO NOTHING""",
            (source_id, election, family, geography, as_of),
        )
        conn.execute(
            """INSERT INTO turnout_counts
               (source_id, as_of, election, family, geography, county, district, category,
                party, measure, value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                as_of,
                election,
                family,
                geography,
                county,
                district,
                category,
                party,
                measure,
                value,
            ),
        )
```

**Step 2: Write the failing tests**

Add near the top of `tests/test_turnout_web.py` (after the fixtures):

```python
# --- congressional-district labels ------------------------------------------------------------

def test_the_district_label_normalizes_both_printed_spellings():
    """The xlsx sheets print 'Congressional District Code 01' where the PDF tables print
    '01'; both read as District 1. Anything not spelled as a numbered district passes
    through (Undetermined keeps its own name)."""
    assert turnout.district_label("Congressional District Code 01") == "District 1"
    assert turnout.district_label("01") == "District 1"
    assert turnout.district_label("Congressional District Code 08") == "District 8"
    assert turnout.district_label("8") == "District 8"
    assert turnout.district_label("Undetermined") == "Undetermined"


def test_the_district_sort_puts_the_districts_in_order_undetermined_last():
    """Districts sort by number whatever spelling the row prints; Undetermined is last."""
    raws = ["Congressional District Code 08", "01", "Undetermined",
            "Congressional District Code 02"]
    labels = [turnout.district_label(r) for r in sorted(raws, key=turnout.district_sort)]
    assert labels == ["District 1", "District 2", "District 8", "Undetermined"]
```

**Step 3: Run to verify failure**

Run: `uv run pytest tests/test_turnout_web.py -k district_label_or_undetermined -q` — use `-k "district"` instead since the functions don't exist yet:
Run: `uv run pytest tests/test_turnout_web.py -k "district and label" -q`
Expected: FAIL with `AttributeError: ... has no attribute 'district_label'`.

**Step 4: Implement**

In `src/skipjack/web/turnout.py`, after the imports (`import sqlite3` becomes):

```python
import re

import sqlite3
```

and add near the top (after `_SENT_MEASURES`):

```python
_DISTRICT_RE = re.compile(r"(?:Congressional District Code )?(\d\d)")


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
```

**Step 5: Run to verify pass**

Run: `uv run pytest tests/test_turnout_web.py -k "district" -q`
Expected: 2 passed.

**Step 6: Commit**

```bash
git add tests/test_turnout_web.py src/skipjack/web/turnout.py
git commit -m "the turnout district label, both of the board's printed spellings

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Geography-parameterized area queries (no behavior change)

**Files:**
- Modify: `src/skipjack/web/turnout.py:45-57` (`_county_cells` → `_area_cells` + `_area_measures`)
- Test: none new — this is a refactor guarded by the existing county tests

**Step 1: Replace `_county_cells` with the geography-parameterized pair**

`_county_cells` (turnout.py lines 45-57) filters `county = ? AND district = ''`. Congressional rows are the reverse (`county = '' AND district = ?`). Replace the function with:

```python
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
    column, area_filter = _GEOGRAPHY_FILTERS[geography]
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
```

**Step 2: Delegating call sites**

- In `county_rows` (lines ~187-188): `_county_cells(conn, election, "sent-returned", county, as_of)` → `_area_cells(conn, election, "sent-returned", "county", county, as_of)`; `_county_cells(conn, election, "requests", county, requests_as_of)` → `_area_cells(conn, election, "requests", "county", county, requests_as_of)`.
- `_family_county` (lines 335-349, the certified single-county reader) becomes a thin delegator:

```python
def _family_county(
    conn: sqlite3.Connection, election: str, family: str, county: str, as_of: str
) -> dict[str, int]:
    """One county's counts for one certified family at one snapshot, at the row the
    county's Total printed."""
    return _area_measures(conn, election, family, "county", county, as_of)
```

**Step 3: Run the existing tests (must not change behavior)**

Run: `uv run pytest tests/test_turnout_web.py -q`
Expected: all pass (the county row shape, certified joins and page tests are unchanged).

**Step 4: Commit**

```bash
git add src/skipjack/web/turnout.py
git commit -m "the turnout area queries, one geography at a time

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `district_rows` — the uncertified CD table's data

**Files:**
- Modify: `src/skipjack/web/turnout.py` (after `county_rows`)
- Test: `tests/test_turnout_web.py`

**Step 1: Write the failing tests**

One shared label per district joins families that spell it differently. The row dict mirrors `county_rows` with `district` in place of `county`.

```python
# congressional-district rows: one district xlsx-spelled (sent-returned) and the same
# district PDF-spelled (requests), plus a second district and Undetermined
CD_ONE_SENT = [
    ("congressional", "", "Congressional District Code 01", "ALL", p, m, v)
    for p, m, v in [
        ("DEM", "sent", 32040), ("REP", "sent", 19045), ("OTH", "sent", 11871),
        ("ALL", "sent", 62956),
        ("DEM", "received", 10270), ("REP", "received", 5653), ("OTH", "received", 3043),
        ("ALL", "received", 18966),
        ("ALL", "accepted", 0),
    ]
]
CD_ONE_REQUESTED = [
    ("congressional", "", "01", "ALL", "ALL", "total", 5300),
    ("congressional", "", "01", "ALL", "ALL", "mailed", 4100),
    ("congressional", "", "01", "ALL", "ALL", "print-at-home", 250),
]
CD_TWO = [
    ("congressional", "", "02", "ALL", "ALL", "sent", 41000),
    ("congressional", "", "02", "ALL", "ALL", "received", 12000),
]
UNDetermined = [
    ("congressional", "", "Undetermined", "ALL", "ALL", "sent", 900),
    ("congressional", "", "Undetermined", "ALL", "ALL", "received", 100),
]


def test_district_rows_join_the_two_printed_spellings(conn):
    """One election's files spell its districts two ways; the families join on the
    normalized label, so District 1 carries both the mail-in and the request figures."""
    add(conn, "2026-10-09", CD_ONE_SENT)
    add(conn, "2026-10-10", CD_ONE_REQUESTED, family="requests", source_id="s2")
    rows = {r["district"]: r for r in turnout.district_rows(conn, "GG26", "2026-10-09", "2026-10-10")}
    assert set(rows) == {"District 1"}
    row = rows["District 1"]
    # the xlsx-spelled sent-returned family and the pdf-spelled requests family met
    assert row["sent"] == 62956 and row["requests_total"] == 5300
    assert row["mailed"] == 4100 and row["dem_sent"] == 32040


def test_district_rows_order_and_undetermined(conn):
    """Rows come out District 1.. in number order whatever the spelling, Undetermined
    last."""
    add(conn, "2026-10-09", CD_TWO + UNDetermined + CD_ONE_SENT)
    rows = turnout.district_rows(conn, "GG26", "2026-10-09")
    assert [r["district"] for r in rows] == ["District 1", "District 2", "Undetermined"]


def test_a_district_missing_from_a_snapshot_reads_as_unknown_not_zero(conn):
    """The county rule, at the district grain: an area absent from the newer snapshot
    is absent from the rows, not zeroed."""
    add(conn, "2026-10-09", CD_ONE_SENT + CD_TWO)
    add(conn, "2026-10-10", CD_TWO, source_id="s2")
    rows = turnout.district_rows(conn, "GG26", "2026-10-10")
    assert [r["district"] for r in rows] == ["District 2"]
```

(`UNDetermined` follows the file's existing title-style constant naming.)

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turnout_web.py -k "district_rows" -q`
Expected: FAIL with `AttributeError: ... has no attribute 'district_rows'`.

**Step 3: Implement**

In `src/skipjack/web/turnout.py`, after `county_rows`:

```python
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
    _district_areas); Undetermined sorts last. A district absent from a family's
    snapshot is absent from the row (unknown, not zero); districts only in one family
    read with the other family's columns empty.
    """
    requests_as_of = requests_as_of or as_of
    if not (areas := _district_areas(
        conn, election, {"sent-returned": as_of, "requests": requests_as_of}
    )):
        return []
    out: list[dict] = []
    for label, spellings in sorted(
        areas.items(), key=lambda kv: district_sort(next(iter(kv[1].values())))
    ):
        sent = (
            _area_cells(
                conn, election, "sent-returned", "congressional",
                spellings["sent-returned"], as_of,
            )
            if "sent-returned" in spellings
            else {}
        )
        method_cells = (
            _area_cells(
                conn, election, "requests", "congressional",
                spellings["requests"], requests_as_of,
            )
            if "requests" in spellings
            else {}
        )
        if not sent and not method_cells:
            continue
        out.append(_sent_row("district", label, sent, method_cells))
    return out
```

**Step 4: Extract the shared row dict `_sent_row`** (DRY — the county and district rows are otherwise identical). In `county_rows`, replace the `row: dict = {...}` build with a call, and add above `county_rows`:

```python
def _sent_row(label_key: str, label: str, sent: dict, method_cells: dict) -> dict:
    """The two families' counts as one table row: mail-in sent/received/accepted with
    the party splits, and the request totals — keyed by the row's area label."""
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
            sent[("ALL", "received")] / sent[("ALL", "sent")]
            if sent.get(("ALL", "sent"))
            else None
        ),
        "requests_total": method_cells.get(("ALL", "total")),
        "mailed": method_cells.get(("ALL", "mailed")),
        "print_at_home": method_cells.get(("ALL", "print-at-home")),
    }
    return row
```

and in `county_rows` the per-county body becomes:

```python
        if not sent and not method_cells:
            continue  # the county reports on the other day only
        out.append(_sent_row("county", county, sent, method_cells))
```

**Step 5: Run the tests**

Run: `uv run pytest tests/test_turnout_web.py -q`
Expected: all pass — the new district tests and every county test (the county row keys did not change).

**Step 6: Commit**

```bash
git add src/skipjack/web/turnout.py tests/test_turnout_web.py
git commit -m "the turnout district rows, both spellings one district

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `certified_district_rows` and `publishable_geographies`

**Files:**
- Modify: `src/skipjack/web/turnout.py`
- Test: `tests/test_turnout_web.py`

**Step 1: Write the failing tests**

```python
# PG24-style official-turnout sheet at CD grain: per-CD ballots by mode and the sheet's
# own eligible figure; a campaign-time sent-returned table that must stay off the page
CD_SHEET = [
    ("congressional", "", "01", "ALL", "ALL", m, v)
    for m, v in [
        ("election-day", 180852), ("early-voting", 153234),
        ("vote-by-mail", 81343), ("provisional", 11627),
        ("eligible", 562346),
    ]
]
CD_SHEET_TWO = [
    ("congressional", "", "Congressional District Code 02", "ALL", "ALL", m, v)
    for m, v in [
        ("election-day", 70000), ("early-voting", 60000),
        ("vote-by-mail", 50000), ("provisional", 2000),
        ("eligible", 500000),
    ]
]
CD_SHEET_UNDETERMINED = [
    ("congressional", "", "Undetermined", "ALL", "ALL", m, v)
    for m, v in [("provisional", 6393), ("eligible", 0)]
]
CD_CAMPAIGN_TRACKING = [
    ("congressional", "", "01", "ALL", "ALL", "sent", 300000),
    ("congressional", "", "01", "ALL", "ALL", "received", 100000),
]


def test_certified_district_rows_read_the_sheet(conn):
    """A certified election's CD table is its official-turnout sheet: ballots by mode,
    the sheet's own eligible figure, and the modes-over-eligible share the board
    computes itself. Campaign-time tallies at the same grain stay off the page."""
    add(conn, "2024-12-11", CD_SHEET + CD_SHEET_TWO + CD_SHEET_UNDETERMINED,
        family="official-turnout", election="PG24")
    add(conn, "2024-12-07", CD_CAMPAIGN_TRACKING, family="sent-returned", source_id="s2",
        election="PG24")
    rows = {r["district"]: r for r in turnout.certified_district_rows(
        conn, "PG24", {"official-turnout": "2024-12-11", "sent-returned": "2024-12-07"}
    )}
    assert set(rows) == {"District 1", "District 2", "Undetermined"}
    one = rows["District 1"]
    assert one["cast"] == 180852 + 153234 + 81343 + 11627
    assert one["by_mode"]["election-day"] == 180852
    assert one["eligible"] == 562346
    # 427056 / 562346
    assert one["turnout"] == (180852 + 153234 + 81343 + 11627) / 562346
    # the two printed spellings joined: both districts, by number, Undetermined last
    assert [r["district"] for r in rows.values()] == ["District 1", "District 2", "Undetermined"]
    # a district whose sheet prints an eligible of 0 gets no percentage (Undetermined)
    assert rows["Undetermined"]["turnout"] is None
    assert rows["Undetermined"]["cast"] == 6393
    # the certified page reads the sheet, not the campaign tracking file
    assert all("sent" not in r for r in rows.values())


def test_publishable_geographies_gate_on_the_records(conn):
    """Which subpages an election gets: rows at the grain, and — for a certified
    election — its figures of record there, not the campaign-time tallies."""
    # the in-progress general: every campaign family publishes
    add(conn, "2026-10-09", ALLEGANY)
    add(conn, "2026-10-09", CD_ONE_SENT, source_id="s2")
    assert turnout.publishable_geographies(conn, "GG26") == ["county", "congressional"]
    # the certified primary: the sheet's CD table publishes, the campaign one does not
    add(conn, "2024-12-11", CD_SHEET, family="official-turnout", election="PG24",
        source_id="s3")
    add(conn, "2024-11-01", CD_CAMPAIGN_TRACKING, family="sent-returned", election="GP26",
        source_id="s4")
    assert turnout.publishable_geographies(conn, "GP26") == ["county"]
    assert turnout.publishable_geographies(conn, "PG24") == ["county", "congressional"]
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turnout_web.py -k "certified_district or publishable_geographies" -q`
Expected: FAIL with `AttributeError`.

**Step 3: Implement**

In `src/skipjack/web/turnout.py`, in the certified section (after `CERTIFIED_CANVASS_FAMILIES`):

```python
# The certified record families the page tables read. A certified election's pages
# show its figures of record — the canvass, the accepted-ballot count, the certified
# mail-in and request reports, and the official turnout sheets — while the
# campaign-time tracking files (sent-returned, early-voting, same-day,
# left-to-count) stay in the archive and off the pages.
CERTIFIED_PAGE_FAMILIES = (
    "county-canvass", "votes-accepted", "mail-voting", "requests", "official-turnout",
)
```

and after `certified_county_rows`:

```python
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
                conn, election, "official-turnout", "congressional",
                spellings["official-turnout"], wanted["official-turnout"],
            )
            if "official-turnout" in spellings
            else {}
        )
        counted = (
            _area_measures(
                conn, election, "votes-accepted", "congressional",
                spellings["votes-accepted"], wanted["votes-accepted"],
            )
            if "votes-accepted" in spellings
            else {}
        )
        envelopes = (
            _area_cells(
                conn, election, "mail-voting", "congressional",
                spellings["mail-voting"], wanted["mail-voting"],
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
                "turnout": (
                    cast / sheet["eligible"] if sheet.get("eligible") else None
                ),
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
```

Also add the module constant `_MODES = ("early-voting", "election-day", "vote-by-mail", "provisional")` near the top and use it in `certified_district_rows` **and** in the two `certified_headline`/`certified_county_rows` comprehensions that already spell the tuple out (`by_mode` loops at lines ~379-397 and ~495-511) — replace the inline tuples with `_MODES` so the mode list lives once.

Add the `re` module-level import of `may_publish`'s sibling: `from skipjack.pipeline.turnout import ELECTIONS, may_publish` already exists (used by `snapshot_sources`) — no import change needed for this task.

**Step 4: Run the tests**

Run: `uv run pytest tests/test_turnout_web.py -q`
Expected: all pass (including every pre-existing certified-county test — `_MODES` substitution must not change the state headline).

**Step 5: Commit**

```bash
git add src/skipjack/web/turnout.py tests/test_turnout_web.py
git commit -m "the certified district table and the geography pages a page reads

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Routes and templates — the subpages and the hub

**Files:**
- Modify: `src/skipjack/web/routes/turnout.py`
- Create: `src/skipjack/web/templates/turnout_geography.html`
- Modify: `src/skipjack/web/templates/turnout_election.html`, `turnout_final.html`
- Test: `tests/test_turnout_web.py`

**Step 1: Write the failing tests**

Update the page tests that assert county-table content on the hub, and add subpage tests. In `test_turnout_pages_render_the_real_election` (db-backed): the county-table assertions move to the county page; the hub keeps tiles/chart and gains the links row:

```python
@needs_db
def test_turnout_pages_render_the_real_election():
    """... (keep the docstring, add: the election page is now the hub; the county
    table lives on the county subpage)"""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app
    from skipjack.web.dates import day_label
    from skipjack.web.db import get_db

    client = TestClient(create_app())
    r = client.get("/turnout/")
    assert r.status_code == 200
    assert "2026 General Election" in r.text
    r = client.get("/turnout/gg26/")
    assert r.status_code == 200
    with get_db() as conn:
        headline = turnout.headline(conn, "GG26")
    requests_total = headline["requests"]["requests_total"]
    sent_returned = headline["sent-returned"]
    for value in (requests_total, sent_returned["sent"], sent_returned["received"]):
        assert f"{value:,}" in r.text
    assert day_label(sent_returned["as_of"]) in r.text
    assert "November 3, 2026" in r.text
    assert 'id="turnout-data"' in r.text
    # the hub links to the area pages and carries no sortable county table
    assert "/turnout/gg26/county/" in r.text
    assert "/turnout/gg26/congressional/" in r.text
    assert "data-sortable" not in r.text
    assert client.get("/turnout/nope/").status_code == 404


@needs_db
def test_the_county_subpage_carries_the_county_table():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app
    from skipjack.web.db import get_db

    client = TestClient(create_app())
    r = client.get("/turnout/gg26/county/")
    assert r.status_code == 200
    # the mail-in table, with its party-split headers, moved here
    assert r.text.count("(Dem / Rep / other)") == 2
    assert "data-sortable" in r.text
    with get_db() as conn:
        headline = turnout.headline(conn, "GG26")
    assert f'{headline["sent-returned"]["sent"]:,}' in r.text


@needs_db
def test_the_district_subpages_read_the_report_that_published_their_grain():
    """GG26's CD page is campaign-time mail-in tracking; PG24's is the certified
    sheet with the per-district turnout share; GP26 has no publishable CD records, so
    its page says so."""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/turnout/gg26/congressional/")
    assert r.status_code == 200
    assert "District 1" in r.text and "District 8" in r.text

    r = client.get("/turnout/pg24/congressional/")
    assert r.status_code == 200
    assert "District 1" in r.text and "Eligible voters" in r.text and "Turnout" in r.text
    # a certified page reads the sheet: no campaign-time request columns
    assert "Requests</th>" not in r.text

    r = client.get("/turnout/gp26/congressional/")
    assert r.status_code == 200
    assert "No congressional-district figures" in r.text


@needs_db
def test_the_geography_routes_404_the_unknowns():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    assert client.get("/turnout/gg26/notageography/").status_code == 404
    assert client.get("/turnout/nope/county/").status_code == 404
```

And in the made-up-data page tests (`test_the_page_renders_a_change_from_a_second_snapshot`, `test_the_as_of_sentence_says_a_shared_date_once`): change the fetched URL from `/turnout/gg26/` to `/turnout/gg26/county/` and move the county-table assertions with it; keep one assertion on the hub that the links row appears:

```python
    html = TestClient(create_app()).get("/turnout/gg26/county/").text
```
(the `html.count("(ballot figures as of")` sentence logic stays with the table page since the county page repeats the as-of line).

Also update `test_real_gp26_page_renders_the_certified_data` if it asserts `data-sortable` on `/turnout/gp26/` — it does not, but it does assert the page's tiles; leave as-is unless the suite fails.

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turnout_web.py -q`
Expected: FAIL (the subpage routes don't exist; hub still carries the table).

**Step 3: Implement the route**

In `src/skipjack/web/routes/turnout.py`:

```python
_GEOGRAPHY_LABELS = {"county": "county", "congressional": "congressional district"}


@router.get("/{slug}/{geography}/")
async def election_geography(request: Request, slug: str, geography: str):
    """One election's table at one geography: the county page the election page used
    to carry, and the congressional-district page beside it."""
    if geography not in _GEOGRAPHY_LABELS:
        raise HTTPException(status_code=404)
    with get_db() as conn:
        available = {e["slug"]: e["code"] for e in qt.elections(conn)}
        code = available.get(slug)
        if code is None or code not in ELECTIONS:
            raise HTTPException(status_code=404)
        geographies = qt.publishable_geographies(conn, code)
        certified = ELECTIONS[code].certified
        if certified:
            cert = qt.certified_headline(conn, code)
            if geography == "county":
                dates = _certified_dates(cert)
                rows = qt.certified_county_rows(conn, code, dates)
            else:
                dates = {
                    f: a for f, a in cert["as_ofs"].items()
                    if f in qt.CERTIFIED_PAGE_FAMILIES and a
                }
                rows = qt.certified_district_rows(conn, code, dates)
            tiles: list[dict] = []
        else:
            headline = qt.headline(conn, code)
            sent_as_of = headline.get("sent-returned", {}).get("as_of")
            requests_as_of = headline.get("requests", {}).get("as_of")
            rows = (
                qt.county_rows(conn, code, sent_as_of or requests_as_of,
                               requests_as_of=requests_as_of)
                if geography == "county"
                else qt.district_rows(conn, code, sent_as_of or requests_as_of,
                                      requests_as_of=requests_as_of)
            )
            dates: dict[str, str] = {}
            if sent_as_of:
                dates["sent-returned"] = sent_as_of
            if requests_as_of:
                dates["requests"] = requests_as_of
        sources = qt.snapshot_sources(conn, code)
        shown_dates = sorted({d for d in dates.values() if d})
    return request.app.state.templates.TemplateResponse(
        request,
        "turnout_geography.html",
        {
            "election": ELECTIONS[code],
            "geography": geography,
            "geography_label": _GEOGRAPHY_LABELS[geography],
            "certified": certified,
            "rows": rows,
            "sources": sources,
            # the page says which days it shows: distinct as-ofs, oldest last
            "shown_dates": shown_dates,
            # the election page links only to the grains with published figures and
            # this page's table drops the two request columns when no row has one
            "geographies": geographies,
            "show_requests": any(r.get("requests_total") is not None for r in rows),
        },
    )
```

And in the existing `election` handler: drop the `rows` fetch entirely (both branches), and build the context's `geographies` from `qt.publishable_geographies(conn, code)`; pass `tiles`, `cert`, `headline`, `chart`, `sources`, `reasons`, `dates` (certified) and `geographies` — `rows` leaves the context. The uncertified branch keeps computing the as-of dates it shows in its sentence (it already works off `headline`).

**Step 4: Create `src/skipjack/web/templates/turnout_geography.html`**

```jinja
{% extends "base.html" %}
{% import "_macros.html" as m with context %}
{% block title %}{{ election.name.title() }} turnout by {{ geography_label }}{% endblock %}

{% block content %}
<div class="viz-root">
<h1>{{ election.name.title() }}</h1>

{% if certified %}
<p>Final turnout. The figures are the State Board's certified canvass reports —
    the same records as the <a href="{{ base }}/turnout/{{ election.slug }}/">statewide
    figures</a>, one row per {{ geography_label }}.</p>
{% else %}
<p class="secondary">Mail-in ballot tracking from the State Board's reports, one row per
    {{ geography_label }}. <a href="{{ base }}/turnout/{{ election.slug }}/">Statewide
    figures</a>.</p>
{% endif %}
{% if shown_dates %}<p class="secondary">Figures as of
    {{ shown_dates | map("day_label") | join(", ") }}.</p>{% endif %}

{% if rows %}
{% if certified %}
<div class="overflow-auto">
<table data-sortable>
    <thead>
        <tr>
            <th>{{ "County" if geography == "county" else "District" }}</th>
            {% if geography == "county" %}
            <th class="num">Ballots cast</th>
            <th class="num">Votes counted</th>
            {% endif %}
            <th class="num">Early voting (cast)</th>
            <th class="num">Election day</th>
            <th class="num">Vote by mail</th>
            <th class="num">Provisional</th>
            {% if geography == "county" %}
            <th class="num">Mail ballots accepted</th>
            <th class="num">Mail ballots rejected</th>
            {% else %}
            <th class="num">Eligible voters</th>
            <th class="num">Turnout</th>
            {% endif %}
        </tr>
    </thead>
    <tbody>
        {% for r in rows %}
        <tr>
            <td>{{ r.county if geography == "county" else r.district }}</td>
            <td class="num" data-sort="{{ m.sortkey(r.cast) }}">{{ m.num(r.cast) }}</td>
            {% if geography == "county" %}
            <td class="num" data-sort="{{ m.sortkey(r.counted) }}">{{ m.num(r.counted) }}</td>
            {% endif %}
            <td class="num">{{ m.num(r.by_mode.get("early-voting")) }}</td>
            <td class="num">{{ m.num(r.by_mode.get("election-day")) }}</td>
            <td class="num">{{ m.num(r.by_mode.get("vote-by-mail")) }}</td>
            <td class="num">{{ m.num(r.by_mode.get("provisional")) }}</td>
            {% if geography == "county" %}
            <td class="num">{{ m.num(r.mail_voting.get("accepted")) }}</td>
            <td class="num">{{ m.num(r.mail_voting.get("rejected")) }}</td>
            {% else %}
            <td class="num" data-sort="{{ m.sortkey(r.eligible) }}">{{ m.num(r.eligible) }}</td>
            <td class="num" data-sort="{% if r.turnout is none %}{% else %}{{ r.turnout }}{% endif %}">{% if r.turnout is not none %}{{ m.p(r.turnout * 100, 1) }}{% else %}<span class="muted">&mdash;</span>{% endif %}</td>
            {% endif %}
        </tr>
        {% endfor %}
    </tbody>
</table>
</div>
{% if geography == "congressional" %}
<p class="note">Turnout is each sheet's ballots by mode over the eligible figure that
    same sheet prints for the district; where the sheet prints no eligible voters
    (Undetermined) the column is left blank. The board computes its statewide
    percentage the same way.</p>
{% endif %}
{% else %}
<div class="overflow-auto">
<table data-sortable>
    <thead>
        <tr>
            <th>{{ "County" if geography == "county" else "District" }}</th>
            {% if show_requests %}
            <th class="num">Requests</th>
            <th class="num">Print-at-home</th>
            {% endif %}
            <th class="num">Ballots sent <span class="muted">(Dem / Rep / other)</span></th>
            <th class="num">Received back <span class="muted">(Dem / Rep / other)</span></th>
            <th class="num">Accepted</th>
            <th class="num">Return rate</th>
        </tr>
    </thead>
    <tbody>
        {% for r in rows %}
        <tr>
            <td>{{ r.county if geography == "county" else r.district }}</td>
            {% if show_requests %}
            <td class="num" data-sort="{{ m.sortkey(r.requests_total) }}">{{ m.num(r.requests_total) }}</td>
            <td class="num" data-sort="{{ m.sortkey(r.print_at_home) }}">{{ m.num(r.print_at_home) }}</td>
            {% endif %}
            <td class="num" data-sort="{{ m.sortkey(r.sent) }}">{{ m.num(r.sent) }}
                {% if r.sent is not none %}<span class="muted">({{ m.num(r.dem_sent) }} / {{ m.num(r.rep_sent) }} / {{ m.num(r.oth_sent) }})</span>{% endif %}</td>
            <td class="num" data-sort="{{ m.sortkey(r.received) }}">{{ m.num(r.received) }}
                {% if r.received is not none %}<span class="muted">({{ m.num(r.dem_received) }} / {{ m.num(r.rep_received) }} / {{ m.num(r.oth_received) }})</span>{% endif %}</td>
            <td class="num" data-sort="{{ m.sortkey(r.accepted) }}">{{ m.num(r.accepted) }}</td>
            <td class="num" data-sort="{% if r.return_rate is none %}{% else %}{{ r.return_rate }}{% endif %}">{% if r.return_rate is not none %}{{ m.p(r.return_rate * 100, 1) }}{% else %}<span class="muted">&mdash;</span>{% endif %}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
</div>
{% endif %}
{% else %}
<p class="note">No {{ geography_label }} figures are published for this election yet.
    {% if certified %}Its campaign-time tallies stay in the archive and off the site;
    the certified {{ geography_label }} report appears here when the State Board posts
    it.{% endif %}</p>
{% endif %}

<p class="note">Every row is one of the State Board's press-room snapshots, listed on the
    <a href="{{ base }}/about">About</a> page; the numbers behind this page are
    downloadable under <a href="{{ base }}/data/">Data</a>.</p>

{% for s in sources %}
<div class="source-strip">
    <strong>Source:</strong>
    <a href="{{ s.url }}" target="_blank" rel="noopener">{{ s.title }}</a>,
    downloaded {{ s.fetched|day_label }}, read by {{ s.method }},
    fingerprint <code>{{ s.sha256 }}</code>
    {%- if s.csv %} — <a href="{{ base }}/downloads/{{ s.csv }}">CSV</a> ·
    <a href="{{ base }}/downloads/{{ s.provenance }}">provenance</a>{% endif %}
</div>
{% endfor %}
</div>
{% endblock %}
```

(`day_label` is already registered as a `base` global filter via `configure_templates` — the other templates call `|day_label` without import; the `map("day_label")` filter name resolves the same way.)

**Step 5: Edit the hub templates**

`turnout_election.html`:
- Keep the certified branch's include and the uncertified tiles/chart as-is.
- Replace the `{% if rows %}...{% endif %}` "By county" table block (lines 46-78) with the links row:

```jinja
{% if geographies %}
<p class="secondary">The same figures by area:
    {% for g in geographies %}<a href="{{ base }}/turnout/{{ election.slug }}/{{ g }}/">{{ "by county" if g == "county" else "by congressional district" }}</a>{% if not loop.last %} · {% endif %}{% endfor %}.</p>
{% endif %}
```

- The "Every row is one of the State Board's press-room snapshots" note (lines 82-86): change its guard from `{% if rows %}` to `{% if geographies %}` (the hub no longer has rows; the numbers are still downloadable on the Data page).

`turnout_final.html`:
- Delete the whole `{% if rows %}...{% endif %}` "By county" table block (lines 46-80). The tiles, the mail-in figures note, the eligible-voters note, and the rejection-reason tables stay (they are statewide records).

**Step 6: Run the tests and fix the hub expectations**

Run: `uv run pytest tests/test_turnout_web.py -q`
Expected: all pass — including the updated page tests. If any other test asserts hub-table content, move its assertions to the geography page the same way.

Run: `uv run pytest -q` (full suite)
Expected: pass; `tests/test_static_site.py::test_every_page_in_the_plan_is_written` still passes because the new URLs aren't in `page_urls()` yet (Task 7).

**Step 7: Commit**

```bash
git add src/skipjack/web/routes/turnout.py src/skipjack/web/templates/turnout_geography.html src/skipjack/web/templates/turnout_election.html src/skipjack/web/templates/turnout_final.html tests/test_turnout_web.py
git commit -m "the turnout hub and its county and district pages

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: The static build publishes the subpages

**Files:**
- Modify: `src/skipjack/web/static_site.py:53-92` (`page_urls`)
- Test: `tests/test_static_site.py`

**Step 1: Write the failing test**

```python
def test_the_turnout_geography_pages_are_published_per_grain():
    """Each election's geography subpage is planned only where that grain has figures:
    the general election gets both, the certified primary gets its county page only,
    and the 2024 election's certified CD sheet gets a congressional page."""
    urls = page_urls()
    assert "/turnout/gg26/county/" in urls
    assert "/turnout/gg26/congressional/" in urls
    assert "/turnout/gp26/county/" in urls
    assert "/turnout/gp26/congressional/" not in urls
    assert "/turnout/pg24/county/" in urls
    assert "/turnout/pg24/congressional/" in urls
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_static_site.py::test_the_turnout_geography_pages_are_published_per_grain -q`
Expected: FAIL (`/turnout/gg26/county/` not in urls).

**Step 3: Implement**

In `page_urls()` (static_site.py), the turnout block becomes:

```python
    if turnout_codes:
        urls.append("/turnout/")
        for code in turnout_codes:
            urls.append(f"/turnout/{code.lower()}/")
            urls += [f"/turnout/{code.lower()}/{g}/" for g in publishable_geographies(conn, code)]
```

with the import at the top: `from skipjack.web.turnout import publishable_geographies`. The `conn` in scope is the `with get_db() as conn` opened at the top of `page_urls()` — keep the geography query inside it.

**Step 4: Run the static-site suite**

Run: `uv run pytest tests/test_static_site.py -q`
Expected: all pass — `test_every_page_in_the_plan_is_written` compares the built tree to `page_urls()`, `test_build_has_no_broken_links` walks the links, and the new nested paths ride `page_path()` unchanged.

**Step 5: Commit**

```bash
git add src/skipjack/web/static_site.py tests/test_static_site.py
git commit -m "the site builds the county and district turnout pages

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Full verification and the real site

**Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: all pass.

**Step 2: Rebuild the published site**

Run: `make site SITE_BASE=/skipjack`
Expected: `Built ... pages ... in /Users/dwillis/code/skipjack/site`, "Links are prefixed with /skipjack".

**Step 3: Spot-check the built pages**

```bash
grep -c "District 1" site/turnout/gg26/congressional/index.html site/turnout/pg24/congressional/index.html
grep -c "By county" site/turnout/gg26/index.html
ls site/turnout/gp26/congressional/ 2>&1 | tail -1
```
Expected: both CD pages contain "District 1"; the hub mentions by county (and by congressional district); the GP26 congressional path does not exist.

**Step 4: No commit needed** (site/ is gitignored); the feature code is already committed task by task.

---

## Verification checklist (before declaring done)

- [ ] `uv run pytest -q` all pass.
- [ ] `/turnout/gg26/` has no sortable table; links to both subpages.
- [ ] `/turnout/gg26/congressional/` mail-in columns, Districts 1–8, Undetermined last; request columns present only when the requests family has CD rows.
- [ ] `/turnout/pg24/congressional/` sheet columns with per-district turnout %; no campaign-time columns.
- [ ] `/turnout/gp26/congressional/` renders the empty state (live); no static page for it.
- [ ] `/turnout/{unknown}/county/` and `/turnout/gg26/{unknown}/` are 404s.
- [ ] The county subpage reproduces the old hub table's rows for the same election (spot-check one county's numbers against the pre-change page).
- [ ] Static build's `check_links` passes with the new nested URLs.