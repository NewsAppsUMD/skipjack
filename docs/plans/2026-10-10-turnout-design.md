# Turnout section — design

Approved 2026-10-10. Scope settled in brainstorming: the section starts with the 2026
general election (GG26) only; every in-place-updated file is snapshotted, not overwritten;
a scheduled GitHub Action takes the daily snapshot; launch is one page with a sortable
county table (congressional/legislative parsed, download-only); data lives in new tables.

## What the State Board publishes

The press-room election pages (`press_room/index.html`, one block per election code such
as GG26) pre-list every report file for that election under `press_room/2026_stats/GG26/`.
Missing files answer HTTP 200 with an HTML template page, so "published" means a `PDF`
(`%PDF`) or Excel (`PK`) signature, not a 200.

Live today for GG26:

| File (pdf + xlsx) | Shape |
|---|---|
| `Mail-in_Ballot_Request_Counts_with_Chart` | county x delivery method (AGENT, FAXED, IN PERSON, MAILED, OTHERS, PRINT-AT-HOME) + TOTAL; a header line "As of: October 9, 2026 6 PM" |
| `Absentees_Sent_and_Returned_by_{County,Congressional,Legislative}` | 5 voter categories (ALL, Civilian Overseas, Domestic Civilian, Military Overseas, Military US) x county (+ district rows on the Sheet2 of each file); columns DEM/REP/OTH/TOTAL x SENT/RECEIVED/ACCEPTED; header "As of: October 9" |

Not yet live (listed on the page, go live around/election and after certification):
election night unofficial turnout, early voting, same-day registration, eligible
active/inactive voters, "Official by Party and {County,Congressional,Legislative,Precinct}",
"Official County and Canvass", "Official Votes Accepted by County and Canvass",
"Official Mail-In Voting" (with rejection reasons), "Official Provisional Voting",
"Absentees Ballots Left To Count".

`Navigating_Official_Turnout_Statistics.pdf` defines the terms the section must respect:

- **Turnout** = ballots cast (election day + early voting + vote by mail + provisional,
  whether or not they were counted) divided by **eligible voters** (registered voters who
  are not "incomplete" or "cancelled" — including active, inactive and pending). So an
  official turnout percentage is not computed from active registered voters, and not all
  cast ballots were counted: the accepted figures come from the canvass reports.
- **Ballots Sent / Received / Accepted**: sent = latest mail-in ballot issued to the voter
  (includes FWA ballots); received = envelopes the local board marked received; accepted =
  canvassed and counted. Accepted stays 0 until the post-election canvass.

## Decisions

- **Elections**: GG26 only at launch. The schema is election-keyed so later elections
  (GP26 backfill, future cycles, post-certification official families) are increments.
- **Snapshots**: a file whose SHA-256 matches the latest archived revision is skipped; a
  changed file is archived under a unique name and parsed into a new dated snapshot keyed
  by the "As of" date parsed from the document itself.
- **Cadence**: scheduled GitHub Action runs the same commands a person runs (`make
  turnout`), daily through the election and canvass.
- **Storage**: new tables `turnout_sources` and `turnout_counts` (below). Same three-step
  idiom as the monthly reports: fetch archives originals, parse writes reconciled CSVs +
  provenance sidecars, build-db loads them.
- **Page grain**: one `/turnout/gg26/` page — statewide headline, trend charts, sortable
  county table. Congressional and legislative variants are parsed and published as
  downloads only. County subpages are a later increment.
- **Reading the spreadsheets**: XLSX via calamine when the file is a valid zip; fall back
  to the PDF twin (natural-pdf) otherwise — the live requests xlsx has corrupt zip
  members on SBE's side, so the fallback is exercised from day one.

## Data model

```sql
turnout_sources(
  source_id,            -- e.g. sbe-turnout-gg26-sent-returned-county-<fetchtimestamp>
  election,             -- GG26
  family,               -- requests | sent-returned (official families later)
  geography,            -- county | congressional | legislative
  as_of,                -- YYYY-MM-DD parsed from "As of:"
  source_url, source_name, fetch_date, sha256, document_title, notes
)
turnout_counts(
  source_id, election, family, geography, as_of,
  county,               -- 24 names; empty for rows that are only district-level
  district,             -- e.g. "Councilmanic District 1"; empty on county rows
  category,             -- ALL / Civilian Overseas / ... ; TOTAL rows carry TOTAL
  party,                -- DEM | REP | OTH | ALL
  measure,              -- requests family: agent, faxed, in-person, mailed, others,
                        --   print-at-home, total
                        -- sent-returned family: sent, received, accepted
  value
)
```

CSV layout under `data/turnout/`:

```
data/turnout/GG26/requests/2026-10-09.csv
data/turnout/GG26/requests/2026-10-09_provenance.json
data/turnout/GG26/sent-returned/county/2026-10-09.csv
data/turnout/GG26/sent-returned/county/2026-10-09_provenance.json
data/turnout/GG26/sent-returned/congressional/2026-10-09.csv
data/turnout/GG26/sent-returned/legislative/2026-10-09.csv
```

Each CSV is one archived snapshot; rows mirror `turnout_counts`. If the same as-of date
ever yields two different revisions, the later manifest row wins for that key (same rule
as the monthly reports) and the provenance sidecar records both.

Reconciliation, before anything is written:

- requests: each county's methods sum to its TOTAL; counties sum to the statewide TOTAL.
- sent-returned: DEM+REP+OTH equals TOTAL within every row; each county's rows sum to its
  county total; categories sum to the grand total.
- Anything that does not add up fails the parse (no row is written) — a new mismatch
  requires looking at the PDF and recording it in `source_discrepancies.csv`, as the
  monthly reports do.

## Pipeline

```
uv run skipjack turnout-fetch   # download any new revision; sha-dedupe; append manifest
uv run skipjack turnout-parse   # xlsx-first, PDF fallback; reconcile; write data/turnout/
uv run skipjack turnout-parse --as-of 2026-10-09  # optional filter
make turnout                    # fetch + parse (build-db already covers both tables)
```

Archives:

```
archives/sbe/campaign_stats/GG26/requests__county__<as-of>__<fetchstamp>.pdf
archives/sbe/campaign_stats/GG26/sent-returned__county__<as-of>__<fetchstamp>.xlsx
```

The manifest (`archives/manifest.csv`) gains rows with `source_id` per archived revision
and the SHA-256 of each file.

## Site

- Nav gains `Turnout` → `/turnout/`.
- `/turnout/` — elections index; one row (2026 general) until a second election exists.
- `/turnout/gg26/` —
  - Header: "2026 general election", election day, latest snapshot's as-of date, and when
    each family was last updated.
  - A definitions note: what requests/sent/received/accepted mean, that accepted counts
    stay 0 until the canvass, and that a turnout percentage (cast over eligible voters,
    counted or not) arrives only after certification.
  - Headline numbers (latest snapshot, with the change since the previous snapshot).
  - Trend charts: statewide mail-in requests and envelopes received, by snapshot date.
  - Sortable county table: requests total, sent (DEM/REP/OTH/total), received
    (DEM/REP/OTH/total), accepted, return rate = received/sent.
  - Provenance: link to each snapshot file and its sidecar.
- `build-site` renders the new routes; the data page lists the turnout CSVs; the About
  page lists the archived sources from `turnout_sources`.
- The "participation is not turnout" caveat on voter-file pages links to this section.

## Daily CI

`.github/workflows/turnout-daily.yml`: schedule (after SBE's morning file refresh), steps
sync, uv sync, turnout-fetch, turnout-parse, build-db, tests, build-site; commit only if
`data/turnout/`, `archives/` or `skipjack.sqlite` artifacts changed; the push to main
deploys the site exactly as today.

## Tests

- Pipeline on small synthetic xlsx and PDF fixtures: a clean table parses; a table that
  does not reconcile raises and writes nothing; corrupted xlsx falls back to the PDF twin.
- Snapshot dedupe: fetching an unchanged file adds no archive row; a changed file does.
- Web: `/turnout/` and `/turnout/gg26/` render with `needs_db` skips; the county table
  shows the latest snapshot and the change from the one before.
- Static site: the GG26 page renders into `site/`.