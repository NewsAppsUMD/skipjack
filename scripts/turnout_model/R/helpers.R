# Shared helpers for the turnout-model scripts (scripts/turnout_model/0*.R).
#
# Sourced by R/bootstrap.R, which every script sources first (run with Rscript from the repo
# root). Helpers here: the database connection, the 24 canonical county names, election and
# mode labels, party grouping, Census county name normalization, integer splitting, and the
# provenance writer used for every file the scripts write.

suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(jsonlite)
})

DB_PATH <- "skipjack.db"
INPUTS_DIR <- "data/turnout_model/inputs"

CANONICAL_COUNTIES <- c(
  "Allegany", "Anne Arundel", "Baltimore City", "Baltimore County", "Calvert", "Caroline",
  "Carroll", "Cecil", "Charles", "Dorchester", "Frederick", "Garrett", "Harford", "Howard",
  "Kent", "Montgomery", "Prince George's", "Queen Anne's", "Somerset", "St. Mary's", "Talbot",
  "Washington", "Wicomico", "Worcester"
)

PARTY_GROUPS <- c("DEM", "REP", "OTH")

# Elections in the model, in the order rows are written.
ELECTION_LEVELS <- c("GG18", "GG22", "GP22", "PP24", "PG24", "GP26")

# turnout_counts mode measures and the column names they become.
MODE_COLS <- c("election-day" = "election_day", "early-voting" = "early_voting",
               "vote-by-mail" = "vote_by_mail", "provisional" = "provisional")

# Open a read-only connection to skipjack.db; the caller disconnects.
db_connect <- function(path = DB_PATH) {
  if (!file.exists(path)) stop("missing ", path, "; build it with `make build-db`")
  DBI::dbConnect(RSQLite::SQLite(), path, flags = RSQLite::SQLITE_RO)
}

# Run a query and return a tibble.
db_query <- function(con, sql, params = NULL) {
  out <- if (is.null(params)) DBI::dbGetQuery(con, sql) else DBI::dbGetQuery(con, sql, params = params)
  tibble::as_tibble(out)
}

# DEM and REP keep their own group; every other party code (UNA, UNAF, GRN, LIB, WCP, BAR,
# NLM, OTH) is OTH, matching the board's mail-in files. Total rows (any code matching TOTAL or
# ALL, in any case) and NA are not parties and are refused rather than folded into OTH.
party_group <- function(party) {
  bad <- is.na(party) | grepl("TOTAL|ALL", party, ignore.case = TRUE)
  if (any(bad)) stop("party_group() got total-like or NA party codes: ", paste(unique(party[bad]), collapse = ", "))
  dplyr::case_when(party == "DEM" ~ "DEM", party == "REP" ~ "REP", TRUE ~ "OTH")
}

# Stop unless the set of `counties` is exactly the 24 canonical names.
check_counties <- function(counties, what) {
  bad <- setdiff(unique(counties), CANONICAL_COUNTIES)
  missing <- setdiff(CANONICAL_COUNTIES, counties)
  if (length(bad) || length(missing)) {
    stop(what, ": non-canonical counties [", paste(bad, collapse = ", "),
         "], missing [", paste(missing, collapse = ", "), "]")
  }
  invisible(TRUE)
}

# Census NAME ("Baltimore city, Maryland", "St. Mary's County, Maryland") to canonical name.
normalize_census_county <- function(name) {
  x <- sub(",\\s*Maryland$", "", name)
  x <- dplyr::case_when(
    x == "Baltimore city" ~ "Baltimore City",
    x == "Baltimore County" ~ "Baltimore County",
    TRUE ~ sub(" County$", "", x)
  )
  bad <- name[!x %in% CANONICAL_COUNTIES]
  if (length(bad)) stop("Census county names that do not map: ", paste(bad, collapse = "; "))
  x
}

# Stop with a message when a check fails; return the check's name so it can be logged.
check <- function(ok, name, detail = NULL) {
  if (!isTRUE(all(ok))) stop("check failed: ", name, if (!is.null(detail)) paste0(" -- ", detail))
  name
}

# Doubles as text with at most 15 significant digits, never in scientific notation, so a
# rounded value prints as itself (11.3609, not 11.360900000000001). NA stays NA.
format_double <- function(x) {
  out <- sprintf("%.15g", x)
  sci <- grepl("e", out, fixed = TRUE) & !is.na(x)
  out[sci] <- vapply(x[sci], function(v) format(v, digits = 15, scientific = FALSE, drop0trailing = TRUE), "")
  out[is.na(x)] <- NA_character_
  out
}

# Write a tidy CSV deterministically (rows already arranged by the caller). Double columns are
# written with format_double(); dates and other classed columns are left to readr.
write_input_csv <- function(df, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  df <- dplyr::mutate(df, dplyr::across(dplyr::where(function(x) is.double(x) && !is.object(x)), format_double))
  readr::write_csv(df, path, na = "")
  invisible(path)
}

# Split an integer total into integer parts proportional to `shares` (largest remainder), so
# the parts sum exactly to the total. Ties break by position.
split_integer <- function(total, shares) {
  if (length(total) != 1 || is.na(total) || anyNA(shares) || any(shares < 0)) {
    stop("split_integer() needs one non-missing total and non-negative, non-missing shares")
  }
  if (sum(shares) == 0) stop("split_integer() got shares that sum to zero (total ", total, ")")
  raw <- as.numeric(total) * as.numeric(shares) / sum(as.numeric(shares))
  base <- floor(raw)
  left <- total - sum(base)
  if (left > 0) {
    bump <- order(-(raw - base), seq_along(raw))[seq_len(left)]
    base[bump] <- base[bump] + 1
  }
  base
}

# The running script's path as given to Rscript (--file=), for provenance `generated_by`.
script_path <- function() {
  f <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
  if (length(f) != 1) stop("script_path() works only under Rscript; pass generated_by explicitly")
  f
}

# Write <name>_provenance.json next to a written <name>.csv or <name>.csv.gz. `generated_by` is the script
# that wrote it, `sources` lists tables and queries, `source_ids` the skipjack source ids,
# `checks` the names of the checks that passed.
write_provenance <- function(path, description, sources, generated_by, source_ids = character(),
                             checks = character(), notes = NULL, extra = list()) {
  prov <- c(
    list(
      file = path,
      description = description,
      generated_by = generated_by,
      run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
      sources = sources,
      source_ids = sort(unique(source_ids)),
      checks_passed = as.list(checks)
    ),
    if (!is.null(notes)) list(notes = notes),
    extra
  )
  out <- sub("\\.csv(\\.gz)?$", "_provenance.json", path)
  if (identical(out, path)) stop("write_provenance() needs a .csv or .csv.gz path, got ", path)
  writeLines(jsonlite::toJSON(prov, auto_unbox = TRUE, pretty = TRUE, null = "null"), out)
  invisible(out)
}

# Collect the source_id values of the rows a query touched (a vector, deduplicated).
ids_of <- function(...) sort(unique(unlist(lapply(list(...), function(d) d$source_id))))
