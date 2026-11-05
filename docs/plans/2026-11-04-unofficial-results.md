# Unofficial GG26 results implementation plan

Design: `2026-11-04-unofficial-results-design.md`. Read it first: it covers the source pages,
the parsing traps (`NR`, the 0 printed for questions, the single Mail-In column), the checks, the
publish rule and the decisions made with the user. Work on a new branch `unofficial-results` off
`main`, TDD, one commit per task in the repo's lowercase sentence style. Time matters: the count
is running now, so Tasks 1–4 should go up before the canvasses finish.

The board's pages saved on 2026-11-04 (refreshed 09:21:22 AM) are in this session's scratchpad.
Task 1 fetches fresh copies for the fixtures rather than depending on that directory.

## Task 1: page readers (pure functions, no network)

- New `src/skipjack/pipeline/results_unofficial.py`, reader half only:
  - `snapshot_id("11/04/2026 09:21:22 AM") -> "2026-11-04T092122"` (24-hour time);
    `last_refreshed(html) -> str` (raises when missing).
  - `read_county_page(html) -> CountyPage` (county name from the `<h1>`, normalised to
    `COUNTY_CODES` names: "Allegany County" → "Allegany", "Baltimore City" stays as it is,
    "Saint Mary's" → "St. Mary's"). It yields one row per candidate/choice:
    `kind ("contest"|"question"), office, district, office_detail, vote_for, candidate, party,
    write_in, precincts_reported, precincts_total, early, election_day, mail_in, provisional
    (int | None for NR), total`. A contest is an `<h2>` plus `<h3>` "Vote for N" plus the
    precincts line plus a table. A question is an `<hgroup>` `<h2>` with `<p>` detail lines,
    `<th><span>` labels and no Party column. District is split off the `<h2>` text by one regex,
    the same idea as the CSVs' Office District column. "Other Write-Ins" sets `write_in=1`.
  - `read_statewide_page(html)` and `read_questions_page(html)` return the statewide rows used
    by the cross-check.
  - `read_canvass_status(home_html) -> list[CountyStatus]`: the table found by its
    "# Precincts Reporting" header. Each canvass cell maps to `complete` / `partial` /
    `not_reported` from the image `alt` ("Canvass Complete", "Canvass Partially Complete", none).
  - `apply_not_reported(page, statuses)`: a printed 0 becomes None when no canvass feeding
    that vote type has reported in the county. The mapping is early→EV, election_day→EN,
    mail_in→MIB 1 *and* MIB 2 (both must be unreported), provisional→Prov. `NR` is already
    None.
  - Done 2026-11-04: `read_results_page` handles county and statewide pages alike, with three
    table kinds (`contest`, `retention` for judges' continuance, `question`). Over all 34 pages
    of the 09:21:22 snapshot every row's figures add up to its printed total, and the county sums
    match every statewide page. The one exception is the circuit-court page (`gen_results_2026_7`),
    which splits each circuit by county under `<h4>` and must be cross-checked per county in
    Task 2.
  - `KNOWN_OFFICES`: the 56 headings seen on 2026-11-04, question headings matched by pattern
    (`Question \d+: …`, `Question [A-Z]: …`). An unknown heading raises.
- Fixtures in `tests/fixtures/results_unofficial/`: real pages fetched fresh, namely Allegany
  (county 1: questions printing 0 provisional), Caroline (6: mail-in `NR`), Montgomery (16: the
  largest), statewide Governor (`gen_results_2026_1.html`), the statewide questions page, and the
  home page. They are public and small (15–100 KB each), so real pages beat made-up ones here.
- `tests/test_results_unofficial.py`: the snapshot id; Allegany Governor rows (Moore 5,658 total,
  Provisional None); Caroline mail-in None; Allegany Question 1 provisional 0 → None after
  `apply_not_reported`; question labels and detail parsed; an unknown office raises; the canvass
  table has 24 counties with Baltimore City's EN `partial`.

## Task 2: registry, fetch, parse, checks, CLI; first real snapshot

- In the same module: `UnofficialElection(code, base_url, county_pages=24,
  statewide_pages=9, questions_page="gen_qresults_2026_1_00_ALL.html",
  status_url="https://elections.maryland.gov/")`, `UNOFFICIAL_RESULTS = {"GG26": …}` pointing at
  `RESULTS_ELECTIONS["GG26"]`. Add `is_live(code) = code in UNOFFICIAL_RESULTS and not
  RESULTS_ELECTIONS[code].published`. This is the single publish rule from the design's §3.
- `unofficial_fetch(election="GG26", force=False)`: does nothing and logs once when not
  `is_live`. Otherwise it downloads the 34 results pages and the home page into memory (httpx,
  same client settings as `fetch.fetch_file`, without `expect_csv`/`expect_pdf`) and requires
  one shared `Last refreshed`. If they differ, it refetches once, then raises. It skips when every
  page's sha256 equals the newest archived snapshot's. Otherwise it writes atomically to
  `archives/sbe/results-unofficial/GG26/<snapshot>/<page>` (the home page as `status.html`) and
  appends one manifest row per page with source id
  `sbe-results-unofficial-GG26-<snapshot>-<page stem>`.
- `unofficial_parse(election="GG26", snapshot=None, force=False)`: snapshots not yet parsed (or
  the named one). For each, it reads everything, applies `apply_not_reported`, and runs the checks
  (cross-checking the circuit-court page per county):
  row totals, `Totals` rows, county sums against the statewide contest pages and the statewide
  questions page, and known offices. Then it writes `data/results_unofficial/GG26/<snapshot>/county/<NN>-<slug>.csv`
  plus `_provenance.json` (`ProvenanceSidecar.from_source`, family `results-unofficial`, with
  `checks` and `last_refreshed`) and `canvass-status.csv` plus its sidecar. It is all-or-nothing
  per snapshot, written to a temp directory and renamed into place.
- `cli.py`: `unofficial-fetch` and `unofficial-parse` (`--election`, `--force`; parse also takes
  `--snapshot`), the same shape as `results-fetch`. `Makefile`: a `results-unofficial` target and
  a `.PHONY` entry.
- Tests: the fetch with a mocked transport covers mixed refresh times (raises), unchanged bytes
  (skips) and `published=True` (does nothing). The parse over the Task 1 fixtures covers a
  Governor cross-check failure when one county page is altered, and nothing being written on
  failure.
- Run it for real, check the numbers against the board's Governor page by eye, and commit the
  archives, manifest and data.

- Done 2026-11-04: snapshot 2026-11-04T160222 (refreshed 04:02:22 PM) is archived and parsed.
  Every check passed: 2,703 rows and 145 statewide tables (the circuit-court page per county).
  Decisions made while building:
  - A snapshot is skipped when its name (the board's refreshed time) is already archived. The
    board's time stays put while nothing changes (09:21:22 AM was still current at 11:40 AM), so
    the name is enough to know nothing is new, and no per-page hashes are compared.
  - The home page's own "Last updated" time is logged when it differs from the results pages,
    not treated as fatal. A cached home page should not block a snapshot.
  - The statewide Supreme Court page puts the judge's "Vote Yes or No" heading in an `<h4>`
    under the district, where the county pages use an `<h3>`. The reader accepts it at either
    level.
  - The statewide pages are read only for the cross-check. Nothing from them is written.

## Task 3: schema and loader

- `models.py`: `results_unofficial` (`id, source_id REFERENCES sources, election, snapshot,
  last_refreshed, county, kind, office, district, office_detail, vote_for, candidate, party,
  write_in, precincts_reported, precincts_total, total_votes`), `results_unofficial_votes`
  (`result_id, vote_type, votes` nullable), and `results_unofficial_status` (`election, snapshot,
  county, last_updated, precincts_reporting, ev, en, mib1, prov, mib2`). Index on
  `(election, snapshot, office, district)` and `(election, snapshot, county)`.
- `build_db.load_results_unofficial` loads every snapshot under `data/results_unofficial/`. It
  requires sidecars (`ResultsLoadError`), checks the CSV's election and snapshot against the
  sidecar and path, and is called from `build()` beside `load_results`.
- `tests/test_results_unofficial_db.py`: a fixture snapshot loads, NR rows have NULL votes, and
  two snapshots coexist.

## Task 4: web, contests

- New `web/results_unofficial.py` returns the same dict shapes as `web/results.py`'s `hub`,
  `contest`, `county_list`, `county_page`, `contest_slugs`, `county_slugs`, so the existing
  templates render it. It always reads the newest snapshot. It adds `as_of` (the board's
  refreshed time, formatted), `canvass` (the status rows) and `unofficial=True`. Vote types are
  early / election day / mail-in / provisional with the board's labels. There are no winner flags.
  Contest grouping follows `_scope` rules, using the unofficial office names. A small
  `OFFICE_GROUPS` map in this module ties them to statewide / congress / senate / delegates /
  local.
- `web/results.py`: `elections(conn)` also returns live unofficial elections (with
  `unofficial: True`), so `/results/` lists GG26 first, tagged "Unofficial, count in progress".
- `routes/results.py`: `_code` already resolves through `elections()`. Each route picks the
  `qu` module when `is_live(code)`, otherwise `qr`. The source strip links the board's results
  pages for that snapshot.
- Templates: a new `_unofficial.html` holds the banner (as-of time, what is not counted) and the
  24 × 5 canvass table, included at the top of the hub, contest, county index and county pages
  when `unofficial`. In `_results.html`, a None vote cell renders as
  `<span class="muted">not yet counted</span>`, and totals and shares get "so far". The map
  caption on the contest page carries the as-of time.
- `tests/test_results_unofficial_web.py`: on the hub, the banner and as-of time render and there
  is no ✓. Caroline's county page shows "not yet counted" under Mail-In. The Governor contest page
  shows statewide 1,163,909 for Moore (fixture snapshot) and a map. PG24 pages are unchanged
  (no banner, no unofficial rows).

- Done 2026-11-04. Decisions made while building:
  - `results.elections()` stays certified-only, because the About page counts certified files
    from it and the real-data tests run the certified queries over it. A new
    `results_unofficial.site_elections()` lists the certified elections plus a live one. The
    routes and the static site read that, and `contest_slugs_for` / `county_slugs_for` pick the
    right module. The static-site part of Task 6 is therefore already done.
  - The hub's district lists show "Ahead so far, with votes": the top `vote_for` candidates by
    votes counted, never a winner mark. Retention contests show none.
  - Party names are mapped to the certified files' codes (Democratic → DEM and so on), so the
    D − R margin map works unchanged.
  - The as-of time reads "4:02 p.m. on Nov. 4, 2026", so a sentence never ends on "p.m.".
  - **Contest-page URLs will change at certification.** The election, county-index and county
    URLs stay the same, but the unofficial pages use the board's office names
    (`representative-in-congress-district-1-all`) and the certified CSVs use their own
    (`u-s-congress-1-all` in earlier years). Links to a GG26 contest page will 404 once the
    certified results replace these pages, unless redirects are added then.
  - Checked in the browser: the hub, contest page with map, county page and canvass table.
    At phone width the page does not scroll sideways. The site has no dark theme.

## Task 5: web, ballot questions

- `web/results_unofficial.py`: a `questions` group on the hub (Questions 1–3, statewide
  For/Against and shares). `question(conn, code, slug)` returns the statewide choices, a county
  table (For, Against, For share, a "not yet counted" note where provisional/mail-in is None) and
  the board's question-text link (`gen_qtext_2026_C_<n>.html`). The county page lists the
  county's questions, lettered ones included, after its contests.
- Slugs are `question-1` … `question-3`. `contest_slugs` includes them so the static site picks
  them up. The `/{slug}/{contest_slug}/` route dispatches to `question()` for `question-…` slugs.
- `results_question.html` and `maps.results_map` get a question case: For share by county,
  `sequential()`, `fmt_pct(1)`.
- Tests: the Question 1 page shows For 1,303,235 statewide, links to the question text and has a
  map. Allegany's county page lists Questions 1–3 with provisional "not yet counted".

- Done 2026-11-04. Decisions made while building:
  - The map is each county's For minus Against in points, on a scale centred on zero, rather
    than the For share. A plain share scale hides the 50% line, which is what decides whether
    a question passes or fails in a county.
  - Lettered questions belong to one county each ("Question A" is a different question in each
    of eight counties), so they appear only on their county's page, tagged "county question".
    Numbered questions count as statewide only when they are on all 24 counties' ballots.
  - The county table on a question page names the vote types each county has not reported
    yet (Caroline: mail-in and provisional), using the zeros that the parse turned into
    not-yet-counted.
  - A question links to the board's ballot text (`gen_qtext_2026_C_<n>.html`) and does not
    copy it.

## Task 6: publish gate, downloads, site, docs

- `web/downloads.py`: add `results_unofficial` to `DOWNLOAD_ROOTS`. A file is offered only when
  `is_live(code)` and it sits in that election's newest snapshot directory. `routes/data.py`
  gets `/downloads/results_unofficial/{relative:path}` with the same gate and a "GG26 unofficial
  (as of …)" block in the listing.
- `static_site.page_urls()` needs no new code if Task 4 routed `elections()` and the slug helpers
  correctly. Add a test that the GG26 URLs appear when live and are gone when the registry says
  `published=True`. That test should also cover pages, downloads and data listing all dropping
  the unofficial track, with archives untouched.
- README: one line under Results describing the unofficial track, `make results-unofficial`, and
  when it disappears.

- Done 2026-11-05. `downloads.unofficial_snapshot` names a live election's newest parsed
  snapshot directory (from the files, not the database, so the static site's copy and the live
  route agree); `unofficial_offered` gates both `download_files` and the route. The Data page
  lists the canvass status first, then the 24 county files, under "GG26 unofficial (as of …)",
  with a note that a blank vote cell means not reported. Older snapshots stay in `data/` and the
  archives but are never offered. The static-site test runs against the built `skipjack.db`.

## Task 7: cadence

- `.github/workflows/turnout-daily.yml`: after the turnout step, add
  `uv run skipjack unofficial-fetch && uv run skipjack unofficial-parse` (both do nothing once
  GG26 is published). Add `data/results_unofficial archives/sbe/results-unofficial` to the
  change check and to `git add`, and make the commit message
  `turnout and results snapshot $(date -u +%F)`.
- Before the provisional and second mail-in canvass days, run `make results-unofficial` by hand
  and push. Unchanged snapshots are skipped, so extra runs cost nothing.

- Done 2026-11-05. The unofficial fetch and parse are their own step with
  `continue-on-error`, so a failed snapshot (a refresh landing mid-fetch twice, or a page that
  no longer adds up) does not hold back the day's turnout snapshot; a last step turns the run
  red when it failed. The schedule stays once a day at 1:37 p.m. Eastern.

## Task 8: verification

- `make results-unofficial`, `make build-db`, `pytest -m "not slow"`,
  `make site SITE_BASE=/skipjack`. Then a browser check of `/results/`, `/results/gg26/`, the
  Governor and Question 1 pages, and the Caroline and Allegany county pages, in light and dark
  and at phone width. Flip `published=True` locally and check that the GG26 pages and downloads
  disappear, then revert.

- Done 2026-11-05. The live run found a canvass mark the reader did not know: from the
  9:29 a.m. refresh, Anne Arundel's early-voting and election-night cells carry a check with a
  superscript w, alt text "Results have been reported and write-in votes ate assigned." (the
  page's key says "are assigned"). The parse failed loudly, as designed; the mark is now read as
  complete in either spelling, and any other new mark still fails. The key also lists a
  "Local" mark (only local offices posted) not yet seen on the page; it would fail the parse the
  same way. Snapshot 2026-11-05T092946 then parsed with every check passing (2,199 rows; MIB 1
  still 18 counties partial, Prov and MIB 2 none). 842 tests pass; the static site built with
  `/skipjack` (1,257 pages, links checked) and copies only the newest snapshot's files. In the
  browser, `/results/`, the hub, Governor, Question 1, the county index and the Caroline,
  Allegany and Anne Arundel pages all read the 9:29 a.m. count with no winner marks, and none
  scrolls sideways at 375px. The site has no dark theme, so there was none to check. With
  `published=True` set locally, the pages, both download paths, the Data-page block, the
  results index tag and the static site's GG26 URLs all dropped, the make target did nothing,
  and the archives and parsed files stayed; the flag was then reverted.

## Follow-up (not in this plan)

The certified pipeline should read `GG26_…QuestionResults.csv` before GG26 is certified, so
the question pages survive the switch to certified results. If that hasn't landed by then,
questions drop off with the unofficial track, as the design says.
