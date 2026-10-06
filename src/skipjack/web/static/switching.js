// Monthly party changes by the party the voter left, with the state's election months marked.
// The three big parties share a chart; the other parties are far smaller and get their own.
import {
  addCard, count, full, lastIndex, monthLabel, redrawOnResize, renderLineChart, token,
} from "./charthelpers.js";

const data = JSON.parse(document.getElementById("switching-data").textContent);
const series = data.series.map((s) => ({ ...s, color: token(s.token) }));
const box = document.getElementById("switching-chart");

const main = addCard(box, {
  title: "Voters who changed party, by the party they left",
  wide: true,
  legend: series.map((s) => ({ name: s.name, color: s.color })),
});
main.sub.textContent =
  "Monthly, statewide. Dashed lines mark primary elections, dotted lines general elections.";

const other = addCard(box, { title: "Voters leaving other parties", wide: true });
const last = lastIndex(data.other.values);
other.sub.textContent =
  `${full(data.other.values[last])} in ${monthLabel(data.dates[last])}. ` +
  "Greens, Libertarians, Independents and the rest, on their own scale." +
  (data.other.note ? ` ${data.other.note}` : "");

let drawn = [];
function draw() {
  drawn.forEach((c) => c.destroy());
  drawn = [
    renderLineChart(main.chart, {
      dates: data.dates,
      series,
      format: count,
      labelFormat: full,
      markers: data.markers,
      height: 360,
      endLabels: true,
      ariaLabel: "Voters who changed party each month, by the party they left. Figures are in the tables below.",
    }),
    renderLineChart(other.chart, {
      dates: data.dates,
      series: [{ name: data.other.name, color: token(data.other.token), values: data.other.values }],
      format: count,
      labelFormat: full,
      markers: data.markers.map(({ label, ...rest }) => rest),
      height: 180,
      ariaLabel: "Voters who left minor parties each month.",
    }),
  ];
}

draw();
redrawOnResize(box, draw);
