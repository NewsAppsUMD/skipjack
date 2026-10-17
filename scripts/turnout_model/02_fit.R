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
# backtest against a naive baseline.
#
# Inputs: data/turnout_model/inputs/training.csv and covariates.csv (01_build_inputs.R).
# Writes to data/turnout_model/gg26/: structure_effects.csv, structure_draws.csv.gz,
# election_levels.csv, election_level_draws.csv.gz, backtest.csv, backtest_cells.csv (each with
# a <name>_provenance.json) and fit_summary.json. Seeded (set.seed(20261103)), so a re-run
# writes byte-identical CSVs.

source("scripts/turnout_model/R/bootstrap.R")
suppressPackageStartupMessages(library(lme4))
if (!requireNamespace("MASS", quietly = TRUE)) stop("MASS is required (MASS::mvrnorm)")

SCRIPT <- tryCatch(script_path(), error = function(e) "scripts/turnout_model/02_fit.R")
OUT_DIR <- "data/turnout_model/gg26"
SEED <- 20261103
N_DRAWS <- 1000
N_BOOT <- 100
CTRL <- glmerControl(optimizer = "bobyqa", optCtrl = list(maxfun = 2e5))
MIDTERMS <- c("GG18", "GG22")

# Covariate candidates in order of preference; a candidate is kept unless it correlates above
# |r| = 0.7 with one already kept, up to MAX_COVARIATES. (median_age is preferred to
# share_65plus, which measures nearly the same thing.)
COVARIATE_CANDIDATES <- c("z_share_ba_plus", "z_median_age", "z_share_moved_past_year",
                          "z_share_owner_occupied", "z_share_65plus", "z_median_hh_income")
MAX_COVARIATES <- 4
COR_LIMIT <- 0.7

RESPONSE <- "cbind(ballots_cast, active - ballots_cast)"
RE_FULL <- c("(1 | county)", "(1 | county:party_group)", "(1 | county:election_type)",
             "(1 | county:party_group:election_type)", "(1 | obs)")
# Random-effect terms entering the midterm structure effect, and the level each is read at.
S_TERMS <- c("county:party_group", "county:election_type")

build_formula <- function(covariates, re_terms) {
  as.formula(paste(RESPONSE, "~ 0 + ep +", paste(c(covariates, re_terms), collapse = " + ")))
}
formula_text <- function(f) paste(trimws(deparse(f, width.cutoff = 500L)), collapse = " ")

# Model frame: factors, one obs id per row, ep = the election x party cells present.
model_frame <- function(rows) {
  rows |>
    mutate(
      county = factor(county, CANONICAL_COUNTIES),
      party_group = factor(party_group, PARTY_GROUPS),
      election = factor(election, ELECTION_LEVELS),
      election_type = factor(election_type),
      ep = interaction(election, party_group, sep = ":", drop = TRUE, lex.order = TRUE),
      obs = factor(seq_len(n()))
    )
}

fit_glmer <- function(f, data) {
  warns <- character()
  fit <- withCallingHandlers(
    glmer(f, data = data, family = binomial, control = CTRL),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") }
  )
  conv <- unlist(fit@optinfo$conv$lme4$messages)
  list(fit = fit, messages = unique(c(conv, warns)), singular = isSingular(fit))
}

variance_components <- function(fit) {
  as.data.frame(VarCorr(fit)) |>
    transmute(term = grp, variance = round(vcov, 8), sd = round(sdcor, 8))
}

# Point estimate of s_cp for the 72 cells: covariate part + u_county:party + u_county:midterm.
structure_point <- function(fit, cells, covariates) {
  beta <- fixef(fit)
  re <- ranef(fit)
  check(all(S_TERMS %in% names(re)), "fit has the random-effect terms of s_cp")
  cov_part <- as.vector(as.matrix(cells[, covariates]) %*% beta[covariates])
  u_cp <- re[["county:party_group"]][paste(cells$county, cells$party_group, sep = ":"), 1]
  u_cm <- re[["county:election_type"]][paste(cells$county, "midterm_general", sep = ":"), 1]
  if (anyNA(u_cp) || anyNA(u_cm)) stop("missing conditional modes for some county x party cells")
  cov_part + u_cp + u_cm
}

alpha_name <- function(election, party) paste0("ep", election, ":", party)

# Rate a cell set: for each party, the alpha that makes the cells' predicted ballots sum to the
# party's actual total, then the predicted ballots.
calibrate <- function(active, s, party, target_by_party) {
  pred <- numeric(length(active))
  for (p in names(target_by_party)) {
    i <- party == p
    g <- function(a) sum(active[i] * plogis(a + s[i])) - target_by_party[[p]]
    a <- uniroot(g, c(-15, 15), tol = 1e-12)$root
    pred[i] <- active[i] * plogis(a + s[i])
  }
  pred
}

main <- function() {
  t_start <- proc.time()[["elapsed"]]
  checks <- character()
  dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

  # -------------------------------------------------------------------------------------------
  # Data
  # -------------------------------------------------------------------------------------------

  training_all <- read_csv(file.path(INPUTS_DIR, "training.csv"), show_col_types = FALSE)
  covariates_tbl <- read_csv(file.path(INPUTS_DIR, "covariates.csv"), show_col_types = FALSE)
  checks <- c(checks, check(nrow(training_all) == 432, "training.csv has 432 rows"))
  check_counties(covariates_tbl$county, "covariates.csv")
  checks <- c(checks, check(nrow(covariates_tbl) == 24, "covariates.csv has one row per county"))

  training <- training_all |> filter(!closed_primary_party)
  checks <- c(checks, check(nrow(training) == 360, "360 rows after dropping closed-primary rows"))
  checks <- c(checks, check(all(training$ballots_cast >= 0 & training$ballots_cast <= training$active),
                            "0 <= ballots_cast <= active in every row"))
  dat <- model_frame(training |> left_join(covariates_tbl, by = "county"))
  checks <- c(checks, check(!anyNA(dat[, COVARIATE_CANDIDATES]), "every training row has covariates"))

  # -------------------------------------------------------------------------------------------
  # Covariates
  # -------------------------------------------------------------------------------------------

  cor_mat <- cor(as.matrix(covariates_tbl[, COVARIATE_CANDIDATES]))
  covariates <- character()
  for (v in COVARIATE_CANDIDATES) {
    if (length(covariates) >= MAX_COVARIATES) break
    if (all(abs(cor_mat[v, covariates]) <= COR_LIMIT)) covariates <- c(covariates, v)
  }
  dropped_cov <- setdiff(COVARIATE_CANDIDATES, covariates)
  cat("Covariates kept:", paste(covariates, collapse = ", "), "\n")

  # -------------------------------------------------------------------------------------------
  # Fit: the briefed formula, its two prescribed simplifications, and the final formula, which
  # is simplification 1 without (1 | county) because that variance is estimated at exactly 0.
  # -------------------------------------------------------------------------------------------

  variants <- list(
    list(label = "full", re = RE_FULL),
    list(label = "simplification 1: drop (1 | county:party_group:election_type)",
         re = setdiff(RE_FULL, "(1 | county:party_group:election_type)")),
    list(label = "simplification 2: also drop (1 | county:election_type)",
         re = setdiff(RE_FULL, c("(1 | county:party_group:election_type)", "(1 | county:election_type)"))),
    list(label = "final: simplification 1 without (1 | county)",
         re = setdiff(RE_FULL, c("(1 | county:party_group:election_type)", "(1 | county)")))
  )
  history <- list()
  for (v in variants) {
    f <- build_formula(covariates, v$re)
    r <- fit_glmer(f, dat)
    vc <- variance_components(r$fit)
    history[[length(history) + 1]] <- list(
      label = v$label, formula = formula_text(f), singular = r$singular,
      messages = as.list(r$messages), aic = round(AIC(r$fit), 3),
      sd = as.list(setNames(round(vc$sd, 5), vc$term))
    )
    cat(sprintf("%-65s singular=%s AIC=%.1f\n", v$label, r$singular, AIC(r$fit)))
    final <- r
    final_formula <- f
  }
  for (h in history[1:3]) {
    if (!h$singular) stop("prescribed variant '", h$label, "' is no longer singular; revisit the formula choice")
  }
  county_sd <- history[[2]]$sd[["county"]]
  checks <- c(checks, check(county_sd < 1e-4, "(1 | county) variance is ~0 in simplification 1, so dropping it leaves the fit unchanged",
                            paste("county sd", county_sd)))
  fit <- final$fit
  checks <- c(checks, check(!final$singular, "final fit is not singular"))
  checks <- c(checks, check(length(final$messages) == 0, "final fit converged without warnings",
                            paste(final$messages, collapse = "; ")))
  checks <- c(checks, check(abs(AIC(fit) - history[[2]]$aic - (-2)) < 0.01,
                            "final fit has the same likelihood as simplification 1 (one fewer parameter)"))
  vc <- variance_components(fit)
  sigma_obs <- vc$sd[vc$term == "obs"]
  print(vc)

  # Overdispersion before (1 | obs): the final formula without it.
  f_noobs <- build_formula(covariates, setdiff(variants[[4]]$re, "(1 | obs)"))
  nobs_fit <- fit_glmer(f_noobs, dat)
  overdispersion <- sum(residuals(nobs_fit$fit, type = "pearson")^2) / df.residual(nobs_fit$fit)
  cat(sprintf("Overdispersion ratio without (1 | obs): %.2f\n", overdispersion))

  # -------------------------------------------------------------------------------------------
  # Checks on the fit
  # -------------------------------------------------------------------------------------------

  dat$fitted_ballots <- fitted(fit) * dat$active
  mid_check <- dat |>
    filter(election %in% MIDTERMS) |>
    group_by(election, party_group) |>
    summarise(observed = sum(ballots_cast), fitted = sum(fitted_ballots), .groups = "drop") |>
    mutate(rel = fitted / observed - 1)
  print(as.data.frame(mid_check))
  checks <- c(checks, check(all(abs(mid_check$rel) < 0.001),
                            "GG18 and GG22 fitted ballots sum by party to within 0.1% of observed"))

  cells <- expand_grid(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS) |>
    left_join(covariates_tbl, by = "county")
  s_hat <- structure_point(fit, cells, covariates)
  beta <- fixef(fit)
  u_obs <- ranef(fit)$obs
  mid_rows <- which(dat$election %in% MIDTERMS)
  idx <- match(paste(dat$county[mid_rows], dat$party_group[mid_rows]), paste(cells$county, cells$party_group))
  recon <- plogis(beta[alpha_name(dat$election[mid_rows], dat$party_group[mid_rows])] + s_hat[idx] +
                    u_obs[as.character(dat$obs[mid_rows]), 1])
  checks <- c(checks, check(max(abs(recon - fitted(fit)[mid_rows])) < 1e-8,
                            "invlogit(alpha_hat + s_hat + u_obs) reproduces fitted rates in every midterm cell"))
  cell_rate_gap <- max(abs(recon - dat$ballots_cast[mid_rows] / dat$active[mid_rows]))
  cat(sprintf("Largest midterm cell gap between fitted and observed rate: %.3f points\n", 100 * cell_rate_gap))
  checks <- c(checks, check(cell_rate_gap < 0.01, "midterm fitted rates within 1 point of observed in every cell",
                            paste("max gap", cell_rate_gap)))

  # -------------------------------------------------------------------------------------------
  # Draws
  # -------------------------------------------------------------------------------------------

  set.seed(SEED)
  V <- as.matrix(vcov(fit))
  beta_draws <- MASS::mvrnorm(N_DRAWS, beta, V)
  re_cv <- ranef(fit, condVar = TRUE)
  re_draw <- function(term, levels) {
    modes <- re_cv[[term]][levels, 1]
    sds <- sqrt(attr(re_cv[[term]], "postVar")[1, 1, match(levels, rownames(re_cv[[term]]))])
    if (anyNA(modes) || anyNA(sds)) stop("missing conditional variance for ", term)
    matrix(rnorm(length(levels) * N_DRAWS, modes, sds), nrow = length(levels))  # levels x draws
  }
  cp_levels <- paste(cells$county, cells$party_group, sep = ":")
  cm_levels <- paste(CANONICAL_COUNTIES, "midterm_general", sep = ":")
  u_cp_draws <- re_draw("county:party_group", cp_levels)
  u_cm_draws <- re_draw("county:election_type", cm_levels)
  cov_draws <- as.matrix(cells[, covariates]) %*% t(beta_draws[, covariates, drop = FALSE])  # cells x draws
  s_draws <- cov_draws + u_cp_draws + u_cm_draws[match(cells$county, CANONICAL_COUNTIES), ]

  structure_effects <- cells |>
    select(county, party_group) |>
    mutate(
      s_mean = round(s_hat, 6),
      s_sd = round(apply(s_draws, 1, sd), 6),
      s_q05 = round(apply(s_draws, 1, quantile, 0.05, names = FALSE), 6),
      s_q95 = round(apply(s_draws, 1, quantile, 0.95, names = FALSE), 6)
    )
  draw_mean_gap <- max(abs(rowMeans(s_draws) - s_hat))
  checks <- c(checks, check(draw_mean_gap < 0.02, "draw means of s_cp within 0.02 of the point estimates",
                            paste("max gap", draw_mean_gap)))
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
  lev_names <- alpha_name(lev$election, lev$party_group)
  election_level_draws <- tibble(
    draw = rep(seq_len(N_DRAWS), each = nrow(lev)),
    election = rep(lev$election, N_DRAWS),
    party_group = rep(lev$party_group, N_DRAWS),
    alpha = round(as.vector(t(beta_draws[, lev_names])), 6),
    sigma_obs = round(sigma_obs, 6)
  )

  # -------------------------------------------------------------------------------------------
  # Interval width cross-check: statewide GG22 ballots predicted from the election level and the
  # structure effects (no obs effect), from the draws and from a parametric bootMer.
  # -------------------------------------------------------------------------------------------

  gg22 <- dat |> filter(election == "GG22")
  gg22_idx <- match(paste(gg22$county, gg22$party_group), paste(cells$county, cells$party_group))
  gg22_alpha_names <- alpha_name("GG22", gg22$party_group)
  sim_stat <- vapply(seq_len(N_DRAWS), function(k) {
    sum(gg22$active * plogis(beta_draws[k, gg22_alpha_names] + s_draws[gg22_idx, k]))
  }, numeric(1))
  point_stat <- sum(gg22$active * plogis(beta[gg22_alpha_names] + s_hat[gg22_idx]))
  re_no_obs <- as.formula(paste("~", paste(paste0("(1 | ", S_TERMS, ")"), collapse = " + ")))
  gg22_rows <- which(dat$election == "GG22")
  boot_fun <- function(m) sum(dat$active[gg22_rows] * predict(m, re.form = re_no_obs, type = "response")[gg22_rows])
  checks <- c(checks, check(abs(boot_fun(fit) / point_stat - 1) < 1e-8, "bootMer statistic matches the point estimate on the fit"))
  t_boot <- proc.time()[["elapsed"]]
  set.seed(SEED)
  boot_warns <- 0L
  boot <- withCallingHandlers(
    bootMer(fit, boot_fun, nsim = N_BOOT, type = "parametric", use.u = FALSE),
    warning = function(w) { boot_warns <<- boot_warns + 1L; invokeRestart("muffleWarning") }
  )
  boot_secs <- proc.time()[["elapsed"]] - t_boot
  boot_t <- boot$t[, 1]
  boot_ok <- boot_t[is.finite(boot_t)]
  widths <- list(
    statistic = "statewide GG22 ballots, sum of active x invlogit(alpha_GG22,p + s_cp), without the obs effect",
    point_estimate = round(point_stat),
    observed = sum(gg22$ballots_cast),
    simulation = list(n = N_DRAWS, sd = round(sd(sim_stat)),
                      q05 = round(quantile(sim_stat, 0.05, names = FALSE)),
                      q95 = round(quantile(sim_stat, 0.95, names = FALSE)),
                      width_90 = round(diff(quantile(sim_stat, c(0.05, 0.95), names = FALSE)))),
    bootmer = list(n = N_BOOT, n_finite = length(boot_ok), warnings = boot_warns, seconds = round(boot_secs),
                   sd = round(sd(boot_ok)),
                   q05 = round(quantile(boot_ok, 0.05, names = FALSE)),
                   q95 = round(quantile(boot_ok, 0.95, names = FALSE)),
                   width_90 = round(diff(quantile(boot_ok, c(0.05, 0.95), names = FALSE))),
                   type = "parametric, use.u = FALSE")
  )
  cat(sprintf("GG22 statewide: simulation 90%% width %s, bootMer 90%% width %s (%d s)\n",
              format(widths$simulation$width_90, big.mark = ","), format(widths$bootmer$width_90, big.mark = ","),
              round(boot_secs)))

  # -------------------------------------------------------------------------------------------
  # Backtest: leave one midterm out
  # -------------------------------------------------------------------------------------------

  backtest_rows <- list()
  cell_rows <- list()
  refits <- list()
  for (held in c("GG22", "GG18")) {
    other <- setdiff(MIDTERMS, held)
    sub <- model_frame(training |> filter(election != held) |> left_join(covariates_tbl, by = "county"))
    r <- fit_glmer(final_formula, sub)
    rvc <- variance_components(r$fit)
    refits[[held]] <- list(held_out = held, singular = r$singular, messages = as.list(r$messages),
                           sd = as.list(setNames(round(rvc$sd, 5), rvc$term)))
    cat(sprintf("Refit without %s: singular=%s messages=%s\n", held, r$singular, paste(r$messages, collapse = "; ")))
    s_ref <- structure_point(r$fit, cells, covariates)

    truth <- training |> filter(election == held)
    oth <- training |> filter(election == other)
    k <- match(paste(truth$county, truth$party_group), paste(cells$county, cells$party_group))
    ko <- match(paste(truth$county, truth$party_group), paste(oth$county, oth$party_group))
    if (anyNA(k) || anyNA(ko) || nrow(truth) != 72) stop("backtest cells do not line up for ", held)
    naive_s <- qlogis(oth$ballots_cast[ko] / oth$active[ko])
    naive_s <- naive_s - ave(naive_s, truth$party_group)
    targets <- tapply(truth$ballots_cast, truth$party_group, sum)[PARTY_GROUPS]
    pred <- calibrate(truth$active, s_ref[k], truth$party_group, targets)
    naive <- calibrate(truth$active, naive_s, truth$party_group, targets)
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
      tibble(held_out = held, grain = grain,
             metric = c("rate_mae_pts", "rate_max_abs_pts", "share_mae_pts"),
             model = c(mean(abs(err(d$predicted))), max(abs(err(d$predicted))), mean(abs(shr(d$predicted)))),
             naive = c(mean(abs(err(d$naive))), max(abs(err(d$naive))), mean(abs(shr(d$naive)))))
    }
    backtest_rows[[held]] <- bind_rows(metrics(county_tbl, "county"), metrics(cell, "county_party"))
    cell_rows[[held]] <- cell
    cat(sprintf("\nWorst counties, held out %s (rate error, points):\n", held))
    print(as.data.frame(county_tbl |>
      mutate(actual_rate = round(100 * actual / active, 2), model_err = round(100 * (predicted - actual) / active, 2),
             naive_err = round(100 * (naive - actual) / active, 2)) |>
      arrange(desc(abs(model_err))) |> select(county, actual_rate, model_err, naive_err) |> head(5)))
  }
  backtest <- bind_rows(backtest_rows) |> mutate(across(c(model, naive), \(x) round(x, 4)))
  backtest_cells <- bind_rows(cell_rows) |>
    mutate(predicted = round(predicted, 1), naive = round(naive, 1)) |>
    select(held_out, county, party_group, actual, predicted, naive)
  cat("\nBacktest:\n")
  print(as.data.frame(backtest))

  # -------------------------------------------------------------------------------------------
  # Write
  # -------------------------------------------------------------------------------------------

  inputs_src <- list(training = file.path(INPUTS_DIR, "training.csv"), covariates = file.path(INPUTS_DIR, "covariates.csv"))
  model_note <- paste("Model:", formula_text(final_formula), "(binomial glmer, bobyqa). s_cp = covariate part +",
                      "u_county:party_group + u_county:election_type at midterm_general; (1 | county) is",
                      "dropped (variance estimated at exactly 0), so it contributes nothing. See fit_summary.json.")

  p <- file.path(OUT_DIR, "structure_effects.csv")
  write_input_csv(structure_effects, p)
  write_provenance(p, generated_by = SCRIPT,
    "Midterm structure effect s_cp per county x party group: logit-scale turnout relative to the statewide party level in a midterm general.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(columns = list(s_mean = "point estimate (fixed effects and conditional modes)",
                                s_sd = "sd over the 1,000 draws", s_q05 = "5th percentile of draws", s_q95 = "95th percentile of draws"),
                 max_gap_draw_mean_vs_point = round(draw_mean_gap, 5)))

  p <- file.path(OUT_DIR, "structure_draws.csv.gz")
  write_input_csv(structure_draws, p)
  write_provenance(p, generated_by = SCRIPT,
    "1,000 approximate posterior draws of s_cp per county x party group, joint with election_level_draws.csv.gz by draw.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(method = paste("Fixed effects from MASS::mvrnorm(fixef, vcov); random effects from independent normals",
                                "at their conditional modes and conditional variances (ranef condVar). Seed", SEED)))

  p <- file.path(OUT_DIR, "election_levels.csv")
  write_input_csv(election_levels, p)
  write_provenance(p, generated_by = SCRIPT,
    "Statewide election x party level alpha (logit scale) from the structure model; alpha_se from vcov(fit).",
    sources = inputs_src, checks = checks, notes = model_note)

  p <- file.path(OUT_DIR, "election_level_draws.csv.gz")
  write_input_csv(election_level_draws, p)
  write_provenance(p, generated_by = SCRIPT,
    "1,000 draws of the GG18 and GG22 statewide party levels alpha, joint with structure_draws.csv.gz by draw; sigma_obs is the observation-level sd, constant across draws.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(sigma_obs = paste("sd of (1 | obs), the cell-level overdispersion term, estimated at", round(sigma_obs, 6),
                                   "; lme4 gives no sampling distribution for it, so every draw carries the estimate.",
                                   "Add a fresh N(0, sigma_obs^2) per new cell when forecasting.")))

  p <- file.path(OUT_DIR, "backtest.csv")
  write_input_csv(backtest, p)
  write_provenance(p, generated_by = SCRIPT,
    "Leave-one-midterm-out backtest: refit without the held-out midterm, statewide party levels fixed at the held-out truth, errors at county and county x party grain, against a naive baseline.",
    sources = inputs_src, checks = checks, notes = model_note,
    extra = list(metrics = list(rate_mae_pts = "mean absolute error of turnout rate (ballots / active), percentage points",
                                rate_max_abs_pts = "largest absolute error of turnout rate, points",
                                share_mae_pts = "mean absolute error of share of statewide (all-party) ballots, points"),
                 naive = "s_cp = logit(the other midterm's county x party rate) minus its party mean, calibrated the same way",
                 refits = unname(refits)))

  p <- file.path(OUT_DIR, "backtest_cells.csv")
  write_input_csv(backtest_cells, p)
  write_provenance(p, generated_by = SCRIPT,
    "Backtest cells: actual, model-predicted and naive ballots per county x party group for each held-out midterm.",
    sources = inputs_src, checks = checks, notes = model_note)

  runtime <- round(proc.time()[["elapsed"]] - t_start)
  summary <- list(
    file = file.path(OUT_DIR, "fit_summary.json"),
    generated_by = SCRIPT,
    run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
    sources = inputs_src,
    formula = formula_text(final_formula),
    formula_notes = paste("ep = interaction(election, party_group, drop = TRUE): a free statewide level for each of the",
                          "15 election x party cells present (OTH is absent from the closed primaries)."),
    simplification = paste("The briefed formula and both prescribed simplifications are singular: (1 | county:party_group:election_type)",
                           "is estimated at 0 (it is nearly the same as (1 | obs), with at most three rows per cell and election type),",
                           "and (1 | county) is estimated at exactly 0 once the covariates are in (sd 0.195 without them):",
                           "the covariates and (1 | county:party_group) take up all county-level variation. The final fit is",
                           "simplification 1 without (1 | county); since that variance was 0 the estimates are unchanged."),
    fit_history = history,
    n_rows = nrow(dat),
    n_rows_by_election = as.list(table(as.character(dat$election))),
    singular = final$singular,
    convergence_messages = as.list(final$messages),
    aic = round(AIC(fit), 3),
    variance_components = vc,
    sigma_obs = round(sigma_obs, 6),
    covariates = list(chosen = covariates, dropped = dropped_cov,
                      rule = paste("candidates in preference order", paste(COVARIATE_CANDIDATES, collapse = ", "),
                                   "; keep one unless |r| >", COR_LIMIT, "with one already kept; at most", MAX_COVARIATES),
                      correlation = as.data.frame(round(cor_mat, 3)) |> tibble::rownames_to_column("covariate"),
                      coefficients = as.list(round(beta[covariates], 5))),
    overdispersion_without_obs = list(ratio = round(overdispersion, 3),
                                      formula = formula_text(f_noobs),
                                      definition = "sum of squared Pearson residuals / residual df",
                                      messages = as.list(nobs_fit$messages)),
    interval_widths = widths,
    backtest_refits = unname(refits),
    draws = list(n = N_DRAWS, seed = SEED),
    runtime_seconds = runtime,
    checks_passed = as.list(checks)
  )
  writeLines(jsonlite::toJSON(summary, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA),
             file.path(OUT_DIR, "fit_summary.json"))
  cat(sprintf("\nDone in %d s.\n", runtime))
  invisible(NULL)
}

main()
