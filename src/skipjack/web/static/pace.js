// The election page's pace chart: the share of mail-in ballots sent that had come back,
// by days before election day, for the election in progress and the earlier elections it
// is compared with. Data comes in a JSON script tag; the axis is days out, counting down.
import { redrawOnResize, renderLineChart, token } from "./charthelpers.js";

const data = JSON.parse(document.getElementById("pace-data").textContent);
const COLORS = [token("--pace-now"), token("--pace-1"), token("--pace-2")];
const series = data.series.map((s, i) => ({ ...s, color: COLORS[i % COLORS.length] }));

const legend = document.getElementById("pace-legend");
for (const s of series) {
  const li = document.createElement("li");
  const key = document.createElement("span");
  key.className = "key";
  key.style.background = s.color;
  li.append(key, document.createTextNode(s.name));
  legend.appendChild(li);
}

const box = document.getElementById("pace-chart");
const dayText = (d) => (d === 0 ? "election day" : `${d} day${d === 1 ? "" : "s"} out`);
const pct = (v) => `${Math.round(v)}%`;

let drawn = null;
function draw() {
  drawn?.destroy();
  // ~64px between labels, as the turnout chart spaces its dates; election day always labelled
  const step = Math.max(1, Math.ceil((data.days.length * 64) / Math.max(box.clientWidth, 200)));
  const last = data.days.length - 1;
  const xLabels = data.days.map((d, i) =>
    i === last ? "election day" : (last - i) % step ? "" : String(d),
  );
  drawn = renderLineChart(box, {
    dates: data.days.map(String),
    series,
    format: pct,
    labelFormat: (v) => `${v.toFixed(1)}%`,
    height: 300,
    endLabels: true,
    bridgeGaps: true,
    tipLabel: (i) => dayText(data.days[i]),
    xLabels,
    ariaLabel:
      "Share of mail-in ballots sent that had come back, by days before election day, for this election and earlier ones. The table below has the figures.",
  });
}

draw();
redrawOnResize(box.parentElement, draw);
