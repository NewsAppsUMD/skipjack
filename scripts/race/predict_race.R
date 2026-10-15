#!/usr/bin/env Rscript
# Estimate each registered voter's race with Bayesian Improved Surname Geocoding (BISG), using
# the wru package (Imai and Khanna 2016; https://github.com/kosukeimai/wru).
#
#   Rscript scripts/race/predict_race.R <dir> [--census-csv <path>]
#
# <dir> is the directory `skipjack race-input` wrote (it holds input.csv). Keep it OUTSIDE this
# repository: input.csv and predictions.csv are row-level personal data. This script reads
# input.csv and writes next to it:
#
#   predictions.csv   vtr_id, pred_whi, pred_bla, pred_his, pred_asi, pred_oth, geo_level
#   run.json          versions, match rates and run time, read by `skipjack voterfile`
#   census_zcta.rds, census_county.rds   the Census tables wru conditioned on (offline re-runs)
#
# It also writes the county voting-age population by race (2020 Census, P.L. 94-171 table P4) to
# archives/census/md_county_vap_2020.csv. `skipjack voterfile` uses it to show how far the
# estimates sit from the Census, and registers it in archives/manifest.csv.
#
# One-time setup (needs a free Census API key: https://api.census.gov/data/key_signup.html):
#
#   export CENSUS_API_KEY=...
#   Rscript -e 'install.packages(c("wru", "tidycensus", "jsonlite"))'
#
# wru downloads its first and middle name lists from its GitHub release on first use. Tested with
# wru 3.1.0 and R 4.5.2. The Census key can live in ~/.Renviron instead of the environment.
#
# Model: names.to.use = "surname, first, middle", conditioned on the voter's ZIP code tabulation
# area (ZCTA). A voter with no ZIP, or a ZIP that is not a ZCTA, is conditioned on county
# instead (geo_level = "county"). The model is not given party, age or sex, so the estimates
# cannot echo the party and age tables they are later crossed with.

suppressPackageStartupMessages({
  library(wru)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("usage: predict_race.R <dir> [--census-csv <path>]")
dir <- normalizePath(args[1], mustWork = TRUE)
census_csv <- "archives/census/md_county_vap_2020.csv"
if ("--census-csv" %in% args) census_csv <- args[which(args == "--census-csv") + 1]

key <- Sys.getenv("CENSUS_API_KEY")
if (key == "") stop("Set CENSUS_API_KEY (free from api.census.gov)")

input_path <- file.path(dir, "input.csv")
if (!file.exists(input_path)) stop("No input.csv in ", dir)
started <- Sys.time()

voters <- read.csv(input_path, colClasses = "character", na.strings = character(0))
stopifnot(identical(
  names(voters), c("vtr_id", "surname", "first", "middle", "state", "county", "zcta")
))
message("Read ", nrow(voters), " voters")

# ---- Census tables wru conditions on ------------------------------------------------------

load_census <- function(geo) {
  path <- file.path(dir, paste0("census_", geo, ".rds"))
  if (file.exists(path)) return(readRDS(path))
  data <- get_census_data(
    key = key, states = "MD", year = "2020", census.geo = geo, age = FALSE, sex = FALSE
  )
  saveRDS(data, path)
  data
}
census_zcta <- load_census("zcta")
census_county <- load_census("county")

zcta_table <- census_zcta[["MD"]][["zcta"]]
if (is.null(zcta_table) || !"zcta" %in% names(zcta_table)) {
  stop("wru returned no ZCTA table for MD; check the wru version (needs 3.x)")
}
known_zcta <- unique(as.character(zcta_table$zcta))

# ---- Predictions --------------------------------------------------------------------------

voters$geo_level <- ifelse(voters$zcta %in% known_zcta, "zcta", "county")
voters$surname[voters$surname == ""] <- NA_character_

predict_group <- function(df, geo, census_data, names_to_use) {
  keep <- c("vtr_id", "surname", "first", "middle", "state", "county", "zcta")
  out <- predict_race(
    voter.file = df[, keep],
    census.geo = geo,
    census.key = key,
    census.data = census_data,
    year = "2020",
    model = "BISG",
    names.to.use = names_to_use,
    impute.missing = TRUE
  )
  out[, c("vtr_id", "pred.whi", "pred.bla", "pred.his", "pred.asi", "pred.oth")]
}

# County by county, so a statewide run does not hold every merge in memory at once.
predict_all <- function(df, names_to_use) {
  pieces <- list()
  for (geo in c("zcta", "county")) {
    part <- df[df$geo_level == geo, ]
    if (nrow(part) == 0) next
    census_data <- if (geo == "zcta") census_zcta else census_county
    for (county in sort(unique(part$county))) {
      chunk <- part[part$county == county, ]
      message(geo, " / county ", county, ": ", nrow(chunk), " voters (", names_to_use, ")")
      piece <- predict_group(chunk, geo, census_data, names_to_use)
      piece$geo_level <- geo
      pieces[[length(pieces) + 1]] <- piece
    }
  }
  out <- do.call(rbind, pieces)
  names(out) <- sub("\\.", "_", names(out))
  out[, c("vtr_id", "pred_whi", "pred_bla", "pred_his", "pred_asi", "pred_oth", "geo_level")]
}
pred_cols <- c("pred_whi", "pred_bla", "pred_his", "pred_asi", "pred_oth")

pred <- predict_all(voters, "surname, first, middle")

# A voter can come back NA: wru imputes a surname it does not know from the other voters in the
# same batch, so a batch with no known surname (or an unusual two-word middle name) has nothing to
# impute from. Re-run those voters inside a batch padded with voters whose surnames are known
# (dropped afterwards), first on all names, then on fewer, and record how many needed it.
known_surname <- toupper(voters$surname) %in% toupper(wru::surnames2010$surname)
retry_padded <- function(bad_ids, names_to_use) {
  parts <- list(voters[voters$vtr_id %in% bad_ids, ])
  bad <- parts[[1]]
  for (g in unique(bad$geo_level)) {
    for (county in unique(bad$county[bad$geo_level == g])) {
      pool <- voters[!voters$vtr_id %in% bad_ids & voters$geo_level == g & known_surname, ]
      pad <- head(pool, 200)
      pad$county <- county
      pad$vtr_id <- paste0("pad-", g, "-", county, "-", seq_len(nrow(pad)))
      parts[[length(parts) + 1]] <- pad
    }
  }
  out <- predict_all(do.call(rbind, parts), names_to_use)
  out[out$vtr_id %in% bad_ids, ]
}
retried <- c()
for (names_to_use in c("surname, first, middle", "surname, first", "surname")) {
  bad_ids <- pred$vtr_id[!complete.cases(pred[, pred_cols])]
  if (length(bad_ids) == 0) break
  message(length(bad_ids), " voters came back NA; retrying with padding on ", names_to_use)
  retried[names_to_use] <- length(bad_ids)
  pred <- rbind(pred[!pred$vtr_id %in% bad_ids, ], retry_padded(bad_ids, names_to_use))
}
pred <- pred[match(voters$vtr_id, pred$vtr_id), ]

bad <- !complete.cases(pred[, pred_cols])
if (any(bad)) {
  bad_zcta <- voters$zcta[match(pred$vtr_id[bad], voters$vtr_id)]
  message("NA predictions: ", sum(bad), " (", table(pred$geo_level[bad]), " by geo_level ",
          paste(names(table(pred$geo_level[bad])), collapse = "/"), ")")
  message("ZCTAs with NA predictions: ", paste(head(sort(table(bad_zcta), decreasing = TRUE), 8),
          names(head(sort(table(bad_zcta), decreasing = TRUE), 8)), sep = "x ", collapse = ", "))
  stop("Some predictions are NA; not writing")
}
if (nrow(pred) != nrow(voters)) stop("Prediction rows (", nrow(pred), ") != input rows")
if (anyDuplicated(pred$vtr_id)) stop("Duplicate vtr_id in predictions")
sums <- rowSums(pred[, grep("^pred_", names(pred))])
if (max(abs(sums - 1)) > 1e-6) stop("Some rows' probabilities do not sum to 1")

write.csv(pred, file.path(dir, "predictions.csv"), row.names = FALSE, quote = FALSE)

# ---- County voting-age population by race (the calibration table) -------------------------

if (!requireNamespace("tidycensus", quietly = TRUE)) stop("Install the tidycensus package")
wanted <- c(
  vap_total = "P4_001N", vap_hispanic = "P4_002N", vap_white = "P4_005N",
  vap_black = "P4_006N", vap_asian = "P4_008N"
)
labels <- tidycensus::load_variables(2020, "pl", cache = TRUE)
labels <- labels[labels$name %in% wanted, ]
expect <- c(
  P4_001N = "Total", P4_002N = "Hispanic or Latino", P4_005N = "White alone",
  P4_006N = "Black or African American alone", P4_008N = "Asian alone"
)
for (v in names(expect)) {
  row <- labels[labels$name == v, ]
  if (nrow(row) != 1 || !grepl(expect[[v]], row$label, fixed = TRUE) ||
      !grepl("18 YEARS AND OVER", row$concept, fixed = TRUE)) {
    stop("Census variable ", v, " is not what this script expects: ", row$label, " / ", row$concept)
  }
}
vap <- tidycensus::get_decennial(
  geography = "county", state = "MD", year = 2020, sumfile = "pl",
  variables = unname(wanted), key = key, output = "wide"
)
vap <- data.frame(
  county_fips = substr(vap$GEOID, 3, 5),
  county = sub(", Maryland$", "", vap$NAME),
  vap_total = vap$P4_001N, vap_white = vap$P4_005N, vap_black = vap$P4_006N,
  vap_hispanic = vap$P4_002N, vap_asian = vap$P4_008N
)
vap$vap_other <- vap$vap_total - vap$vap_white - vap$vap_black - vap$vap_hispanic - vap$vap_asian
if (nrow(vap) != 24 || any(vap$vap_other < 0)) stop("County VAP table looks wrong")
dir.create(dirname(census_csv), recursive = TRUE, showWarnings = FALSE)
write.csv(vap, census_csv, row.names = FALSE)

# ---- Run record ---------------------------------------------------------------------------

surname_known <- toupper(voters$surname) %in% toupper(wru::surnames2010$surname)
run <- list(
  method = "BISG (wru predict_race, model = BISG)",
  names_used = "surname, first, middle",
  conditioned_on = "ZIP code tabulation area, county where there is none",
  census_year = "2020",
  wru_version = as.character(packageVersion("wru")),
  r_version = R.version.string,
  voters = nrow(voters),
  share_zcta = mean(voters$geo_level == "zcta"),
  share_county_fallback = mean(voters$geo_level == "county"),
  share_surname_on_2010_list = mean(surname_known, na.rm = TRUE),
  share_blank_surname = mean(is.na(voters$surname)),
  voters_na_on_all_names = unname(retried["surname, first, middle"]),
  voters_na_on_surname_and_first = unname(retried["surname, first"]),
  voters_na_on_surname_alone = unname(retried["surname"]),
  census_county_csv = census_csv,
  minutes = as.numeric(difftime(Sys.time(), started, units = "mins"))
)
write(toJSON(run, auto_unbox = TRUE, pretty = TRUE, digits = 6), file.path(dir, "run.json"))
message("Done in ", round(run$minutes, 1), " minutes; wrote predictions.csv and run.json")
