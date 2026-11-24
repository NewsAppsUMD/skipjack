# `skipjack get`: a data-pulling CLI

**Date:** 2026-11-23

## What

Read-only `get` and `list` subcommands on the existing `skipjack` command. They pull voter
registration, results, turnout and a county's whole history, from the published skipjack site
by default or live from the State Board for results and turnout, and print JSON (default) or
CSV.

## Decisions

- **Packaging:** the same package, with a light install. The heavy dependencies move to
  extras so an outside user installs only what `get` needs.
- **SBE source:** live fetch-and-parse for the cheap formats only: results (county CSVs) and
  turnout (press-room XLSX, and PDF with the `pdf` extra). Registration always comes from
  skipjack: its PDFs need OCR and the hand-checked errata.
- **County history:** everything for a county in one document: registration by month, turnout
  by election and results over time.

## Commands

```
skipjack get registration [--table monthly|activity|summary] [--county NAME] [--party CODE] [--from YYYY-MM] [--to YYYY-MM]
skipjack get results --election GG22 [--county NAME] [--office TEXT] [--party-file dem|rep|non|all]
skipjack get turnout --election GG26 [--family requests] [--geography county] [--as-of latest|YYYY-MM-DD|all]
skipjack get county NAME [--section registration|turnout|results]
skipjack list [elections|counties|turnout CODE]
```

Shared by every `get`:

- `--format json|csv` (default `json`)
- `--source skipjack|sbe` (default `skipjack`)
- `-o FILE` (default stdout)
- `--base-url` or `SKIPJACK_URL`: another deployment
- `--data-dir`: read a local checkout instead of the network

## Output

- **JSON:** an envelope, `{"meta": {...}, "rows": [...]}`. `meta` carries the dataset, source,
  source URLs, as-of date, retrieval time, the filters applied and an `official` flag, so
  provenance travels with the data.
- **CSV:** rows only, with the same columns as the files in `data/`.
- **County history in CSV:** the document is nested, so `--format csv` requires `--section`
  and writes that section as flat rows. Without it the command fails and lists the sections.

## Sources

| Dataset | `--source skipjack` | `--source sbe` |
|---|---|---|
| Registration | The combined `registration_*_all.csv` files, filtered client-side | Refused, with an error pointing to the skipjack source |
| Results | `downloads/results/<CODE>/county/*.csv` | The board's county CSVs, read in memory with the existing `_read_coded` / `_read_named` readers |
| Turnout | `downloads/turnout/<CODE>/<family>/<geography>/<as-of>.csv` | The board's press-room file through the existing parsers; XLSX families work in the light install, PDF families need the `pdf` extra |
| County history | New `downloads/county/<slug>.json` | Refused: it is a skipjack-derived product |

- **No silent fallback.** If the SBE fetch or parse fails, the command exits non-zero and
  suggests `--source skipjack`.
- **GG26 while the count is live:** results come from the unofficial snapshot with
  `official: false`, under the same gating as the site.
- An unknown election, family, geography or county fails with the valid choices from the
  catalog.

## Site build changes

The CLI cannot discover files on static hosting, and county history is not published as a file
today. The site build gains two outputs:

1. `downloads/index.json`: a catalog of elections (code, name, date, published), the turnout
   families, geographies and as-of dates of each election, the registration months and the
   counties. `list` and argument validation read it.
2. `downloads/county/<slug>.json`: one document per county, in three sections:
   - registration by month and party;
   - turnout by election (certified figures, plus the newest snapshot of a running one);
   - results over time, from the existing `county_trends()`.

Both follow the existing publish gates (`may_publish`, `results_published`, `is_live`).

## Code layout

- `src/skipjack/client/`: `sources.py` (the skipjack HTTP/local source and the SBE source),
  `catalog.py`, `output.py` (the envelope and CSV writers) and `commands.py`.
- `cli.py` registers `get` and `list`; the existing commands are untouched.
- `pipeline/results.py` and `pipeline/turnout.py` get pure "bytes in, rows out" entry points,
  so the SBE source parses without writing to `archives/` or relying on repo-relative paths
  such as `RESULTS_DATA_DIR`.

## Light install

- **Core:** `httpx`, `polars`, `python-calamine`.
- **Extras:** `pdf` (natural-pdf, rapidocr, llm, llm-anthropic) and `web` (fastapi, uvicorn,
  jinja2, aiosqlite, datasette, datasette-vega, openpyxl, xlrd).
- The Makefile and the workflows switch to `uv sync --all-extras`.
- Heavy imports stay inside the commands that use them; a missing extra gives an error naming
  the install command.
- Outside users: `uvx --from git+https://github.com/NewsAppsUMD/skipjack skipjack get ...`
  works at once. A bare `uvx skipjack` needs a PyPI release; whether the name is free is not
  checked.

## Build order

1. The catalog and county JSON in the site build, with tests against the publish gates.
2. The client's skipjack source and output layer, then `list` and `get registration`.
3. `get results` and `get turnout` from the skipjack source.
4. `get county`, with its CSV sections.
5. The pipeline's pure parse functions, then the SBE source for results and turnout.
6. The dependency split, the Makefile and CI, and a README section.

Tests use `httpx.MockTransport` and fixture files, with no network; one opt-in live smoke test
covers each source.

## Left out of v1

- On-disk caching (the largest file is about 3 MB).
- Voter-file aggregates.
- Precinct results.
- The statewide wide CSVs, easy to add later as `get results --wide`.
