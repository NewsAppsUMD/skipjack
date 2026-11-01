# A simple turnout model for Maryland's 2026 general election

*Written 2026-10-18. A companion to the full model in `scripts/turnout_model/` and its report,
`docs/turnout-model-2026.html`. Every number below comes from running the code in this document
against the committed inputs. Updated 2026-11-01 with a version that counts the early votes, mail
ballots and same-day registrations already in, through the board's October 29 early-voting and
October 30 mail-in files, with county-level estimates for both versions.*

## The idea in one paragraph

Each county has a turnout rate it tends to hit in a midterm. Take each county's rate from a past
midterm, multiply it by the county's 2026 active registration, and add up the counties. Maryland
has two recent midterms with certified county turnout, 2018 and 2022, and they bracket the
plausible range: 2022 was a low-turnout year and 2018 a high one. A logistic regression with one
dummy per county and one per election does exactly this, and gives it a formal structure.

Five days out, more than half of the likely vote may already be in: early voting is over and most
mail ballots are back. A second version below takes those ballots as known and models only the
election-day vote.

## The model

The unit is a county in a midterm general: 24 counties times 2 elections, 48 rows. The outcome is
ballots cast out of active registration. The predictors are the county and the election:

```
logit(turnout rate) = county effect + election effect
```

- The **county effect** captures how a county turns out relative to the others, whatever the year.
- The **election effect** captures how high turnout ran statewide that year. With two elections
  it is a single number: how far 2022 ran below 2018.

The model is fit as quasibinomial rather than binomial. Turnout varies across counties far more
than coin-flip randomness would allow, and the dispersion estimate of about 350 says so. Plain
binomial standard errors would be too small by a factor of about 19.

## The forecast

The forecast needs a 2026 election effect, and the data cannot supply one. Two midterms tell the
model that 2022 ran below 2018, not where 2026 will fall. So the forecast takes a weight `w`
between the two years: 1 is a 2018-like year, 0 a 2022-like year, and 0.5 is halfway between on
the log-odds scale.

| Scenario | Weight on 2018 | Ballots cast | Rate on active registration |
|---|---|---|---|
| 2022-like | 0 | 2,118,947 | 49.3% |
| Halfway | 0.5 | 2,332,841 | 54.2% |
| 2018-like | 1 | 2,543,176 | 59.1% |

The rates match 2022's actual 49.3% and 2018's 59.1% because each county keeps its own rate, and
the 2026 registration is distributed across counties much as it was in those years. The total
ballots differ from the actuals because 2026 registration is larger.

Counties at the ends of the halfway scenario, on active registration:

| Highest | Rate | Lowest | Rate |
|---|---|---|---|
| Talbot | 65.8% | Baltimore City | 42.6% |
| Kent | 64.7% | Prince George's | 48.1% |
| Howard | 62.6% | Cecil | 51.1% |

## The code

Run from the repository root after `make turnout-model` or `Rscript
scripts/turnout_model/01_build_inputs.R` has built the inputs.

```r
library(dplyr)

training <- read.csv("data/turnout_model/inputs/training.csv")
denoms <- read.csv("data/turnout_model/inputs/denominators_gg26.csv")

# One row per county per midterm: ballots cast and active registration.
midterms <- training |>
  filter(election %in% c("GG18", "GG22")) |>
  group_by(election, county) |>
  summarise(ballots = sum(ballots_cast), active = sum(active), .groups = "drop")

# Projected 2026 active registration by county.
active26 <- denoms |>
  group_by(county) |>
  summarise(active = sum(active))

# County and election dummies, quasibinomial.
m <- glm(cbind(ballots, active - ballots) ~ county + election,
         family = quasibinomial, data = midterms)

# w is the weight on 2018: 1 = a 2018-like year, 0 = a 2022-like year.
forecast <- function(w) {
  eta18 <- predict(m, mutate(active26, election = "GG18"))
  eta22 <- predict(m, mutate(active26, election = "GG22"))
  mutate(active26, rate = plogis(w * eta18 + (1 - w) * eta22), ballots = active * rate)
}

sum(forecast(0.5)$ballots)   # 2,332,841
```

The inputs come from the State Board of Elections' certified turnout sheets, by way of the
pipeline: `training.csv` holds each county's ballots cast and active registration, and
`denominators_gg26.csv` projects September 2026 active registration to election day.

## It is the ratio model in a regression's clothes

With a dummy for every county and every election, the regression reproduces each county's
observed rate in each year exactly. A forecast at `w = 0` or `w = 1` is just that county's 2022 or
2018 rate times its 2026 registration. You could get the same numbers with arithmetic:

```r
ballots_2026 = active_2026 * ballots_2022 / active_2022
```

The halfway scenario differs slightly from averaging the two rates (2,330,735 ballots), because
the regression averages on the log-odds scale. What the regression adds over arithmetic is
standard errors and a structure that extends: a party term, covariates, or random effects.

## A version that explains why counties differ

Replace the county dummies with two county characteristics from the Census Bureau's American
Community Survey (2020-2024), standardized:

```r
covariates <- read.csv("data/turnout_model/inputs/covariates.csv")
m2 <- glm(cbind(ballots, active - ballots) ~ election + z_share_ba_plus + z_median_age,
          family = quasibinomial, data = inner_join(midterms, covariates, by = "county"))
```

| Term | Estimate | Std. error |
|---|---|---|
| Intercept | 0.409 | 0.042 |
| 2022 (vs 2018) | -0.400 | 0.044 |
| Share with a bachelor's degree or more (z) | 0.119 | 0.024 |
| Median age (z) | 0.256 | 0.044 |

Older counties and better-educated counties turn out more. These three coefficients explain about
70% of the variation in county turnout rates, and the fitted rates miss the actual ones by 3.7
points on average. That is worse than the county-dummy version, which fits the past exactly. The
covariate version is the better tool for explaining the pattern, and for predicting a place with
no turnout history of its own.

## A version that counts the ballots already cast

The county-and-election model ignores how people vote. By November 1 that throws away the best
evidence there is. Early voting ended on October 29, and the board's unofficial count is 604,396
ballots, against 380,874 in 2022 and 663,188 in 2018. By October 30, four days before the
election, 376,967 mail ballots had come back, against 360,354 four days before the 2022 election.

The mode version splits each county's 2026 vote in two:

```
ballots = banked + (active - banked) * election-day rate
```

- **Banked** is the ballots cast before election day: the early-voting count plus mail ballots.
  Mail is the ballots back so far times 2022's late-arrival multiplier for the county (2022's
  certified mail vote over the ballots back four days before that election, 1.50 statewide),
  capped at the ballots sent. Statewide that is 560,540 mail ballots and 1,164,936 banked.
- **The election-day rate** is the share of registrants who had not banked a ballot who voted on
  election day or provisionally. It was 34.6% statewide in 2022 and 48.9% in 2018. Each county
  keeps its own rate, and `w` blends the two years on the log-odds scale, as before.

| Scenario | Weight on 2018 | Election-day and provisional | Ballots cast | Rate on active registration |
|---|---|---|---|---|
| 2022-like election day | 0 | 1,086,099 | 2,251,035 | 52.3% |
| Halfway | 0.5 | 1,302,923 | 2,467,859 | 57.4% |
| 2018-like election day | 1 | 1,534,640 | 2,699,576 | 62.8% |

```r
library(dplyr)
library(tidyr)

# Past midterms by county: ballots banked before election day (early + mail), and the
# election-day rate among the registrants who had not banked one.
past <- training |>
  filter(election %in% c("GG18", "GG22")) |>
  group_by(election, county) |>
  summarise(active = sum(active), banked = sum(early_voting + vote_by_mail),
            on_day = sum(election_day + provisional), .groups = "drop") |>
  mutate(pool = active - banked, rate = on_day / pool)

# 2026 so far: the early-voting count (voting is over) and mail ballots sent and received.
ev <- read.csv("data/turnout/GG26/early-voting/county/2026-10-29.csv") |>
  filter(measure == "total") |>
  select(county, early = value)
mail <- read.csv("data/turnout/GG26/sent-returned/county/2026-10-30.csv") |>
  filter(category == "ALL", party == "ALL") |>
  pivot_wider(id_cols = county, names_from = measure, values_from = value)

# 2022's late-arrival multiplier: final mail vote over ballots back 4 days before election day.
mail22 <- read.csv("data/turnout/GG22/sent-returned/county/2022-11-04.csv") |>
  filter(category == "ALL", party == "ALL", measure == "received") |>
  select(county, received22 = value) |>
  inner_join(training |> filter(election == "GG22") |> group_by(county) |>
               summarise(vbm22 = sum(vote_by_mail)), by = "county") |>
  mutate(late = vbm22 / received22)

now <- active26 |>
  inner_join(ev, by = "county") |>
  inner_join(mail, by = "county") |>
  inner_join(mail22, by = "county") |>
  mutate(mail = pmin(received * late, sent), banked = early + mail, pool = active - banked)

# w is the weight on 2018's election-day rate.
forecast_modes <- function(w) {
  rates <- past |> select(election, county, rate) |> pivot_wider(names_from = election, values_from = rate)
  now |>
    inner_join(rates, by = "county") |>
    mutate(rate = plogis(w * qlogis(GG18) + (1 - w) * qlogis(GG22)),
           ballots = banked + pool * rate)
}

sum(forecast_modes(0.5)$ballots)   # 2,467,859
```

Every scenario here is higher than the matching scenario in the first version. Even a
2022-like election day gives 2,251,035 ballots, close to the first version's halfway case. The
two versions bracket one question the data cannot answer by itself: are the extra early voters
new voters, or 2022's election-day voters who came earlier?

- **The first version** reads the jump in early voting as a change of habit. It ignores modes, so
  every early vote in 2026 is one fewer election-day vote.
- **The mode version** reads it as new voters. It assumes the registrants who have not voted turn
  out at a past year's election-day rate. They will not: the people who voted early are the
  likeliest voters, so the ones left are less likely to vote than 2022's were. That pushes the
  mode version too high.

The truth is somewhere between. The 2024 presidential general shows the habit is shifting: 57.5%
of its ballots were early or by mail, against 45.4% in 2022 and 33.6% in 2018. A useful way to
read the two versions together is to ask what election-day rate a total requires. The first
version's halfway case, 2,332,841 ballots, needs 37.2% of the registrants who have not voted to
vote on November 3, a little above 2022's 34.6%.

### County by county

The two versions disagree most where early voting and mail grew most since 2022. The table
gives each county's halfway case under both versions, its ballots banked before election day,
and the election-day rate the first version's halfway case needs from the registrants who have
not yet voted, beside the rate they turned out at in 2022.

| County | First version, halfway | Mode version, halfway | Difference | Banked | Needed by the first version | 2022 rate |
|---|---|---|---|---|---|---|
| Allegany | 23,821 | 24,760 | +3.9% | 7,179 | 44.3% | 42.3% |
| Anne Arundel | 239,402 | 258,232 | +7.9% | 130,828 | 37.6% | 38.8% |
| Baltimore City | 176,019 | 176,966 | +0.5% | 70,004 | 30.9% | 23.9% |
| Baltimore County | 318,919 | 327,377 | +2.7% | 149,367 | 38.8% | 33.3% |
| Calvert | 41,158 | 44,682 | +8.6% | 22,305 | 39.4% | 40.2% |
| Caroline | 12,383 | 12,698 | +2.5% | 4,409 | 43.1% | 40.9% |
| Carroll | 78,146 | 81,174 | +3.9% | 32,186 | 46.9% | 44.6% |
| Cecil | 37,774 | 39,913 | +5.7% | 15,441 | 38.2% | 36.7% |
| Charles | 67,833 | 73,530 | +8.4% | 34,204 | 34.9% | 32.2% |
| Dorchester | 13,412 | 13,907 | +3.7% | 5,344 | 42.6% | 40.2% |
| Frederick | 122,944 | 130,476 | +6.1% | 55,412 | 44.3% | 43.1% |
| Garrett | 11,997 | 12,198 | +1.7% | 4,423 | 45.3% | 45.3% |
| Harford | 116,429 | 120,281 | +3.3% | 51,181 | 44.3% | 42.6% |
| Howard | 150,597 | 155,244 | +3.1% | 71,913 | 46.7% | 42.1% |
| Kent | 9,328 | 9,497 | +1.8% | 4,211 | 50.2% | 48.1% |
| Montgomery | 402,545 | 435,677 | +8.2% | 234,275 | 36.0% | 34.6% |
| Prince George's | 299,938 | 330,557 | +10.2% | 176,326 | 27.7% | 25.6% |
| Queen Anne's | 25,950 | 26,264 | +1.2% | 11,877 | 47.1% | 44.8% |
| Somerset | 8,075 | 8,215 | +1.7% | 2,981 | 43.3% | 38.4% |
| St. Mary's | 42,810 | 46,433 | +8.5% | 21,895 | 36.8% | 36.8% |
| Talbot | 19,010 | 19,539 | +2.8% | 10,981 | 44.8% | 44.1% |
| Washington | 52,741 | 56,840 | +7.8% | 22,774 | 38.2% | 39.2% |
| Wicomico | 34,731 | 36,506 | +5.1% | 15,746 | 36.8% | 35.4% |
| Worcester | 26,881 | 26,893 | +0.0% | 9,677 | 49.2% | 46.0% |

The mode version runs 10.2% higher in Prince George's, and 7.8% to 8.6% higher in Calvert, St.
Mary's, Charles, Montgomery, Anne Arundel and Washington. Those are the counties where the banked ballots have grown
fastest, and so where the two readings of the early vote, new voters or changed habits, matter
most. In Baltimore City and Worcester the versions agree to within about half a percent.

Read the other way, the first version's halfway case asks the most of Baltimore City: 30.9% of
the registrants who have not voted must vote on November 3, against 23.9% in 2022. It also asks
well above 2022 in Somerset, Baltimore County and Howard. It asks less than 2022 in Anne Arundel,
Washington and Calvert, and about the same in Garrett and St. Mary's.

```r
by_county <- forecast(0.5) |>
  select(county, first = ballots) |>
  inner_join(forecast_modes(0.5) |> select(county, modes = ballots, banked, pool), by = "county") |>
  inner_join(past |> filter(election == "GG22") |> select(county, rate22 = rate), by = "county") |>
  mutate(difference = modes / first - 1, needed = (first - banked) / pool)
```

The full model's report has the same check for its own county forecasts, under all three
scenarios.

**Same-day registration.** Maryland lets people register and vote at once, during early voting
and on election day. Those voters are not in the September registration that every version of
this model multiplies, so they are ballots the cells cannot see. 2022's same-day registrants are
already inside its rates, because certified ballots cast include them, so only 2026's excess
matters. During early voting, 3,802 people registered and voted, against 2,442 in 2022, a rise of
56%. If election-day same-day registration grows as much from 2022's 11,456, 2026 has about
21,600 same-day registrants, against about 14,500 for 2022's count scaled to 2026 registration.
The difference, about 7,100 ballots, is 0.3% of the vote. The 3,562 voters who changed their
address at the polls (1,730 in 2022) were already registered and add nothing.

```r
sd26 <- read.csv("data/turnout/GG26/same-day/county/2026-10-29.csv")
sd22 <- read.csv("data/turnout/GG22/same-day/county/2022-11-08.csv")
n <- function(d, m) sum(d$value[d$measure == m])

growth <- n(sd26, "new-total") / (n(sd22, "new-total") - n(sd22, "new-election-day"))
sdr26 <- n(sd26, "new-total") + n(sd22, "new-election-day") * growth
active22 <- sum(as.numeric(past$active[past$election == "GG22"]))
sdr26 - n(sd22, "new-total") * sum(as.numeric(now$active)) / active22   # 7,142
```

## How well it predicts

The full model's backtest used a party-level version of this approach as its naive baseline. Each
midterm was held out in turn, and the other one supplied each county and party's rate. The
statewide level for each party was set to the actual result, so only the county pattern was being
tested. On county turnout rates, on active registration:

| Held-out midterm | Naive baseline, average miss | Full model, average miss |
|---|---|---|
| 2022 | 2.54 points | 2.25 points |
| 2018 | 2.52 points | 2.17 points |

The full model is better, but not by much. Most of a county's turnout is predictable from its own
past.

## How it compares with the full model

The halfway scenario is 2,332,841 ballots, within 0.1% of the full model's central forecast of
2,330,865. That agreement is partly luck. The full model's level rule now puts Democrats and
Republicans halfway between 2022 and 2018 and everyone else at 0.7 of the way to 2018, after the
early-vote and mail returns gave every party a 2018-like vote. It also adds 7,142 same-day
registrants. (Before the early-vote data, on October 16, its central forecast was 2,282,279.)
County by county, the simple model runs between 2.7% below and 1.3% above the full model's
central forecast. The largest gaps are Cecil, Somerset, Howard and Garrett.

The full model adds five things to this one:

- **A party split.** It forecasts Democrats, Republicans and everyone else separately in each
  county, which also gives the electorate's partisan mix.
- **Random effects instead of dummies.** Small counties are pulled toward similar counties
  instead of trusting two noisy years.
- **Cell-level noise.** The term the quasibinomial dispersion is pointing at, which also produces
  intervals.
- **Composition and a written level rule.** An adjustment for how the electorate has changed
  since 2022, and a rule that sets `w` from registration, mail-in, primary and early-vote data
  instead of by hand.
- **The ballots already cast, used carefully.** The early votes and mail returns per registrant,
  against 2022 at the same number of days out, cast one vote in the level rule rather than setting
  the total outright, which keeps a change of habit from being read as a surge. They also fix the
  mode split, and the full report shows the election-day rate each scenario needs. Same-day
  registration beyond 2022's level is added to every scenario.

## What it cannot do

- **Choose the level.** The weight on 2018 versus 2022 is the main forecasting decision, and this
  model leaves it entirely to you. The early vote narrows it but does not settle it, because it
  cannot tell new voters from voters who changed how they vote.
- **Give intervals for 2026.** Its standard errors describe the fit to the past, not the
  uncertainty about the coming election.
- **See change.** It assumes each county's turnout relative to the state holds steady, so it
  ignores shifts in age, party or new registrants. The mode version sees the early vote, but
  assumes the voters left behave like a past year's.
- **Use official counts for 2026.** The early-voting and same-day figures are the board's
  unofficial campaign tallies, and the mail figures count envelopes back before the canvass
  accepts or rejects them.
- **Use the eligible denominator.** It works on active registration, as the board's 2018 and 2022
  sheets did. The board's 2026 guide divides by all eligible voters (active plus inactive), which
  gives a lower percentage.
