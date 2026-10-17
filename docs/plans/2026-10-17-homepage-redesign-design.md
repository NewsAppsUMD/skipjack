# Homepage redesign: visuals first, election-aware lead — design

Date: 2026-10-17
Approved: left-aligned logo masthead; election-aware lead (tiles + map); three chart cards;
compact Explore strip.

## Context

The homepage was registration-only: a centered logo hero, six sentences, one party line
chart, a party-margin county map, a party table and five link cards. The site now also
computes live mail-in turnout (daily snapshots), young-voter pace and the registration and
removal breakdowns, and none of it reached the homepage. The redesign makes the page
skimmable: a number, a picture and one sentence per finding.

## Design

### Masthead

`div.hero` is a left-aligned flex row: the 128px logo beside the h1, the tagline and a muted
"what is current" line (registration month, mail-in ballots as of, election day). Same
`hero` / `hero-logo` hooks. On phones the logo shrinks to about 5rem and stays beside the
title.

### Lead

A two-column block that stacks on phones. Its mode follows the calendar.

* **Election mode**: a non-certified election in `ELECTIONS` has turnout snapshots and today
  falls from its first snapshot through election day + 21. Four tiles (requests, sent,
  received with the return rate, accepted), each with its change since the previous report;
  a sentence; a link to the election page; and the "mail ballots returned" county map,
  each county linking to the election's county table.
* **Registration mode**: otherwise. Four tiles (active voters with month and year change;
  the three party shares with year-over-year point change), the total and record-share
  sentences, a link to the county trends, the party-margin map, and an "Also this month"
  pair of sentences (inactive voters, fastest- and slowest-growing counties).

### Chart cards

1. Registration pulse: active voters by party since 2010.
2. Young voters pace: running total of first-time 18-to-22 registrants by week, this year
   against earlier cycles (omitted when no voter file snapshot has the aggregates).
3. Registrations and removals (full width): the rolling 12-month totals of each.

Each card carries one sentence and a link to its page.

### Explore strip

One compact list of links to the site's sections, replacing the five link cards.

## Implementation shape

* `web/turnout.py`: `lead_election(conn, today)`, pure.
* `web/summary.py`: `tiles(conn)`; `highlights()` also returns the sentences by key.
* `web/routes/home.py`: `home_context(conn, today=None)`.
* `web/maps.py`, `_maps.html`: a `link="href"` kind, so the turnout map can link each county
  to another page.
* `home.html`, `home.js`, `style.css`.

## Verification

Full test suite; a local `make site SITE_BASE=/skipjack` build checked in both modes and at
phone width.
