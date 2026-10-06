// Small dependency-free SVG line chart for monthly registration series.
//
// renderLineChart(el, opts) draws into `el` and returns { setIndex, destroy }.
// Charts created with the same `group` share one crosshair, so hovering any chart
// highlights the same month in all of them.
//
// opts = {
//   dates:  ["2010-01", ...],
//   series: [{ name, color, values: [number|null, ...] }],
//   format: (v) => string,        // axis tick formatting
//   labelFormat: (v) => string,   // end-label and tooltip formatting (defaults to format)
//   detail: (seriesIdx, i) => string | null,  // optional secondary tooltip text
//   xLabels: string[]             // optional; a tick for every non-empty label (ordinal axes)
//   tipLabel: (i) => string       // optional tooltip heading (defaults to the month)
//   xInset: number                // optional px of padding inside the plot, left and right
//   markers: [{ index, label?, kind? }]  // optional vertical rules, e.g. election months;
//                                 // kind ("primary" or "general") picks the style
//   height, endLabels, group, ariaLabel
// }

const SVG_NS = "http://www.w3.org/2000/svg";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthLabel(d) {
  const [y, m] = d.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(max, count) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

export class ChartGroup {
  constructor() {
    this.charts = new Set();
  }
  set(i, source) {
    for (const c of this.charts) c.showIndex(i, c === source);
  }
  clear() {
    for (const c of this.charts) c.showIndex(null, false);
  }
}

export function renderLineChart(container, opts) {
  const {
    dates,
    series,
    format = (v) => v.toLocaleString(),
    labelFormat = format,
    detail = null,
    xLabels = null,
    tipLabel = null,
    xInset = 0,
    markers = [],
    height = 320,
    endLabels = false,
    group = null,
    ariaLabel = "Line chart",
  } = opts;

  container.replaceChildren();
  container.classList.add("lc");
  // Never wider than the box: the smallest grid cards are about 234px inside their padding.
  const width = Math.max(container.clientWidth, 200);
  const labelsOn = endLabels && width >= 560;
  const m = { top: 10, right: labelsOn ? 180 : 14, bottom: 26, left: 58 };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const n = dates.length;

  const max = Math.max(0, ...series.flatMap((s) => s.values.filter((v) => v != null)));
  const ticks = niceTicks(max, height < 200 ? 2 : 5);
  const yMax = ticks[ticks.length - 1];
  const x = (i) => m.left + xInset + (n <= 1 ? (w - 2 * xInset) / 2 : (i / (n - 1)) * (w - 2 * xInset));
  const y = (v) => m.top + h - (v / yMax) * h;

  const svg = el("svg", {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": ariaLabel,
    tabindex: "0",
    class: "lc-svg",
  });
  container.appendChild(svg);

  // Gridlines and y ticks.
  const grid = el("g", { class: "lc-grid" }, svg);
  for (const t of ticks) {
    el("line", { x1: m.left, x2: m.left + w, y1: y(t), y2: y(t),
                 class: t === 0 ? "lc-baseline" : "lc-gridline" }, grid);
    const label = el("text", { x: m.left - 8, y: y(t), class: "lc-tick", "text-anchor": "end",
                               "dominant-baseline": "middle" }, grid);
    label.textContent = format(t);
  }

  // X ticks: the supplied labels, or January of every k-th year (at least ~64px apart).
  if (xLabels) {
    xLabels.forEach((label, i) => {
      if (!label) return;
      const t = el("text", { x: x(i), y: m.top + h + 18, class: "lc-tick",
                             "text-anchor": "middle" }, grid);
      t.textContent = label;
    });
  } else {
    const years = dates.map((d, i) => [d, i]).filter(([d]) => d.endsWith("-01"));
    const yearPx = n > 1 ? (12 / (n - 1)) * w : w;
    const every = Math.max(1, Math.ceil(64 / yearPx));
    years.forEach(([d, i], k) => {
      if (k % every) return;
      const t = el("text", { x: x(i), y: m.top + h + 18, class: "lc-tick",
                             "text-anchor": "middle" }, grid);
      t.textContent = d.slice(0, 4);
    });
  }

  // Vertical rules, drawn first so the lines sit on top of them.
  for (const mk of markers) {
    if (mk.index < 0 || mk.index >= n) continue;
    el("line", { x1: x(mk.index), x2: x(mk.index), y1: m.top, y2: m.top + h,
                 class: `lc-marker lc-marker-${mk.kind ?? "primary"}` }, svg);
    if (mk.label) {
      const t = el("text", { x: x(mk.index), y: m.top + 9, class: "lc-marker-label",
                             "text-anchor": "middle" }, svg);
      t.textContent = mk.label;
    }
  }

  // Lines, broken where a party did not appear on the report.
  for (const s of series) {
    let d = "";
    let pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    el("path", { d, class: "lc-line", stroke: s.color }, svg);
  }

  // End labels with a line key; nudged apart with a leader line when they collide.
  if (labelsOn) {
    const items = series
      .map((s) => {
        const last = s.values.findLastIndex((v) => v != null);
        return last < 0 ? null : { s, last, y0: y(s.values[last]), y: y(s.values[last]) };
      })
      .filter(Boolean)
      .sort((a, b) => a.y0 - b.y0);
    const gap = 16;
    for (let k = 1; k < items.length; k++) {
      items[k].y = Math.max(items[k].y, items[k - 1].y + gap);
    }
    const overflow = items.length ? items[items.length - 1].y - (m.top + h) : 0;
    if (overflow > 0) items.forEach((it) => (it.y -= overflow));
    const lx = m.left + w + 10;
    for (const it of items) {
      const g = el("g", { class: "lc-endlabel" }, svg);
      if (Math.abs(it.y - it.y0) > 1) {
        el("path", { d: `M${x(it.last) + 3},${it.y0}L${lx - 2},${it.y}`, class: "lc-leader" }, g);
      }
      el("line", { x1: lx, x2: lx + 10, y1: it.y, y2: it.y, stroke: it.s.color,
                   class: "lc-key" }, g);
      const t = el("text", { x: lx + 15, y: it.y, "dominant-baseline": "middle",
                             class: "lc-endtext" }, g);
      const name = el("tspan", {}, t);
      name.textContent = `${it.s.name} `;
      const val = el("tspan", { class: "lc-endvalue" }, t);
      val.textContent = labelFormat(it.s.values[it.last]);
    }
  }

  // Crosshair layer.
  const hover = el("g", { class: "lc-hover", visibility: "hidden" }, svg);
  const rule = el("line", { y1: m.top, y2: m.top + h, class: "lc-rule" }, hover);
  const dots = series.map((s) => el("circle", { r: 4, fill: s.color, class: "lc-dot" }, hover));
  const hit = el("rect", { x: m.left, y: m.top, width: w, height: h, fill: "transparent" }, svg);

  const tip = document.createElement("div");
  tip.className = "lc-tip";
  tip.hidden = true;
  container.appendChild(tip);

  let current = null;
  const chart = {
    showIndex(i, withTip) {
      current = i;
      if (i == null) {
        hover.setAttribute("visibility", "hidden");
        tip.hidden = true;
        return;
      }
      hover.setAttribute("visibility", "visible");
      rule.setAttribute("x1", x(i));
      rule.setAttribute("x2", x(i));
      series.forEach((s, k) => {
        const v = s.values[i];
        dots[k].setAttribute("visibility", v == null ? "hidden" : "visible");
        if (v != null) {
          dots[k].setAttribute("cx", x(i));
          dots[k].setAttribute("cy", y(v));
        }
      });
      if (!withTip) { tip.hidden = true; return; }
      buildTip(i);
      tip.hidden = false;
      const tw = tip.offsetWidth;
      // Stay inside the plot so the tooltip never covers the end labels.
      const left = x(i) + 14 + tw > m.left + w ? x(i) - 14 - tw : x(i) + 14;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${m.top}px`;
    },
  };

  function buildTip(i) {
    tip.replaceChildren();
    const head = document.createElement("div");
    head.className = "lc-tip-date";
    head.textContent = tipLabel ? tipLabel(i) : monthLabel(dates[i]);
    tip.appendChild(head);
    const rows = series
      .map((s, k) => ({ s, k, v: s.values[i] }))
      .sort((a, b) => (b.v ?? -1) - (a.v ?? -1));
    for (const { s, k, v } of rows) {
      const row = document.createElement("div");
      row.className = "lc-tip-row";
      const key = document.createElement("span");
      key.className = "lc-tip-key";
      key.style.background = s.color;
      const val = document.createElement("strong");
      val.textContent = v == null ? "not on report" : labelFormat(v);
      const name = document.createElement("span");
      name.className = "lc-tip-name";
      const extra = v != null && detail ? detail(k, i) : null;
      name.textContent = [series.length > 1 ? s.name : "", extra ? `(${extra})` : ""]
        .filter(Boolean)
        .join(" ");
      row.append(key, val, name);
      tip.appendChild(row);
    }
  }

  const indexAt = (evt) => {
    const r = svg.getBoundingClientRect();
    const px = evt.clientX - r.left;
    const span = w - 2 * xInset;
    return Math.min(n - 1, Math.max(0, Math.round(((px - m.left - xInset) / span) * (n - 1))));
  };
  const set = (i) => (group ? group.set(i, chart) : chart.showIndex(i, true));
  const clear = () => (group ? group.clear() : chart.showIndex(null, false));

  hit.addEventListener("pointermove", (e) => set(indexAt(e)));
  hit.addEventListener("pointerleave", clear);
  svg.addEventListener("focus", () => set(current ?? n - 1));
  svg.addEventListener("blur", clear);
  svg.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -12, PageDown: 12 }[e.key];
    if (e.key === "Home") set(0);
    else if (e.key === "End") set(n - 1);
    else if (step) set(Math.min(n - 1, Math.max(0, (current ?? n - 1) + step)));
    else return;
    e.preventDefault();
  });

  if (group) group.charts.add(chart);
  return {
    destroy() {
      if (group) group.charts.delete(chart);
    },
  };
}
