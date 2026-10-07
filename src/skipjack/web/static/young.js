// Charts for the young voters page: a running total of new registrations by week for each
// election year, and the share of new registrants who are unaffiliated, year by year.
import {
  addCard, full, redrawOnResize, renderLineChart, token,
} from "./charthelpers.js";

const data = JSON.parse(document.getElementById("young-data").textContent);
const percent = (v) => `${Math.round(v)}%`;
const percentOne = (v) => `${v.toFixed(1)}%`;

// Newest year in green, the one before in orange, older ones muted.
const palette = [token("--series-new"), token("--series-removed"), token("--text-muted")];
const colorFor = (index, total) => palette[Math.min(total - 1 - index, palette.length - 1)];
const series = data.series.map((s, i) => ({
  name: String(s.year),
  color: colorFor(i, data.series.length),
  values: s.values,
  year: s.year,
}));

const runningBox = document.getElementById("running-chart");
const running = addCard(runningBox, {
  title: "New registrations by 18-to-22-year-olds, running total",
  wide: true,
  legend: series.map((s) => ({ name: s.name, color: s.color })),
});
running.sub.textContent =
  `Each line adds up one year's first-time registrants through the year. Dashed lines mark each year's primary; ` +
  `the dotted line marks ${data.cutoff_label}, where this year's data ends.`;

const monthLabels = data.weeks.map(() => "");
for (const [name, week] of Object.entries(data.month_weeks)) monthLabels[week - 1] = name;
const markers = [
  ...series
    .filter((s) => data.primary_weeks[s.year])
    .map((s) => ({ index: data.primary_weeks[s.year] - 1, kind: "primary", color: s.color })),
  { index: data.cutoff_week - 1, kind: "general" },
];

const trendBox = document.getElementById("trend-chart");
const trend = addCard(trendBox, {
  title: "Share of new registrants who are unaffiliated",
  wide: true,
  legend: [
    { name: "Aged 18 to 22 when they registered", color: token("--party-una") },
    { name: "23 and older", color: token("--party-minor") },
  ],
});
trend.sub.textContent =
  `Registrations from January 1 to ${data.cutoff_label} of each year. Party is each voter's party today, not the party they chose when they registered.`;

let drawn = [];
function draw() {
  drawn.forEach((c) => c.destroy());
  drawn = [
    renderLineChart(running.chart, {
      dates: data.weeks.map(String),
      xLabels: monthLabels,
      tipLabel: (i) => `Week of ${data.week_labels[i]}`,
      xInset: 8,
      series,
      markers,
      format: (v) => (v >= 1e4 ? `${Math.round(v / 1e3)}k` : full(v)),
      labelFormat: full,
      height: 320,
      endLabels: true,
      ariaLabel: "Running total of first-time registrations by 18-to-22-year-olds, week by week, for each election year.",
    }),
    renderLineChart(trend.chart, {
      dates: data.years.map(String),
      xLabels: data.years.map((y) => (y % 2 === 0 ? String(y) : "")),
      tipLabel: (i) => `Registered ${data.years[i]}, January 1 to ${data.cutoff_label}`,
      xInset: 12,
      series: [
        { name: "18 to 22", color: token("--party-una"), values: data.young },
        { name: "23 and older", color: token("--party-minor"), values: data.older },
      ],
      format: percent,
      labelFormat: percentOne,
      detail: (k, i) => {
        const n = (k === 0 ? data.young_n : data.older_n)[i];
        return n == null ? null : `${full(n)} registrants`;
      },
      height: 280,
      endLabels: true,
      ariaLabel: "Share of new registrants who are unaffiliated, by age at registration, by year.",
    }),
  ];
}

draw();
redrawOnResize(runningBox, draw);
