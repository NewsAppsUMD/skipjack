# The Turnout data: what Skipjack built, and how to read it

*Written 2026-10-10, after the first two turnout increments. Everything here traces to an
archived file with a provenance sidecar — see the
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

Seven elections are live today:

| Election | State | What the page shows |
|---|---|---|
| **2026 primary** (`/turnout/gp26/`) | Certified July 23, 2026 | The finals: ballots cast (902,818) and counted (891,567), the four voting modes with shares, mail-in ballots with rejection reasons, provisional outcomes, and mail-in ballot requests |
| **2024 general** (`/turnout/pg24/`) | Certified (canvass as of 2024-12-11, re-posted April 2026) | The finals: ballots cast (3,067,931) and counted (3,062,158), the four voting modes with shares, mail-in ballots with rejection reasons, mail-in ballot requests, and the board's own turnout percentage as its official Turnout sheet printed it |
| **2024 primary** (`/turnout/pp24/`) | Certified June 7, 2024 | The finals: ballots cast (1,016,857) and counted (1,005,373), the four voting modes, mail-in ballots with rejection reasons, provisional outcomes, mail-in ballot requests, and the board's printed turnout percentage (27.6%, over active registration) |
| **2022 general** (`/turnout/gg22/`) | Certified (canvass as of 2022-12-08, corrected re-posts April 2026) | The finals: ballots cast (2,034,265) and counted (2,030,446), the four voting modes, mail-in ballots with rejection reasons, provisional outcomes, mail-in ballot requests, and the board's printed turnout percentage (49.3%, over active registration) |
| **2018 general** (`/turnout/gg18/`) | Certified (the board's election archive; the prints carry no date, filed under election day 2018-11-06) | The finals: ballots cast (2,335,128) and counted (2,327,542), the four voting modes, absentee ballots with rejection reasons, provisional outcomes, and the board's printed turnout percentage (59.1%, 2,335,128 over 3,954,027 active registration) |
| **2022 primary** (`/turnout/gp22/`) | Certified August 18, 2022 | The finals: ballots cast (1,033,903) and counted (1,022,573), the four voting modes, mail-in ballots with rejection reasons, provisional outcomes, mail-in ballot requests, and the board's printed turnout percentage (27.4%, over active registration) |
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
   same reason; the Turnout section is the official side of that distinction. What the
   board *prints* as the percentage moved between eras: the 2018-, 2022- and 2024-era official
   turnout sheets computed it over active registration alone (those elections' pages show
   that printed percentage and say so); the 2026 guide's construction is what GP26's page uses while
   the sheet has not republished.

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

The `early-voting` and `same-day` files are the board's campaign-time tallies, printed
"Unofficial" — the eight-day early-voting turnout and the same-day registrations. A
**certified election publishes none of its early-voting tallies**: its early-voting figures
of record come from the certified canvass reports (`may_publish()` in
`skipjack.pipeline.turnout` gates every published surface — the page, the downloads, and
the Data page). The same-day files have no certified counterpart, so they publish for every
election on `/turnout/<code>/same-day/`, labelled unofficial. The elections still being
tracked keep their snapshots; the files stay parsed and archived for the record either way.

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
  counted, each mode, mail-in accepted/rejected), and the rejection-reason tables for both
  ballot kinds.
- Each snapshot gets a source strip: the original document's link, when it was downloaded,
  and how it was read; the CSV and the provenance record are downloadable
  by link and listed on the Data page; every archived source is on the About page. A strip
  for an unofficial tally family of a certified election documents the read without links —
  nothing for a previous election publishes an unofficial figure.

## Automation

`.github/workflows/turnout-daily.yml` snapshots once a day in the early afternoon Eastern
(after the SBE's morning file refresh), for as long as anything is in flight: the running
general's mail-in files until the canvass ends, GP26's certified-denominator workbooks when
the board re-publishes them (see the sidebar below), and the general's certified families
when they appear after certification. The sha-dedupe keeps an unchanged day cheap; a
non-reconciling file makes the run red rather than publishing a wrong number; the
snapshot commits what changed and pushes, then starts the Pages workflow itself
(GitHub ignores pushes made by a workflow's own token); a person's push deploys the
ordinary way. `make turnout` runs the fetch and parse by hand; commit and push what
it produces and the ordinary human push triggers the Pages workflow.

## Known data quirks (the SBE's own numbers, documented honestly)

- **Same-day by-party undercounts the county file it parallels** in four elections (new
  registrations: GP26 2,162 vs 2,179; PP24 3,628 vs 3,708; GP22 2,901 vs 2,909; PG24 one
  short in Prince George's), with no state row in that file to check against; each
  snapshot's sidecar names the counties, and the same-day page says how many. Every other
  grain matches the county file.
- **Same-day location files list a site twice** when it served as an early-voting center and
  an election-day polling place (GG22, GP22, PP24); the location's counts are both rows'.
- **The 2022 same-day district sheets** print "CONGRESSIONAL DISTTICT CODE" and "LEGISLATIVE
  DISTRICT CODE" headers; both are read.
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

- The **special elections** of 2024 and 2025 (SPP24, SPG25, SPP25): the same families are
  published; the 2022 and 2024 regular elections are live.
- **Permanent mail-in voters by party and county** (the PMIBs monthly report) and the
  **by-location subcounty pages** — data layers the section can grow into.
- **Unofficial election-night turnout** — excluded by decision.

## The 2022 and 2024 backfill (PP24, GP22, GG22)

Added October 17, 2026 beside PG24, from the same families at the same grains. What differs:

- **Where the files are.** GG22's sit in `press_room/2022_stats/GG22/` (PG24's layout, with
  `Updated-` re-posts). The primaries' sit loose in `press_room/2024_stats/` (PP24) and
  `press_room/2022_stats/` (GP22), beside the later elections' folders, with no `Updated-`
  re-posts. GP22's mail-in report is named without PG24's "(NEW)". Every printed title was
  read: the "ZOfficial by Party…" sheets are the genuine election's, not stale copies.
- **Figures of record only.** The eight-day early-voting files are not fetched for these three
  elections (a certified election never publishes them), nor are the single left-to-count
  sheets. Their same-day files are, for the same-day pages.
- **2022 sent-and-returned sheets print no accepted columns**; the parser reads them without.
- **The board's own print slips** — modes that do not sum to a printed total, or a printed
  share that is not its count over its base (26 canvass, accepted, mail-in and provisional
  rows and 12 by-party county, district and legislative cells in PP24, one by-party cell and
  Talbot's three provisional shares in GP22) — are an explicit registry checked against each archived
  file's text layer: `_TALLIED_PRINT_ERRORS`, `_TALLIED_SHARE_PRINT_ERRORS` and
  `_OFFICIAL_BLOCK_PRINT_ERRORS` in `pipeline/turnout.py`. The row publishes as printed; a
  slip outside the registry fails the parse.
- **Posts that do not reconcile stay archived and unparsed** (`UNRECONCILED_SNAPSHOTS`):
  GG22's December 2022 votes-accepted original (its counties' vote-by-mail counts sum 792
  short of its Statewide total; the April 2026 re-post is the corrected one the web reads) and
  the April 2026 re-posts of GG22's three by-party workbooks (their party blocks differ from
  their Statewide blocks by hundreds in nearly every area; the December 2022 originals
  reconcile and are the figures of record).
- **New parties on the 2022 rolls:** Bread and Roses (`BAR`, an eligible-sheet column) and
  the Working Class Party (a by-party block label), alongside the existing codes.

## The 2018 general (GG18), and why not 2014

Added October 17, 2026 as a second midterm anchor beside GG22. What differs:

- **Where the files are.** The press room keeps no 2018 tree; the certified reports sit in the
  board's election archive at `elections/archive/2018/turnout/general/`, listed on
  `elections/archive/2018/index.html`. That index lists exactly seven stat files, all
  ingested: `Official by Party and County/Congressional/Legislative.xlsx` (`official-turnout`,
  the xlsx only, as for every election; the pdf prints are not fetched), `Official
  Turnout.pdf` (`county-canvass`), `Official Accepted Turnout.pdf` (`votes-accepted`),
  `Official ABSENTEE Voting.pdf` (`mail-voting`) and `Official Provisional Voting.pdf`
  (`provisional`). There are no eligible-voter workbooks, no campaign mail-in files and no
  eight-day tallies for 2018 (probed under the 2022 names: placeholder pages). The precinct
  csv is not ingested.
- **Older column names, same measures.** The by-party sheets print `POLLS / EV / ABS / PROV /
  ELIGIBLE VOTERS` (the Democrat block spells `ELIGIBLE_VOTERS`); the canvass and accepted
  prints say `Polling Place (Election Day)` and `Absentee`. These are label aliases onto
  election-day / early-voting / vote-by-mail / provisional / eligible; the mail-in and
  provisional prints already used today's headers. The party blocks are Statewide, Democrat,
  Republican, Libertarian, Green, Unaffiliated and Other Parties, all existing codes.
- **The sheet's eligible measure is active registration**, as on the 2022 and 2024 sheets:
  3,954,027 statewide, and the board's printed TURNOUT column (59.06%) is the four modes over
  it. The page shows that printed percentage; there is no active-plus-inactive denominator
  for 2018 (and so no "workbooks have not re-published" note on its page).
- **No printed date.** The four 2018 PDFs print no report date, as-of or election date, and the
  by-party sheets print only the election date. `Election.undated_as_of` files every 2018
  document under election day, 2018-11-06 (the date the by-party sheets do print); an
  election without that setting still fails a dateless file, as before.
- **Every 2018 file reconciles as printed**: no print slips registered. The by-party county
  sheet's Statewide block (2,335,128 ballots cast) equals the canvass print's Statewide
  total, and the accepted print counts 2,327,542.
- **2014 is not ingested.** The 2014 archive (`elections/archive/2014/turnout/general/`)
  does have by-party workbooks (`GG14_Turnout_by_party_by_county.xlsx`, with CD and LD twins listed),
  but in a different layout -- the party name sits in each block's header row where the
  area label would be, with no LBE column label, title rows or date -- which the header-driven
  parser cannot read without a layout variant. Its canvass print
  (`GG14_Statewide Turnout by County and Canvass.pdf`) has a text layer, but prints Baltimore
  County as "Baltimore" and curly apostrophes, and on its own carries no party or eligible
  figures. Both are left for a later increment if a 2014 anchor is wanted.
