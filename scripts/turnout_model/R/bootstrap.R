# Startup for every turnout-model script; each begins with
#
#   source("scripts/turnout_model/R/bootstrap.R")
#
# and is run with Rscript from the repo root. Checks the working directory, activates the renv
# project in scripts/turnout_model, and sources the shared helpers.

if (!file.exists("scripts/turnout_model/renv/activate.R")) {
  stop("run turnout-model scripts from the repo root, e.g. Rscript scripts/turnout_model/01_build_inputs.R")
}
Sys.setenv(RENV_PROJECT = normalizePath("scripts/turnout_model"))
source("scripts/turnout_model/renv/activate.R")
source("scripts/turnout_model/R/helpers.R")
