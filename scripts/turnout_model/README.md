# Turnout model

An R model of Maryland's November 3, 2026 general election turnout by county and party group
(DEM, REP, OTH = every other party plus unaffiliated).

## Running

Run every script with Rscript from the repo root, after `make build-db` has built
`skipjack.db`, in order:

    Rscript scripts/turnout_model/01_build_inputs.R
    Rscript scripts/turnout_model/02_fit.R          # about 3 minutes
    Rscript scripts/turnout_model/03_compose.R
    Rscript scripts/turnout_model/04_forecast.R

After a new GG26 mail-in snapshot reaches `skipjack.db`, rerun 01 (which rebuilds `indices.csv`)
and then 04. 04 reads the daily snapshots straight from the database, but its level rule reads
the mail figures in `indices.csv` and checks that the two agree. 02 and 03 depend only on
certified results and the voter file, so they need no rerun.

Statewide rows in every model output have `county = Maryland`.

Each script begins with `source("scripts/turnout_model/R/bootstrap.R")`, which checks that it is
running from the repo root, sets `RENV_PROJECT` to `scripts/turnout_model`, sources
`scripts/turnout_model/renv/activate.R`, and then sources `R/helpers.R` (database connection,
county names, election and mode labels, party grouping, integer splitting, provenance writer)
and `R/model.R` (structure-model helpers). Opening R inside `scripts/turnout_model/` also
activates the project, through its `.Rprofile`.

## renv

`DESCRIPTION` lists the direct dependencies and `renv.lock` pins them (explicit snapshot). To set
up a new machine, `renv::restore()` from inside this directory; on a machine that already has
the packages in its user library, `renv::hydrate()` links them in without compiling anything.
After adding a dependency to `DESCRIPTION`, run `renv::snapshot()` here and commit `renv.lock`.

## Scripts

- `01_build_inputs.R` writes the model's inputs to `data/turnout_model/inputs/`, each with a
  `<name>_provenance.json`: `training.csv`, `inactive_ratio.csv`, `denominators_gg26.csv`,
  `covariates.csv`, `indices.csv` and `polls_2026.csv` (header only, filled by hand and never
  overwritten). It archives the ACS 5-year county pull to
  `archives/census/md_county_acs5_<vintage>.csv` once and reuses it; `REFRESH_ACS=1` pulls it
  again (needs `CENSUS_API_KEY` in `~/.Renviron`).

- `02_fit.R` fits the structure model (a binomial `lme4::glmer` of ballots over active
  registration, with a statewide level per election x party, county covariates, county random
  effects by party and by election type, and an observation-level effect) and writes to
  `data/turnout_model/gg26/`: `structure_effects.csv` and 1,000 draws in
  `structure_draws.csv.gz` (midterm structure effect per county x party, logit scale),
  `election_levels.csv` and `election_level_draws.csv.gz` (GG18/GG22 statewide party levels,
  with `sigma_obs`), `backtest.csv` and `backtest_cells.csv` (leave one midterm out, against a
  naive baseline), and `fit_summary.json` (formula, why it was simplified, variance components,
  interval-width cross-check). Draw files join on `draw`. The alpha draws in
  `election_level_draws.csv.gz` are not for use uncalibrated: they are drawn independently of
  the random effects, so alpha + s carries the uncertainty of the population mean; the forecast
  calibrates the statewide level per draw instead. Seeded, so re-runs on this machine are
  byte-identical, but `MASS::mvrnorm`'s `eigen()` may differ across BLAS builds.

- `03_compose.R` writes `data/turnout_model/gg26/composition_factors.csv`: for each county x
  party cell, the logit-scale change `comp = factor_age + factor_new` that the forecast adds to
  the cell's linear predictor, from the 2026-08-12 voter-file aggregates (`voter_file_counts`,
  used only as ratios within the snapshot). `factor_age` compares the cell's expected 2022-midterm
  participation under its 2026 and 2022 age mix, both by age on election day with one 2022
  propensity schedule; `factor_new` is the pull of recent registrants
  beyond their age, with `rho_gen = sqrt(rho)` (a judgment; `factor_new_rho1` and
  `factor_new_rho_raw` are the sensitivities with `rho_gen` = 1 and = rho). It is a small bonus in
  every cell, because the 2020 registration surge makes the 2019-2022 cohort a larger share than
  the 2023-2026 one. `n26`, the cell's current registrants, is the weight for centring `comp`.
  `county = Maryland` rows are statewide, for reporting only.
  Deterministic.

- `04_forecast.R` is the forecast. It writes to `data/turnout_model/gg26/`, each file with
  provenance:
  - `reference_levels.csv`: for each structure draw, the GG18 and GG22 statewide party levels
    alpha* that reproduce that year's actual party ballots with its own active registration. The
    cell noise eps ~ N(0, sigma_obs^2) is integrated by 20-node Gauss-Hermite quadrature
    (`gauss_hermite_normal()` and `calibrate_alpha()` in `R/model.R`).
  - `level_rule.csv`: the 2026 level rule (below), with each index, its vote and the weight w.
    Sensitivity columns (`_latest`, `_recent`) show the mail index, its vote and w under the
    alternative mail projections. `level_rule_text.csv` holds the rule, the mail note, the
    interval note and the composition note as one row of text.
  - `forecast_county_party.csv`, `forecast_county.csv` and `forecast_statewide.csv`: ballots and
    rates by scenario.
    - The county files carry the four main scenarios plus `actual_GG18` and `actual_GG22` rows
      from `training.csv`. Their interval columns equal the point value, and the actual rows
      leave eligible and CVAP empty.
    - `forecast_statewide.csv` is in long form (`scenario, measure, mean, q05, q50, q95`), so
      the actual rows need no NA for the denominators they lack. It also has the mail
      sensitivities `central_mail_latest` and `central_mail_recent`, each with
      `ballots_minus_central`.
    - `headline_range` is the range to quote: low's q05 to high's q95, around the central mean.
  - `mode_split.csv`: the central scenario by mode, per county and for Maryland. County ballots
    are rounded by largest remainder so they sum to the statewide central total. Columns
    `gg22_share_*` give GG22's actual mode shares for comparison.

  Each cell is `ballots = active_2026 * invlogit(alpha_scenario + s + comp_centred + eps)`.
  comp is centred within each party (weighted by 2026 active) because its statewide level is not
  identified. The scenario `central_comp_level` uses the uncentred comp as a sensitivity.
  eps is antithetic: draws 501-1000 reuse the negated eps of draws 1-500.

  The level rule, on the logit scale per party p:
  - low = alpha*_GG22
  - high = alpha*_GG18
  - central = (1 - w_p) alpha*_GG22 + w_p alpha*_GG18

  w_p starts at 0.5 and moves 0.1 per vote, bounded to [0.2, 0.8]. Each vote is +1 for
  2018-like and -1 for 2022-like:
  1. Registration momentum. position = (m26 - m22) / (m18 - m22). The vote is +1 at 2/3 or
     above and -1 at 1/3 or below. It is 0 when |m18 - m22| < 0.002, or when the position lies
     outside [-0.5, 1.5], which is extrapolation beyond both analogues.
  2. Mail requests per registrant. Projected final GG26 sent per 2026 active registrant, over
     GG22 final sent per GG22 active registrant. The projection is an OLS line over all the
     daily snapshots, extended to the 2026-10-27 request deadline. Each county's projection is
     floored at its latest count.
     - The vote depends on the projection choice. The sent series is step-like, so the slope
       over all snapshots is much steeper than the last few days'. Holding the latest count, or
       extending only the last three snapshots, gives lower indices; see the sensitivity columns
       and scenarios.
     - No GG22 trajectory of daily sent counts exists to anchor the choice.
  3. Primary participation per registrant. GP26 ballots per active registrant over GP22's.
     OTH's vote is always 0, because the primaries are closed.

  Indices 2 and 3 vote +1 at 1.05 or above and -1 at 0.95 or below. `level_rule.csv` also
  shows the raw-count ratios beside the per-registrant indices.

  An identity check refits GG18 and GG22 from their own levels and must reproduce each party's
  actual statewide ballots within 0.1%. Within a scenario, the intervals reflect only the cell
  noise and the shift of the 2026 mix, because every draw is calibrated to a reference year. The
  spread from low to central to high is the level uncertainty. Seeded and deterministic.

`R/model.R` (sourced by `R/bootstrap.R`) holds the structure-model helpers shared with the
forecast: `model_frame()`, `build_formula()`, `fit_glmer()`, `cell_key()`, `alpha_name()`,
`structure_point()`, `make_draws()`, `calibrate()`, and the forecast's `gauss_hermite_normal()`,
`expected_rate()` and `calibrate_alpha()`.

## What `active` and `eligible` mean

- `training.csv` `active`: for certified elections, the board's official-turnout `eligible`
  measure, which is active registration on the election's books (in primaries the board's OTH
  figure covers only voters eligible for some contest, so it is much smaller than registered
  OTH). For GP26 it is `voter_registration` active voters for 2026-05. `ballots_cast` over
  `active` is the turnout rate the model fits.
- `denominators_gg26.csv` `active`: projected GG26 official-turnout `eligible` (active
  registration), i.e. 2026-09 active registration times the mean 2018/2022 ratio of the
  official-turnout figure to September active registration. This is the denominator matching
  `training.csv` `active`.
- `denominators_gg26.csv` `eligible`: `active` times (1 + mean inactive/active ratio), active
  plus inactive registrants, a broader base for comparison only.
- `denominators_gg26.csv` `cvap`: county citizen voting-age population (ACS), repeated on each
  party row; never sum it across party rows.
