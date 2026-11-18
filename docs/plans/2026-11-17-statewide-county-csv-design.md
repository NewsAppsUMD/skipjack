# Statewide races as one wide CSV per contest

**Date:** 2026-11-17

## What

A downloadable CSV for every statewide race, one file per contest, in the shape of
`md_pres_county.csv` in the jour405 course repository:

```
jurisdiction,harris,trump,oliver,stein,kennedy,others,total
Allegany,9231,22141,130,136,363,136,32137
```

One row per county, one column per candidate on the ballot, then `others` (every write-in
row added up, the board's "Other Write-Ins" line included) and `total`.

## Which contests

Every contest the results pages class as statewide whose office is President, U.S. Senate,
Governor, Comptroller or Attorney General: generals, and each party's primary as its own file.
The judges' retention questions are statewide too but are for/against votes, so they are left
out. The 2026 general's unofficial count is included while it is live, with a blank cell where a
county has not reported a count yet; the file is withdrawn with the rest of the unofficial
downloads when the certified results are published.

## Columns

- `jurisdiction`: the county as the results files name it, except `St. Mary's`, written
  `Saint Mary's` as the reference file does.
- A candidate column is the lower-case last name of the first person on the ballot line
  (`Kamala D. Harris and Tim Walz` → `harris`), with Jr./Sr./II-style suffixes dropped and
  accents folded away. Two candidates sharing a last name get `first_last` columns instead.
  Columns run in the contest's vote order, most votes first, as the contest page does.
- `others`: the write-in rows summed. 0 when a contest has none (the primaries).
- `total`: the county's votes in the contest.

## Where

- Built from the database, never stored in `data/`: `/downloads/statewide/<CODE>-<office>[-<party>].csv`
  (`PG24-president.csv`, `GP26-governor-dem.csv`, `GG26-governor.csv`).
- The live app serves them from a route; the static site writes them beside the combined files.
- Each statewide contest page links its file under the county table; the Data page lists
  them per election.

## Code

`skipjack.web.statewide_csv`: `column_names`, `contest_csv` (from the contest dict both
results modules already build), `downloads` (the list of files), `csv_for` (one file's text).
