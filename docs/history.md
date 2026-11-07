# A short history of Skipjack

Skipjack set out to do one thing well: take Maryland election data out of scattered PDFs and
spreadsheets, make it clean, and let every number be traced back to the document it came from.
Here's how it got from an empty repo to election night in about five weeks.

## October 1: launch

The project began with an initial commit that already had plenty in it: 16 years of the State
Board of Elections' monthly voter registration reports (January 2010 onward), parsers to read
them, a small web app and a GitHub workflow. Skipjack was named for the Chesapeake Bay
skipjack, Maryland's state boat.

## October 6–7: registration comes alive

Next came voter registration trends and better county pages, plus the first voter file
snapshot (September 2024) and a look at young voters.

## October 10–11: turnout arrives

This was the busiest stretch. In two days Skipjack gained a full turnout track: design docs,
database tables, a parser for each of the board's report types (mail-in requests, ballots
sent and returned, early voting, same-day registration, provisionals, canvass counts) and the
first snapshots of the 2026 general. The 2026 primary was backfilled, and the 2024 general
followed. Along the way the project set a firm rule: certified elections show only their
certified figures of record, never the board's unofficial tallies.

## October 12 onward: the bot takes a shift

A daily workflow started running, and the **skipjack bot** began committing turnout snapshots
whenever the board posted new numbers. By October 14 that same run was redeploying the site
itself.

## October 13–14: geography and a new look

Turnout got county and congressional district pages. The site also got its brand: the
skipjack logo in the masthead, the nav and the browser tab.

## October 15: maps and results

Server-rendered county maps showed up on five pages, along with estimated voter race
(aggregates only). Then came the big one, a **Results** tab covering every primary and general
from 2020 through the 2026 primary, county by county. Each file is checked against the
statewide totals before it's published.

## October 17–18: a front page and a forecast

The home page was redesigned to lead with mail-in ballots while an election is under way.
That same weekend brought a full **turnout model**, built in R: a multilevel fit across past
elections, adjustments for how the electorate's makeup has shifted, a forecast for the 2026
general by county and party, and a report that checks its own claims. A simpler companion
model got its own explainer. Turnout maps switched to a green palette so a return rate never
looks partisan.

## October 24–29: early voting and same-day registration

When early voting opened, Skipjack added it by county and district. Then came backfilled daily
mail-in reports from 2022 and 2024, so this year's pace could be compared with theirs, and
same-day registration counts for six elections. The home page put all of it in tabs.

## November 1–3: forecast goes public

The turnout model was updated to account for early votes, mail returns and same-day
registration, and the forecast got its own place on the site under a Turnout menu. The
October registration report landed, and the voters page started opening with a statewide
summary.

## November 4–6: election night and after

On election night Skipjack started reading the board's **unofficial results** as they came
in. Each snapshot is checked row by row and county by county before it goes up behind a clear
"unofficial and incomplete" banner. Ballot questions, downloads and daily refreshes followed.
Then came an **"ahead of the ticket"** feature showing down-ballot candidates who outpolled
their party's top-of-ticket nominee, and a home page that leads with the live statewide races.

## By the numbers

- **~190 commits** in five weeks
- **25** of them from the skipjack bot, keeping the data fresh
- **202** monthly registration reports, from 2010 to now
- One state boat, sailing on 🌊
