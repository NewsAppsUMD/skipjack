# Unofficial Tallies Removal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A certified election's page and downloads carry only its figures of record — no figures from the board's unofficial eight-day turnout files (`early-voting`, `same-day`).

**Architecture:** One publish rule (`may_publish(election, family)` in the pipeline, keyed off `ELECTIONS[code].certified`) applied at every published surface: the certified page's tiles/columns, the download listing, the data page, the live download route, and the page's source-strip links. Parsing and storage stay complete.

**Tech Stack:** FastAPI/Jinja2, SQLite db, pytest. Run tests with `uv run pytest -q -m "not slow"` (the CI gate).

**Design:** `docs/plans/2026-10-11-unofficial-tallies-design.md`

---

### Task 1: The `may_publish` rule and the download filter

**Files:**
- Modify: `src/skipjack/pipeline/turnout.py` (just below the `ELECTIONS` dict, ~line 182)
- Modify: `src/skipjack/web/downloads.py`
- Modify: `src/skipjack/web/app.py:53-59` (mount loop)
- Modify: `src/skipjack/web/routes/data.py` (new route)
- Test: `tests/test_turnout.py` (pipeline rule), `tests/test_static_site.py` (filter), `tests/test_turnout_web.py` (live route)

**Step 1: Failing test for the rule** — append to `tests/test_turnout.py` (it already does `from skipjack.pipeline import turnout`):

```python
def test_may_publish_which_report_families():
    """A certified election publishes only its figures of record: the board's
    unofficial eight-day turnout tallies (early-voting, same-day) stay out of the
    pages and downloads of a previous election. An in-flight election keeps its page,
    and families the certified reports actually use are always publishable."""
    assert turnout.may_publish("GP26", "county-canvass")
    assert turnout.may_publish("GP26", "requests")
    assert turnout.may_publish("GG26", "sent-returned")
    assert turnout.may_publish("GG26", "early-voting")
    assert not turnout.may_publish("GP26", "early-voting")
    assert not turnout.may_publish("GP26", "same-day")
```

**Step 2: Run** `uv run pytest tests/test_turnout.py -q` — expect FAIL (no attribute `may_publish`).

**Step 3: Implement** in `src/skipjack/pipeline/turnout.py`, right after the `ELECTIONS` dict:

```python
# The board's eight-day early-voting and same-day registration files are campaign-time
# tallies printed "Unofficial". A certified election's published figures come from its
# certified canvass reports alone, so those families stay in the archive and out of the
# pages and downloads of a previous election.
UNOFFICIAL_TALLY_FAMILIES = ("early-voting", "same-day")


def may_publish(election: str, family: str) -> bool:
    """Whether the site may publish this report family's figures for the election."""
    return family not in UNOFFICIAL_TALLY_FAMILIES or not (
        election in ELECTIONS and ELECTIONS[election].certified
    )
```

**Step 4: Run the rule test** — expect PASS.

**Step 5: Failing test for the download filter** — in `tests/test_static_site.py`, after
`test_downloads_are_only_aggregates`:

```python
def test_download_files_drops_unofficial_tallies_of_certified_elections(tmp_path):
    """A previous election's downloads carry its figures of record; the unofficial
    eight-day turnout tallies are neither linked nor downloadable."""
    (tmp_path / "turnout" / "GP26" / "early-voting" / "county").mkdir(parents=True)
    (tmp_path / "turnout" / "GP26" / "same-day" / "county").mkdir(parents=True)
    (tmp_path / "turnout" / "GP26" / "requests" / "county").mkdir(parents=True)
    (tmp_path / "turnout" / "GG26" / "sent-returned" / "county").mkdir(parents=True)
    (tmp_path / "voter_registration" / "monthly").mkdir(parents=True)
    for path, text in [
        ("turnout/GP26/early-voting/county/2026-06-23.csv", "x"),
        ("turnout/GP26/early-voting/county/2026-06-23_provenance.json", "{}"),
        ("turnout/GP26/same-day/county/2026-06-23.csv", "x"),
        ("turnout/GP26/requests/county/2026-06-19.csv", "x"),
        ("turnout/GG26/sent-returned/county/2026-10-09.csv", "x"),
        ("voter_registration/monthly/2026-08.csv", "x"),
        ("voter_registration/notes.txt", "not a download"),
    ]:
        (tmp_path / path).write_text(text)
    assert download_files(tmp_path) == [
        Path("turnout/GG26/sent-returned/county/2026-10-09.csv"),
        Path("turnout/GP26/requests/county/2026-06-19.csv"),
        Path("voter_registration/monthly/2026-08.csv"),
    ]
```

Add `from pathlib import Path` to the file's imports if missing (check the header first).

**Step 6: Run** `uv run pytest tests/test_static_site.py::test_download_files_drops_unofficial_tallies_of_certified_elections -q` — expect FAIL (early-voting/same-day files listed).

**Step 7: Implement** — rewrite `src/skipjack/web/downloads.py`:

```python
"""Which data files are offered for download.

Only aggregated, publishable files under these folders are ever served or copied into the
static site. Raw archives and the SQLite database are never included by default.
"""

from __future__ import annotations

from pathlib import Path

from skipjack.pipeline.turnout import may_publish

DOWNLOAD_ROOTS = ("voter_registration", "voter_file", "turnout")
DOWNLOAD_SUFFIXES = {".csv", ".json"}
SQLITE_NAME = "skipjack.db"


def download_files(data_dir: Path) -> list[Path]:
    """Paths relative to ``data_dir`` of every downloadable file, sorted.

    A certified election does not publish the board's unofficial tally families:
    its page and downloads carry the certified figures of record alone.
    """
    found = []
    for root in DOWNLOAD_ROOTS:
        base = data_dir / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not (p.is_file() and p.suffix in DOWNLOAD_SUFFIXES):
                continue
            relative = p.relative_to(data_dir)
            parts = relative.parts
            if root == "turnout" and len(parts) >= 3 and not may_publish(parts[1], parts[2]):
                continue
            found.append(relative)
    return sorted(found)
```

**Step 8: Run** the filter test — expect PASS.

**Step 9: Failing test for the live serving route** — in `tests/test_turnout_web.py`,
after `test_the_turnout_csv_download_route_serves_files`. Check `ls data/turnout/GP26/requests/county/`
first: if a requests CSV exists there, keep the GP26-200 assert with its real filename,
else drop that line (the GG26 line covers "official families still serve"):

```python
@needs_db
def test_the_live_app_does_not_serve_a_certified_election_s_unofficial_tallies():
    """Files under data/ must not leak through the mount: the unofficial tallies of a
    certified election 404 at direct download addresses too."""
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    client = TestClient(create_app())
    assert client.get("/downloads/turnout/GG26/requests/county/2026-10-09.csv").status_code == 200
    for forbidden in (
        "/downloads/turnout/GP26/early-voting/county/2026-06-23.csv",
        "/downloads/turnout/GP26/same-day/county/2026-06-23.csv",
        "/downloads/turnout/GP26/../../skipjack.db",
    ):
        assert client.get(forbidden).status_code == 404
```

**Step 10: Run** — expect FAIL (HTTPX: the mounted StaticFiles returns 200s, so the
first-assert version may pass; the 404 asserts fail with a 200 response).

**Step 11: Implement** — `src/skipjack/web/downloads.py` gains the roots it mounts:

```python
# voter_registration and voter_file are mounted and served wholesale; the turnout files
# are gated per family instead (see routes/data.py:turnout_download).
MOUNTED_ROOTS = ("voter_registration", "voter_file")
```

`src/skipjack/web/app.py` — the mount loop reads `for folder in MOUNTED_ROOTS:` and
`from skipjack.web.downloads import DOWNLOAD_ROOTS` becomes
`from skipjack.web.downloads import MOUNTED_ROOTS`.

`src/skipjack/web/routes/data.py` — imports and one new route:

```python
from fastapi import APIRouter, HTTPException, Request, Response
from skipjack.pipeline.turnout import may_publish
from skipjack.web.downloads import DOWNLOAD_SUFFIXES, download_files
from skipjack.web.paths import DATA_DIR
```

```python
@router.get("/downloads/turnout/{relative:path}")
async def turnout_download(relative: str):
    """Serve a turnout snapshot's CSV or provenance file, gated per family: the
    unofficial tallies of a certified election are not published."""
    parts = Path(relative).parts
    if parts and not any(p in ("..", ".") for p in parts):
        election_family = (parts[0], parts[1]) if len(parts) >= 3 else None
        if election_family and may_publish(*election_family):
            target = DATA_DIR / "turnout" / relative
            if target.is_file() and target.suffix in DOWNLOAD_SUFFIXES:
                return Response(
                    content=target.read_bytes(),
                    media_type="application/json" if target.suffix == ".json" else "text/csv",
                )
    raise HTTPException(404)
```

**Step 12: Run** the route test — expect PASS. Then run files again quickly:
`uv run pytest tests/test_static_site.py tests/test_turnout_web.py tests/test_turnout.py -q`.

**Step 13: Commit**

```bash
git add src/skipjack/pipeline/turnout.py src/skipjack/web/downloads.py src/skipjack/web/app.py src/skipjack/web/routes/data.py tests/test_turnout.py tests/test_static_site.py tests/test_turnout_web.py
git commit -m "may_publish: a certified election serves only its figures of record"
```

### Task 2: The certified page drops the unofficial tiles and columns

**Files:**
- Modify: `src/skipjack/web/routes/turnout.py:152-173` (`_certified_tiles` early-voting and same-day blocks), `:75-91` (`_certified_dates` tuple)
- Modify: `src/skipjack/web/turnout.py:364-372` (`certified_headline` family loop), `:413-489` (`certified_county_rows` joins and fields)
- Modify: `src/skipjack/web/turnout.py:243-258` (`snapshot_sources` csv/provenance, conditional)
- Modify: `src/skipjack/web/templates/turnout_final.html` (two `<th>`, two `<td>`), `src/skipjack/web/templates/turnout_election.html` (source-strip link conditional)
- Test: `tests/test_turnout_web.py`

**Step 1: Failing tests.** In `test_real_gp26_page_renders_the_certified_data`, replace
the `assert "185,031" in r.text` and `assert "2,179" in r.text` lines with:

```python
    # a certified page carries only its figures of record: nothing from the board's
    # unofficial eight-day turnout files, not even their download links
    for forbidden in (
        "185,031",
        "2,179",
        "Early voting (the EV tally)",
        ">Same-day</th>",
        "/downloads/turnout/GP26/early-voting",
        "/downloads/turnout/GP26/same-day",
    ):
        assert forbidden not in r.text
```

In `test_certified_county_rows_join_the_families`, add unofficial-family rows for
Allegany before `rows = turnout.certified_county_rows(...)`:

```python
    add(
        conn,
        "2026-07-23",
        [("county", "Allegany", "", "ALL", "ALL", "total", 1308)],
        family="early-voting",
    )
    add(
        conn,
        "2026-07-23",
        [("county", "Allegany", "", "ALL", "ALL", "ALL", m, v) for m, v in
         [("new-total", 11), ("addrchg-total", 5), ("total", 16)]],
        family="same-day",
    )
```

…and after the existing row asserts:

```python
    assert "early_voting_total" not in row
    assert "same_day" not in row
```

**Step 2: Run** `uv run pytest tests/test_turnout_web.py -q` — expect the two updated
tests FAIL (GP26 page still contains the figures; row still carries the keys).

**Step 3: Implement.**

`src/skipjack/web/turnout.py` — `certified_headline`'s family loop loses its last two
tuples:

```python
    for family, measures in (
        ("mail-voting", ("sent", "received", "accepted", "rejected")),
        ("requests", ("total",)),
    ):
```

`certified_county_rows` — delete the `early = ...` join (lines ~453-455), the
`same_day = ...` join (~456), the `may_voting`'s neighbours stay; remove
`"early_voting_total"` and `"same_day"` from the row dict (~481-486). The rest of the
function is unchanged.

`snapshot_sources` — a source whose family the election no longer publishes carries no
download links:

```python
            "fetched": r["fetch_date"][:10],
            "method": r["extraction_method"],
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
```

(import `may_publish` alongside the module's other pipeline imports — check the top of
`src/skipjack/web/turnout.py`; it likely already imports from `skipjack.pipeline.turnout`.)


`src/skipjack/web/routes/turnout.py`:
- `_certified_dates`: delete the `"early-volving"`/`"same-day"` entries from the tuple
  (`"early-voting"`, `"early-voting"` careful: exact strings `"early-voting"` and
  `"same-day"`).
- `_certified_tiles`: delete the early-voting tile block
  (`if cert.get("early-voting", {}).get("total"):` …) and the same-day block
  (`same_day = cert.get("same-day")` …).

`src/skipjack/web/templates/turnout_final.html`: delete
`<th class="num">Early voting (the EV tally)</th>` and `<th class="num">Same-day</th>`
and their two matching `<td class="num">` cells.

`src/skipjack/web/templates/turnout_election.html`: the source strip becomes
conditional on the links existing:

```jinja
{% if s.csv %} — <a href="{{ base }}/downloads/{{ s.csv }}">CSV</a> ·
    <a href="{{ base }}/downloads/{{ s.provenance }}">provenance</a>{% endif %}
```

**Step 4: Run** `uv run pytest tests/test_turnout_web.py -q` — expect all PASS.

**Step 5: Commit**

```bash
git add src/skipjack/web/turnout.py src/skipjack/web/routes/turnout.py src/skipjack/web/templates/turnout_final.html src/skipjack/web/templates/turnout_election.html tests/test_turnout_web.py
git commit -m "certified turnout pages carry only certified figures"
```

### Task 3: Data-page listing respects the filter

**Files:**
- Modify: `src/skipjack/web/routes/data.py:56-57` (skip unofficial family dirs)
- Test: `tests/test_turnout_web.py`

**Step 1: Failing test** — in `tests/test_turnout_web.py`, after
`test_data_page_lists_the_turnout_snapshots`:

```python
@needs_db
def test_the_data_page_does_not_list_a_certified_election_s_unofficial_tallies():
    from fastapi.testclient import TestClient

    from skipjack.web.app import create_app

    html = TestClient(create_app()).get("/data/").text
    assert "/downloads/turnout/GP26/early-voting/" not in html
    assert "/downloads/turnout/GP26/same-day/" not in html
    assert "/downloads/turnout/GP26/requests/" in html
```

**Step 2: Run** — expect FAIL.

**Step 3: Implement** in `turnout_listing`'s family loop:

```python
        for family_dir in sorted(p for p in election_dir.iterdir() if p.is_dir()):
            if not may_publish(election_dir.name, family_dir.name):
                continue
```

**Step 4: Run** — expect PASS. **Step 5: Commit**

```bash
git add src/skipjack/web/routes/data.py tests/test_turnout_web.py
git commit -m "the data page lists no unofficial tallies for a certified election"
```

### Task 4: Docs

**Files:**
- Modify: `docs/turnout-data-summary.md` (the GP26 bullet ~line 29, and after the
  family table ~line 102)
- Modify: `README.md` (the turnout page bullet, ~line 40)

**Step 1:** Rewrite the `docs/turnout-data-summary.md` GP26 row to the certified
content and add, after the family table, one sentence: a certified election's page
carries only its certified figures; the unofficial `early-voting`/`same-day` snapshots
are parsed for the record and never published. Adjust the row's trailing cell to drop
"early-voting and same-day totals".

**Step 2:** In `README.md` line ~40, remove "early-voting totals and same-day
registrations" from the certified-page description, keeping the rest of the list
intact.

**Step 3: Commit**

```bash
git add docs/turnout-data-summary.md README.md
git commit -m "docs: certified pages publish only certified figures"
```

### Task 5: Verify and ship

**Step 1:** `uv run ruff check src tests` (lint the touched files at minimum; the repo
has ruff config in `pyproject.toml`).
**Step 2:** `uv run pytest -q -m "not slow"` — the CI gate; expect all green.
**Step 3:** Build the site locally and check links resolve with purged files:
`uv run skipjack build-site --out /tmp/sj-site --base-path /skipjack` — the build must
report no problems (its `check_links` would flag any dangling data-page/source-strip
link to a purged file).
**Step 4:** Screenshot `/turnout/gp26/` locally (headless Chrome against
`uv run skipjack serve` on a free port) and eyeball: no EV-tally/Same-day column, no
185,031/2,179 tiles.
**Step 5:** Commit any stragglers, push to main, watch the "Publish site" workflow to
success (`gh run list`), then verify the live `https://newsappsumd.github.io/skipjack/turnout/gp26/`
page (no ET figures, data page shows no GP26 early-voting/same-day sections).