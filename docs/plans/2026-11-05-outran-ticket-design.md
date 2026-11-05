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
