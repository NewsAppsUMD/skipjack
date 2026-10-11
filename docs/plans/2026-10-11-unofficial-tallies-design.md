# No unofficial tallies for previous elections

## The rule

An election in `ELECTIONS` with `certified=True` is a *previous election*. For those,
nothing sourced from the board's unofficial eight-day turnout files — the `early-voting`
and `same-day` families — is displayed or downloadable. The in-flight election (GG26)
keeps its campaign-time page; once certified, the same rule applies to it automatically.

The GP26 page today publishes four unofficial figures: the 185,031 "early-voting ballots
cast" tile and the "Early voting (the EV tally)" column (Unofficial Early Voting Turnout,
By County), and the 2,179/617 same-day tile and Same-day column (Unofficial Same Day
Registration and Address Change Turnout).

## Approach: refuse at the publish layer

The pipeline keeps parsing and storing every file; snapshots stay in git and the db so
provenance and reconciliation are untouched. Only published surfaces filter:

1. **Certified page** — `routes/turnout.py::_certified_tiles` drops the early-voting and
   same-day tiles; `web/turnout.py::certified_headline` drops their keys;
   `certified_county_rows` stops joining those families and returns no
   `early_voting_total`/`same_day`; `routes/turnout.py::_certified_dates` no longer lists
   them; `turnout_final.html` drops the two columns. What remains: ballots cast, votes
   counted, the four certified modes with shares, mail-in accepted/rejected, requests,
   and turnout over eligible (when the denominator workbooks return).
2. **Downloads and data page** — `web/downloads.py::download_files()` excludes
   unofficial-tally files for certified elections (by parsing the `turnout/<election>/<family>/…`
   relative path). It is the single list behind the download route, the data-page listing,
   and the static-site copy, so all three stay consistent and internal links stay valid.
   Per the user's call, the published download surface is purged too; the raw archives
   under `archives/` and the parsed snapshots under `data/` stay in git.

## Testing

- `test_real_gp26_page_renders_the_certified_data` flips the 185,031/2,179 asserts to
  absence, and adds asserts that no EV-tally or Same-day column is rendered and the GP26
  unofficial downloads are not offered.
- `test_certified_county_rows_join_the_families` extends with early-voting/same-day rows
  present and asserts they are not surfaced.
- New `download_files` test: GP26 unofficial families excluded; GP26 official families
  and every GG26 file kept; non-turnout data unaffected.
- Data-page listing assert: GP26 early-voting/same-day CSVs absent.
- Full non-slow suite green (the CI gate).

## Docs

`docs/turnout-data-summary.md` rewrites its GP26 page bullet to the certified figures
and states the standing rule. The About page and per-page source strips keep documenting
every file the pipeline read, as provenance.

## Rejected alternative

Stop parsing or storing the unofficial families for certified elections — heavier, loses
the in-repo record, and changes nothing readers see.