<p align="center">
  <img src="src/skipjack/web/static/img/skipjack.webp" alt="Line drawing of a Chesapeake Bay skipjack sailing on blue waves, inside a circle" width="220">
</p>

<h1 align="center">Skipjack</h1>

<p align="center"><em>Maryland elections data, traced to the source.</em></p>

Skipjack turns Maryland election data that sits in scattered PDFs and spreadsheets into clean
datasets and a small website. Every number can be followed back to the document it came from.
It is named for the Chesapeake Bay skipjack, Maryland's state boat.

Once GitHub Pages is switched on, the site publishes to
<https://newsappsumd.github.io/skipjack/>.

## What's inside

| Dataset | Coverage | Source |
|---|---|---|
| **Voter registration by county and party** | Jan 2010 to Aug 2026, 200 monthly reports | State Board of Elections (SBE) *Voter Registration Activity Reports*, PDF |
| **Registration activity** (address, name and party changes, inactive voters) | Same reports | Same |
| **New registrations and removals**, by method and by reason | Same reports, statewide | Same |
| **Voter file aggregates** (participation by party, age and district; voting habits; first-time registrants by age at registration) | Snapshots of 2024-09-11 and 2026-08-12 | SBE statewide voter list, aggregates only |

### The site

- **Home**: a few plain sentences about the latest report, written from the data, and a statewide chart.
- **Voter Registration**: a table for every month with the change from the month and year before, and
  the original PDF, parse details and any known errors in the State Board's report at the top. County
  trend pages have party lines and 12-month rolling totals of new registrations and removals.
- **How people register**, **Removals** and **Party switching**: statewide trends in new registrations
  by method, removals by reason and party changes around each primary.
- **Errata**: every place the State Board's own report does not add up.
- **Voter File**: participation among current voters, by party, age, county and district. Each county has
  a link between its registration trends and its voter file page.
- **Young voters**: how many 18-to-22-year-olds registered for the first time from January 1 to the
  snapshot date, against the same stretch of earlier election years, which party they choose, and how it
  differs by county. Earlier years are undercounted because voters leave the list, so the page measures
  that between the two snapshots and shows the comparison both ways.
- **Voter file comparison**: how the totals moved between two snapshots (September 2024 and August 2026),
  by party, county and age, set beside the State Board's monthly reports, plus how many voters from each
  registration year are still on the list. It compares counts only and says so.
- **Data**: every CSV and JSON file as a download, each with its provenance file, plus all months of each
  table combined into one file with a `source_id` on every row.
- **About**: every source document, with a link to the original.

> **Participation is not turnout.** The voter file lists the people registered today. Voters who
> have since moved, died or been removed are missing, so past elections are undercounted: the
> file holds about 87% of the 2022 electorate and 99% of the 2026 one. The site labels these
> figures "participation among current voters" and shows the gap next to them. For official
> turnout, use the SBE's published results.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv installs it if needed). The
repository holds everything the site is built from, so there is nothing to download.

```bash
git clone git@github.com:NewsAppsUMD/skipjack.git
cd skipjack
uv sync
make build-db    # builds skipjack.db from the CSV and JSON files in data/
make serve       # http://127.0.0.1:8000
```

## Commands

| Command | What it does |
|---|---|
| `make fetch` | Download any new monthly report PDFs from the SBE into `archives/` |
| `make parse` | Parse the PDFs into CSVs under `data/voter_registration/`, checking each one |
| `make voterfile VOTERFILE_DIR="/path/to/voter files"` | Aggregate a statewide voter file into `data/voter_file/` |
| `make build-db` | Build `skipjack.db` from everything in `data/` |
| `make serve` | Run the site with auto-reload, with the Datasette explorer at `/explorer` |
| `make site SITE_BASE=/skipjack` | Render the whole site as static files into `site/` |
| `make test` | Run the tests |

`make all` runs fetch, parse and build-db. Each make target wraps `uv run skipjack <command>`;
run `uv run skipjack --help` for the options.

## How it works

```
SBE website ──fetch──▶ archives/          original PDFs; SHA-256 of each in manifest.csv
                           │
                         parse             tables read with natural-pdf (OCR where a PDF's
                           │               fonts are scrambled), then checked against the
                           ▼               report's own totals
                         data/             a CSV plus a *_provenance.json for every file
                           │
                       build-db
                           ▼
                      skipjack.db ──▶ FastAPI site + Datasette explorer
                           └───────▶ build-site ──▶ site/ ──▶ GitHub Pages

voter file (kept outside the repo) ──voterfile──▶ data/voter_file/<snapshot>/*.json
```

### Accuracy

Nothing is written until it adds up. Each report is reconciled against its own printed totals:
party columns must sum to each county's total, and counties must sum to the statewide row. A
month that does not reconcile fails instead of publishing a wrong number.

Sixteen reports contain arithmetic errors in the State Board's own tables, mostly a row total of
0 beside non-zero party counts. Each was checked against the PDF and is recorded with a note in
[`data/voter_registration/source_discrepancies.csv`](data/voter_registration/source_discrepancies.csv).
Any new mismatch fails the parse until someone checks the PDF and adds a row. The site lists them on its errata page.

### Provenance

Every data file has a sidecar, for example `2026-08_provenance.json`, recording the source URL,
when it was fetched, the SHA-256 of the original, when it was parsed, the parser version, whether
the text was read directly or by OCR, and the checks that passed.

## Data files

```
data/voter_registration/monthly/YYYY-MM.csv    report_date, county, party, active_voters, party_name
data/voter_registration/activity/YYYY-MM.csv   report_date, county, measure, party, value
data/voter_registration/summary/YYYY-MM.csv    report_date, section, category, party, value
data/voter_file/<snapshot>/*.json              suppressed aggregates, plus snapshot.json
```

Combined files for the whole history (`registration_monthly_all.csv`, `registration_activity_all.csv`
and `registration_summary_all.csv`) are built from the database when the site is built and are not
stored in `data/`.

Some reports leave a category's row out in months when it has nothing to report (same-day
registration, for instance). The trend pages count a missing row as zero from the month the category
first appears, and a value printed as NA as unknown.

Party codes change over time (Americans Elect and Bread and Roses appear for a few years), so
columns are read from each report's header rather than from a fixed list.

## The voter file

The statewide voter list is sold by the SBE to applicants who agree to use it only for purposes
related to the electoral process. It is row-level personal data, so it is **never stored in this
repository**. `skipjack voterfile` reads it from a folder outside the project and writes only
aggregates:

```bash
make voterfile VOTERFILE_DIR="/path/to/voter files"
```

An older export may be a single file with no readme. Point the command at the file and give its date:

```bash
uv run skipjack voterfile --path /path/to/export.txt --snapshot-date 2024-09-11
```

With no readme there is no record count to check, so only the malformed-row limit applies, and the
snapshot's notes say so. Election columns dated after the snapshot are ignored, because an export made
before an election has an empty column for it.

- Counts of 1 to 9 voters are hidden, and no output contains a name, ID or birth date.
- The command refuses to write unless the row count matches the file's readme, allowing for a few
  malformed lines.
- A test runs before every deploy to confirm the committed aggregates follow these rules.

## Publishing

`.github/workflows/pages.yml` publishes the site on every push to `main`. It installs from the
lock file, builds the database, runs the tests, builds the site, and deploys only if all of that
passes. One-time setup: in the repository's **Settings > Pages**, set the source to
**GitHub Actions**.

Pages sites are public, even for a private repository.

## Project layout

```
src/skipjack/
  cli.py              the `skipjack` command
  models.py           SQLite schema
  pipeline/           fetch, vrar (PDF parsing), voterfile, provenance, build_db
  web/                FastAPI app, routes, templates, static files, static_site.py
data/                 the committed CSV and JSON, with provenance files
archives/             original PDFs and the download manifest
tests/                pytest suite; the voter file tests use made-up data
.github/workflows/    GitHub Pages deploy
```

## License

Code is released under the [MIT License](LICENSE). The data files are licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): please credit the Maryland State
Board of Elections as the source.
