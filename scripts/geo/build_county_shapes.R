#!/usr/bin/env Rscript
# Build src/skipjack/web/geo/md_counties.json: Maryland's 24 county outlines as SVG paths.
#
#   Rscript scripts/geo/build_county_shapes.R
#
# Source: U.S. Census Bureau cartographic boundary file, counties, 1:5,000,000, 2023
# (https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_5m.zip), read with
# tigris. The cartographic files are clipped to the shoreline, so the Chesapeake Bay shows as
# water, and their generalization is consistent between neighbours, so no further
# simplification is needed. Outlines are projected to Maryland State Plane (EPSG:6487), scaled
# into a 1000-unit-wide viewBox with y pointing down, and rounded to 0.1 unit.
#
# Run once; the output is committed. Needs the sf, tigris and jsonlite packages.

suppressPackageStartupMessages({
  library(sf)
  library(tigris)
  library(jsonlite)
})
options(tigris_use_cache = TRUE)

out <- "src/skipjack/web/geo/md_counties.json"
width <- 1000
pad <- 4

md <- counties(state = "MD", cb = TRUE, resolution = "5m", year = 2023, progress_bar = FALSE)
md <- st_transform(md, 6487)
stopifnot(nrow(md) == 24)

bb <- st_bbox(md)
scale <- (width - 2 * pad) / (bb[["xmax"]] - bb[["xmin"]])
height <- ceiling((bb[["ymax"]] - bb[["ymin"]]) * scale + 2 * pad)
fmt <- function(v) sub("\\.0$", "", sprintf("%.1f", v))
to_xy <- function(m) {
  cbind(
    (m[, 1] - bb[["xmin"]]) * scale + pad,
    (bb[["ymax"]] - m[, 2]) * scale + pad
  )
}

ring_path <- function(ring) {
  xy <- to_xy(ring)
  xy <- xy[-nrow(xy), , drop = FALSE]  # the ring repeats its first point
  pts <- paste(fmt(xy[, 1]), fmt(xy[, 2]), sep = ",")
  paste0("M", pts[1], "L", paste(pts[-1], collapse = " "), "Z")
}

geom_path <- function(g) {
  polys <- if (inherits(g, "MULTIPOLYGON")) unclass(g) else list(unclass(g))
  paste(unlist(lapply(polys, function(p) vapply(p, ring_path, ""))), collapse = "")
}

# The DB's county names (see COUNTY_FIPS in src/skipjack/pipeline/voterfile.py).
names_by_fips <- c(
  "001" = "Allegany", "003" = "Anne Arundel", "005" = "Baltimore County", "009" = "Calvert",
  "011" = "Caroline", "013" = "Carroll", "015" = "Cecil", "017" = "Charles",
  "019" = "Dorchester", "021" = "Frederick", "023" = "Garrett", "025" = "Harford",
  "027" = "Howard", "029" = "Kent", "031" = "Montgomery", "033" = "Prince George's",
  "035" = "Queen Anne's", "037" = "St. Mary's", "039" = "Somerset", "041" = "Talbot",
  "043" = "Washington", "045" = "Wicomico", "047" = "Worcester", "510" = "Baltimore City"
)
stopifnot(setequal(md$COUNTYFP, names(names_by_fips)))

label_points <- to_xy(st_coordinates(st_point_on_surface(st_geometry(md))))
counties <- list()
for (i in seq_len(nrow(md))) {
  fips <- md$COUNTYFP[i]
  counties[[names_by_fips[[fips]]]] <- list(
    fips = fips,
    d = geom_path(st_geometry(md)[[i]]),
    label_xy = round(label_points[i, ], 1)
  )
}
counties <- counties[order(names(counties))]

shapes <- list(
  source = "U.S. Census Bureau cartographic boundary file, counties, 1:5,000,000, 2023",
  source_url = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_5m.zip",
  projection = "EPSG:6487 (NAD83(2011) / Maryland), y down",
  viewBox = paste(0, 0, width, height),
  counties = counties
)
dir.create(dirname(out), showWarnings = FALSE, recursive = TRUE)
write(toJSON(shapes, auto_unbox = TRUE, digits = NA), out)
message("Wrote ", out, " (", file.size(out), " bytes, viewBox ", shapes$viewBox, ")")
