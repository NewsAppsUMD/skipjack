# The Turnout data: what Skipjack built, and how to read it

*Written 2026-10-10, after the first two turnout increments. Everything here traces to an
archived file with a SHA-256 fingerprint and a provenance sidecar — see the
[design docs](#the-papers) for the term definitions and decisions.*

## In one page

Skipjack added a **Turnout** section that tracks Maryland election campaigns the way the
State Board of Elections (SBE) publishes them. Each press-room campaign publishes a set of
statistical reports — the daily mail-in files while the campaign runs, then the certified
canvass reports after the election. Skipjack now ingests **every report category except the
election-night unofficial turnout report**, and:

- snapshots each file the day it changes, keyed by the "as of" date printed in the document
  itself, so a campaign's climb is visible day by day;
- reconciles every table against its own printed totals before publishing anything (a table
  that does not add up fails the run instead of publishing a wrong number);
- publishes county tables, trend charts, rejection-reason breakdowns and the certified
  turnout percentages (computed, per the SBE's own guide, from counts — never stored from a
  printed percentage column);
- keeps every figure traceable: archive file + manifest row + provenance sidecar per
  document, and a `source_id` on every combined row.

Two elections are live today:

| Election | State | What the page shows |
|---|---|---|
| **2026 primary** (`/turnout/gp26/`) | Certified July 23, 2026 | The finals: ballots cast (902,818) and counted (891,567), the four voting modes with shares, mail-in ballots with rejection reasons, provisional outcomes, early-voting and same-day totals |
| **2026 general** (`/turnout/gg26/`) | In progress (election day November 3) | The mid-campaign mail-in tracking: ballot requests, ballots sent, envelopes received back, and accepted-in-the-canvass (currently ~0) |

## Where the data comes from

The SBE's [press room](https://elections.maryland.gov/press_room/index.html) organizes each
election's reports under an election code (GP26, GG26) at URLs like
`press_room/2026_stats/GP26/`, and the page pre-lists files that do not exist yet — a
missing file answers HTTP 200 with an HTML template page, so "published" is decided by the
file's first bytes (`%PDF` or `PK`), never by the status code.

State Board's [turnout guide](https://elections.maryland.gov/press_room/Navigating_Official_Turnout_Statistics.pdf)
defines the vocabulary; two of its points are easy to trip on:

1. **Turnout counts ballots cast, not counted.** The numerator is everyone with a record of
   casting a ballot, whether or not the ballot later counted — rejected provisional and
   mail-in ballots included. The counted ballots are a different report (the canvass).
2. **The denominator is eligible voters, not active registration.** Eligible = registered
   voters not in "incomplete" or "cancelled" status — active, inactive **and** pending
   together. Skipjack's voter-file pages say "participation among current voters" for the
   same reason; the Turnout section is the official side of that distinction.

## What the pipeline does

```
press_room/2026_stats/<code>/…  ──turnout-fetch──▶  archives/sbe/campaign_stats/<code>/
                                                       <family>__<geography>__<as-of>__<sha>.<ext>
       sha-dedupe: a day whose bytes match the newest archived revision adds nothing
                                                │
                                        turnout-parse
       xlsx read by calamine; header-driven (labels from the header, never position)
       reconciliation: row sums, printed block totals, category totals, grand totals
                                                ▼
                              data/turnout/<code>/<family>/<geography>/<as-of>.csv
                              + <as-of>_provenance.json sidecar per snapshot
                                                │
                                             build-db
                                                ▼
                     skipjack.db: turnout_sources (one row per archived revision)
                                  turnout_counts (one row per count, every row traced)
```

Rules the parsers work under (all established in the first increment and carried over):

- **Reconciliation stays within one document** — never the PDF against its xlsx twin, one
  geography against another, or county grain against district grain. (The SBE's own files
  legitimately disagree between grains; the county workbook's county table reconciles to
  605,052 ballots sent while the same file's councilmanic detail reconciles internally to
  524,248 — different tables of different populations, documented as such.)
- **Percentages are computed, not stored.** The printed TURNOUT/share columns are dropped at
  parse; the site computes them from the counts within the printed tolerances (checked where
  the board prints shares a parser can reproduce).
- **A county is one of Maryland's 24 or nothing.** A row whose county is not one of the
  canonical spellings fails the parse — a misread cell is not a 25th county.
- **Duplicates fail.** The same table pasted twice (two pages, or a sheet repeat) would
  reconcile per-table and publish double counts; the parsers reject repeated identities.
- **Suppressed ≠ zero.** In the eligible-voters workbooks a blank party cell is a suppressed
  low count and publishes nothing; the row checks run over the printed cells.

## The report families

| family | files (GP26) | measures |
|---|---|---|
| `requests` | xlsx + pdf twin (county) | the delivery methods (agent, faxed, in-person, mailed, others, print-at-home/web-delivered) + total |
| `sent-returned` | xlsx x county/CD/LD | sent / received / accepted x DEM/REP/OTH/ALL |
| `left-to-count` | xlsx x 3 geo | the three tallied measures + `left-to-count` (the canvass tracker) |
| `official-turnout` | xlsx x 3 geo (certified) | election-day / early-voting / vote-by-mail / provisional / eligible, per party (Statewide block = `ALL`) |
| `county-canvass` | pdf (certified) | the four modes + total turnout per county |
| `votes-accepted` | pdf (certified) | the four modes + total, votes counted |
| `mail-voting` | pdf (certified) | sent / received / accepted / rejected per county, + the rejection reasons (statewide) |
| `provisional` | pdf (certified) | cast / accepted-in-full / accepted-in-part / rejected per county, + reasons |
| `eligible-active` | xlsx x 4 geo (certified) | `active` per county/party |
| `eligible-inactive` | xlsx (certified) | `inactive` per county/party |
| `early-voting` | pdf x {county, county-party, CD, LD, councilmanic, location} | `day-1`…`day-8` + `total` + `eligible` |
| `same-day` | pdf x the same grains | new-registrations and address-changes per day + the election-day/election-totals arithmetic |

The county workbooks' district detail grain (councilmanic districts on Sheet2, the
legislative file's county-expansion tail) is reconciled against its printed totals and
**not published** — one grain per file, so nothing sums twice; the district files publish
the district grain.

## The site

- `/turnout/` — the elections index (one row per election with data).
- `/turnout/gg26/` — the campaign-tracking layout: as-of-labelled headline tiles with the
  change since the previous snapshot, a day-by-day trend chart (requests, sent, received,
  accepted on one axis), and a sortable county table.
- `/turnout/gp26/` — the certified layout: turnout percentage (when the denominator files
  are up), ballots cast and counted, the four modes with shares, the by-county table (cast,
  counted, each mode, mail-in accepted/rejected, early-voting, same-day), and the
  rejection-reason tables for both ballot kinds.
- Each snapshot gets a source strip: the original document's link, when it was downloaded,
  how it was read, and its fingerprint; the CSV and the provenance record are downloadable
  by link and listed on the Data page; every archived source is on the About page.

## Automation

`.github/workflows/turnout-daily.yml` snapshots once a day in the early afternoon Eastern
(after the SBE's morning file refresh), for as long as anything is in flight: the running
general's mail-in files until the canvass ends, GP26's certified-denominator workbooks when
the board re-publishes them (see the sidebar below), and the general's certified families
when they appear after certification. The sha-dedupe keeps an unchanged day cheap; a
non-reconciling file makes the run red rather than publishing a wrong number; a `git push`
is what deploys the site (the Pages workflow runs on it). `make turnout` runs the same.

## Known data quirks (the SBE's own numbers, documented honestly)

- **Same-day by-party undercounts the county file it parallels** (2,162 vs 2,179 new
  registrations; Baltimore County −6, Charles −3, Howard −3, Anne Arundel −2, +1 elsewhere),
  with no state row in that workbook to check against; the snapshot sidecar notes it.
- **The eligible workbooks' grand-total party columns disagree with their own rows** (the
  live inactive file's DEM cell) — that printed layer is not reconciled; the rows and the
  Total columns are.
- **The GG26 requests workbook is zip-corrupt at the source** (bad zipfile offsets, stable
  across downloads). The requests report parses from its PDF twin.
- **The two certified canvass sheets disagree with each other by design**: 902,818 ballots
  cast versus 891,567 votes counted (the difference is the rejected ballots).
- **The denominator files briefly vanished**: the eight certified-denominator files
  (official-turnout × 3, eligible × 5) served real data all day on 2026-10-10 and went back
  to placeholder pages when the board re-published its tree that evening. The parsers are
  shipped and tested; they ingest automatically on the next fetch.

## Verification

- **451 pytest tests pass**, heavily weighted toward the negative cases (torn arithmetic,
  renamed columns, duplicated identities, unrecognized labels, corrupt workbooks, and the
  shared loader's guards).
- The site builds **266 pages** with a clean internal-link check, in both a root build and
  a `/skipjack`-prefixed one (what the Pages deploy does).
- `turnout-fetch` re-runs are verified no-ops against the live files (same bytes → nothing).
- Every number can be traced: database row → source_id → manifest row (URL, date, SHA-256)
  → archived original.

## The papers

- `docs/turnout-data-summary.md` — this one.
- `docs/plans/2026-10-10-turnout-design.md` — the first increment's design (the campaign
  files, the snapshot mechanics, the terms).
- `docs/plans/2026-10-10-turnout.md` — its implementation plan.
- `docs/plans/2026-10-10-turnout-gp26-design.md` — the certified families' design (+ the
  first ingest's observations).
- `docs/plans/2026-10-10-turnout-gp26.md` — its implementation plan.

## Not yet on the site (deliberate)

- The **2022/2024 elections** (the same families are published for GP22/GG22/PP24/PG24 and
  the 2024/2025 special elections, including 2026-revised copies for GG22/PG24) — the
  parsers generalize; the backfill is a later increment.
- **Permanent mail-in voters by party and county** (the PMIBs monthly report) and the
  **by-location subcounty pages** — data layers the section can grow into.
- **Unofficial election-night turnout** — excluded by decision.