# County results over time: `/results/county/{slug}/`, 2010–2026

## Context

The Results tab has a page per election per county, but nothing compares a county's results
across elections. This adds a page per county showing how its results for federal, state and
county offices have changed since 2010, generals and primaries, with the 2026 general's
unofficial count as a flagged provisional last point.

Decisions made with the user (2026-11-07): horizon back to 2010 from the State Board's archive;
generals and primaries; a new page family under Results (`/results/county/` and
`/results/county/{slug}/`) rather than tabs on the per-election county pages, linked from the
Results index, from every election's county page, and from the county subnav as "Results over
time". Assumed without asking: Baltimore City's Mayor, Council President, Comptroller and City
Council count as its county-level offices; other municipal offices (Cumberland, Hagerstown) are
left out.

## What the source looks like (verified 2026-11-07)

The 2026-10-15 design doc said the board's data pages carry no county CSVs before 2020. That was
wrong. `https://elections.maryland.gov/elections/archive/{year}/election_data/` for 2010, 2012,
2014, 2016 and 2018 publishes per-county files in the same layout as 2020:
`{Stem}_County_{Year}_General.csv` and
`{Stem}_County_{Democratic|Republican|NonPartisan}_{Year}_Primary.csv`.

- Vote types 2010–2018, generals and primaries: `Early`, `Election Night`, `Absentees`,
  `Provisional`, `2nd Absentees`; the 2010 general adds `3rd Absentees`. Generals carry
  `Write-In?` and a `... Votes Against` twin per type.
- Some cells hold the unquoted token `#NULL#` where zero is meant (the 2010 general's
  `Provisional Votes`, the 2012 Republican primary). Row totals still add up when it reads as 0.
- Winner is `Y` or blank. Office names are padded with trailing spaces.
- Check files: generals 2012–2018 `State_Congressional_Districts_{Year}_General.csv` (`Vote
  Type, County, Candidate Name, Party, Office Name, Office District, Winner, Write-In?, CONG 04,
  CONG 08, …`, scrambled district order); 2010 has `State_Legislative_Districts_2010_General.csv`
  with `LEGS …` columns, whose County `00` rows cover every statewide office. Primaries 2010–2018
  have per-party `State_{Congressional|Legislative}_Districts_{Party}_{Year}_Primary.csv` with a
  shorter header (no Vote Type, no Write-In?). So, unlike PP20, every election has a check.
- Office names drift: `U.S. Congress` (2010, 2022 on), `Rep in Congress` (2012–2016),
  `Representative in Congress` (2018–2020); `Judge Orphans Court` without the apostrophe in 2010
  and 2018. Party codes new to the repo: `CON`, `IND`; `BOT` is the both-party write-in line.
- The 2012 and 2016 presidential primaries repeat President rows by congressional district under
  a blank-district county row, the PP20/PP24 pattern the `_DEDUP` rule already handles.
- `GG18` is also a turnout code; results ids, archive and data directories and URLs are all
  family-prefixed, so nothing collides.

## Design

### Pipeline: one `named` layout, 2010–2020

`layout="named2020"` becomes `layout="named"`, parameterized by the election's year. The
registry gains `check` (`congressional`, `legislative`, `all_state`, `none`) so the parse
dispatches on the check kind, not the layout. Per-election vote types; a header carrying a vote
column the registry does not list fails the parse, as a missing one already did. `#NULL#` reads
as blank, which the named layout turns into 0 with the row total still enforced. Ten elections
are added: GP10, GG10, PP12, PG12, GP14, GG14, PP16, PG16, GP18, GG18. Parse stays all-or-nothing
per election; no speculative row fixups.

### Office crosswalk: `web/offices.py`

The SBE's office names drift across years, so cross-election series need a canonical key. Each
`Office(key, level, title, districted)` sits in a level: federal (president, us_senate,
us_house), state (governor, comptroller, attorney_general, state_senate, house_of_delegates),
county (county_executive, council_president, county_council, county_council_at_large,
commission_president, county_commissioner, commissioner_at_large, sheriff, states_attorney,
clerk, register_of_wills, treasurer, board_of_education, board_of_education_at_large,
orphans_court, and Baltimore City's mayor, city_council_president, city_comptroller,
city_council). Judges, other municipal offices, central committees and convention delegates
have keys but are not shown. `OFFICE_NAMES` maps every SBE spelling to a key; a guard test
requires its key set to equal `results._OFFICE_SCOPE`'s, so a new spelling forces both a scope
and a crosswalk decision. Comptroller follows `results._scope`'s rule: the state office only when
on all 24 ballots, otherwise Baltimore City's.

District eras: `ERAS = {"us_house": (2012, 2022), "state_senate": (2014, 2022),
"house_of_delegates": (2014, 2022)}`. A districted series carries `breaks`; charts leave a gap at
a break and label it "new lines". County council and commissioner districts have no table; a
break is marked when the set of districts in the county's file changes between elections of the
same kind, which misses a redraw that keeps the same labels (said in the page note).

### Query layer: `web/results_trends.py`

Built on `results.county_page` and `results_unofficial.county_page` (the same dispatch as the
routes), so the double-count rule, write-ins, retention contests and GG26 come free. About
10–20 ms per county page, 24 × 18 pages at build time, acceptable for a static build.

`county_trends(conn, slug)` returns `elections` (oldest first, with `cycle` and `unofficial`),
`generals`, `primaries`, `levels: [{level, label, offices: [SERIES]}]`, `summary`, `source_ids`.

- `SERIES = {key, title, district, id, districted, breaks, general: [GPOINT|None per general],
  primary: {dem|rep|non: [PPOINT|None per primary]}}`
- `GPOINT = {election, year, unofficial, era, seats, contested, total, dem: {name, votes,
  share}|None, rep, other_share, margin, winners, leader_party, matched, href, contest_href}`.
  `margin` is Dem share minus Rep share in points, None when not contested (a named DEM and REP
  both present). Multi-seat contests sum each party's candidates. `winners` is the SBE flag
  (empty for GG26); `matched`, for statewide offices, is whether the county's leader's party is
  the flagged winner's.
- `PPOINT = {election, year, unofficial, party_file, seats, candidates, contested, total,
  winner: {name, votes, share}|None, href}`.
- `summary.top` per general: the top-of-ticket contest (`ticket.TOP_OF_TICKET`) with the
  statewide margin from the county sums and `lean = margin − statewide_margin`.
  `summary.primary_electorate` per primary: Dem vs Rep ballots in each party file's first
  contest. `summary.ticket_gap` per general: county-level contested contests chosen by
  `ticket.countywide`, each office's Dem share minus the top of the ticket's.
  `summary.disagreements`: elections where the county's leader or primary winner differed from
  the statewide winner. `summary.winners_wall`: per office, the run of winners with same-name
  runs collapsed to year spans.

### Pages

Routes `/results/county/` and `/results/county/{county_slug}/`, registered above `/{slug}/`
(Starlette matches in order; no election slug is `county`). Static-site URLs added inside the
existing results guard. `county_nav` gets a third link. The per-election county page and the
Results index link to the trends pages.

Rendering follows `web/ticket.py` and `_ticket.html`: Python lays out the SVG in pure functions
(`web/results_trends_plot.py`), Jinja macros in `_results_trends.html` emit it, no JavaScript,
`<title>` tooltips, a `<details>` table twin under every figure. Colours: the party tokens for
identity, the maps' `party_margin` ramp for diverging margin tiles, ink and gray for emphasis,
hollow marks and an "unofficial" tag for GG26. Blue and red stay reserved for the parties. One
axis per chart.

The county page's sections: (1) "How {County} leans": a computed sentence, a hero figure of the
latest top-of-ticket margin and its change since 2010, and the lean timeline (county margin in
ink with dots coloured by the leading party, the statewide margin in gray, the band between
them labelled "lean vs the state"). (2) "The ballot, election by election": a grid of offices by
general, each tile on the margin ramp with the margin printed where it fits, uncontested tiles in
one hue, absent offices hatched, redistricting breaks as a gap. (3) Office lines: small
multiples of Dem and Rep share per office with at least two points. (4) "Ahead of the ticket,
over time": a dot row per general, zero at the top-of-ticket Dem share, a dot per county office,
at most four named and the rest folded into gray. (5) Ballots cast per general, presidential and
gubernatorial years in two steps of one green ramp. (6) Primaries: Dem vs Rep ballots per
primary, and a table of who the county picked with a marker when it disagreed with the
statewide nominee. (7) "When {County} disagreed with Maryland". (8) Winners wall. (9) Sources,
era note, unofficial banner.

The index: a county map coloured by the shift in top-of-ticket margin from 2010 to the latest
certified general, and a sortable table with each county's latest margin, shift, a nine-tile
lean strip and counts.

## What the 2010–2018 parse found (2026-11-07)

- The statewide district files for 2010–2018 (`State_Congressional_Districts_*`, and 2010's
  `State_Legislative_Districts_*`) carry election-day votes only: their County `00` rows equal the
  counties' Election Night column exactly, never the totals. The check for those elections
  compares that column (`ResultsElection.check_votes = "election_night"`) and the sidecar says so.
- Two aggregate write-in rows disagree with the board's own district file by two votes (PG12
  President and GG10 U.S. Senator, both "Other Write-Ins"). They are listed in
  `STATEWIDE_CHECK_EXCEPTIONS`, recorded in the sidecar, not corrected.
- Baltimore City's 2010 general file has 15 rows whose printed total is less than the sum of its
  vote types (write-ins and minor candidates, and the Sheriff's). `ROW_TOTAL_EXCEPTIONS` licenses
  exactly those 15; the printed total is kept and the rows are listed in the sidecar; the loader
  checks the same list.
- `#NULL#` cells read as zero (vote types and, in 2014, the total of all-zero write-in rows); a
  lone `0` in one Against cell with a blank Against total (2012) is read as no Against figure.
- The unofficial 2026 feed spells districts "District 6" and "District A"; the query layer
  normalizes them to the certified "6" and "A" so the 2026 points join their series, and its
  office spellings live in the crosswalk's `UNOFFICIAL_OFFICE_NAMES`.
- School boards are nonpartisan: a contest with no Democrat and no Republican is drawn as a
  neutral "NP" tile, not as uncontested.

## As built (2026-11-07)

Deliberate departures from the first draft above, decided while building against the real pages:

- Share-line cards draw any office with at least two general points (two points are a trend);
  offices with one point are tables only. State Senate and House of Delegates cards, sparse ones
  included, sit in one collapsed disclosure so the State section stays short.
- Grid tiles carry no party chips and cards carry no winner-name labels: text never takes a
  series colour, and names would crowd a 300-pixel card. A tile with only one major party on the
  ballot is a light tint of that party with "D" or "R"; a nonpartisan contest is a neutral "NP"
  tile; an office off that year's ballot is hatched; the unofficial count is an outlined tile.
- The lean chart's zero line is a hairline, not ink; the county line is ink and the state line
  gray, which is where the emphasis belongs. Its range is fitted to the data, so a one-sided
  county fills the plot.
- On phones the full-width charts scroll sideways at their natural size rather than shrinking.
- Winners-wall runs merge across the board's own spelling drift (first and last name, same party)
  and across rotated ballot order in multi-seat contests.
- Baltimore City's county level is labelled "City"; its mayor, council president, comptroller and
  council are county-level offices there, so its presidential years do have county offices.
