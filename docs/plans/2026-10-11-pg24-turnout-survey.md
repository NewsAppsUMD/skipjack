# PG24 shape survey (the fetch spike's findings)

Fetched 2026-10-11 from `press_room/2024_stats/PG24/`: 50 configured stems, 47 archived
(the three `Updated-Official_by_Party_and_*.xlsx` are not published — their pdf re-posts
are). PG24 prints as the **2024 Presidential General Election** (the board's own name);
election day November 5, 2024.

## What parses with the 2026 parsers, unchanged

`requests` (pdf+xlsx, as of 2024-12-06), `sent-returned` ×3 grains (as of 2024-12-07),
`county-canvass` (both the 12/11 original and the 2026-04-30 re-post), `votes-accepted`
(12/11 + 2026-04-21), `mail-voting` (12/11 + 2026-04-21, reconciliation green),
`provisional` (12/11 + 2026-04-21), and `early-voting` at 5 of 6 grains — all pass their
printed-totals reconciliation as-is. The 2024 exports share the 2026 layouts.

## The 2024-only deltas (parse work)

1. **New parties in 2024**: Libertarian (`LIB`) qualified, and No Labels (`NLM`) ran a
   presidential candidate. New party cells appear in the `early-voting`/`same-day`
   county-party sheets, and new party *columns* in the eligible
   (`Dem/Rep/Lib/Other/Una/NLM/Grn/Total` grid) and official-turnout workbooks. The
   2026 party vocab lacks both codes.
2. **Eligible xlsx prints its as-of without a colon**: `Election Date: November 5, 2024`
   + `**As of October 20, 2024` inside one cell — capital-A "As of" but no `:` — so the
   as-of extractor misses it (fetch named the xlsx `unknown`; the pdf twin's footnote
   `as of : June 18, 2026` lowercase rule stays: capital-A only).
3. **Area labels wrap lines in cells**: `Baltimore\nCounty` in the same-day sheets — the
   county lookup needs whitespace collapsed before the normalize map.
4. **Official-turnout by-party sheets use party-name block labels** (Statewide, Democrat,
   Republican, NLM, Libertarian, Other Parties, Unaffiliated, GRN) — the parser's block
   vocab must know them (NLM falling through tripped the duplicate-row guard), and the
   cells arrive as floats.
5. **The eligible/official-turnout PDF twins cannot be read** (their parsers are
   workbook-only; the PDF twins of these two families exist in the press room for PG24
   but not GP26, and GP26's records list the xlsx as the family's canonical format).
   Fix: the file set keeps the xlsx for these two families (matching GP26's shape), the
   already-archived pdf revisions stay in the archive, and `turnout_parse` skips manifest
   snapshots outside the election's configured file set (with a log line) so archived
   history beyond the config is never a failing parse.
6. **The board's own print is off by a few in two same-day counts**: (county row) Prince
   George's prints address-change day cells summing to 634 under a printed ADDRCHG TOTAL
   of 643, and (councilmanic row) 53 vs 61 — the same-day reconciliation catches both.
   GP26's same file reconciles exactly, and the sheets print no election-day address
   column whose sum could close the gap, so these are the board's prints, not a parser
   read. Handled by an explicit waiver registry (election, snapshot, geography, county) →
   printed total, checked against the archived PDF; a different mismatch still fails.

## Re-post notes

The Updated- canvass/mail/provisional re-posts (as of 2026-04-21/30) re-print the same
layout; the web reads each family's latest snapshot, so PG24's certified page shows the
April 2026 corrected figures where the re-posts supersede the December originals.
`left-to-count` files do not exist in the PG24 tree (a canvass-window family the 2024
tree never published).