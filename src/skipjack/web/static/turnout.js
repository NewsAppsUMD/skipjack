// The election's trend chart: mail-in ballot requests, ballots sent, and ballots
// returned, over the State Board's press-room snapshots. Data comes in a JSON
// script tag; dates are days, labeled as dates, so the label callbacks replace the
// default month labels.
import { count, full, redrawOnResize, renderLineChart, token } from "./charthelpers.js";

const data = JSON.parse(document.getElementById("turnout-data").textContent);
const COLORS = {
  Requests: token("--party-una"),
  Sent: token("--party-dem"),
  Received: token("--party-rep"),
  Accepted: token("--party-minor"),
};
const series = data.series.map((s) => ({ ...s, color: COLORS[s.name] }));

const legend = document.getElementById("turnout-legend");
for (const s of series) {
  const li = document.createElement("li");
  const key = document.createElement("span");
  key.className = "key";
  key.style.background = s.color;
  li.append(key, document.createTextNode(s.name));
  legend.appendChild(li);
}

const shortDate = (d) => d;  // yyyy-mm-dd reads fine

const box = document.getElementById("turnout-chart");
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
    tipLabel: (i) => data.dates[i],
    xLabels: data.dates.map(shortDate),
    ariaLabel:
      "Mail-in ballot requests, sent, and received over the campaign. The county table below has the latest figures.",
  });
}

draw();
redrawOnResize(box.parentElement, draw);
