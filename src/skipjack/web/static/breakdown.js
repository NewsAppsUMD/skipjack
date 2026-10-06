// Charts for the methods and removals pages: one small chart per group, each showing the
// total of the 12 months ending at every month. Data comes in a JSON script tag.
import {
  ChartGroup, addCard, count, full, lastIndex, monthLabel, redrawOnResize, renderLineChart, token,
} from "./charthelpers.js";

const data = JSON.parse(document.getElementById("trend-data").textContent);
const neutral = token("--party-minor");
const box = document.getElementById("cards");
const last = (values) => lastIndex(values);

const cards = [];
cards.push({
  title: data.totalTitle,
  ...addCard(box, { title: data.totalTitle, wide: true }),
  values: data.total.rolling,
  monthly: data.total.monthly,
  rolling: true,
  wide: true,
  caption: (i) => `${full(data.total.rolling[i])} in the 12 months to ${monthLabel(data.dates[i])}`,
});
for (const extra of data.extras) {
  cards.push({
    title: extra.title,
    ...addCard(box, { title: extra.title }),
    values: extra.values,
    monthly: null,
    rolling: false,
    caption: (i) => `${full(extra.values[i])} in ${monthLabel(data.dates[i])}; ${extra.note}`,
  });
}
for (const g of data.groups) {
  cards.push({
    title: g.name,
    ...addCard(box, { title: g.name }),
    values: g.rolling,
    monthly: g.monthly,
    rolling: true,
    caption: (i) => `${full(g.rolling[i])} in the 12 months to ${monthLabel(data.dates[i])}`,
  });
}

let charts = [];
function draw() {
  charts.forEach((c) => c.destroy());
  charts = [];
  const group = new ChartGroup();
  for (const card of cards) {
    const i = last(card.values);
    card.sub.textContent = i < 0 ? "Not reported in these months" : card.caption(i);
    charts.push(
      renderLineChart(card.chart, {
        dates: data.dates,
        series: [{ name: card.title, color: neutral, values: card.values }],
        format: count,
        labelFormat: full,
        detail: card.rolling ? (k, j) => `${full(card.monthly[j])} that month` : null,
        height: card.wide ? 220 : 130,
        group,
        ariaLabel: `${card.title} over time. Yearly figures are in the table below.`,
      }),
    );
  }
}

draw();
redrawOnResize(box, draw);
