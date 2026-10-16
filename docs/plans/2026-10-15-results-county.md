# Results tab implementation plan

Design: `2026-10-15-results-county-design.md` (read it first; it names the source files,
layouts, checks, schema, pages and tests). Work on `main`, TDD, one commit per task in the repo's
lowercase sentence style. `site/` is gitignored.

Scratch copies of the SBE CSVs for reference are NOT in the repo; fetch fresh ones when a test
fixture needs a real header (keep fixtures tiny and made-up).

## Task 1: registry, fetch, coded-layout parser, checks
- `src/skipjack/pipeline/results.py`: `ResultsElection`, `RESULTS_ELECTIONS` (GP22, GG22, PP24,
  PG24, GP26, GG26 unpublished), `ResultsFile`, `county_files()`, `results_fetch`,
  `results_parse` with `_read_coded`, per-row total check and statewide cross-check.
- `fetch.fetch_file`: add `expect_csv`.
- `cli.py` `results-fetch` / `results-parse`; Makefile `results` target.
- `tests/test_results.py` first (URLs, source ids, reader fixtures, both checks, missing-file
  rules, HTML-as-unpublished).
- Then run fetch + parse for GP26, PG24, PP24, GP22, GG22 and commit archives, manifest, data.

## Task 2: 2020 layout reader
- `_read_named2020`, PG20 and PP20 registry entries, tests, fetch + parse, commit.

## Task 3: schema and loader
- `models.py` tables, `build_db.load_results`, `ResultsLoadError`, `tests/test_results_db.py`.

## Task 4: web
- `web/results.py` queries, `maps.results_map`, `routes/results.py`, four templates, nav link,
  `app.py` router, `tests/test_results_web.py`, `tests/test_maps.py` case.

## Task 5: site, downloads, docs
- `static_site.page_urls()`, `downloads.py` + `routes/data.py`, README, tests in
  `tests/test_static_site.py`.

## Task 6: verification
- `make build-db`, `pytest -m "not slow"`, `make site SITE_BASE=/skipjack`, browser check of the
  pages listed in the design's Verification section.
