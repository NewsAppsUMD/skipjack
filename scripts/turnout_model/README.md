# Turnout model

An R model of Maryland's November 3, 2026 general election turnout by county and party group
(DEM, REP, OTH = every other party plus unaffiliated).

## Running

Run every script with Rscript from the repo root, after `make build-db` has built
`skipjack.db`:

    Rscript scripts/turnout_model/01_build_inputs.R

Each script begins with `source("scripts/turnout_model/R/bootstrap.R")`, which checks that it is
running from the repo root, sets `RENV_PROJECT` to `scripts/turnout_model`, sources
`scripts/turnout_model/renv/activate.R`, and then sources `R/helpers.R` (database connection,
county names, election and mode labels, party grouping, integer splitting, provenance writer). Opening R inside `scripts/turnout_model/` also
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
