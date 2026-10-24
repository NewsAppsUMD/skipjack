// Tabs (templates/home.html): a [data-tabs] container's [data-tab] children become tab
// panels behind a tablist. Without this script every panel is shown, one after another.

for (const group of document.querySelectorAll("[data-tabs]")) {
    const panels = [...group.querySelectorAll(":scope > [data-tab]")];
    if (panels.length < 2) continue;
    group.classList.add("tabbed");
    const bar = document.createElement("div");
    bar.className = "tab-switch";
    bar.setAttribute("role", "tablist");
    bar.setAttribute("aria-label", group.getAttribute("aria-label") || "Sections");
    const tabs = panels.map((panel, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.id = `${panel.id}-tab`;
        b.textContent = panel.dataset.tab;
        b.setAttribute("role", "tab");
        b.setAttribute("aria-controls", panel.id);
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", b.id);
        panel.removeAttribute("aria-label");
        b.addEventListener("click", () => select(i));
        b.addEventListener("keydown", (e) => {
            const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
            if (!step) return;
            const next = (i + step + panels.length) % panels.length;
            select(next);
            tabs[next].focus();
        });
        bar.append(b);
        return b;
    });
    function select(i) {
        panels.forEach((panel, j) => { panel.hidden = i !== j; });
        tabs.forEach((b, j) => {
            b.setAttribute("aria-selected", String(i === j));
            b.tabIndex = i === j ? 0 : -1;
        });
    }
    group.prepend(bar);
    select(0);
}
