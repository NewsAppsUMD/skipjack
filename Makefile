.PHONY: fetch parse turnout voterfile race build-db site serve test clean

fetch:
	uv run skipjack fetch

parse:
	uv run skipjack parse

# Usage: make turnout   (also run by the daily workflow)
turnout:
	uv run skipjack turnout-fetch
	uv run skipjack turnout-parse

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
