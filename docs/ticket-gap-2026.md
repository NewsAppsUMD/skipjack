# Did the Green and Working Class tickets explain Moore's gap? (GG26)

*Unofficial results, snapshot 2026-11-16. Rerun `uv run python scripts/ticket_gap/analyze.py`
at certification; every table below is in `scripts/ticket_gap/out/tables.md`.*

## The theory

Anthony Brown (Attorney General) and Brooke Lierman (Comptroller) got more votes than Wes
Moore in all 24 jurisdictions. The governor's contest had Green (Ellis) and Working Class
Party (White) tickets; the other two contests had only a Democrat and a Republican. The
theory: voters who left Moore for those tickets voted Democratic one line down.

## Short answer

**Statewide the numbers fit. County by county they only partly fit, and 2022 says the minor
tickets are not the whole story.**

- The sizes match statewide: Brown led Moore by 88,984 and the two tickets got 90,537.
- In 17 of 24 jurisdictions Brown's lead over Moore is *larger* than the Green + WCP vote, so
  part of it came from somewhere else: mostly voters who chose Cox and then Brown.
- Compared with 2022, the gap swung 95,510 votes toward Brown. Minor tickets account for
  about 28,700 of that. About 57,000 is Moore losing the crossover vote he had in 2022.

County totals cannot show what any voter did. They give ranges, not a verdict.

## Statewide

| | Votes | |
|---|---|---|
| Moore | 1,437,649 | |
| Brown | 1,526,633 | +88,984 over Moore |
| Lierman | 1,513,157 | +75,508 over Moore |
| Green + Working Class (governor) | 90,537 | 4.1% of the governor vote |
| Cox | 679,806 | 9,067 more than Rutledge, 11,865 more than Dunn |

Each Democrat's lead over Moore splits exactly into four parts:

| | Brown | Lierman |
|---|---|---|
| Lead over Moore | +88,984 | +75,508 |
| Minor-ticket votes for governor | +90,537 | +90,537 |
| Cox ahead of the down-ballot Republican | +9,067 | +11,865 |
| More write-ins for governor | +3,565 | +3,720 |
| Fewer votes cast than for governor | −14,185 | −30,614 |

- **Range.** If every other source is counted in full, minor-ticket voters still have to
  supply at least 86% of Brown's lead and 79% of Lierman's. At most they supply all of it.
  These are net figures: voters moving in opposite directions cancel and are not seen.
- **Two-party share.** Moore got 67.9% of the Moore + Cox vote; Brown 69.5%, Lierman 69.4%.
  Add every Green and WCP vote to Moore and he reaches 69.2%. That closes most of the
  difference, as the theory predicts.
- **Skipping the contest is not the cause.** About 1.3% of ballots had no vote for governor,
  against 1.9% for Attorney General and 2.6% for Comptroller. More people voted for governor,
  not fewer. (Ballots are the election-night report's cards cast, dated a day before the
  results snapshot, so treat these as approximate.)
- **Who voted for the tickets.** Green got 47,614 votes with 6,391 registered Greens; WCP got
  42,923 with 5,590 registrants. Seven or eight votes per member means nearly all of the
  vote came from Democrats, unaffiliated voters or Republicans.

## The 2022 control

Same four candidates. In 2022 the governor's contest had three minor tickets (61,835 votes)
and Moore ran 6,526 *ahead* of Brown and 70,900 ahead of Lierman.

| Brown's lead over Moore | 2022 | 2026 | Change |
|---|---|---|---|
| Total | −6,526 | +88,984 | +95,510 |
| Minor-ticket votes | +61,835 | +90,537 | +28,702 |
| Cox ahead of the Republican for AG | −47,910 | +9,067 | +56,977 |
| Write-ins | +2,482 | +3,565 | +1,083 |
| Fewer votes cast than for governor | −22,933 | −14,185 | +8,748 |

- In 2022 Peroutka got 47,910 more votes than Cox: tens of thousands of voters chose Moore
  and a Republican for Attorney General. In 2026 that reversed: Cox ran ahead of Rutledge.
- That shift is twice the size of the growth in the minor-ticket vote.
- A caveat on 2022: half of its minor-ticket vote was Libertarian (30,101), which likely drew
  more from Cox than from Moore. Green + WCP alone grew from 31,734 to 90,537.
- In 2018 and 2014 the Democrats for AG and Comptroller ran hundreds of thousands ahead of
  the Democrat for governor because of Hogan. Minor tickets explain almost none of that.

## By county

Full table: `scripts/ticket_gap/out/county_gg26.csv`.

- **True everywhere, but uneven.** Brown's lead over Moore runs from 0.6% of the governor
  vote in Montgomery to 7.3% in Baltimore City. The Green + WCP share varies much less: 2.7%
  (Talbot) to 6.4% (Baltimore City).
- **Gap larger than the minor-ticket vote:** 17 of 24 for Brown, 12 of 24 for Lierman.
  Baltimore County is the clearest: Brown +18,708, minor tickets 14,031, Cox 3,602 ahead of
  Rutledge. There the tickets can explain 75% of the gap at most.
- **Gap much smaller:** Montgomery (Brown +2,330 against 11,261 minor-ticket votes) and Prince
  George's (+8,001 against 10,778). In Montgomery, Rutledge got 2,570 more votes than Cox and
  6,804 fewer people voted for Attorney General than for governor.
- **Lowest floor:** in Kent, Caroline and Queen Anne's the minor tickets need supply only
  about half of Brown's lead; Cox-then-Brown voters can cover the rest.

Regressions across the 24 jurisdictions, weighted by votes:

| Test | Theory predicts | Brown | Lierman |
|---|---|---|---|
| Gap share on Green + WCP share | slope 1, intercept 0 | slope 2.09, intercept −4.5 pts | slope 1.55, intercept −2.9 pts |
| Split by party | both near 1 | Green 1.04, WCP 3.09 | Green 0.25, WCP 2.78 |
| Change since 2022 on change in Green + WCP share | slope 1 | 2.41 | 1.94 |

- The gap does rise with the minor-ticket share, which supports the theory's direction.
- The slope is about 2, not 1: where the tickets did well, Moore lost about one more vote to
  something else for each vote they took. The Working Class share carries most of it.
- The simplest reading: the tickets and the Cox-then-Brown vote are two signs of the same
  thing, a set of voters cooler on Moore than on the rest of the Democratic ticket.
- With 24 points, and Montgomery and Prince George's carrying 30% of the weight, treat these
  as suggestive.

## What would settle it

- **Precinct results.** The board publishes them after certification. About 2,000 precincts
  instead of 24 counties would tighten every range above. Needs a fetcher.
- **Cast vote records.** A ballot-by-ballot count of how Ellis and White voters marked the
  Attorney General and Comptroller contests answers the question outright. `data/cvr/` is
  empty; this needs a request to the board.

## Method notes

- The four-part split is exact because each contest's total is the sum of its candidates;
  the script asserts it for the state and every county.
- "Minor tickets can explain X% to Y%" counts net movement only.
- Regressions are weighted least squares on shares of the governor vote; standard errors are
  in `tables.md`.
