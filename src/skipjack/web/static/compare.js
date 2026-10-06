// The comparison page's chart: the share of each registration-year cohort that is no longer
// on the later voter list.
import { full, redrawOnResize, renderLineChart, token } from "./charthelpers.js";

const data = JSON.parse(document.getElementById("compare-data").textContent);
const box = document.getElementById("cohort-chart");
const percent = (v) => `${Math.round(v)}%`;

let drawn = null;
function draw() {
  if (!box || !data.years.length) return;
  drawn?.destroy();
  drawn = renderLineChart(box, {
    dates: data.years.map(String),
    xLabels: data.years.map((y) => (y % 5 === 0 ? String(y) : "")),
    tipLabel: (i) => `Registered in ${data.years[i]}`,
    series: [{ name: "No longer on the list", color: token("--party-minor"), values: data.left }],
    format: percent,
    labelFormat: (v) => `${v.toFixed(1)}%`,
    detail: (k, i) => `${full(data.before[i])} on the ${data.earlier} list, ${full(data.after[i])} on the ${data.later} list`,
    height: 260,
    ariaLabel: "Share of each registration year's voters no longer on the later list.",
  });
}

draw();
if (box) redrawOnResize(box.parentElement, draw);
