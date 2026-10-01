// Click a column heading to sort a table. Tables opt in with the data-sortable attribute.
// Works from the cell text: "1,234" and "72%" sort as numbers, "<10" and dashes sort last.
// Rows keep their server-rendered order until a heading is clicked.

(function () {
  // A value is a number when the cell text starts with one: "1,234", "72%", "10,742 (23.8%)".
  const LEADING_NUMBER = /^-?\d[\d,]*\.?\d*/;

  function cellValue(cell) {
    const text = cell.textContent.trim();
    const match = text.match(LEADING_NUMBER);
    return { number: match ? parseFloat(match[0].replace(/,/g, "")) : null, text };
  }

  function sortTable(table, index, descending) {
    const body = table.tBodies[0];
    const rows = Array.from(body.rows).map((row, original) => ({
      row,
      original,
      value: cellValue(row.cells[index]),
    }));
    const numeric = rows.some((r) => r.value.number !== null);
    rows.sort((a, b) => {
      let result;
      if (numeric) {
        // Missing values always go to the bottom, whichever way we sort.
        if (a.value.number === null || b.value.number === null) {
          if (a.value.number === b.value.number) return a.original - b.original;
          return a.value.number === null ? 1 : -1;
        }
        result = a.value.number - b.value.number;
      } else {
        result = a.value.text.localeCompare(b.value.text, undefined, { numeric: true });
      }
      return (descending ? -result : result) || a.original - b.original;
    });
    for (const r of rows) body.appendChild(r.row);
    return numeric;
  }

  function enable(table) {
    const headings = table.tHead ? Array.from(table.tHead.rows[0].cells) : [];
    headings.forEach((th, index) => {
      th.tabIndex = 0;
      th.setAttribute("role", "columnheader");
      th.classList.add("sortable");
      const activate = () => {
        const wasAscending = th.getAttribute("aria-sort") === "ascending";
        const wasDescending = th.getAttribute("aria-sort") === "descending";
        // First click: largest first for numbers, A to Z for text. Click again to reverse.
        const probe = table.tBodies[0].rows[0];
        const numberFirst = probe && cellValue(probe.cells[index]).number !== null;
        const descending = wasAscending ? true : wasDescending ? false : numberFirst;
        headings.forEach((h) => h.removeAttribute("aria-sort"));
        sortTable(table, index, descending);
        th.setAttribute("aria-sort", descending ? "descending" : "ascending");
      };
      th.addEventListener("click", activate);
      th.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
    });
  }

  document.querySelectorAll("table[data-sortable]").forEach(enable);
})();
