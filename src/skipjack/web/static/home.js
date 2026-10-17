// The home page's three charts: active voters by party, the running total of young
// registrants, and the rolling 12-month new registrations and removals.
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

// Young registrants, running total by week.
if (data.young) {
  const y = data.young;
  const palette = [token("--series-new"), token("--series-removed"), token("--text-muted")];
  const colorFor = (index, total) => palette[Math.min(total - 1 - index, palette.length - 1)];
  const series = y.series.map((s, i) => ({
    name: String(s.year),
    color: colorFor(i, y.series.length),
    values: s.values,
    year: s.year,
  }));
  legend("home-young-legend", series.map((s) => ({ name: s.name, color: s.color })));
  const monthLabels = y.weeks.map(() => "");
  for (const [name, week] of Object.entries(y.month_weeks)) monthLabels[week - 1] = name;
  const markers = [
    ...series
      .filter((s) => y.primary_weeks[s.year])
      .map((s) => ({ index: y.primary_weeks[s.year] - 1, kind: "primary", color: s.color })),
    { index: y.cutoff_week - 1, kind: "general" },
  ];
  charts.push({
    box: document.getElementById("home-young"),
    draw: (box) =>
      renderLineChart(box, {
        dates: y.weeks.map(String),
        xLabels: monthLabels,
        tipLabel: (i) => `Week of ${y.week_labels[i]}`,
        xInset: 8,
        series,
        markers,
        format: (v) => (v >= 1e4 ? `${Math.round(v / 1e3)}k` : full(v)),
        labelFormat: full,
        height: 240,
        endLabels: true,
        ariaLabel: "Running total of first-time registrations by 18-to-22-year-olds, week by week, for each election year.",
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
