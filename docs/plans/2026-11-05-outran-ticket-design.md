# Ahead of the ticket (county results pages)

2026-11-05. Code: `src/skipjack/web/ticket.py`, the "Ahead of the ticket" section of
`templates/results_county.html`, tests in `tests/test_ticket.py`.

## What it shows

On a county results page for a general election (PG20, GG22, PG24, and GG26 from the unofficial
count while it is live), a table of down-ballot candidates who got more votes in the county
than their party's candidate in the ballot's first contest: President or Governor. Columns:
candidate, party, contest (linked to its card), votes, the top-of-ticket votes, and the lead in
votes and as a share of the top-of-ticket candidate's votes. Biggest lead first. A sentence
gives how many candidates were compared and how many ran ahead.

## Decisions (agreed 2026-11-05)

- **Countywide contests only.** The results files have no precinct or district split of the
  top-of-ticket vote, so a contest covering part of a county cannot be compared fairly with a
  countywide figure, by votes or by share. Precinct results would lift this limit; they are not
  fetched.
- **Unopposed candidates are left out**: no named candidate of another party in the contest, or
  no more named candidates than seats.
- **General elections only**: a primary has no cross-party top of the ticket.
- Only party ballot lines (DEM, REP, LIB, GRN, WCP) are compared; unaffiliated, "other",
  nonpartisan, write-in and retention lines are not.

## Which contests are countywide

- Statewide offices: always.
- Congress / State Senate / House of Delegates: when the county has one district of the office.
- County offices: by votes, because several counties (Calvert, Charles in 2022, Garrett,
  Queen Anne's, St. Mary's, Cecil's council) elect from residency districts countywide and the
  files do not say so. Votes per seat (seats = the SBE's winner flags; `vote_for` on the
  unofficial count) must reach 0.4 of the top-of-ticket votes, and an office with several
  numbered districts must have district ratios adding to at least 1.2 (districts splitting the
  county add to about 1).

Measured on all four generals: district seats at 0.29 or less, countywide contests at 0.52 or
more (multi-seat Orphans' Court races carry heavy undervotes). Dorchester's two-seat Orphans'
Court District 2 is 0.49 and is caught by the partition sum (0.76). PG20 Wicomico Council
District 2 (a lone special election, 0.24) is left out.

## Caveat on the page

In a multi-seat contest a candidate can pick up votes from voters whose own party fielded fewer
candidates than seats; the note says so.

## The election hub's dot plot (2026-11-06)

Code: `ticket.statewide()` and `_plot()`, the macro in `templates/_ticket.html`, the
`.dot-plot` rules in `static/style.css`, tests at the end of `tests/test_ticket.py`.

On the GG26 hub page only (`ticket.HUB_PLOT_ELECTIONS`; the other generals' hubs do not get it,
decided 2026-11-06), after the statewide section, a sorted dot plot: one row per
county, one dot per other statewide candidate of the party leading the top of the ticket, the
dot placed by the gap between that candidate's county votes and the ticket-mate's (President
or Governor), as a share of the ticket-mate's vote. A sentence above gives each candidate's
count of counties ahead and the statewide gap in votes. County names link to the county
page's "Ahead of the ticket" table; each row has a tooltip with the votes.

- **Which candidates.** The party is that of the top-of-ticket candidate with the most votes
  statewide (Moore). Each statewide contest
  gives one series, under the same opponent rule as the county table (no unopposed
  candidates). Ballot order decides the series order and the sort column.
- **Why a share, not votes.** Montgomery's 2,300-vote gap and Caroline's 500 are the same
  story at different sizes; as shares (0.9% and 19.6% in the Nov 5 GG26 count) the
  rural-to-suburban gradient is the picture.
- **Layout is computed in Python** (`_plot`): a fixed 680-wide view box, 150 of it for labels
  pinned to the left margin (the zero line moves when any gap is negative), ticks every 5 points on a domain rounded out to
  the nearest 5. Colors are a fixed order of four non-partisan hues (teal, purple, gold,
  magenta), so nothing reads as red or blue.
- **Caveat worth a sentence in any write-up:** a third-party candidate for Governor (Green,
  Working Class) has no counterpart in a two-way comptroller or attorney general race, so part
  of the gap is those voters, not defectors. In GG26 the gap exceeds their combined vote.
- The hub route builds every county page to get the figures (24 queries); the static site
  renders each hub once, so the cost is only on the live server.
