// The home page's three charts: active voters by party, each party's share of active
// voters, and the rolling 12-month new registrations and removals.
import { count, full, monthLabel, redrawOnResize, renderLineChart, token } from "./charthelpers.js";

const data = JSON.parse(document.getElementById("home-data").textContent);

function legend(id, items) {
  const ul = document.getElementById(id);
  if (!ul) return;
  for (const item of items) {
    const li = document.createElement("li");
    const key = document.createElement("span");
    key.className = "key";
    key.style.background = item.color;
    li.append(key, document.createTextNode(item.name));
    ul.appendChild(li);
  }
}

const charts = [];

// Active voters by party.
if (data.party) {
  const COLORS = {
    Democratic: token("--party-dem"),
    Republican: token("--party-rep"),
    Unaffiliated: token("--party-una"),
  };
  const series = data.party.series.map((s) => ({ ...s, color: COLORS[s.name] }));
  legend("home-party-legend", series.map((s) => ({ name: s.name, color: s.color })));
  charts.push({
    box: document.getElementById("home-party"),
    draw: (box) =>
      renderLineChart(box, {
        dates: data.party.dates,
        series,
        format: count,
        labelFormat: full,
        height: 240,
        endLabels: true,
        ariaLabel: "Active registered voters by party, monthly. The county trends page has the full table.",
      }),
  });
}

// Each party's share of active voters.
if (data.share) {
  const COLORS = {
    Democratic: token("--party-dem"),
    Republican: token("--party-rep"),
    Unaffiliated: token("--party-una"),
  };
  const series = data.share.series.map((s) => ({ ...s, color: COLORS[s.name] }));
  const percent = (v) => `${Math.round(v)}%`;
  const percentOne = (v) => `${v.toFixed(1)}%`;
  legend("home-share-legend", series.map((s) => ({ name: s.name, color: s.color })));
  charts.push({
    box: document.getElementById("home-share"),
    draw: (box) =>
      renderLineChart(box, {
        dates: data.share.dates,
        series,
        format: percent,
        labelFormat: percentOne,
        height: 240,
        endLabels: true,
        ariaLabel: "Each party's share of active registered voters, monthly.",
      }),
  });
}

// New registrations and removals, 12 months rolling.
if (data.flow) {
  const f = data.flow;
  const series = [
    { name: "New registrations", color: token("--series-new"), values: f.new },
    { name: "Removals", color: token("--series-removed"), values: f.removed },
  ];
  legend("home-flow-legend", series.map((s) => ({ name: s.name, color: s.color })));
  charts.push({
    box: document.getElementById("home-flow"),
    draw: (box) =>
      renderLineChart(box, {
        dates: f.dates,
        series,
        format: count,
        labelFormat: full,
        tipLabel: (i) => `12 months to ${monthLabel(f.dates[i])}`,
        height: 220,
        endLabels: true,
        ariaLabel: "New registrations and removals, each totaled over the 12 months ending at every month.",
      }),
  });
}

for (const chart of charts) {
  let drawn = null;
  const redraw = () => {
    drawn?.destroy();
    drawn = chart.draw(chart.box);
  };
  redraw();
  redrawOnResize(chart.box.parentElement, redraw);
}
