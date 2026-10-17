# Turnout model

An R model of Maryland's November 3, 2026 general election turnout by county and party group
(DEM, REP, OTH = every other party plus unaffiliated).

## Running

Run every script from the repo root, after `make build-db` has built `skipjack.db`:

    Rscript scripts/turnout_model/01_build_inputs.R

Each script activates this directory's renv project itself: it checks that it is running from
the repo root, sets `RENV_PROJECT` to `scripts/turnout_model` and sources
`scripts/turnout_model/renv/activate.R`, then sources `R/helpers.R` (database connection, party
grouping, county names, provenance writer). Opening R inside `scripts/turnout_model/` also
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
