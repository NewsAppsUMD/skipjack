# Turnout: the certified families — design (GP26 backfill)

Extends the turnout section to the 2026 primary (GP26) using **every report category except
the election-night unofficial turnout report** (`2026_primary_election_night_unofficial_turnout.pdf`,
excluded as agreed). GG26 stays as launched (its mail-in tracking is mid-campaign); the same
machinery then covers GG26's certified families after its canvass.

Confirmed scope in brainstorming: GP26 first; the 2022/2024 elections are a later increment
using the same parsers.

## The GP26 files (live under press_room/2026_stats/GP26/)

| family | files | shape (read from the live files) |
|---|---|---|
| `requests` | xlsx + pdf | county x delivery methods + total; this election's xlsx reads cleanly (unlike GG26's), so xlsx-first with the PDF as fallback; "As of: June 19, 2026" |
| `sent-returned` | xlsx x county/CD/LD | unchanged from GG26 (existing parser) |
| `left-to-count` | xlsx x 3 geo | the sent-returned sheet plus a `LEFT TO COUNT` column (the canvass tracker) |
| `official-turnout` | xlsx+pdf x county/CD/LD | six party blocks (Statewide / Democrat / WCP / Other Parties / Unaffiliated / GRN): 24 area rows x {Election Day, Early Voting, Vote By Mail, Provisional, Eligible Voters, TURNOUT%} |
| `county-canvass` | pdf | "Total Voter Turnout": county x {Early, Election Day, VBM, Provisional} each with a share, + Total |
| `votes-accepted` | pdf | "Total Votes Counted": county x the same four + Total (the counted ballots) |
| `mail-voting` | pdf | county x {Sent, Received, Accepted, Rejected} with shares |
| `provisional` | pdf | county x {Cast, Accepted in Full, Accepted in Part, Rejected} with shares |
| `eligible-active` | xlsx+pdf x county/CD/LD/councilmanic | county x {DEM, REP, GRN, OTH, UNA, WCP} + TOTAL; "Election Date: June 23, 2026" |
| `eligible-inactive` | xlsx+pdf | county x the same party columns |
| `early-voting` | pdf x county/party/CD/LD/councilmanic/location | 8 early-voting days + shares + total per area (and by party in the by-Party file); headers print "Unofficial Early Voting Turnout" — kept deliberately: these are the canvass-period early-voting tallies the certified reports build on, not the election-night report |
| `same-day` | pdf x county/party/CD/LD/councilmanic/location | `extract_tables()`: 8 days x {NEW registration, ADDRCHG} + election-day NEW + totals |

## Rules carried over or established

- A snapshot is keyed by the date printed in the document: `Report Date:`/`Election Date:`
  on the certified files, `As of:` on the campaign files.
- Reconciliation stays within one sheet of one file (never the PDF against the xlsx, one
  geography against another, or county grain against district grain).
- Party codes join the monthly reports': Democrat→DEM, Republican→REP, GRN→GRN, WCP→WCP,
  Unaffiliated→UNA, "Other Parties"→OTH; the official-turnout file's Statewide block is
  party `ALL`.
- Turnout **percentages are computed** (ballots cast / eligible voters) per the State
  Board's own definitions — the printed TURNOUT column is not stored.
- The same two tables (`turnout_sources`, `turnout_counts`) hold everything; `measure`
  distinguishes the modes (`election-day`, `early-voting`, `vote-by-mail`,
  `provisional`, `sent`, `received`, `accepted`, `rejected`, `active`, `inactive`,
  `new-registrations`, `address-changes`, `left-to-count`, the delivery methods and the
  early-voting/same-day days).
- The page: `/turnout/gp26/` shows the final picture (turnout by party and county, ballots
  by mode, mail-in final figures with rejections, provisional outcomes, early-voting
  totals, denominators); `/turnout/gg26/` keeps its campaign-tracking layout. The index
  lists both elections.