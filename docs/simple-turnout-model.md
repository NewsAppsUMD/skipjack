# A simple turnout model for Maryland's 2026 general election

*Written 2026-10-18. A companion to the full model in `scripts/turnout_model/` and its report,
`docs/turnout-model-2026.html`. Every number below comes from running the code in this document
against the committed inputs.*

## The idea in one paragraph

Each county has a turnout rate it tends to hit in a midterm. Take each county's rate from a past
midterm, multiply it by the county's 2026 active registration, and add up the counties. Maryland
has two recent midterms with certified county turnout, 2018 and 2022, and they bracket the
plausible range: 2022 was a low-turnout year and 2018 a high one. A logistic regression with one
dummy per county and one per election does exactly this, and gives it a formal structure.

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

The halfway scenario is 2,332,841 ballots, about 2% above the full model's central forecast of
2,282,279. Most of the gap is the level: the full model's level rule puts 2026 at 0.4 of the way
from 2022 to 2018 for Democrats and Republicans, not 0.5. County by county, the simple model runs
between 0.9% below and 3.1% above the full model's central forecast. The largest gaps are Howard,
Somerset, Montgomery and Dorchester.

The full model adds four things to this one:

- **A party split.** It forecasts Democrats, Republicans and everyone else separately in each
  county, which also gives the electorate's partisan mix.
- **Random effects instead of dummies.** Small counties are pulled toward similar counties
  instead of trusting two noisy years.
- **Cell-level noise.** The term the quasibinomial dispersion is pointing at, which also produces
  intervals.
- **Composition and a written level rule.** An adjustment for how the electorate has changed
  since 2022, and a rule that sets `w` from registration, mail-in and primary data instead of by
  hand.

## What it cannot do

- **Choose the level.** The weight on 2018 versus 2022 is the main forecasting decision, and this
  model leaves it entirely to you.
- **Give intervals for 2026.** Its standard errors describe the fit to the past, not the
  uncertainty about the coming election.
- **See change.** It assumes each county's turnout relative to the state holds steady, so it
  ignores shifts in age, party or new registrants.
- **Use the eligible denominator.** It works on active registration, as the board's 2018 and 2022
  sheets did. The board's 2026 guide divides by all eligible voters (active plus inactive), which
  gives a lower percentage.
