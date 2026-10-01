.PHONY: fetch parse voterfile build-db site serve test clean

fetch:
	uv run skipjack fetch

parse:
	uv run skipjack parse

# Usage: make voterfile VOTERFILE_DIR="/path/to/voter files"
voterfile:
	uv run skipjack voterfile --path "$(VOTERFILE_DIR)"

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
