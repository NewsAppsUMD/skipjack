// The precinct page: draw the picked contest's table from the county's published precinct file.
// The files are gzipped CSV; a server may already have undone the compression (Content-Encoding:
// gzip), so the bytes are decompressed only when they still start with the gzip magic number.
// The figures are never copied into the page: the file is the one source.

const data = document.getElementById("precinct-contests");
const contests = JSON.parse(data.textContent);
const downloads = data.dataset.downloads;
const select = document.getElementById("precinct-contest");
const table = document.getElementById("precinct-table");
const status = document.getElementById("precinct-status");
const files = {}; // file -> Promise of its parsed rows, fetched once per page

async function text(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const bytes = new Uint8Array(await resp.arrayBuffer());
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return new TextDecoder().decode(bytes);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return await new Response(stream).text();
}

// RFC 4180: a quoted field may hold commas, doubled quotes and newlines. A leading byte-order
// mark is dropped and a blank line is skipped.
function parse(csv) {
  csv = csv.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const ch = csv[i];
    if (quoted) {
      if (ch === '"' && csv[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && csv[i + 1] === "\n") i++;
      row.push(field);
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = []; field = "";
    } else field += ch;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows;
  return body.map((r) => Object.fromEntries(header.map((h, j) => [h, r[j] ?? ""])));
}

// a failed fetch is forgotten, so picking the contest again retries it
function load(file) {
  if (!files[file]) {
    const rows = text(downloads + file).then(parse);
    rows.catch(() => {
      if (files[file] === rows) delete files[file];
    });
    files[file] = rows;
  }
  return files[file];
}

// The CSV prints the board's district ("01"); the database, and so the picker, holds it as the
// loader normalizes it (skipjack.pipeline.build_db._normalize_district): an all-digit district
// drops its leading zeros, anything else ("1A", "") stays.
const district = (d) => (/^[0-9]+$/.test(d) ? String(parseInt(d, 10)) : d);
// A ballot line is a name under a party: one candidate on two party lines is two columns.
const line = (name, party) => `${name}\u0000${party ?? ""}`;
const num = (s) => (s === undefined || s === "" ? null : Number(s));
const add = (a, b) => (a == null ? b : b === null ? a : a + b);
const fmt = (n) => (n === null ? "—" : n.toLocaleString("en-US"));

// kind: "num" for a number column, "row" for a row's precinct heading
function cell(tag, value, kind) {
  const el = document.createElement(tag);
  if (tag === "th") el.scope = kind === "row" ? "row" : "col";
  if (kind === "num") el.className = "num";
  el.textContent = value;
  return el;
}

// A row per precinct, a column per ballot line in the county page's order (with its Against
// count for a retention question), and the votes cast where there is no Against.
function contestTable(c, rows) {
  const mine = rows.filter((r) => r.office === c.office && district(r.district) === c.district);
  const byPrecinct = new Map();
  for (const r of mine) {
    if (!byPrecinct.has(r.precinct)) {
      byPrecinct.set(r.precinct, { r, votes: new Map(), against: new Map() });
    }
    // a ballot line printed twice for one precinct is added, never overwritten
    const p = byPrecinct.get(r.precinct);
    const key = line(r.candidate, r.party);
    p.votes.set(key, add(p.votes.get(key), num(r.total_votes)));
    p.against.set(key, add(p.against.get(key), num(r.total_against)));
  }
  const head = [];
  for (const x of c.candidates) {
    const name = x.party ? `${x.name} (${x.party})` : x.name;
    head.push(c.has_against ? `${name}: for` : name);
    if (c.has_against) head.push(`${name}: against`);
  }
  if (!c.has_against) head.push("Votes cast");
  const body = [...byPrecinct.values()]
    .sort((a, b) => a.r.precinct.localeCompare(b.r.precinct))
    .map((p) => {
      const cells = [];
      for (const x of c.candidates) {
        cells.push(p.votes.get(line(x.name, x.party)) ?? null);
        if (c.has_against) cells.push(p.against.get(line(x.name, x.party)) ?? null);
      }
      if (!c.has_against) cells.push(cells.reduce((s, n) => s + (n || 0), 0));
      return { precinct: p.r.precinct, cong: p.r.congressional, legs: p.r.legislative, cells };
    });
  return { head, body };
}

// "001-012" sorts as 1012 when a heading is clicked (sortable.js reads data-sort)
const precinctOrder = (p) => {
  const m = /^(\d+)-(\d+)$/.exec(p);
  return m ? String(Number(m[1]) * 1000 + Number(m[2])) : "";
};

function draw(c, { head, body }) {
  const withDistricts = body.some((b) => b.cong || b.legs);
  const caption = document.createElement("caption");
  caption.className = "sr-only";
  caption.textContent = `${c.title} in each precinct`;
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  tr.append(cell("th", "Precinct"));
  if (withDistricts) tr.append(cell("th", "Congressional district"), cell("th", "Legislative district"));
  for (const h of head) tr.append(cell("th", h, "num"));
  thead.append(tr);
  const tbody = document.createElement("tbody");
  const sums = head.map(() => null);
  for (const b of body) {
    const row = document.createElement("tr");
    const th = cell("th", b.precinct, "row");
    th.dataset.sort = precinctOrder(b.precinct);
    row.append(th);
    if (withDistricts) row.append(cell("td", b.cong), cell("td", b.legs));
    b.cells.forEach((n, i) => {
      row.append(cell("td", fmt(n), "num"));
      if (n !== null) sums[i] = (sums[i] || 0) + n;
    });
    tbody.append(row);
  }
  // the column sums: the county page's totals (election-day votes through 2018)
  const tfoot = document.createElement("tfoot");
  const total = document.createElement("tr");
  total.append(cell("th", "Total", "row"));
  if (withDistricts) total.append(cell("td", ""), cell("td", ""));
  for (const n of sums) total.append(cell("td", fmt(n), "num"));
  tfoot.append(total);
  table.replaceChildren(caption, thead, tbody, tfoot);
  table.hidden = false;
  // sortable.js enabled the page's tables before this one had headings
  window.skipjackSortable?.enable(table);
  status.textContent = `${c.title}: ${body.length.toLocaleString("en-US")} precincts.`;
}

async function show(anchor) {
  let c = contests.find((x) => x.anchor === anchor);
  if (!c) {
    // a hash naming no contest (or none): the first, and the address says so
    c = contests[0];
    if (location.hash) history.replaceState(null, "", `#${c.anchor}`);
  }
  select.value = c.anchor;
  status.textContent = "Loading…";
  try {
    const rows = await load(c.file);
    if (select.value !== c.anchor) return; // another contest was picked while this one loaded
    draw(c, contestTable(c, rows));
  } catch (err) {
    if (select.value !== c.anchor) return; // a stale failure must not hide another table
    table.hidden = true;
    status.textContent = `Could not load ${c.file.split("/").pop()} (${err.message}).`;
  }
}

select.addEventListener("change", () => {
  history.replaceState(null, "", `#${select.value}`);
  show(select.value);
});
// a malformed escape in the hash ("#%E0") names no contest
function hashAnchor() {
  try {
    return decodeURIComponent(location.hash.slice(1));
  } catch {
    return "";
  }
}
window.addEventListener("hashchange", () => show(hashAnchor()));
show(hashAnchor());
