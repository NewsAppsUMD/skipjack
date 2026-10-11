# 2024 general election (PG24) turnout reports

## What this adds

The November 5, 2024 gubernatorial general as a third election in the turnout section:
`ELECTIONS["PG24"]` (certified=True, election day 2024-11-05) with its press-room file
set at `press_room/2024_stats/PG24/`, parsed into `data/turnout/PG24/`, built into the
db, and published as a certified page at `/turnout/pg24/` — figures of record only
(the standing rule: no unofficial tallies for previous elections).

## The file set (from the press-room index)

- Campaign mail-in: `Mail-in Ballot Request Counts with Chart` (pdf+xlsx),
  `Absentees Sent and Returned` by County/Congressional/Legislative (pdf+xlsx).
- Unofficial eight-day tallies, parsed and archived but never published (PG24 is
  certified): `EarlyVoting` ×6 grains and `SameDayRegistration` ×6 grains.
- Certified reports: `Official_CountyAndCanvass`, `Official_VotesAcceptedBy_CountyAndCanvass`,
  `Official Vote-By-Mail Voting(NEW)`, `Official Provisional Voting`,
  `Official by Party and County/Congressional/Legislative` (xlsx+pdf),
  `Eligible Active Voters` by County/Congressional/Legislative/Councilmanic (xlsx+pdf),
  `Eligible Inactive Voters` (xlsx+pdf).
- The `Updated-` re-posts of the certified files (CountyAndCanvass, VotesAccepted,
  Mail-In, Provisional, by Party ×3): ingested alongside their originals — the queries
  read each family's latest snapshot, so a corrected re-post becomes the newer record
  and the correction history stays in the archive.
- Not ingested: the precinct CSVs, `EarlyVoting RAW data.csv`, `SameDayRegistration RAW
  data.csv`, the EOC report (document, not a stat family).

PG24's eligible-denominator workbooks are published (unlike GP26's, which went to
placeholders), so its certified page can show the turnout percentage over eligible
voters without waiting.

## Approach

Full pipeline entry, spike first:

1. **Fetch spike**: run `turnout-fetch` for PG24; confirm which files are live; check
   each document's printed election name and date is genuinely PG24's (the special
   elections' trees show the board re-uses "Official…" file names for stale content —
   the GP22Only files in SPG25's tree — so the printed title is the truth).
2. **Shape survey**: read each family's printed structure against the 2026 parsers
   (built on 2026 fixtures; 2024 exports may differ in headers, sheet layout, or the
   statewide block). Where they differ, the house idiom applies: fixtures copied from
   the real lines, a red test, then a parser variant.
3. **Parse + reconcile**: as-of extraction ("As of:" / report date), per-family parsers,
   and the printed-totals reconciliation unchanged; a non-reconciling file fails the run
   rather than publishing a wrong number.
4. **Build db and site**: no new web code — `ELECTIONS` + `certified=True` drives the
   slug, the certified layout, `may_publish` (no unofficial tallies on the page or in
   downloads), the data page, and the source strips.

## Testing

Per-family fixtures from the real 2024 lines; real-figure page asserts pinned from the
parsed files (like GG26's 617,496/605,052 and GP26's 902,818 pins); the unofficial-tally
absence asserts on `/turnout/pg24/`; `may_publish`, download-filter, and data-page rules
already covered generically by family, so no new rules.

## Docs

`docs/turnout-data-summary.md` (three elections live; the PG24 row) and the README's
turnout bullet.

## Risks

- 2024 export shapes differing from 2026's (the spike quantifies it before any parser
  work).
- Stale "Official…" names in older trees — mitigated by checking each document's printed
  election name and date in the spike.
- The daily workflow ingests PG24 automatically; its files are static, so the sha-dedupe
  keeps the runs cheap.