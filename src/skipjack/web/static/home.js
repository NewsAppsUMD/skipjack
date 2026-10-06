// The home page's chart: active voters by party, monthly.
import { count, full, redrawOnResize, renderLineChart, token } from "./charthelpers.js";

const data = JSON.parse(document.getElementById("home-data").textContent);
const COLORS = {
  Democratic: token("--party-dem"),
  Republican: token("--party-rep"),
  Unaffiliated: token("--party-una"),
};
const series = data.series.map((s) => ({ ...s, color: COLORS[s.name] }));

const legend = document.getElementById("home-legend");
for (const s of series) {
  const li = document.createElement("li");
  const key = document.createElement("span");
  key.className = "key";
  key.style.background = s.color;
  li.append(key, document.createTextNode(s.name));
  legend.appendChild(li);
}

const box = document.getElementById("home-chart");
let drawn = null;
function draw() {
  drawn?.destroy();
  drawn = renderLineChart(box, {
    dates: data.dates,
    series,
    format: count,
    labelFormat: full,
    height: 300,
    endLabels: true,
    ariaLabel: "Active registered voters by party, monthly. The county trends page has the full table.",
  });
}

draw();
redrawOnResize(box.parentElement, draw);
