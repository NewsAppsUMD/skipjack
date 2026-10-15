// County maps (templates/_maps.html): a hover/tap readout, and buttons that show one map of a
// group at a time. Without this script every map is shown and the <title>s still give values.

for (const fig of document.querySelectorAll(".county-map")) {
    const readout = fig.querySelector(".map-readout");
    for (const county of fig.querySelectorAll("[aria-label] > .map-county")) {
        const label = county.parentElement.getAttribute("aria-label");
        const show = () => { readout.textContent = label; };
        county.parentElement.addEventListener("mouseenter", show);
        county.parentElement.addEventListener("focus", show);
    }
    fig.querySelector("svg").addEventListener("mouseleave", () => { readout.textContent = ""; });
}

for (const group of document.querySelectorAll("[data-map-group]")) {
    const figures = [...group.querySelectorAll(".county-map")];
    if (figures.length < 2) continue;
    const bar = document.createElement("div");
    bar.className = "map-switch";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", group.getAttribute("aria-label") || "Map");
    const buttons = figures.map((fig, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = fig.dataset.title;
        b.addEventListener("click", () => select(i));
        bar.append(b);
        return b;
    });
    function select(i) {
        figures.forEach((fig, j) => { fig.hidden = i !== j; });
        buttons.forEach((b, j) => b.setAttribute("aria-pressed", String(i === j)));
    }
    group.prepend(bar);
    select(0);
}
