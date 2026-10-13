# Turnout county and congressional-district pages — design

Date: 2026-10-13
Approved approach: **A — one parameterized geography subpage** (route + template pair), branching kept small.

## What the user asked for

Separate turnout pages for counties and congressional districts. Today the per-election
turnout page (`/turnout/{slug}/`) carries one sortable table — by county — alongside the
statewide tiles, chart and sources. The `turnout_counts` table already holds rows at the
`congressional` grain for every election in the DB (GG26 mail-in tracking; GP26
early-voting / same-day / left-to-count / mail-in; PG24 the official turnout sheet by CD).

## URLs and pages

- `GET /turnout/{slug}/` — stays, becomes the hub: statewide tiles, day-by-day chart,
  sources, and a links row to the geography subpages that exist for that election.
- `GET /turnout/{slug}/county/` — the county table that lives on the election page today.
- `GET /turnout/{slug}/congressional/` — the CD table.

One route handler serves both subpages. Unknown election → 404; unknown geography → 404;
a real election whose grain has no rows renders an empty-state note. Subpage headings say
what the rows are and the as-of dates, in the style of the current table intro.

## Table shapes (mirror the county behavior)

- **Uncertified election** (GG26): mail-in tracking columns per district — sent /
  received / accepted, party splits, requests, print-at-home, return rate.
- **Certified election** (PG24): ballots cast by mode (early voting, election day,
  vote by mail, provisional), votes counted, mail accepted/rejected; plus a **turnout %**
  column computed per district as sum(modes) / the official sheet's printed per-CD
  `eligible` figure, shown only where that figure exists.

## Data layer (`web/turnout.py`)

The county row builders generalize by one argument: an `_area_cells(..., geography,
area_value)` helper and geography-parameterized queries, so `district_rows()` and
`certified_district_rows()` mirror `county_rows()` / `certified_county_rows()`.

Two wrinkles the data forces:

- **Label normalization.** The stored district value carries two printed spellings within
  one election (GP26 early-voting rows say `01`; its sent-returned rows say
  `Congressional District Code 01`). Rows join on a normalized key:
  `district_label()` maps both spellings to `District 1` … `District 8`; rows carry the
  raw code for sorting (`01` … `08`, `Undetermined` last).
- **Turnout % per CD.** PG24's sheet publishes per-CD `eligible`; the column is
  `sum(modes) / eligible` per district, shown only when that district's eligible figure
  exists — the same board's-own-denominator caveat the county final carries.

`Undetermined` appears as the table's last row rather than being hidden.

## Templates

One new template, `turnout_geography.html`, serves both subpages with a small
`{% if geography == 'congressional' %}` branch for headers and label text. Each subpage
carries the same explanation paragraph, as-of note, sortable table (existing
`_macros.html` machinery), Data note, and source strip the election page uses today, so
provenance stays attached to the pages showing the numbers. The overview template drops
its table section and gains the links row.

## Static build

`page_urls()` appends `/turnout/{code}/county/` and `/turnout/{code}/congressional/` per
election, only when that grain has rows (per-election per-grain query).
`page_path()` already handles nested paths; `check_links()` needs no change as it walks
`page_urls()`. Certified-election unpublished-family gating is untouched (CSV links come
from `snapshot_sources()`).

## Tests

Extend `tests/test_turnout_web.py` (row shapes: dual-spelling join, Undetermined
placement, turnout %; route 200s/404s; the overview no longer contains the county table)
and `tests/test_static_site.py` (subpage URLs present iff data exists; rebuilt-page link
check passes). Fixture data follows the patterns already in those files.

## Out of scope

Legislative, councilmanic, `location` and `county-party` grains (in the DB, not asked
for); per-district trend charts; changes to the certified-figuring publishing gates.