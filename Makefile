.PHONY: fetch parse turnout results voterfile race build-db site serve test clean turnout-model

fetch:
	uv run skipjack fetch

parse:
	uv run skipjack parse

# Usage: make turnout   (also run by the daily workflow)
turnout:
	uv run skipjack turnout-fetch
	uv run skipjack turnout-parse

# County election results from the SBE (one-time, re-runnable; not part of the daily workflow)
# Usage: make results
results:
	uv run skipjack results-fetch
	uv run skipjack results-parse

# Usage: make voterfile VOTERFILE_DIR="/path/to/voter files"
voterfile:
	uv run skipjack voterfile --path "$(VOTERFILE_DIR)"

# Usage: make race VOTERFILE_DIR="/path/to/voter files" RACE_DIR=/path/outside/the/repo/race
# Needs R with wru, tidycensus and jsonlite, and CENSUS_API_KEY (see README, "Estimated race").
# Row-level names and predictions stay in RACE_DIR; only aggregates reach the repository.
race:
	uv run skipjack race-input --path "$(VOTERFILE_DIR)" --out "$(RACE_DIR)"
	Rscript scripts/race/predict_race.R "$(RACE_DIR)/$$(ls -1 "$(RACE_DIR)" | sort | tail -1)"
	uv run skipjack voterfile --path "$(VOTERFILE_DIR)" --race-predictions "$(RACE_DIR)/$$(ls -1 "$(RACE_DIR)" | sort | tail -1)" --force

build-db:
	uv run skipjack build-db

# GG26 turnout forecast in R, then its report (docs/turnout-model-2026.html and .md); run make build-db first.
# Needs R with the renv project in scripts/turnout_model and pandoc (RStudio's, or PANDOC_DIR=/dir/with/pandoc).
# Usage: make turnout-model
PANDOC_DIR ?= /Applications/RStudio.app/Contents/Resources/app/quarto/bin/tools/aarch64
turnout-model:
	@if [ ! -x "$(PANDOC_DIR)/pandoc" ] && ! command -v pandoc >/dev/null 2>&1; then \
		echo "turnout-model: pandoc not found in PANDOC_DIR ($(PANDOC_DIR)) or on PATH; run make turnout-model PANDOC_DIR=/dir/with/pandoc" >&2; \
		exit 1; \
	fi
	Rscript scripts/turnout_model/01_build_inputs.R
	Rscript scripts/turnout_model/02_fit.R
	Rscript scripts/turnout_model/03_compose.R
	Rscript scripts/turnout_model/04_forecast.R
	RSTUDIO_PANDOC="$(PANDOC_DIR)" Rscript -e 'source("scripts/turnout_model/R/bootstrap.R"); rmarkdown::render("scripts/turnout_model/report.Rmd", output_dir = "docs", output_file = "turnout-model-2026.html", envir = new.env())'

# Usage: make site SITE_BASE=/skipjack   (leave SITE_BASE empty for a site served from the root)
site:
	uv run skipjack build-site --base-path "$(SITE_BASE)"

serve:
	uv run skipjack serve --reload

test:
	uv run pytest

clean:
	rm -f skipjack.db skipjack_cvr.db

all: fetch parse build-db
