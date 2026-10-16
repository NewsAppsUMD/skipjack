# Results tab: county-level results for Maryland's primaries and generals, 2020 onward

## Context

Skipjack has registration, voter file and turnout sections but no vote counts. The SBE publishes
each election's results as CSVs on an "election data" page: the current cycle at
`https://elections.maryland.gov/elections/2026/election_data/` and earlier cycles under
`https://elections.maryland.gov/elections/archive/<year>/election_data/`. A **Results** tab covers
the county-level files, for both primaries and generals, across years: a hub per election with its
contests and statewide totals summed from the county files, a page per statewide / congressional /
legislative contest with a county table and map, and a page per county with every contest on its
ballot. Precinct, councilmanic / commissioner, and ballot-question files are later passes; the
design leaves room for them.

Decisions made with the user: statewide + county pages; a one-time, re-runnable fetch (not part of
`turnout-daily.yml`); Non-Partisan primary files included; support several elections, primary and
general, over several years.

## What the source looks like (verified 2026-10-15)

**2022 onward, one naming scheme.** Files are `{CODE}_{NN}{Suffix}.csv`, NN = 01..24 in the SBE's
county order (01 Allegany, 02 Anne Arundel, 03 Baltimore City, 04 Baltimore County, … 17 Prince
George's, 18 Queen Anne's, 19 Saint Mary's, 20 Somerset, … 24 Worcester). Codes: GP22 / GG22
(2022 gubernatorial primary / general), PP24 / PG24 (2024 presidential primary / general), GP26
(2026 primary, at the non-archive URL), GG26 (2026 general, not published yet; the URL answers an
HTML page, as the turnout PDFs do before they exist).

- Primary county files: `{CODE}_{NN}DemocraticResults.csv`, `…RepublicanResults.csv` and, for
  counties with non-partisan contests, `…Non-PartisanResults.csv`. Header (BOM, trailing space
  on the last name): `Office Name, Office District, Candidate Name, Party, Winner, Early Votes,
  Election Night Votes, Mail-In Ballot 1 Votes, Provisional Votes, Mail-In Ballot 2 Votes, Total
  Votes`. `Party` is DEM / REP / NON.
- General county files: `{CODE}_{NN}CountyResults.csv`, all parties in one file. Same first five
  columns, then `Write-In?`, then each vote type as a `… Votes` / `… Votes Against` pair, then
  `Total Votes`, `Total Votes Against`. "Against" is filled only for judges' continuance questions;
  write-ins are rows with `Write-In?` = Y.
- Cross-check files: primaries have `{CODE}_CongressionalBreakDown{Democratic,Republican,
  Non-Partisian}.csv` (the SBE's spelling); generals have `{CODE}_CongressionalBreakDown.csv` with
  a leading `Vote Type (FOR=1,AGAINST=2)` column. Each carries a `County` = `00` "State of
  Maryland" row per candidate whose eight district columns sum to the statewide total. Verified on
  GP26: all 1,558 Democratic rows' totals equal the sum of the five vote-type columns, and the
  county sums for Governor equal the breakdown's statewide row.
- Also present but out of scope now: `…QuestionResults.csv` (ballot questions, a different
  layout), `…Counc…` / `…Commissioner…` (council / commissioner district breakdowns), precinct
  files, legislative breakdowns.

**2020, a second layout.** Files are named by county: `{County}_County_2020_General.csv`
(`Baltimore_City_…`, `Baltimore_County_…`, `St._Marys_…`, `Queen_Annes_…`, `Prince_Georges_…`) and
`{County}_County_{Democratic|Republican|NonPartisan}_2020_Primary.csv`. Columns are reordered
(`Candidate Name, Party, Office Name, Office District, Winner, …`) and the vote types are named
differently: general `Early Voting, Election Day, By Mail, Prov., By Mail 2` (each with an
`Against` twin, plus `Write-In?`); primary `Vote By Mail, In Person Voting, Provisional` (no BOM,
unquoted). The statewide check source is `All_State_2020_General.csv` (County `00` rows) and
`State_Congressional_Districts_2020_General.csv`; the 2020 primary has no statewide file on the
page, so its check is only the per-row total.

**2018 and earlier** publish only non-partisan primary county files and state-level congressional
files on these pages; the partisan county results are not there as CSVs. Out of scope; the plan
notes it so a later pass can use another source.

## Design

Copy the turnout pipeline's shape (`src/skipjack/pipeline/turnout.py`): a registry of elections
and their files, a fetch that archives them with manifest rows, a parse that writes one CSV plus a
`_provenance.json` per source file and refuses to write anything that does not add up, a loader in
`build_db`, a web module with queries, routes, templates and static-site URLs. Everything is keyed
by the SBE election code so adding an election is a registry entry.

### 1. Pipeline: `src/skipjack/pipeline/results.py`

- `@dataclass ResultsElection(code, year, kind: "primary"|"general", name, election_day,
  base_url, layout: "coded"|"named2020", vote_types: list[tuple[label, column_stem]],
  published=True)`. `RESULTS_ELECTIONS` holds GP22, GG22, PP24, PG24, GP26 (published) and PG20,
  PP20 (layout `named2020`) and GG26 (`published=False` until the SBE posts it; fetch tolerates the
  HTML answer and logs "not published", so flipping it later needs no code). `base_url` is the
  archive path for past years and the live path for 2026; a one-line note says 2026 moves to the
  archive after the cycle.
- `@dataclass ResultsFile(election, county_code, county, party_file: "dem"|"rep"|"non"|"all",
  role: "county"|"check")` with `url` (built from the election's layout) and `source_id` like
  `sbe-results-PG24-county-01-all`. `county_files(election)` yields the 24 × (3 or 1) county files
  and the check files. County names use the repo's canonical names (`COUNTY_NORMALIZE` values in
  `pipeline/vrar.py`; the SBE's "Saint Mary's" is the repo's "St. Mary's"); a `COUNTY_CODES` dict
  and a `COUNTY_FILE_STEMS` dict (2020 names) live here.
- `results_fetch(election=None, force=False) -> list[SourceRecord]`: for each file, skip when
  `find_source` + `resolve_archived` has it (unless `force`); download to
  `archives/sbe/results/<CODE>/<file name>`; treat an HTML body as "not published" (add an
  `expect_csv` check beside `expect_pdf` in `fetch.fetch_file`: the body must not start with
  `<` after an optional BOM); `append_to_manifest`. A county's missing `non` file is normal and
  logged; a missing `dem`/`rep`/`all` file is reported at parse time.
- `results_parse(election=None, force=False) -> tuple[int, list[str]]`:
  - a layout reader per layout (`_read_coded`, `_read_named2020`) normalizes each county file to
    the same rows: `election, county, party_file, office, district, candidate, party, winner (0/1),
    write_in (0/1), total_votes, total_against`, plus one row per vote type in a second frame:
    `vote_type, votes, votes_against`. Header whitespace and BOM stripped; the `Against` columns
    are null when blank.
  - writes `data/results/<CODE>/county/<NN>-<slug>-<party_file>.csv` (candidates, wide on the
    identity columns, one column per vote type as `<stem>_votes` / `<stem>_against`, in the
    election's vote-type order) with a `..._provenance.json` sidecar via
    `ProvenanceSidecar.from_source` (election, family `"results"`, geography `"county"`,
    `checks`). Keeping the file wide keeps it human-readable; the loader melts it.
  - per-row check: `total_votes` equals the sum of the vote-type columns (same for `against`).
  - cross-check after all of an election's counties parse: for every contest with a blank
    district, county sums per candidate must equal the check file's `County == "00"` row summed
    across its district columns (FOR rows only for generals). A mismatch, or a missing required
    county file, fails that election's parse with a message naming the file; nothing is written
    for it. 2020 primaries record `"statewide_check": "no statewide file published"`.

### 2. Schema and loader

- `models.py`: `results_county` (`id, source_id REFERENCES sources, election, county, party_file,
  office, district, candidate, party, winner INTEGER, write_in INTEGER, total_votes INTEGER,
  total_against INTEGER`) and `results_votes` (`result_id REFERENCES results_county, vote_type,
  votes, votes_against`), indexes on `(election, office, district, party_file)` and
  `(election, county)`. Reuse the `sources` table via `_upsert_source` in `build_db.py`.
- `build_db.py`: `load_results(conn, data_dir)` over `data/results/<CODE>/county/*.csv`,
  requiring a sidecar (`ResultsLoadError`, like `TurnoutLoadError`), checking the CSV's `election`
  and `county` columns against the sidecar and file name, melting the vote-type columns using the
  registry's list; called from `build()`.

### 3. Web: queries in `src/skipjack/web/results.py`

- `elections(conn)` → the codes with rows, newest first, with year / kind / name / slug (`code.lower()`,
  matching `/turnout/gp26/`).
- `contests(conn, code)` grouped for the hub: statewide (blank district, in all 24 counties),
  `U.S. Senator`, `U.S. Congress`, `State Senator`, `House of Delegates`; a primary's contests are
  per party file, a general's span parties. Each with candidates, statewide totals (summed from
  counties), winner flags, counties reporting.
- `contest(conn, code, slug)` → candidates with statewide totals and per-vote-type sums; county
  table (one row per county in the contest, a column per candidate, total); `counties_in_contest`.
- `county(conn, code, county)` → every contest on that county's ballot in SBE order, grouped by
  party file (primary) or in one list (general), with the vote-type breakdown per candidate and the
  `Against` column only where any row has one.
- Contest slug via `slugify(f"{office} {district} {party_file}")` → `governor-lt-governor-dem`,
  `u-s-congress-5-all`; a `contest_slug()` helper shared by the hub, county pages and `static_site`.
- Maps (`maps.results_map`): a primary contest shows the winner's share by county
  (`sequential()`, `fmt_pct(1)`); a general two-party contest shows the D − R margin
  (`party_margin()`, `fmt_margin`); counties outside the contest are `None` (hatched);
  `link="row"`.

### 4. Routes and templates

- `routes/results.py`, `prefix="/results"`: `/` (elections by year, primary and general side by
  side), `/{slug}/` hub, `/{slug}/county/` index, `/{slug}/county/{county_slug}/`,
  `/{slug}/{contest_slug}/`; 404 for unknown slugs; `include_router` in `web/app.py`.
- Templates `results_index.html`, `results_election.html`, `results_contest.html`,
  `results_county.html`, following `turnout_election.html` / `turnout_geography.html` and the
  `_macros.html` tables; `{% import "_maps.html" as maps with context %}` on the contest page.
  Pages link their SBE source files, as turnout pages do; vote-type columns use the SBE's own
  labels for that election (from the registry) with a one-line note on "Mail-In Ballot 2";
  winners carry a ✓ and a visually hidden "winner" label, always the SBE's `Y`, never computed;
  write-ins labeled.
- `base.html` nav: `<li><a href="{{ base }}/results/">Results</a></li>` after Turnout.
- `static_site.page_urls()`: for each election with rows, add `/results/`, the hub, the county
  index, every county page and every contest page, from the same helper the hub uses.
  `check_links` then covers the section.
- Data page: add `"results"` to `DOWNLOAD_ROOTS` in `web/downloads.py` and a results listing in
  `routes/data.py`, one section per election. No `may_publish` gate: all are certified; GG26 will
  be gated by `published` when it is live and unofficial (note in the registry).

### 5. CLI, Makefile, docs

- `cli.py`: `results-fetch [--election CODE] [--force]`, `results-parse [--election CODE]
  [--force]`, same shape as the turnout commands. `Makefile`: `results:` running both; `.PHONY`.
- `README.md`: a row in "What's inside" (elections covered, 2020–2026), a **Results** bullet
  under "The site", `make results`, the `data/results/<CODE>/county/…` layout line, and the
  project-layout note; one sentence that 2018 and earlier are not on the SBE's data pages as
  county CSVs.
- `docs/plans/2026-10-15-results-county-design.md` and `…-results-county.md` (repo convention),
  committed with the work.

### 6. Tests (`tests/`)

- `test_results.py`: URL and source-id generation for every election, county and party (coded
  and 2020 names); each layout reader on small fixture CSVs (BOM, trailing header space, blank
  districts, several winners, `Against` and `Write-In?` columns, 2020's column order and unquoted
  primary); row-total check fails on a bad row; statewide cross-check fails on a mismatch and
  passes on the real shape, FOR rows only for a general; missing `non` skipped, missing `dem` /
  `all` fails; HTML body treated as not published; `published=False` elections fetch nothing.
- `test_results_db.py`: schema tables; loader rejects a CSV without a sidecar or with a
  county / election mismatch; loads a primary and a general fixture and melts vote types.
- `test_results_web.py`: index lists elections by year; a primary hub groups contests per party
  with totals summed across counties; a general hub spans parties and shows the margin map; a
  contest page's county table and map; a county page shows D / R / NP sections for a primary and
  one list for a general; `Against` shown only where present; 404s; `page_urls()` includes every
  contest and county page per election and none when the tables are empty.
- `test_maps.py`: `results_map` for both kinds, hatched counties outside the contest.

## Implementation order

1. Registry, `ResultsFile`, fetch, the `coded` reader and the checks, with tests → run
   `uv run skipjack results-fetch` and `results-parse` for GP26, PG24, PP24, GG22, GP22; commit
   `archives/sbe/results/`, manifest rows and `data/results/`.
2. The `named2020` reader → fetch and parse PG20 and PP20; commit.
3. Schema + loader + tests → `make build-db`.
4. Web queries, map builder, routes, templates, nav → `make serve`, view the pages.
5. `static_site` URLs, data downloads, README, docs/plans → `make site SITE_BASE=/skipjack`.
6. `make test`; commit on `main` in the repo's lowercase style ("results: county-level results
   for the 2020–2026 primaries and generals from the SBE's CSVs"), per the user's workflow prefs.

## Verification

- `results-fetch` archives each election's county and check files with manifest rows; a second
  run downloads nothing; GG26 logs "not published".
- `results-parse` writes one CSV + sidecar per county file and reports the checks; GP26 Governor
  (Moore 558,768 / Felber 74,312) matches the breakdown file.
- `make build-db && make test` pass (`pytest -m "not slow"`, as `pages.yml` runs it).
- `make serve`, then in the browser preview: `/results/` lists 2026, 2024, 2022 and 2020;
  `/results/pg24/` shows President with the D − R margin map and eight congressional contests;
  `/results/gp26/` shows the party-split primary contests; Montgomery's GP26 county page has D, R
  and NP sections and Caroline's has D and R; a 2020 county page shows that year's vote-type
  labels; the Data page lists and serves the results files.
- `make site SITE_BASE=/skipjack` builds with no broken links.
