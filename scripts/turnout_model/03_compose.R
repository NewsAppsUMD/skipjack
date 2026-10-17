#!/usr/bin/env Rscript
# Compute the turnout model's composition factors: how the 2026 electorate of each county x
# party group differs from its 2022 electorate because of who registered since.
#
#   Rscript scripts/turnout_model/03_compose.R          (from the repo root)
#
# For each county x party cell (DEM, REP, OTH) the change on the logit scale is
# comp = factor_age + factor_new, which the forecast adds to the cell's linear predictor:
#
# - factor_age (A): logit(R26) - logit(R22), where R is the cell's expected 2022-midterm
#   participation under its 2026 and its 2022 age mix, both in age-at-election terms with one
#   propensity schedule: r_cp(b) is the 2022-GG rate by age on 2022 election day (the file's
#   current-age bands spread over single years and shifted back four years; county x age, tilted
#   to the party with statewide party x age rates shifted the same way). The 2026 mix is today's
#   registrants by current age (close to age on 2026-11-03); the 2022 mix is today's registrants
#   scaled by the share of each band registered in time for 2022-GG relative to 2026-GP, then
#   shifted back four years.
# - factor_new (N): the extra pull of recent registrants beyond their age,
#   logit(R26 * (1 - (s26 - s22) * (1 - rho_gen))) - logit(R26), with s26 the share of the
#   cell registered 2023-2026, s22 the share of those registered by 2022 who registered
#   2019-2022, and rho_gen = sqrt(rho), rho the 2026-GP rate of people registered since the 2024
#   general relative to their age-expected rate.
#
# Source: skipjack.db voter_file_counts, snapshot 2026-08-12 (participation among current
# voters, used only as ratios within the snapshot). Writes
# data/turnout_model/gg26/composition_factors.csv and its provenance. Deterministic.

source("scripts/turnout_model/R/bootstrap.R")

SCRIPT <- tryCatch(script_path(), error = function(e) "scripts/turnout_model/03_compose.R")
OUT_DIR <- "data/turnout_model/gg26"
SNAPSHOT <- "2026-08-12"
SUPPRESS_MAX <- 9                       # suppressed counts are 1 to 9
RATE_CAP <- 0.99
CLAMP <- 1e-6
COMP_LIMIT <- 0.25
K_MAX <- 1.05
AGE_BANDS <- c("18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+", "Unknown")
OLDER_BANDS <- c("35-44", "45-54", "55-64", "65-74", "75+")
# new_registrants bands (age at registration) -> turnout_by_age bands (age today).
NEW_BAND_MAP <- c("18-22" = "18-24", "23-29" = "25-34", "30+" = "35+")
NEW_YEARS <- 2023:2026                  # s26 numerator
OLD_YEARS <- 2019:2022                  # s22 numerator
PROFILE_YEARS <- 2025:2026              # new-registrant age profile
# Years between the snapshot (2026-08-12) and the 2022 general (2022-11-08): 3.75, taken as 4.
SHIFT_YEARS <- 4
OLDEST <- 94                            # 75+ is spread over 75-94
BELOW18_MAX <- 0.005

HABIT_NOTE <- paste(
  "No habit-mix factor: metric voter_types classifies each voter over the elections they were",
  "eligible for in this one snapshot, so there is no 2022-era counterpart to compare with.",
  "factor_new is the measurable part of the habit shift."
)
RHO_GEN_NOTE <- paste(
  "rho_gen = sqrt(rho) is a judgment: primaries exaggerate the gap between new and established",
  "voters, so the general-election propensity of new registrants is taken halfway (on the log",
  "scale) between rho and 1. factor_new_no_penalty uses rho_gen = 1, factor_new_full_penalty",
  "rho_gen = rho."
)
S22_NOTE <- paste(
  "Recent cohorts lose more members to moves, so in today's file the 2019-2022 cohort is",
  "understated relative to its size in 2022: s22 is understated and factor_new is biased toward",
  "a larger penalty. A known limitation, not corrected."
)

# ---- reading -----------------------------------------------------------------------------

vfc <- function(con, metric) {
  db_query(con, paste(
    "SELECT county, party_group, election, age_band, reg_year, reg_window, status,",
    "count, eligible FROM voter_file_counts WHERE snapshot_date = ? AND metric = ?"
  ), params = list(SNAPSHOT, metric))
}

# Model party groups from file party groups: DEM and REP as they are, OTH = ALL - DEM - REP so
# that suppressed UNA and minor-party cells do not matter. `vals` are value columns. Run on the
# output of fill_absent(), so absent rows are already 0; a NULL (suppressed, 1-9) stays NA and
# makes the derived OTH NA too.
to_model_groups <- function(df, keys, vals) {
  wide <- df |>
    filter(party_group %in% c("ALL", "DEM", "REP")) |>
    select(all_of(c(keys, "party_group", vals))) |>
    pivot_longer(all_of(vals), names_to = ".value_name", values_to = ".v") |>
    mutate(.col = paste0(.value_name, "__", party_group)) |>
    select(-party_group, -.value_name) |>
    pivot_wider(names_from = .col, values_from = .v)
  out <- list()
  for (p in c("DEM", "REP", "OTH")) {
    d <- wide[keys]
    d$party_group <- p
    for (v in vals) {
      g <- function(pg) {
        x <- wide[[paste0(v, "__", pg)]]
        if (is.null(x)) stop("to_model_groups(): no ", pg, " rows for ", v)
        x
      }
      d[[v]] <- if (p == "OTH") g("ALL") - g("DEM") - g("REP") else g(p)
    }
    out[[p]] <- d
  }
  bind_rows(out)
}

# Add the ALL/DEM/REP rows a group lacks, as 0: the pipeline's count_by only emits groups with
# members, so an absent row is a true zero. Suppressed rows (NULL) stay NA.
fill_absent <- function(df, keys, vals, groups = c("ALL", "DEM", "REP")) {
  grid <- df |> distinct(across(all_of(setdiff(keys, "party_group"))))
  full <- tidyr::crossing(grid, party_group = groups)
  full |>
    left_join(df |> filter(party_group %in% groups) |> mutate(.present = TRUE),
              by = keys) |>
    mutate(across(all_of(vals), ~ ifelse(is.na(.present), 0, .x))) |>
    select(-.present)
}

# Lower and upper bounds of a value that may be suppressed (NA -> 1..9).
lo_of <- function(x) ifelse(is.na(x), 1, x)
hi_of <- function(x) ifelse(is.na(x), SUPPRESS_MAX, x)

# ---- factor A: age mix ------------------------------------------------------------------

# Map from a current-age band to bands of age on 2022 election day. Each band's counts are
# spread uniformly over its single years of current age, shifted back SHIFT_YEARS and re-binned.
# Exception: 2022-era quantities in the current 18-24 band (2022-GG count and eligible, and the
# 2022 mix) are spread over current ages 22-24 only, because nobody younger than about 21.75
# today was 18 by 2022-11-08, and 2022-GG eligible requires that; spreading them over 18-24
# would put four sevenths of them below 18. Unknown stays Unknown.
shift_map <- function() {
  lo <- c("18-24" = 22, "25-34" = 25, "35-44" = 35, "45-54" = 45, "55-64" = 55, "65-74" = 65, "75+" = 75)
  hi <- c("18-24" = 24, "25-34" = 34, "35-44" = 44, "45-54" = 54, "55-64" = 64, "65-74" = 74, "75+" = OLDEST)
  bin <- function(age) {
    cut(age, c(-Inf, 17, 24, 34, 44, 54, 64, 74, Inf), right = TRUE,
        labels = c("below 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"))
  }
  m <- bind_rows(lapply(names(lo), function(b) {
    yrs <- lo[[b]]:hi[[b]]
    tibble(age_band = b, band22 = as.character(bin(yrs - SHIFT_YEARS)), w = 1 / length(yrs))
  })) |>
    group_by(age_band, band22) |> summarise(w = sum(w), .groups = "drop")
  bind_rows(m, tibble(age_band = "Unknown", band22 = "Unknown", w = 1))
}

# Shift value columns `vals` of df (current-age bands in age_band) to age on 2022 election day
# (band22, renamed back to age_band). Rows in other bands (Under 18) are dropped.
shift_to_2022 <- function(df, keys, vals) {
  df |>
    filter(age_band %in% AGE_BANDS) |>
    inner_join(shift_map(), by = "age_band", relationship = "many-to-many") |>
    mutate(across(all_of(vals), ~ .x * w)) |>
    group_by(across(all_of(c(keys, "band22")))) |>
    summarise(across(all_of(vals), sum), .groups = "drop") |>
    rename(age_band = band22)
}

age_rates <- function(con) {
  tba <- vfc(con, "turnout_by_age") |> filter(age_band %in% AGE_BANDS)
  check_counties(setdiff(tba$county, "Maryland"), "turnout_by_age counties")
  rate_c <- function(el) {
    tba |> filter(election == el) |>
      transmute(county, age_band, count, eligible,
                # count 0 is observed even when eligible is suppressed: the rate is then 0
                rate = ifelse(!is.na(count) & count == 0, 0, count / eligible))
  }
  gg22_raw <- rate_c("2022-GG"); gp26 <- rate_c("2026-GP")
  # 2022-GG rates in bands of age on 2022 election day (shifted count / shifted eligible).
  gg22 <- gg22_raw |> select(county, age_band, count, eligible) |>
    shift_to_2022("county", c("count", "eligible")) |>
    mutate(rate = ifelse(!is.na(count) & count == 0, 0, count / eligible))
  k <- gg22_raw |> select(county, age_band, elig22 = eligible) |>
    inner_join(gp26 |> select(county, age_band, elig26 = eligible), by = c("county", "age_band")) |>
    mutate(k = elig22 / elig26)
  list(gg22 = gg22, gg22_raw = gg22_raw, gp26 = gp26, k = k, tba = tba)
}

# Statewide party tilt t_p(a) = party rate / all-party rate, per election (OTH = ALL - DEM - REP).
# For 2022-GG (shift = TRUE) the statewide counts are first shifted to age on 2022 election day.
party_tilt <- function(con, el, shift = FALSE) {
  tap <- vfc(con, "turnout_by_age_party") |> filter(election == el, age_band %in% AGE_BANDS)
  stopifnot(all(is.na(tap$county)))
  tap$county <- "Maryland"
  if (shift) tap <- shift_to_2022(tap, c("county", "party_group"), c("count", "eligible"))
  m <- to_model_groups(fill_absent(tap, c("county", "age_band", "party_group"), c("count", "eligible")),
                       c("county", "age_band"), c("count", "eligible"))
  all <- tap |> filter(party_group == "ALL") |> transmute(age_band, all_rate = count / eligible)
  check(!anyNA(m$count) && !anyNA(m$eligible), paste("turnout_by_age_party", el, "DEM/REP/ALL unsuppressed"))
  m |> transmute(party_group, age_band, party_rate = count / eligible) |>
    left_join(all, by = "age_band") |>
    mutate(tilt = ifelse(all_rate == 0, NA_real_, party_rate / all_rate))
}

# ---- main -------------------------------------------------------------------------------

main <- function() {
  con <- db_connect()
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  checks <- character()
  ck <- function(ok, name, detail = NULL) checks <<- c(checks, check(ok, name, detail))
  cells <- tidyr::crossing(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS)

  ar <- age_rates(con)
  ck(sum(is.na(ar$gg22$rate)) == 0, "every county x age-at-2022 band has a 2022-GG rate (count 0 rates are 0)")
  ck(all(abs(shift_map() |> group_by(age_band) |> summarise(w = sum(w)) |> pull(w) - 1) < 1e-12),
     "the age shift keeps every current band's mass")
  # Mass that lands below 18 at the 2022 election: zero by construction with the 22-24 spread;
  # a uniform 18-24 spread would place 4/7 of the band's 2022-GG eligible there.
  below18 <- ar$gg22 |> filter(county != "Maryland") |> group_by(county) |>
    summarise(share = sum(eligible[age_band == "below 18"], na.rm = TRUE) / sum(eligible, na.rm = TRUE))
  ck(all(below18$share < BELOW18_MAX), "shifted 2022-GG eligible below age 18 is under 0.5% of each county's eligible")
  uniform_below18 <- ar$gg22_raw |> filter(county != "Maryland") |> group_by(county) |>
    summarise(share = (4 / 7) * sum(eligible[age_band == "18-24"]) / sum(eligible, na.rm = TRUE))
  # Unknown age: statewide 2022-GG eligible equals 2026-GP eligible, so every county's does
  # (each county's 2022 count is at most its 2026 count): k = 1 where suppressed.
  st_unk <- ar$k |> filter(county == "Maryland", age_band == "Unknown")
  ck(st_unk$elig22 == st_unk$elig26, "statewide Unknown-age eligible is equal for 2022-GG and 2026-GP")
  k <- ar$k |> filter(county != "Maryland") |>
    mutate(k = ifelse(age_band == "Unknown" & is.na(elig22) & is.na(elig26), 1, k))
  tilt22 <- party_tilt(con, "2022-GG", shift = TRUE)
  tilt26 <- party_tilt(con, "2026-GP")

  # Cell rates r_cp(a): county x age rate times the party tilt, capped below RATE_CAP.
  cell_rate <- function(rates, tilt) {
    tidyr::crossing(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS, age_band = AGE_BANDS) |>
      left_join(rates |> select(county, age_band, r_c = rate), by = c("county", "age_band")) |>
      left_join(tilt |> select(party_group, age_band, tilt), by = c("party_group", "age_band")) |>
      mutate(r = ifelse(r_c == 0, 0, pmin(r_c * tilt, RATE_CAP)))
  }
  r22 <- cell_rate(ar$gg22, tilt22)
  r26gp <- cell_rate(ar$gp26, tilt26)
  # A county with no Unknown-age registrants has no Unknown row (absent = no members); every other
  # band must have a rate.
  ck(!anyNA(r22$r[r22$age_band != "Unknown"]) && !anyNA(r26gp$r[r26gp$age_band != "Unknown"]),
     "every cell x age band 18-24 to 75+ has a tilted rate")

  # 2026 mix: age_by_party, all statuses.
  abp_raw <- vfc(con, "age_by_party") |> filter(county != "Maryland")
  abp <- to_model_groups(fill_absent(abp_raw, c("county", "age_band", "party_group"), "count"),
                         c("county", "age_band"), "count")
  # bounds for the reconciliation check
  abp_b <- fill_absent(abp_raw, c("county", "age_band", "party_group"), "count") |>
    mutate(lo = lo_of(count), hi = hi_of(count)) |>
    select(county, age_band, party_group, lo, hi) |>
    pivot_wider(names_from = party_group, values_from = c(lo, hi))
  abp_bounds <- bind_rows(
    abp_b |> transmute(county, age_band, party_group = "DEM", lo = lo_DEM, hi = hi_DEM),
    abp_b |> transmute(county, age_band, party_group = "REP", lo = lo_REP, hi = hi_REP),
    abp_b |> transmute(county, age_band, party_group = "OTH",
                       lo = pmax(0, lo_ALL - hi_DEM - hi_REP), hi = hi_ALL - lo_DEM - lo_REP)
  )
  pbc_raw <- vfc(con, "party_by_county") |> filter(county != "Maryland")
  pbc <- to_model_groups(
    fill_absent(pbc_raw |> group_by(county, party_group) |>
                  summarise(count = if (anyNA(count)) NA_real_ else sum(count), .groups = "drop"),
                c("county", "party_group"), "count"),
    "county", "count") |> rename(cell_total = count)
  ck(!anyNA(pbc$cell_total), "party_by_county totals (active + inactive) are unsuppressed for every cell")
  recon <- abp_bounds |> group_by(county, party_group) |>
    summarise(lo = sum(lo), hi = sum(hi), .groups = "drop") |>
    inner_join(pbc, by = c("county", "party_group"))
  ck(nrow(recon) == 72 && all(recon$cell_total >= recon$lo & recon$cell_total <= recon$hi),
     "age bands of age_by_party (with Under 18; suppressed bands as 1-9) sum to the cell's party_by_county total")
  exact <- abp |> group_by(county, party_group) |>
    summarise(s = sum(count), .groups = "drop") |> inner_join(pbc, by = c("county", "party_group"))
  ck(all(is.na(exact$s) | exact$s == exact$cell_total),
     "where no band is suppressed, age_by_party bands sum exactly to the party_by_county total")

  e26 <- abp |> filter(age_band %in% AGE_BANDS) |> rename(e26 = count)
  unknown_dropped <- e26 |> filter(is.na(e26)) |> select(county, party_group, age_band)
  ck(all(unknown_dropped$age_band == "Unknown"), "only Unknown-age bands of e26 are suppressed")
  e26 <- e26 |> filter(!is.na(e26))

  used_k <- e26 |> left_join(k |> select(county, age_band, k), by = c("county", "age_band")) |>
    filter(e26 > 0) |> pull(k)
  ck(!anyNA(used_k) && all(used_k > 0 & used_k <= K_MAX), "k_c(a) in (0, 1.05] for every band in use")

  # 2022 mix, in bands of age on 2022 election day: today's registrants scaled by k_c(a) (people
  # registered in time for 2022 as a share of those in time for 2026-GP, same current-age band),
  # then shifted back four years. Summed over party this is close to the county's shifted 2022-GG
  # eligible, and it keeps each party's own age profile.
  e22 <- e26 |>
    left_join(k |> select(county, age_band, k), by = c("county", "age_band")) |>
    mutate(e22 = e26 * k) |>
    shift_to_2022(c("county", "party_group"), "e22")
  # One propensity schedule: 2022-GG rates by age on election day, applied to the 2022 mix
  # (ages in 2022) and to the 2026 mix (today's age, close to age on 2026-11-03).
  rate_of <- function(mix, val) {
    mix |> left_join(r22 |> select(county, party_group, age_band, r), by = c("county", "party_group", "age_band")) |>
      group_by(county, party_group) |>
      summarise(n = sum(.data[[val]]), v = sum(.data[[val]] * r), .groups = "drop")
  }
  m26 <- rate_of(e26, "e26"); m22 <- rate_of(e22 |> filter(age_band != "below 18"), "e22")
  ck(!anyNA(m26$v) && !anyNA(m22$v), "every band in use has a rate")
  age_cells <- m26 |> rename(n26 = n, v26 = v) |>
    inner_join(m22 |> rename(n22 = n, v22 = v), by = c("county", "party_group")) |>
    mutate(R26 = v26 / n26, R22 = v22 / n22)
  # Sanity: with the 2026 mix set to the 2022 mix, factor_age is exactly zero.
  m_same <- rate_of(e22 |> filter(age_band != "below 18") |> rename(e26 = e22), "e26")
  same <- m_same |> inner_join(m22, by = c("county", "party_group"), suffix = c("_a", "_b"))
  ck(all(abs(qlogis(same$v_a / same$n_a) - qlogis(same$v_b / same$n_b)) < 1e-9),
     "factor_age is 0 (to 1e-9) when the 2026 mix equals the shifted 2022 mix")

  # ---- factor N: shares --------------------------------------------------------------------
  rc_raw <- vfc(con, "registration_cohorts") |> filter(county != "Maryland")
  ck(!anyNA(rc_raw$reg_year), "registration_cohorts carries no NULL reg_year rows (the metric excludes them)")
  recent <- rc_raw |> filter(reg_year %in% c(OLD_YEARS, NEW_YEARS)) |>
    fill_absent(c("county", "party_group", "reg_year"), "count")
  rec <- to_model_groups(recent, c("county", "reg_year"), "count") |>
    mutate(window = ifelse(reg_year %in% NEW_YEARS, "new", "old"))
  ck(!anyNA(rec$count), "registration_cohorts 2019-2026 counts are unsuppressed for DEM, REP and ALL")
  shares <- rec |> group_by(county, party_group) |>
    summarise(n_new = sum(count[window == "new"]), n_old = sum(count[window == "old"]), .groups = "drop") |>
    inner_join(pbc, by = c("county", "party_group")) |>
    mutate(by_2022 = cell_total - n_new, s26 = n_new / cell_total, s22 = n_old / by_2022)
  ck(nrow(shares) == 72, "shares for 72 cells")
  # registration_cohorts vs party_by_county: statewide rows are computed before suppression, so
  # their difference is exactly the number of registrants with a NULL reg_year. In the county
  # rows the gap is that plus suppressed old years (1-9 each).
  st_tot <- function(m) db_query(con, "SELECT SUM(count) AS n FROM voter_file_counts WHERE snapshot_date = ?
    AND metric = ? AND county = 'Maryland' AND party_group = 'ALL'", params = list(SNAPSHOT, m))$n
  null_reg_year <- st_tot("party_by_county") - st_tot("registration_cohorts")
  ck(null_reg_year >= 0 && null_reg_year <= 0.001 * st_tot("party_by_county"),
     "statewide NULL reg_year registrants (party_by_county minus registration_cohorts) are under 0.1% of registrants")
  gap <- rc_raw |> filter(party_group %in% c("ALL", "DEM", "REP")) |> group_by(county, party_group) |>
    summarise(known = sum(count, na.rm = TRUE), n_supp = sum(is.na(count)), .groups = "drop") |>
    inner_join(pbc_raw |> group_by(county, party_group) |> summarise(total = sum(count), .groups = "drop"),
               by = c("county", "party_group")) |>
    mutate(gap = total - known)
  ck(all(gap$gap >= gap$n_supp & gap$gap <= SUPPRESS_MAX * gap$n_supp + null_reg_year),
     "county registration_cohorts sums fall short of party_by_county totals by suppressed old years plus at most the statewide NULL reg_year count")

  # ---- factor N: rho ---------------------------------------------------------------------
  nvv_raw <- vfc(con, "new_voters_voted")
  nvv_c <- nvv_raw |> filter(county != "Maryland")
  nvv <- to_model_groups(fill_absent(nvv_c, c("county", "party_group"), c("count", "eligible")),
                         "county", c("count", "eligible"))
  nvv_all <- nvv_c |> filter(party_group == "ALL") |> select(county, count, eligible)
  ck(all(nvv_raw$eligible[nvv_raw$party_group %in% c("DEM", "REP", "ALL")] > 0, na.rm = TRUE),
     "new_voters_voted eligible is positive for DEM, REP and ALL")
  st_nvv <- nvv_raw |> filter(county == "Maryland", party_group %in% c("DEM", "REP")) |> arrange(party_group)
  sum_nvv <- nvv |> filter(party_group %in% c("DEM", "REP")) |> group_by(party_group) |>
    summarise(count = sum(count), eligible = sum(eligible)) |> arrange(party_group)
  ck(!anyNA(sum_nvv$count) && all(sum_nvv$count == st_nvv$count & sum_nvv$eligible == st_nvv$eligible),
     "county new_voters_voted DEM and REP sum to the statewide rows")

  # Age profile of reg_year 2025-2026 first-time registrants (age at registration).
  nr_raw <- vfc(con, "new_registrants") |> filter(county != "Maryland", reg_year %in% PROFILE_YEARS)
  nr_sum <- nr_raw |> filter(party_group %in% c("ALL", "DEM", "REP")) |>
    group_by(county, party_group, age_band) |>
    summarise(count = if (anyNA(count)) NA_real_ else sum(count), .groups = "drop") |>
    fill_absent(c("county", "party_group", "age_band"), "count")
  prof_cell <- nr_sum |> filter(party_group %in% c("DEM", "REP")) |>
    group_by(county, party_group) |> mutate(ok = !anyNA(count), w = count / sum(count)) |> ungroup()
  prof_all <- nr_sum |> filter(party_group == "ALL") |> group_by(county) |>
    mutate(w_all = count / sum(count)) |> ungroup() |> select(county, age_band, w_all)
  ck(!anyNA(prof_all$w_all), "county ALL new-registrant age profiles are unsuppressed")
  profile <- prof_cell |> left_join(prof_all, by = c("county", "age_band")) |>
    mutate(w_use = ifelse(ok, w, w_all)) |>
    group_by(county, party_group) |> mutate(profile_source = ifelse(all(ok), "cell", "county ALL")) |> ungroup()
  profile_all <- prof_all |> rename(w_use = w_all) |> mutate(party_group = "ALL", profile_source = "county ALL")

  # Age-expected 2026-GP rate per new-registrant band: 18-22 -> 18-24, 23-29 -> 25-34, 30+ -> the
  # cell's 35+ average (weighted by the cell's current registrants in those bands).
  band_rate <- function(rates_cell, weights) {
    young <- rates_cell |> filter(age_band %in% c("18-24", "25-34")) |>
      transmute(county, party_group, nr_band = names(NEW_BAND_MAP)[match(age_band, NEW_BAND_MAP)], rate = r)
    older <- rates_cell |> filter(age_band %in% OLDER_BANDS) |>
      inner_join(weights, by = c("county", "party_group", "age_band")) |>
      group_by(county, party_group) |> summarise(rate = sum(w * r) / sum(w), .groups = "drop") |>
      mutate(nr_band = "30+")
    bind_rows(young, older)
  }
  w_cell <- e26 |> select(county, party_group, age_band, w = e26)
  br_cell <- band_rate(r26gp |> filter(party_group %in% c("DEM", "REP")), w_cell)
  # County ALL: untilted county rate, weighted by all registrants.
  r_all <- ar$gp26 |> filter(county != "Maryland") |> transmute(county, party_group = "ALL", age_band, r = rate)
  w_all <- abp_raw |> filter(party_group == "ALL", age_band %in% OLDER_BANDS) |>
    transmute(county, party_group = "ALL", age_band, w = count)
  br_all <- band_rate(r_all, w_all)

  expect <- function(prof, br, obs) {
    prof |> select(county, party_group, nr_band = age_band, w_use, profile_source) |>
      inner_join(br, by = c("county", "party_group", "nr_band")) |>
      group_by(county, party_group, profile_source) |>
      summarise(exp_rate = sum(w_use * rate), .groups = "drop") |>
      inner_join(obs, by = c("county", "party_group")) |>
      mutate(obs_rate = count / eligible, exp_count = exp_rate * eligible, rho = obs_rate / exp_rate)
  }
  rho_dr <- expect(profile, br_cell, nvv |> filter(party_group %in% c("DEM", "REP")))
  rho_all <- expect(profile_all, br_all, nvv_all |> mutate(party_group = "ALL"))
  ck(nrow(rho_dr) == 48 && nrow(rho_all) == 24, "rho inputs for 48 DEM/REP cells and 24 county ALL rows")
  ck(!anyNA(rho_all$rho), "county ALL rho is computable everywhere")
  pooled <- rho_dr |> filter(!is.na(count))
  rho_pooled <- sum(pooled$count) / sum(pooled$exp_count)
  # statewide ALL rho (last fallback): summed county ALL rows
  rho_state_all <- sum(rho_all$count) / sum(rho_all$exp_count)

  rho_tbl <- cells |>
    left_join(rho_dr |> select(county, party_group, rho_cell = rho, profile_source), by = c("county", "party_group")) |>
    left_join(rho_all |> select(county, rho_county_all = rho), by = "county") |>
    mutate(
      rho_source = case_when(
        party_group == "OTH" ~ "statewide DEM/REP pooled",
        !is.na(rho_cell) ~ "cell",
        !is.na(rho_county_all) ~ "county ALL",
        TRUE ~ "statewide ALL"
      ),
      rho = case_when(
        rho_source == "statewide DEM/REP pooled" ~ rho_pooled,
        rho_source == "cell" ~ rho_cell,
        rho_source == "county ALL" ~ rho_county_all,
        TRUE ~ rho_state_all
      ),
      profile_source = ifelse(party_group == "OTH", NA_character_, profile_source)
    )

  # ---- assemble ----------------------------------------------------------------------------
  factor_new <- function(R26, s26, s22, rho_gen) {
    arg <- R26 * (1 - (s26 - s22) * (1 - rho_gen))
    qlogis(pmin(pmax(arg, CLAMP), 1 - CLAMP)) - qlogis(R26)
  }
  out <- cells |>
    inner_join(age_cells, by = c("county", "party_group")) |>
    inner_join(shares |> select(county, party_group, n_new, n_old, by_2022, cell_total, s26, s22),
               by = c("county", "party_group")) |>
    inner_join(rho_tbl |> select(county, party_group, rho, rho_source), by = c("county", "party_group")) |>
    mutate(
      factor_age = qlogis(R26) - qlogis(R22),
      rho_gen = sqrt(rho),
      factor_new = factor_new(R26, s26, s22, rho_gen),
      factor_new_no_penalty = factor_new(R26, s26, s22, 1),
      factor_new_full_penalty = factor_new(R26, s26, s22, rho),
      comp = factor_age + factor_new
    )

  # Statewide rows per party from the summed cell counts (report only, not used in the forecast).
  exp_dr <- rho_dr |> group_by(party_group) |>
    summarise(rho = sum(count) / sum(exp_count), .groups = "drop")
  state <- out |> group_by(party_group) |>
    summarise(v26 = sum(v26), n26 = sum(n26), v22 = sum(v22), n22 = sum(n22),
              n_new = sum(n_new), n_old = sum(n_old), by_2022 = sum(by_2022), cell_total = sum(cell_total),
              .groups = "drop") |>
    mutate(county = "Maryland", R26 = v26 / n26, R22 = v22 / n22, s26 = n_new / cell_total, s22 = n_old / by_2022) |>
    left_join(exp_dr, by = "party_group") |>
    mutate(rho = ifelse(party_group == "OTH", rho_pooled, rho),
           rho_source = ifelse(party_group == "OTH", "statewide DEM/REP pooled", "statewide summed cells"),
           factor_age = qlogis(R26) - qlogis(R22),
           rho_gen = sqrt(rho),
           factor_new = factor_new(R26, s26, s22, rho_gen),
           factor_new_no_penalty = factor_new(R26, s26, s22, 1),
           factor_new_full_penalty = factor_new(R26, s26, s22, rho),
           comp = factor_age + factor_new)

  cols <- c("county", "party_group", "R22", "R26", "factor_age", "rho", "rho_gen", "s22", "s26",
            "factor_new", "factor_new_no_penalty", "factor_new_full_penalty", "comp", "rho_source")
  cell_rows <- out |>
    mutate(county = factor(county, CANONICAL_COUNTIES), party_group = factor(party_group, PARTY_GROUPS)) |>
    arrange(county, party_group) |> mutate(across(c(county, party_group), as.character))
  state_rows <- state |> mutate(party_group = factor(party_group, PARTY_GROUPS)) |>
    arrange(party_group) |> mutate(party_group = as.character(party_group))

  ck(nrow(cell_rows) == 72 && !anyNA(cell_rows$comp), "72 cell rows with no NA in comp")
  big <- cell_rows |> filter(abs(comp) > COMP_LIMIT)
  ck(nrow(big) == 0, "|comp| <= 0.25 for every cell",
     paste(big$county, big$party_group, round(big$comp, 3), collapse = "; "))
  ck(all(cell_rows$s26 > 0 & cell_rows$s26 < 1 & cell_rows$s22 > 0 & cell_rows$s22 < 1), "s22 and s26 in (0, 1)")
  ck(all(cell_rows$rho > 0), "rho > 0")

  final <- bind_rows(cell_rows, state_rows) |> select(all_of(cols)) |>
    mutate(across(where(is.double), ~ round(.x, 6)))
  path <- file.path(OUT_DIR, "composition_factors.csv")
  write_input_csv(final, path)

  write_provenance(
    path,
    generated_by = SCRIPT,
    description = paste(
      "Composition change factors per county x party group (DEM, REP, OTH = ALL - DEM - REP), logit scale:",
      "comp = factor_age + factor_new, added by the forecast to each cell's linear predictor. R22 and R26 are",
      "the cell's expected 2022-midterm participation under its 2022 and 2026 age mix; s22, s26 the recent-registrant",
      "shares; rho the relative 2026-primary propensity of people registered since the 2024 general,",
      "rho_gen = sqrt(rho) its general-election version. county = Maryland rows are statewide per party from",
      "summed cell counts, for reporting only."
    ),
    sources = list(
      table = "skipjack.db voter_file_counts",
      snapshot_date = SNAPSHOT,
      metrics = list(
        turnout_by_age = "county x age band (age today): 2022-GG and 2026-GP count / eligible; k_c(a) = 2022-GG eligible / 2026-GP eligible",
        turnout_by_age_party = "statewide party x age band: party tilt t_p(a) for 2022-GG (factor_age) and 2026-GP (rho)",
        age_by_party = "county x party x age band, all statuses: the 2026 mix e26",
        party_by_county = "county x party x status: cell totals (active + inactive) for the e26 reconciliation and as the share denominators",
        registration_cohorts = "county x party x reg_year: 2019-2022 and 2023-2026 counts",
        new_voters_voted = "county x party: registered after 2024-11-05 and eligible for 2026-GP, count voted / eligible",
        new_registrants = "county x party x age at registration (18-22, 23-29, 30+) x reg_year, first-time registrants: reg_year 2025-2026 age profile"
      )
    ),
    checks = checks,
    notes = list(
      method = list(
        factor_age = paste(
          "Age-at-election terms with one propensity schedule. Each current-age band's counts are spread uniformly",
          "over its single years (18-24: 7 years, 25-34 to 65-74: 10, 75+ treated as 75-94), shifted back 4 years",
          "(snapshot 2026-08-12 to the 2022 general on 2022-11-08 is 3.75 years; 4 is used) and re-binned into the",
          "standard bands by age on 2022 election day. r22_c(b) = shifted 2022-GG count / shifted eligible",
          "(turnout_by_age, county); t_p(b) the same ratio for the party over ALL from shifted turnout_by_age_party;",
          "r22_cp(b) = r22_c(b) * t_p(b), capped at 0.99. e22_cp(b) = shift(age_by_party_cp(a) * k_c(a)), with",
          "k_c(a) = 2022-GG eligible / 2026-GP eligible by county x current-age band: this keeps each party's own age",
          "profile, where splitting the county's shifted eligible by party would need a county party x age split",
          "the file does not give for 2022. e26_cp(b) = age_by_party in current bands (today's age is close to age on",
          "2026-11-03). R22 = sum e22 r22 / sum e22, R26 = sum e26 r22 / sum e26, A = logit(R26) - logit(R22).",
          "Under 18 dropped; Unknown kept as its own band, unshifted. In-script sanity check: with e26 set to the",
          "shifted e22, A = 0 to 1e-9."
        ),
        age_shift_18_24 = paste(
          "Deviation from a uniform spread: 2022-era quantities in the current 18-24 band (2022-GG count and",
          "eligible, and the 2022 mix age_by_party * k) are spread over current ages 22-24 only. Nobody younger than",
          "about 21.75 today was 18 by 2022-11-08, which 2022-GG eligibility requires, and k for 18-24 (about 0.40,",
          "near 3/7) agrees. A uniform 18-24 spread would place 4/7 of the band's 2022-GG eligible below age 18:",
          sprintf("%.2f%% to %.2f%% of a county's eligible, above the 0.5%% tolerance.",
                  100 * min(uniform_below18$share), 100 * max(uniform_below18$share)),
          "With the 22-24 spread the mass below 18 is zero by construction (checked under 0.5% per county)."
        ),
        factor_new = "N = logit(R26 * (1 - (s26 - s22) * (1 - rho_gen))) - logit(R26), argument clamped to [1e-6, 1 - 1e-6].",
        rho = paste(
          "DEM and REP: observed 2026-GP rate of new_voters_voted over the age-expected rate, sum over bands of the",
          "cell's 2025-2026 new-registrant age profile times the cell's 2026-GP rate (county x age rate times the",
          "statewide 2026-GP party tilt): 18-22 -> 18-24, 23-29 -> 25-34, 30+ -> the cell's 35+ average weighted by",
          "its current registrants. OTH: statewide DEM/REP pooled rho (sum of observed over sum of expected counts",
          "across the 48 DEM/REP cells), since closed primaries make OTH primary rates meaningless. Fallbacks if a",
          "cell's count were suppressed: county ALL (untilted rates), then statewide ALL; rho_source records which was used."
        ),
        rho_gen = RHO_GEN_NOTE,
        habit_mix = HABIT_NOTE
      ),
      limitations = list(
        factor_age_survivors = paste(
          "Known residual bias, not corrected: the 2022 mix is built from today's file, so people who died or moved",
          "since 2022 are missing from it. Deaths remove high-propensity elderly voters, which understates R22 (and",
          "pushes factor_age up); moves remove low-propensity young voters, which overstates R22 (and pushes",
          "factor_age down). The net sign is unknown. The file holds about 87% of the 2022 electorate, so the",
          "missing 13% is large next to the factor."
        ),
        factor_new_sign = paste(
          "s26 < s22 in every cell: the 2019-2022 cohort (with the 2020 registration surge) is a larger share of",
          "those registered by 2022 than the 2023-2026 cohort is of today's registrants, so factor_new is a small",
          "bonus, not a penalty, everywhere. The s22 move bias works against this, so the true bonus is larger."
        ),
        rho_ages = paste(
          "rho's age-expected rate uses 2026-GP rates by current age: the primary (2026-06-23) is seven weeks",
          "before the snapshot, so current age is age at that election. The new-registrant profile bands are age",
          "at registration in 2025-2026, at most about 1.6 years younger than current age."
        ),
        s22 = S22_NOTE,
        participation_among_current_voters = "All rates and shares are ratios within the 2026-08-12 snapshot, which holds about 87% of the 2022 electorate; never levels.",
        profile_mismatch = paste(
          "new_voters_voted counts everyone with a state registration date after 2024-11-05 (including 16-17 year-old",
          "preregistrants and November-December 2024 registrants), while the age profile comes from new_registrants,",
          "first-time registrants aged 18+ at registration in reg_year 2025-2026, whose bands are age at registration",
          "rather than age today. The profile is an approximation of the age mix of the rho population."
        )
      ),
      deviations = list(
        registration_cohorts_null_reg_year = paste(
          "registration_cohorts excludes NULL reg_year rows (the pipeline filters them), and its oldest years are",
          "sometimes suppressed. Share denominators therefore use the cell's party_by_county total (active + inactive),",
          "which includes NULL reg_year registrants; s22's denominator is that total minus the 2023-2026 counts, so",
          "NULL reg_year registrants count as registered by 2022. Statewide, NULL reg_year registrants number",
          null_reg_year, "(party_by_county minus registration_cohorts, both unsuppressed statewide)."
        ),
        rho_party_tilt = paste(
          "The brief's age-expected 2026-GP rate for a county x party cell uses turnout_by_age, which has no party; the",
          "county x age rate is tilted to the party with the statewide 2026-GP party x age rates, as factor_age does",
          "for 2022-GG, so that DEM and REP rho compare like with like (all-party primary rates include unaffiliated",
          "voters who cannot vote in them)."
        ),
        e26_reconciliation = paste(
          "age_by_party has no total row; its bands (with Under 18) are reconciled to party_by_county totals, exactly",
          "where no band is suppressed and within the 1-9 bounds of suppressed bands otherwise."
        ),
        unknown_age = paste(
          "The Unknown age band has a 2022-GG count of 0 in every county (rate 0, observed even where eligible is",
          "suppressed) and, statewide, equal 2022-GG and 2026-GP eligible counts, so k = 1 in every county. Where a",
          "cell's Unknown e26 is suppressed (1-9 people) or not derivable, the band is left out of that cell's mixes;",
          "with rate 0 and equal weight in both mixes its effect on factor_age is under 1e-4."
        ),
        unknown_age_dropped_cells = paste(unknown_dropped$county, unknown_dropped$party_group, sep = ":")
      )
    ),
    extra = list(
      statewide_pooled_dem_rep_rho = round(rho_pooled, 6),
      statewide_all_rho = round(rho_state_all, 6),
      statewide_null_reg_year_registrants = null_reg_year,
      shift_years = SHIFT_YEARS,
      age_shift_map = shift_map() |> mutate(w = round(w, 6)),
      shifted_2022_below_18_max_share = max(below18$share),
      uniform_spread_2022_below_18_share_range = round(range(uniform_below18$share), 6),
      new_registrant_profile_fallbacks = profile |> filter(profile_source != "cell") |>
        distinct(county, party_group) |> mutate(cell = paste(county, party_group, sep = ":")) |> pull(cell),
      columns = list(
        R22 = "expected 2022-GG participation under the cell's 2022 age mix (ratio within the snapshot)",
        R26 = "the same under the 2026 age mix",
        factor_age = "logit(R26) - logit(R22)",
        rho = "2026-GP propensity of registrants since the 2024 general relative to their age-expected rate",
        rho_gen = "sqrt(rho): judged general-election propensity (see notes)",
        s22 = "share of registrants by end of 2022 who registered 2019-2022",
        s26 = "share of current registrants who registered 2023-2026",
        factor_new = "new-registrant factor with rho_gen = sqrt(rho)",
        factor_new_no_penalty = "with rho_gen = 1 (sensitivity)",
        factor_new_full_penalty = "with rho_gen = rho (sensitivity)",
        comp = "factor_age + factor_new",
        rho_source = "where rho came from"
      )
    )
  )

  options(width = 200)
  cat("Statewide composition factors (summed cells):\n")
  print(as.data.frame(final |> filter(county == "Maryland")), row.names = FALSE)
  cat("\nFive largest |comp| cells:\n")
  print(as.data.frame(final |> filter(county != "Maryland") |> arrange(desc(abs(comp))) |> head(5)), row.names = FALSE)
  cat("\nStatewide pooled DEM/REP rho:", round(rho_pooled, 6), "\n")
  cat("Unknown-age bands left out:", nrow(unknown_dropped), "cells\n")
  cat("Wrote", path, "\n")
}

main()
