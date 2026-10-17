#!/usr/bin/env Rscript
# Fit the turnout model's structure model and backtest it:
#
#   Rscript scripts/turnout_model/02_fit.R          (from the repo root)
#
# A binomial glmer of ballots cast over active registration for every county x party group in
# five certified elections and the 2026 primary (closed-primary OTH rows dropped), with a free
# statewide level per election x party, county covariates, county random effects by party and
# election type, and an observation-level effect for overdispersion. From it, the midterm
# structure effect s_cp of each county x party cell (logit scale, relative to the statewide
# party level) and the statewide GG18/GG22 levels, with 1,000 approximate posterior draws
# (fixed effects from their sampling distribution, random effects from their conditional
# distributions), a parametric bootMer cross-check of interval width, and a leave-one-midterm-out
# backtest against a naive baseline. Model helpers (formula, draws, calibration) are in
# R/model.R.
#
# Inputs: data/turnout_model/inputs/training.csv and covariates.csv (01_build_inputs.R).
# Writes to data/turnout_model/gg26/: structure_effects.csv, structure_draws.csv.gz,
# election_levels.csv, election_level_draws.csv.gz, backtest.csv, backtest_cells.csv (each with
# a <name>_provenance.json) and fit_summary.json. Seeded (set.seed(20261103)), so a re-run on
# the same machine writes byte-identical CSVs.

source("scripts/turnout_model/R/bootstrap.R")
if (!requireNamespace("MASS", quietly = TRUE)) stop("MASS is required (MASS::mvrnorm)")

SCRIPT <- tryCatch(script_path(), error = function(e) "scripts/turnout_model/02_fit.R")
OUT_DIR <- "data/turnout_model/gg26"
SEED <- 20261103
N_DRAWS <- 1000
N_BOOT <- 100
MIDTERMS <- c("GG18", "GG22")

# Covariate candidates in order of preference; a candidate is kept unless it correlates above
# |r| = 0.7 with one already kept, up to MAX_COVARIATES. (median_age is preferred to
# share_65plus, which measures nearly the same thing.)
COVARIATE_CANDIDATES <- c("z_share_ba_plus", "z_median_age", "z_share_moved_past_year",
                          "z_share_owner_occupied", "z_share_65plus", "z_median_hh_income")
MAX_COVARIATES <- 4
COR_LIMIT <- 0.7

# Random-effect variants, tried in order; the first that is non-singular and converges without
# messages is used. Each drops terms from the full set.
RE_FULL <- c("(1 | county)", "(1 | county:party_group)", "(1 | county:election_type)",
             "(1 | county:party_group:election_type)", "(1 | obs)")
RE_VARIANTS <- list(
  list(label = "full", drop = character()),
  list(label = "drop (1 | county:party_group:election_type)",
       drop = "(1 | county:party_group:election_type)"),
  list(label = "drop (1 | county:party_group:election_type) and (1 | county)",
       drop = c("(1 | county:party_group:election_type)", "(1 | county)")),
  list(label = "drop (1 | county:party_group:election_type), (1 | county) and (1 | county:election_type)",
       drop = c("(1 | county:party_group:election_type)", "(1 | county)", "(1 | county:election_type)"))
)
# The variant selected when this script was written; a different selection is reported, not fatal.
EXPECTED_VARIANT <- "drop (1 | county:party_group:election_type) and (1 | county)"

ALPHA_CAVEAT <- paste(
  "Not for use uncalibrated: the alpha draws and the random-effect draws behind s_cp are independent,",
  "so alpha + s_cp carries the uncertainty of the population mean (statewide GG22 ballots sd about 42,000),",
  "and the parametric bootMer (use.u = FALSE) agrees with that width for the same reason rather than",
  "validating it. The forecast calibrates the statewide level per draw and does not use the alpha spread."
)

# ---------------------------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------------------------

load_data <- function() {
  training_all <- read_csv(file.path(INPUTS_DIR, "training.csv"), show_col_types = FALSE)
  covariates_tbl <- read_csv(file.path(INPUTS_DIR, "covariates.csv"), show_col_types = FALSE)
  checks <- check(nrow(training_all) == 432, "training.csv has 432 rows")
  check_counties(covariates_tbl$county, "covariates.csv")
  checks <- c(checks, check(nrow(covariates_tbl) == 24, "covariates.csv has one row per county"))
  training <- training_all |> filter(!closed_primary_party)
  checks <- c(checks, check(nrow(training) == 360, "360 rows after dropping closed-primary rows"))
  checks <- c(checks, check(all(training$ballots_cast >= 0 & training$ballots_cast <= training$active),
                            "0 <= ballots_cast <= active in every row"))
  dat <- model_frame(training |> left_join(covariates_tbl, by = "county"))
  checks <- c(checks, check(!anyNA(dat[, COVARIATE_CANDIDATES]), "every training row has covariates"))
  cells <- expand_grid(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS) |>
    left_join(covariates_tbl, by = "county")
  list(training = training, covariates_tbl = covariates_tbl, dat = dat, cells = cells, checks = checks)
}

select_covariates <- function(covariates_tbl) {
  cor_mat <- cor(as.matrix(covariates_tbl[, COVARIATE_CANDIDATES]))
  chosen <- character()
  for (v in COVARIATE_CANDIDATES) {
    if (length(chosen) >= MAX_COVARIATES) break
    if (all(abs(cor_mat[v, chosen]) <= COR_LIMIT)) chosen <- c(chosen, v)
  }
  cat("Covariates kept:", paste(chosen, collapse = ", "), "\n")
  list(chosen = chosen, dropped = setdiff(COVARIATE_CANDIDATES, chosen), cor_mat = cor_mat)
}

# Try RE_VARIANTS in order and keep the first non-singular fit with no convergence messages.
fit_variants <- function(dat, covariates) {
  history <- list()
  for (v in RE_VARIANTS) {
    re <- setdiff(RE_FULL, v$drop)
    f <- build_formula(covariates, re)
    r <- fit_glmer(f, dat)
    vc <- variance_components(r$fit)
    ok <- !r$singular && length(r$messages) == 0
    history[[length(history) + 1]] <- list(
      label = v$label, formula = formula_text(f), singular = r$singular,
      messages = as.list(r$messages), aic = round(AIC(r$fit), 3),
      sd = as.list(setNames(round(vc$sd, 5), vc$term)),
      zero_sd_terms = as.list(vc$term[vc$sd < 1e-4]), selected = ok
    )
    cat(sprintf("%-95s singular=%s messages=%d AIC=%.1f\n", v$label, r$singular, length(r$messages), AIC(r$fit)))
    if (ok) {
      matches <- identical(v$label, EXPECTED_VARIANT)
      cat(sprintf("Selected variant: %s (%s)\n", v$label,
                  if (matches) "the expected variant" else paste("NOT the expected variant,", EXPECTED_VARIANT)))
      return(list(fit = r$fit, formula = f, re = re, label = v$label, history = history,
                  matches_expected = matches))
    }
  }
  stop("no random-effect variant is non-singular and converges without messages; see the variants printed above")
}

overdispersion_without_obs <- function(dat, covariates, re) {
  f <- build_formula(covariates, setdiff(re, "(1 | obs)"))
  r <- fit_glmer(f, dat)
  ratio <- sum(residuals(r$fit, type = "pearson")^2) / df.residual(r$fit)
  cat(sprintf("Overdispersion ratio without (1 | obs): %.2f\n", ratio))
  list(ratio = round(ratio, 3), formula = formula_text(f),
       definition = "sum of squared Pearson residuals / residual df", messages = as.list(r$messages))
}

# Fitted values against the data: party sums in the midterms, and the decomposition of each
# midterm cell's fitted rate into alpha + s + u_obs.
check_fit <- function(fit, dat, cells, covariates, s_hat) {
  mid <- dat |> mutate(fitted_ballots = fitted(fit) * active) |>
    filter(election %in% MIDTERMS) |>
    group_by(election, party_group) |>
    summarise(observed = sum(ballots_cast), fitted = sum(fitted_ballots), .groups = "drop") |>
    mutate(rel = fitted / observed - 1)
  print(as.data.frame(mid))
  checks <- check(all(abs(mid$rel) < 0.001), "GG18 and GG22 fitted ballots sum by party to within 0.1% of observed")

  beta <- fixef(fit)
  u_obs <- ranef(fit)$obs
  rows <- which(dat$election %in% MIDTERMS)
  idx <- match(cell_key(dat$county[rows], dat$party_group[rows]), cell_key(cells$county, cells$party_group))
  recon <- plogis(beta[alpha_name(dat$election[rows], dat$party_group[rows])] + s_hat[idx] +
                    u_obs[as.character(dat$obs[rows]), 1])
  checks <- c(checks, check(max(abs(recon - fitted(fit)[rows])) < 1e-8,
                            "invlogit(alpha_hat + s_hat + u_obs) reproduces fitted rates in every midterm cell"))
  gap <- max(abs(recon - dat$ballots_cast[rows] / dat$active[rows]))
  cat(sprintf("Largest midterm cell gap between fitted and observed rate: %.3f points\n", 100 * gap))
  checks <- c(checks, check(gap < 0.01, "midterm fitted rates within 1 point of observed in every cell",
                            paste("max gap", gap)))
  list(checks = checks, cell_rate_gap_pts = round(100 * gap, 3))
}

draw_tables <- function(fit, cells, covariates, s_hat, draws, sigma_obs) {
  beta <- fixef(fit)
  V <- as.matrix(vcov(fit))
  s_draws <- draws$s
  structure_effects <- cells |>
    select(county, party_group) |>
    mutate(
      s_mean = round(s_hat, 6),
      s_sd = round(apply(s_draws, 1, sd), 6),
      s_q05 = round(apply(s_draws, 1, quantile, 0.05, names = FALSE), 6),
      s_q95 = round(apply(s_draws, 1, quantile, 0.95, names = FALSE), 6)
    )
  gap <- max(abs(rowMeans(s_draws) - s_hat))
  checks <- check(gap < 0.02, "draw means of s_cp within 0.02 of the point estimates", paste("max gap", gap))
  structure_draws <- tibble(
    draw = rep(seq_len(N_DRAWS), each = nrow(cells)),
    county = rep(cells$county, N_DRAWS),
    party_group = rep(cells$party_group, N_DRAWS),
    s = round(as.vector(s_draws), 6)
  )
  checks <- c(checks, check(nrow(structure_draws) == 72000, "structure_draws has 72,000 rows"))

  ep_names <- grep("^ep", names(beta), value = TRUE)
  election_levels <- tibble(term = ep_names) |>
    mutate(election = sub("^ep([^:]+):.*$", "\\1", term), party_group = sub("^.*:", "", term),
           alpha_hat = round(unname(beta[term]), 6), alpha_se = round(sqrt(diag(V)[term]), 6)) |>
    arrange(match(election, ELECTION_LEVELS), match(party_group, PARTY_GROUPS)) |>
    select(election, party_group, alpha_hat, alpha_se)
  checks <- c(checks, check(nrow(election_levels) == 15, "15 election x party levels (no OTH in primaries)"))
  lev <- expand_grid(election = MIDTERMS, party_group = PARTY_GROUPS)
  election_level_draws <- tibble(
    draw = rep(seq_len(N_DRAWS), each = nrow(lev)),
    election = rep(lev$election, N_DRAWS),
    party_group = rep(lev$party_group, N_DRAWS),
    alpha = round(as.vector(t(draws$beta[, alpha_name(lev$election, lev$party_group)])), 6),
    sigma_obs = round(sigma_obs, 6)
  )
  list(structure_effects = structure_effects, structure_draws = structure_draws,
       election_levels = election_levels, election_level_draws = election_level_draws,
       draw_mean_gap = round(gap, 5), checks = checks)
}

# Statewide GG22 ballots predicted from the election level and the structure effects (no obs
# effect), from the draws and from a parametric bootMer.
cross_check <- function(fit, dat, cells, s_hat, draws) {
  beta <- fixef(fit)
  gg22 <- dat |> filter(election == "GG22")
  k <- match(cell_key(gg22$county, gg22$party_group), cell_key(cells$county, cells$party_group))
  a_names <- alpha_name("GG22", gg22$party_group)
  sim <- vapply(seq_len(N_DRAWS), function(d) sum(gg22$active * plogis(draws$beta[d, a_names] + draws$s[k, d])), numeric(1))
  point <- sum(gg22$active * plogis(beta[a_names] + s_hat[k]))
  re_no_obs <- as.formula(paste("~", paste(paste0("(1 | ", S_TERMS, ")"), collapse = " + ")))
  rows <- which(dat$election == "GG22")
  boot_fun <- function(m) sum(dat$active[rows] * predict(m, re.form = re_no_obs, type = "response")[rows])
  checks <- check(abs(boot_fun(fit) / point - 1) < 1e-8, "bootMer statistic matches the point estimate on the fit")

  t0 <- proc.time()[["elapsed"]]
  set.seed(SEED)
  n_warn <- 0L
  boot <- withCallingHandlers(
    bootMer(fit, boot_fun, nsim = N_BOOT, type = "parametric", use.u = FALSE),
    warning = function(w) { n_warn <<- n_warn + 1L; invokeRestart("muffleWarning") }
  )
  secs <- proc.time()[["elapsed"]] - t0
  bt <- boot$t[, 1]
  checks <- c(checks, check(length(bt) == N_BOOT && all(is.finite(bt)), "every bootMer replicate is finite",
                            paste(sum(is.finite(bt)), "of", N_BOOT, "finite")))
  summ <- function(x) list(sd = round(sd(x)), median = round(median(x)),
                           q05 = round(quantile(x, 0.05, names = FALSE)), q95 = round(quantile(x, 0.95, names = FALSE)),
                           width_90 = round(diff(quantile(x, c(0.05, 0.95), names = FALSE))))
  widths <- list(
    statistic = "statewide GG22 ballots, sum of active x invlogit(alpha_GG22,p + s_cp), without the obs effect",
    caveat = ALPHA_CAVEAT,
    point_estimate = round(point),
    observed = sum(gg22$ballots_cast),
    simulation = c(list(n = N_DRAWS), summ(sim)),
    bootmer = c(list(n = N_BOOT, n_finite = sum(is.finite(bt)), warnings = n_warn, seconds = round(secs),
                     type = "parametric, use.u = FALSE"), summ(bt),
                list(median_minus_point = round(median(bt) - point),
                     median_minus_point_pct = round(100 * (median(bt) / point - 1), 3)))
  )
  cat(sprintf("GG22 statewide: point %s, simulation 90%% width %s, bootMer 90%% width %s, bootMer median - point %s (%d s)\n",
              format(widths$point_estimate, big.mark = ","), format(widths$simulation$width_90, big.mark = ","),
              format(widths$bootmer$width_90, big.mark = ","), format(widths$bootmer$median_minus_point, big.mark = ","),
              round(secs)))
  list(widths = widths, checks = checks)
}

# Leave one midterm out: refit without it, fix each party's statewide level at the truth, and
# score county and county x party predictions against a naive baseline.
backtest <- function(training, covariates_tbl, cells, covariates, formula) {
  metric_rows <- list()
  cell_rows <- list()
  refits <- list()
  losses <- list()
  checks <- character()
  for (held in c("GG22", "GG18")) {
    other <- setdiff(MIDTERMS, held)
    sub <- model_frame(training |> filter(election != held) |> left_join(covariates_tbl, by = "county"))
    r <- fit_glmer(formula, sub)
    rvc <- variance_components(r$fit)
    refits[[held]] <- list(held_out = held, singular = r$singular, messages = as.list(r$messages),
                           sd = as.list(setNames(round(rvc$sd, 5), rvc$term)))
    checks <- c(checks, check(!r$singular && length(r$messages) == 0,
                              paste("backtest refit without", held, "is non-singular and converges without messages"),
                              paste("singular", r$singular, "messages:", paste(r$messages, collapse = "; "))))
    s_ref <- structure_point(r$fit, cells, covariates)

    truth <- training |> filter(election == held)
    oth <- training |> filter(election == other)
    k <- match(cell_key(truth$county, truth$party_group), cell_key(cells$county, cells$party_group))
    ko <- match(cell_key(truth$county, truth$party_group), cell_key(oth$county, oth$party_group))
    if (anyNA(k) || anyNA(ko) || nrow(truth) != 72) stop("backtest cells do not line up for ", held)
    naive_s <- qlogis(oth$ballots_cast[ko] / oth$active[ko])
    naive_s <- naive_s - ave(naive_s, truth$party_group)
    checks <- c(checks, check(all(is.finite(naive_s)), paste("backtest", held, "naive structure effects are finite")))
    targets <- tapply(truth$ballots_cast, truth$party_group, sum)[PARTY_GROUPS]
    pred <- calibrate(truth$active, s_ref[k], truth$party_group, targets, election = held)
    naive <- calibrate(truth$active, naive_s, truth$party_group, targets, election = held)
    checks <- c(checks, check(abs(sum(pred) - sum(truth$ballots_cast)) < 1e-3 && abs(sum(naive) - sum(truth$ballots_cast)) < 1e-3,
                              paste("backtest", held, "calibrated totals match actual statewide ballots")))

    cell <- tibble(held_out = held, county = truth$county, party_group = truth$party_group,
                   active = truth$active, actual = truth$ballots_cast, predicted = pred, naive = naive)
    total <- sum(cell$actual)
    county_tbl <- cell |> group_by(held_out, county) |>
      summarise(across(c(active, actual, predicted, naive), sum), .groups = "drop")
    metrics <- function(d, grain) {
      err <- function(x) 100 * (x / d$active - d$actual / d$active)
      shr <- function(x) 100 * (x / total - d$actual / total)
      unit <- if (grain == "county") d$county else cell_key(d$county, d$party_group)
      tibble(held_out = held, grain = grain,
             metric = c("rate_mae_pts", "rate_max_abs_pts", "share_mae_pts"),
             model = c(mean(abs(err(d$predicted))), max(abs(err(d$predicted))), mean(abs(shr(d$predicted)))),
             naive = c(mean(abs(err(d$naive))), max(abs(err(d$naive))), mean(abs(shr(d$naive)))),
             worst_model = c(NA, unit[which.max(abs(err(d$predicted)))], NA),
             worst_naive = c(NA, unit[which.max(abs(err(d$naive)))], NA))
    }
    m <- bind_rows(metrics(county_tbl, "county"), metrics(cell, "county_party")) |>
      mutate(across(c(model, naive), \(x) round(x, 4)))
    for (i in which(m$model > m$naive)) {
      losses[[length(losses) + 1]] <- c(
        list(held_out = held, grain = m$grain[i], metric = m$metric[i], model = m$model[i], naive = m$naive[i]),
        if (!is.na(m$worst_model[i])) list(worst_model = m$worst_model[i], worst_naive = m$worst_naive[i])
      )
    }
    metric_rows[[held]] <- m |> select(held_out, grain, metric, model, naive)
    cell_rows[[held]] <- cell
    cat(sprintf("\nWorst counties, held out %s (rate error, points):\n", held))
    print(as.data.frame(county_tbl |>
      mutate(actual_rate = round(100 * actual / active, 2), model_err = round(100 * (predicted - actual) / active, 2),
             naive_err = round(100 * (naive - actual) / active, 2)) |>
      arrange(desc(abs(model_err))) |> select(county, actual_rate, model_err, naive_err) |> head(5)))
  }
  metrics_tbl <- bind_rows(metric_rows)
  cells_tbl <- bind_rows(cell_rows) |>
    mutate(predicted = round(predicted, 1), naive = round(naive, 1)) |>
    select(held_out, county, party_group, actual, predicted, naive)
  cat("\nBacktest:\n")
  print(as.data.frame(metrics_tbl))
  cat("Metrics where the model loses to the naive baseline:\n")
  for (l in losses) cat(sprintf("  %s %s %s: model %.4f vs naive %.4f%s\n", l$held_out, l$grain, l$metric, l$model, l$naive,
                                if (!is.null(l$worst_model)) paste0(" (worst: ", l$worst_model, ")") else ""))
  list(metrics = metrics_tbl, cells = cells_tbl, refits = unname(refits), losses = losses, checks = checks)
}

write_outputs <- function(o) {
  checks <- o$checks
  inputs_src <- list(training = file.path(INPUTS_DIR, "training.csv"), covariates = file.path(INPUTS_DIR, "covariates.csv"))
  model_note <- paste("Model:", formula_text(o$sel$formula), "(binomial glmer, bobyqa). s_cp = covariate part +",
                      "u_county:party_group + u_county:election_type at midterm_general; (1 | county) is not in the",
                      "selected formula (its variance was estimated at 0). See fit_summary.json.")
  tabs <- o$tabs

  p <- file.path(OUT_DIR, "structure_effects.csv")
  write_input_csv(tabs$structure_effects, p)
  write_provenance(p, generated_by = SCRIPT,
    "Midterm structure effect s_cp per county x party group: logit-scale turnout relative to the statewide party level in a midterm general.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(columns = list(s_mean = "point estimate (fixed effects and conditional modes)",
                                s_sd = "sd over the 1,000 draws", s_q05 = "5th percentile of draws", s_q95 = "95th percentile of draws"),
                 max_gap_draw_mean_vs_point = tabs$draw_mean_gap))

  p <- file.path(OUT_DIR, "structure_draws.csv.gz")
  write_input_csv(tabs$structure_draws, p)
  write_provenance(p, generated_by = SCRIPT,
    "1,000 approximate posterior draws of s_cp per county x party group, joinable with election_level_draws.csv.gz by draw.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(method = paste("Fixed effects from MASS::mvrnorm(fixef, vcov); random effects from independent normals",
                                "at their conditional modes and conditional variances (ranef condVar). Seed", SEED,
                                "; reproducible on this machine, but mvrnorm's eigen() may differ across BLAS builds."),
                 caveat = ALPHA_CAVEAT))

  p <- file.path(OUT_DIR, "election_levels.csv")
  write_input_csv(tabs$election_levels, p)
  write_provenance(p, generated_by = SCRIPT,
    "Statewide election x party level alpha (logit scale) from the structure model; alpha_se from vcov(fit).",
    sources = inputs_src, checks = checks, notes = model_note, extra = list(caveat = ALPHA_CAVEAT))

  p <- file.path(OUT_DIR, "election_level_draws.csv.gz")
  write_input_csv(tabs$election_level_draws, p)
  write_provenance(p, generated_by = SCRIPT,
    "1,000 draws of the GG18 and GG22 statewide party levels alpha, joinable with structure_draws.csv.gz by draw; sigma_obs is the observation-level sd, constant across draws. Not for use uncalibrated.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(caveat = ALPHA_CAVEAT,
                 sigma_obs = paste("sd of (1 | obs), the cell-level overdispersion term, estimated at", round(o$sigma_obs, 6),
                                   "; lme4 gives no sampling distribution for it, so every draw carries the estimate.",
                                   "Add a fresh N(0, sigma_obs^2) per new cell when forecasting.")))

  bt <- o$bt
  p <- file.path(OUT_DIR, "backtest.csv")
  write_input_csv(bt$metrics, p)
  write_provenance(p, generated_by = SCRIPT,
    "Leave-one-midterm-out backtest: refit without the held-out midterm, statewide party levels fixed at the held-out truth, errors at county and county x party grain, against a naive baseline.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(metrics = list(rate_mae_pts = "mean absolute error of turnout rate (ballots / active), percentage points",
                                rate_max_abs_pts = "largest absolute error of turnout rate, points",
                                share_mae_pts = "mean absolute error of share of statewide (all-party) ballots, points"),
                 naive = "s_cp = logit(the other midterm's county x party rate) minus its party mean, calibrated the same way",
                 model_loses_to_naive = bt$losses,
                 refits = bt$refits))

  p <- file.path(OUT_DIR, "backtest_cells.csv")
  write_input_csv(bt$cells, p)
  write_provenance(p, generated_by = SCRIPT,
    "Backtest cells: actual, model-predicted and naive ballots per county x party group for each held-out midterm.",
    sources = inputs_src, checks = checks, notes = model_note)

  sel <- o$sel
  summary <- list(
    file = file.path(OUT_DIR, "fit_summary.json"),
    generated_by = SCRIPT,
    run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
    sources = inputs_src,
    formula = formula_text(sel$formula),
    formula_notes = paste("ep = interaction(election, party_group, drop = TRUE): a free statewide level for each of the",
                          "15 election x party cells present (OTH is absent from the closed primaries)."),
    selection = list(
      rule = "random-effect variants tried in order; the first non-singular fit with no convergence messages is used",
      selected = sel$label,
      expected = EXPECTED_VARIANT,
      selected_matches_expected = sel$matches_expected,
      why = paste("The full formula and the one without (1 | county:party_group:election_type) are singular:",
                  "the county x party x type term is estimated at 0 (it is nearly the same as (1 | obs), with at most",
                  "three rows per cell and election type), and (1 | county) is estimated at 0 once the covariates are in:",
                  "the covariates and (1 | county:party_group) take up all county-level variation. Dropping (1 | county)",
                  "as well leaves the estimates unchanged. zero_sd_terms in each attempt lists the terms at 0.")
    ),
    fit_history = sel$history,
    n_rows = nrow(o$dat),
    n_rows_by_election = as.list(table(as.character(o$dat$election))),
    singular = isSingular(sel$fit),
    convergence_messages = list(),
    aic = round(AIC(sel$fit), 3),
    variance_components = o$vc,
    sigma_obs = round(o$sigma_obs, 6),
    covariates = list(chosen = o$cov$chosen, dropped = o$cov$dropped,
                      rule = paste("candidates in preference order", paste(COVARIATE_CANDIDATES, collapse = ", "),
                                   "; keep one unless |r| >", COR_LIMIT, "with one already kept; at most", MAX_COVARIATES),
                      correlation = as.data.frame(round(o$cov$cor_mat, 3)) |> tibble::rownames_to_column("covariate"),
                      coefficients = as.list(round(fixef(sel$fit)[o$cov$chosen], 5))),
    overdispersion_without_obs = o$overdispersion,
    max_midterm_cell_gap_fitted_vs_observed_rate_pts = o$fitcheck$cell_rate_gap_pts,
    alpha_draws = ALPHA_CAVEAT,
    interval_widths = o$cross$widths,
    backtest = list(model_loses_to_naive = bt$losses, refits = bt$refits),
    draws = list(n = N_DRAWS, seed = SEED,
                 reproducibility = "byte-identical re-runs on this machine; mvrnorm's eigen() may differ across BLAS builds"),
    runtime_seconds = o$runtime,
    checks_passed = as.list(checks)
  )
  writeLines(jsonlite::toJSON(summary, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA),
             file.path(OUT_DIR, "fit_summary.json"))
}

main <- function() {
  t_start <- proc.time()[["elapsed"]]
  dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

  d <- load_data()
  cov <- select_covariates(d$covariates_tbl)
  sel <- fit_variants(d$dat, cov$chosen)
  vc <- variance_components(sel$fit)
  sigma_obs <- vc$sd[vc$term == "obs"]
  print(vc)
  overdispersion <- overdispersion_without_obs(d$dat, cov$chosen, sel$re)

  s_hat <- structure_point(sel$fit, d$cells, cov$chosen)
  fitcheck <- check_fit(sel$fit, d$dat, d$cells, cov$chosen, s_hat)
  draws <- make_draws(sel$fit, d$cells, cov$chosen, N_DRAWS, SEED)
  tabs <- draw_tables(sel$fit, d$cells, cov$chosen, s_hat, draws, sigma_obs)
  cross <- cross_check(sel$fit, d$dat, d$cells, s_hat, draws)
  bt <- backtest(d$training, d$covariates_tbl, d$cells, cov$chosen, sel$formula)

  runtime <- round(proc.time()[["elapsed"]] - t_start)
  write_outputs(list(
    checks = c(d$checks, fitcheck$checks, tabs$checks, cross$checks, bt$checks),
    dat = d$dat, cov = cov, sel = sel, vc = vc, sigma_obs = sigma_obs, overdispersion = overdispersion,
    fitcheck = fitcheck, tabs = tabs, cross = cross, bt = bt, runtime = runtime
  ))
  cat(sprintf("\nDone in %d s.\n", runtime))
  invisible(NULL)
}

main()
