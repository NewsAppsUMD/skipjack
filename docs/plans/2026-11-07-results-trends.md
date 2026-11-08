# County results over time: `/results/county/{slug}/`, 2010–2026

## Context

The Results tab has a page per election per county, but nothing compares a county's results
across elections. The user wants a place to see how each county's results for federal, state
and county offices have changed over time, with creative visuals.

Decisions made with the user:
- **Horizon: back to 2010** using the SBE archive. Verified today: the archive pages for 2010,
  2012, 2014, 2016 and 2018 publish per-county CSVs in the *same* named layout as 2020
  (`{Stem}_County_{Year}_General.csv`, `{Stem}_County_{Democratic|Republican|NonPartisan}_{Year}_Primary.csv`),
  with statewide check files every year. The 2026-10-15 design doc's claim that pre-2020
  county CSVs don't exist was wrong; README and `results_index.html` repeat it and need fixing.
- **Generals and primaries.**
- **A new page family under Results**, not tabs on the per-election county pages:
  `/results/county/` (index of 24) and `/results/county/{slug}/`. Linked from the Results
  index, every election's county page, and a third link in `county_nav` ("Results over time").

Assumptions (not asked): GG26's unofficial count is included as a flagged provisional last
point. Baltimore City's Mayor / Council President / Comptroller / City Council count as its
"county" level offices (the city has no county executive); other municipal offices
(Cumberland, Hagerstown) are left out.

Verified data facts that shape the plan (lead + Plan agent, live SBE fetches today):
- Vote types 2010–2018 (both kinds): `Early, Election Night, Absentees, Provisional, 2nd Absentees`;
  the 2010 general adds `3rd Absentees`. Generals carry `Write-In?` and `... Votes Against` twins.
- `#NULL#` appears unquoted where zero is meant (2010 general, 2012 REP primary); `_int()` must
  treat it as blank. Winner is `Y`/blank (accept `YES` too). Office names are padded with spaces
  (already stripped by `_frame`).
- Check files: generals 2012–2018 `State_Congressional_Districts_{Year}_General.csv`
  (`Vote Type, County, Candidate Name, Party, Office Name, Office District, Winner, Write-In?, CONG 04, CONG 08, …`,
  scrambled order); 2010 has only `State_Legislative_Districts_2010_General.csv` (same header
  with `LEGS …` columns, County `00` rows cover every statewide office). Primaries 2010–2018 have
  per-party `State_{Congressional|Legislative}_Districts_{Party}_{Year}_Primary.csv` with a
  shorter header (no Vote Type, no Write-In?). So, unlike PP20, no election lacks a check.
- Office names drift: `U.S. Congress` (2010, 2022+), `Rep in Congress` (2012–2016),
  `Representative in Congress` (2018–2020); `Judge Orphans Court` (no apostrophe) in 2010/2018.
  New party codes: `CON`, `IND`, `BOT` (both-party write-in line).
- 2012/2016 presidential primaries repeat President rows by congressional district; the
  existing `_DEDUP` rule in `web/results.py` already handles it.
- `GG18` is also a turnout code; no collision (source ids, archive dirs, data dirs and URLs are
  all family-prefixed), but note it in the registry.
- `web/trends.py` and `routes/trends.py` already exist (registration trends), so the new module
  is `web/results_trends.py` and the partial is `templates/_results_trends.html`.

## Phase A: pipeline, the `named` layout for 2010–2020

File: `src/skipjack/pipeline/results.py` (readers `_read_named2020`, `_read_wide`, `_int`,
`_check_statewide`, `_parse_election`, `ResultsFile.file_name`, `county_files`).

1. Registry. Rename layout `"named2020"` → `"named"` (one layout parameterized by year).
   Add `VOTE_TYPES_ARCHIVE` (five types, stems `early, election_night, absentees, provisional, absentees_2`)
   and `VOTE_TYPES_2010_GENERAL` (+ `absentees_3`). Add `ResultsElection.check` in
   `{"congressional", "legislative", "all_state", "none"}`; `_election()` sets layout/vote
   types/check by year. Ten new entries with SBE codes and days (match `web/elections.py`
   lists): GP10 2010-09-14, GG10 2010-11-02, PP12 2012-04-03, PG12 2012-11-06, GP14 2014-06-24,
   GG14 2014-11-04, PP16 2016-04-26, PG16 2016-11-08, GP18 2018-06-26, GG18 2018-11-06.
2. File names: named county files per year; check files per `check` kind (general → one `all`
   check; primary → one per party file, missing `non` tolerated as today).
3. Reader: `#NULL#` → blank → 0 under the named layout (row-total check still enforced);
   winner `Y`/`YES`; **fail loudly on vote columns in the header that the registry does not
   list** (today it only fails the other way; allow 2020's `N/A Votes`).
4. Check: `_check_statewide` accepts `Congressional District…`, `CONG …` and `LEGS …` columns;
   the Vote Type split only for generals; `_parse_election` dispatches on `e.check`. Keep
   parse all-or-nothing. Bump `PARSER_VERSION` to 0.3.0. No speculative malformed-row fixups
   (five 2018 generals checked, all clean); if a real file trips `_frame`, add a per-file
   `row_fixups` recorded in the sidecar.
5. Tests in `tests/test_results.py` (new `fill_named` helper beside `fill2020`):
   registry/days/vote types/check per election; named URLs incl. GG10 legislative and GP14
   per-party checks; 2010 six vote types; `#NULL#`; winner spellings; unregistered vote column
   rejected; check readers for the three header shapes; GG10 parse names the legislative file;
   mismatch writes nothing; fetch skips absent `non` files. `tests/test_results_db.py`: GG10
   loads six vote types. Rename the `named2020` references.
6. Refresh: `make results` (walks the registry, skips archived) then `make build-db`. Commit
   per year: "results: 2010 archive files and parsed county results" etc. Then add
   `Rep in Congress` to `CONGRESS_OFFICES` and `Judge Orphans Court` (and whatever
   `test_real_every_office_is_classified` reports) to `LOCAL_OFFICES` in `web/results.py`,
   and pin a few statewide totals against the SBE site (e.g. GG14 Hogan, PG16 Clinton).

## Phase B: office crosswalk and query layer

### `src/skipjack/web/offices.py` (new)

`Office(key, level, title, districted)`; `OFFICES` in display order; `OFFICE_NAMES` mapping
every SBE spelling to a key (`us_house` ← the three Congress spellings; `orphans_court` ← both
spellings; `board_of_education_at_large` ← both; judges → `judge`; Cumberland/Hagerstown →
`municipal`; Cent Comm → `party`; convention delegates → `convention`). Levels: `federal`
(president, us_senate, us_house), `state` (governor, comptroller, attorney_general,
state_senate, house_of_delegates), `county` (county_executive, council_president,
county_council, county_council_at_large, commission_president, county_commissioner,
commissioner_at_large, sheriff, states_attorney, clerk, register_of_wills, treasurer,
board_of_education(+at_large), orphans_court; for Baltimore City also mayor, city council
president, city comptroller, city council → level `county`, titles "Mayor" etc.).
`canonical(office, scope)` reuses `results._scope`'s Comptroller rule (statewide only when on
all 24 ballots, else Baltimore City's). Eras: `ERAS = {"us_house": (2012, 2022),
"state_senate": (2014, 2022), "house_of_delegates": (2014, 2022)}`; `era(key, year)`.

Guard tests `tests/test_offices.py`: `set(results._OFFICE_SCOPE) == set(OFFICE_NAMES)` so a
new SBE spelling forces both a scope and a crosswalk decision; levels agree with scopes;
spelling variants share a key; Comptroller demotion; display order.

### `src/skipjack/web/results_trends.py` (new)

Built on `results.county_page` / `results_unofficial.county_page` (same dispatch as
`routes/results._queries`), so `_DEDUP`, write-ins, retention shape and GG26 come free.
Cost measured ~10–20 ms per county page; 24 × 18 ≈ 5–10 s at build time, acceptable.

```python
county_slugs(conn) -> list[str]            # SBE order, counties with rows
counties_index(conn) -> list[dict]         # per county: latest top margin, spark, shift since 2010
county_trends(conn, slug) -> dict | None
```
Shape: `elections` (oldest first, with `cycle` presidential/gubernatorial and `unofficial`),
`generals`, `primaries`, `levels: [{level, label, offices: [SERIES]}]`, `summary`, `source_ids`.
- `SERIES = {key, title, district, id, districted, breaks: [years], general: [GPOINT|None per general], primary: {dem|rep|non: [PPOINT|None per primary]}}`.
- `GPOINT = {election, year, unofficial, era, seats, contested, total, dem: {name, votes, share}|None, rep: …, other_share, margin (D−R points, None if uncontested), winners: [{name, party}] (SBE flag; [] on GG26), leader_party, matched (statewide offices: county leader's party == flagged winner's party; None without a flag), href (county page anchor), contest_href}`.
  Multi-seat contests sum the party's candidates; `contested` = named DEM and REP both present.
- `PPOINT = {election, year, unofficial, party_file, seats, candidates, contested, total, winner: {name, votes, share}|None, href}`.
- `summary.top` per general: the top-of-ticket contest (`ticket.TOP_OF_TICKET`) as a GPOINT
  plus `statewide_margin` (statewide D−R from the county sums via `results.contest`) and
  `lean = margin − statewide_margin`.
- `summary.primary_electorate` per primary: Dem vs Rep ballots in each party file's first
  contest, `dem_share_of_two`.
- `summary.ticket_gap` per general: for county-level contested contests chosen by
  `ticket.countywide`, `gap = dem_share − top dem_share`, `mean_gap`.
- `summary.disagreements`: elections where the county's leader (general) or primary winner
  differed from the statewide winner (SBE flag), newest first, with names.
- `summary.winners_wall` per office: the sequence of winners (name, party, year), with
  runs of the same name collapsed to "2010–2022" so incumbency reads at a glance.
- Breaks: from `ERAS` for districted federal/state series; data-driven for county districts
  (district set changes between elections of the same kind). Excluded levels: judicial,
  municipal (non-Baltimore), party, convention.

Tests `tests/test_results_trends.py` (in-memory sqlite, `add` helper copied from
`test_results_web.py`, `N_COUNTIES=2`, two counties, three generals + two primaries spanning
a 2022 break, a multi-seat contest, a retention judge, a write-in line, GG26 unofficial rows):
axis order and split; general point shares/margin/winners; uncontested → no margin;
multi-seat summing; `matched` follows the flag and is None without one; us_house breaks at
2012/2022 with `era`; county break on district-set change; excluded kinds; Comptroller in a
presidential year is not a state series; primary points; electorate; top-of-ticket by cycle;
ticket gap; GG26 flagged last point; unknown county → None; `counties_index` rows;
DB-backed (`needs_db`) "every county builds and has the three levels".

## Phase C: pages and visuals

### Routes and plumbing

- `routes/results.py`: `@router.get("/county/")` and `@router.get("/county/{county_slug}/")`
  registered **above** `/{slug}/` (Starlette matches in order; no election slug is `county`).
  Context: `trends`, `sources` via `source_links`, `data_page`, `unofficial` when a GG26 point
  exists. Update the module docstring's route-order note.
- `static_site.page_urls()`: add `/results/county/` and one URL per `county_slugs(conn)`
  inside the existing `OperationalError` guard.
- `_macros.html` `county_nav`: third link "Results over time" → `/results/county/{slug}/`
  (`active == "results"`); statewide lands on the index.
- `results_county.html`: a note under the lead linking "how {{ county }} has voted since 2010".
- `results_index.html`: a "By county, over time" article at the top; fix the pre-2020 note.
- `_results.html`: let `breadcrumb(election=none, trail)` handle a page with no election.

### The county page `results_county_trends.html` (visual design)

Rendering: server-side SVG laid out in Python and emitted by Jinja macros in
`templates/_results_trends.html`, the pattern of `web/ticket.py` `_plot` + `_ticket.html`
(no JS; `<title>` tooltips; keyboard-focusable `<a>` per mark; a `<details>` table twin under
every figure for the WCAG/table-view rule). Layout code lives in `web/results_trends_plot.py`
(pure functions from the dict shapes above to pixel dicts), tested like `test_ticket.py`.
Colours: the site's party tokens (`--party-dem #2a78d6`, `--party-rep #e34948`, minor
`#52514e`) for identity, the map `party_margin` ramp (`--party-0…4` in `maps.css`) for the
diverging margin tiles, ink/gray for emphasis, hollow marks + "unofficial" tag for GG26.
Blue/red stay reserved for the parties; non-party measures use green/gold as the maps do.
New CSS classes `.trend-*` in `style.css`. Each chart has one axis; nothing dual-axis.

Sections, in order, each under a `subnav` anchor:

1. **Lead: "How {County} leans"**, a hero line + the *lean timeline*. One sentence computed
   from `summary.top`: "Montgomery has voted Democratic at the top of the ticket in all 9
   generals since 2010; its margin widened from D+41 to D+60." A hero figure of the latest
   margin and the change since 2010. The chart: x = generals 2010→2026 (ordinal, presidential
   years marked with a filled tick, gubernatorial with an open one), y = D−R margin in
   points, zero line in ink. Two series on one axis, same units: the **county** margin as a
   2px line in ink with 8px dots coloured by the leading party, and **Maryland statewide** as a
   gray line (emphasis form). The band between them is a light fill labelled "lean vs the
   state" at the right end. GG26 point hollow. Rows link to that election's county page.
2. **"The ballot, election by election"** (the signature visual): a heatmap grid. Rows = every
   office in the three levels (grouped with level headers: Federal, State, County; districted
   offices one row per district the county has voted in), columns = generals 2010→2026. Each
   cell is a tile on the `party_margin` diverging ramp with the margin printed where it fits
   ("D+23"), winner's party as a chip; uncontested cells are a single-hue tile with "unopp.";
   offices not on that year's ballot are hatched (the maps' `none` convention); a redistricting
   break draws a 2px surface gap and a tiny "new lines" marker in the header. Click a tile →
   `GPOINT.href`. The grid reads like a nine-column ballot history and makes split tickets pop
   (a red Sheriff row through a blue county). Table twin: the same grid as `<table>`.
3. **Office lines (small multiples)**, `.multiples` grid, one card per series with ≥3 general
   points: Dem share and Rep share as two lines (party colours, direct-labelled at the end,
   legend present), "other" omitted from lines but in the table; the line does not connect
   across a break (gap + "new lines"); hollow last point when unofficial; a winner's-name
   label on the extreme points only (selective labels, never every point). Multi-seat series
   titled "party vote share".
4. **"Ahead of the ticket, over time"**: a dot-row plot in the style of the existing
   `dot_plot` but transposed, one row per general, zero = the top-of-ticket Dem share, one dot
   per county office (`summary.ticket_gap`), colour per office from `ticket.SERIES_COLORS`
   (cap at 4 named offices; the rest fold into "other county offices" in gray). Reads as
   "county offices ran 12 points to the right of the ticket in 2014, 3 in 2022".
5. **Ballots cast**: a column chart of `summary.top.total` per general, presidential years in
   the darker of two steps of one green ramp, gubernatorial in the lighter (ordinal, legend
   present); no second axis. (A later pass can divide by registration from the voters
   tables for a turnout rate.)
6. **Primaries**: two parts. *Primary electorate*: paired columns per primary, Dem ballots vs
   Rep ballots in the top contest (party colours), with `dem_share_of_two` printed above each
   pair. *Who the county picked*: a compact table per party file: year, top contest, county's
   winner and share, field size, and a "≠ statewide" marker when the county disagreed with
   the statewide nominee; county-office primaries listed under it (Sheriff D, State's Attorney
   D…) as text rows, since names change every cycle and a chart would mislead.
7. **"When {County} disagreed with Maryland"**: `summary.disagreements` as a short list
   (general and primary), newest first, each linking to the contest page. Empty state: "never
   since 2010".
8. **Winners wall**: per office, the run of winners as party-coloured chips with year spans
   ("Sheriff: Jenkins (R) 2010–2022"), county offices first. This is where incumbency and
   open seats are visible without a chart.
9. Source strip (`r.source_strip`) naming every election's files; a note on eras and on the
   data-driven council breaks; the unofficial banner when GG26 is present.

### The index `results_county_trends_index.html`

A county map (reuse `maps.county_map` with the `party_margin` scale) coloured by **the shift
in top-of-ticket margin, 2010 → latest certified general** (diverging: moved toward D / toward
R), `link="county_page"` → each county's trends page; a sortable table (`data-sortable`) of 24
rows: county, latest margin, shift since 2010, a 9-cell inline mini-strip of tiles (the lean
sparkline, same ramp), counts of generals/primaries covered.

### Tests

- `tests/test_results_trends_plot.py`: layout functions on synthetic dicts (tick positions,
  zero line, hollow unofficial flag, break gaps, tile classes come from the map `Scale`,
  fold-to-other at 4 series).
- `tests/test_results_routes.py`: index lists every county; county page renders the three
  level headings, the grid, sources; unknown county 404; `/results/county/` and
  `/results/county/allegany/` are not read as an election/contest and `/results/gp26/county/`
  still works; election county page and Results index link to trends; base-path test
  covers the new URLs.
- `tests/test_web.py` (or voterfile tests): `county_nav` shows "Results over time".
- `tests/test_static_site.py`: new URLs in `page_urls`; still none when tables are empty.
- `tests/test_maps.py`: every new `.trend-*` colour class is styled.

## Docs, README, commits

- `docs/plans/2026-11-07-results-trends-design.md` (decisions, verified sources, crosswalk,
  eras, dict shapes, the visual spec above) and `docs/plans/2026-11-07-results-trends.md`
  (task list). Append a correction to `2026-10-15-results-county-design.md`'s "2018 and
  earlier" paragraph. README: data row "2010 through 2024 and the 2026 primary", the Results
  bullet, a sentence on the trends pages. Makefile needs no new target.
- Commits on main, lowercase, in this order: (1) named layout + tests; (2) one commit per
  year of archives/data 2010→2018; (3) office classification + pinned totals;
  (4) crosswalk; (5) query layer; (6) plot layout + templates + routes + nav + static site;
  (7) docs/readme. Execute per the user's workflow prefs (subagent-driven, plan in
  `docs/plans/`).

## Verification

- `uv run pytest -q` after each step; `uv run ruff check src tests`.
- After the refresh: `make build-db`, `uv run pytest -k real`; spot-check statewide totals
  against the SBE site for GG10, GG14, PG16, GG18.
- `make site SITE_BASE=/skipjack`: zero broken links; record page count and wall time before
  and after (ten elections add ≈1,500 pages).
- Browser preview (`make serve` via launch.json): `/results/county/` map and table;
  `/results/county/montgomery/`, `/results/county/garrett/` (a red county), `/results/county/baltimore-city/`
  (city offices under County); check the grid hatching for offices absent in a year, the
  2012/2022 breaks on the U.S. House row, the hollow GG26 point, the table twins, phone width;
  `/voters/county/allegany/` shows the third nav link; `/results/gg14/` and
  `/results/gg10/governor-lt-governor-all/` render from the new data.
