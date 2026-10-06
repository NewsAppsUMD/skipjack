// Small helpers shared by the trend pages' chart scripts.
import { renderLineChart, ChartGroup, monthLabel } from "./linechart.js";

export { renderLineChart, ChartGroup, monthLabel };

// A CSS custom property from the page's .viz-root, e.g. token("--party-dem").
export function token(name) {
  const root = document.querySelector(".viz-root");
  return getComputedStyle(root).getPropertyValue(name).trim();
}

// Axis labels: 1.2M, 85k, 950.
export function count(v) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`;
  return Math.round(v).toLocaleString();
}

export const full = (v) => Math.round(v).toLocaleString();

// A chart card: a heading, a one-line caption, an optional legend, and an empty chart box.
// Everything from the data goes in through textContent.
export function addCard(parent, { title, wide = false, legend = [] }) {
  const card = document.createElement("section");
  card.className = wide ? "viz-card wide" : "viz-card";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const sub = document.createElement("p");
  sub.className = "sub";
  card.append(heading, sub);
  if (legend.length) {
    const ul = document.createElement("ul");
    ul.className = "legend";
    for (const item of legend) {
      const li = document.createElement("li");
      const key = document.createElement("span");
      key.className = "key";
      key.style.background = item.color;
      li.append(key, document.createTextNode(item.name));
      ul.appendChild(li);
    }
    card.appendChild(ul);
  }
  const chart = document.createElement("div");
  card.appendChild(chart);
  parent.appendChild(card);
  return { sub, chart };
}

// Redraw `draw` whenever the box changes width, once resizing settles.
export function redrawOnResize(box, draw) {
  let timer;
  new ResizeObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(draw, 150);
  }).observe(box);
}

// The index of the last value that is not null, or -1.
export const lastIndex = (values) => values.findLastIndex((v) => v != null);
