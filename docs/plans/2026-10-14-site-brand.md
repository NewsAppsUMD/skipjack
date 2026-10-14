# Site brand: logo, hero title, favicon — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Put the README's skipjack logo on the site (header nav + homepage hero), enlarge the hero title, and ship the logo as a favicon.

**Architecture:** Template + CSS only for markup (the build already copies `static/`); two derived icon assets generated once from `skipjack.webp` with Pillow (already in the environment) and committed. All styling in `style.css`; scoping keeps every other page's headings untouched.

**Tech Stack:** Jinja2 templates (`src/skipjack/web/templates/`), `style.css`, FastAPI TestClient tests, Pillow for the one-off icon generation.

**Design doc:** `docs/plans/2026-10-14-site-brand-design.md` (committed).

**Facts you must know:**
- `src/skipjack/web/static/img/skipjack.webp`: 640×640, the README logo; already copied into every build at `site/static/img/` (`static_site.py:223` copytreet of `STATIC_DIR`) — it is just unreferenced today. No favicon exists; `base.html`'s head has none.
- `hgroup` is used only by `home.html` (lines 10-13: `<h1>Skipjack</h1><p>Navigating the Maryland electorate through data</p>`); `base.html`'s brand is `src/skipjack/web/templates/base.html:16` (`<li><a href="{{ base }}/" class="brand"><strong>Skipjack</strong></a></li>`); `base.html:1-10` is the head.
- `tests/test_web.py` uses a module-level `TestClient(app)` and runs only when `skipjack.db` exists (`pytestmark`); with no base-path override, `{{ base }}` renders empty, so links look like `/static/img/...`.
- iOS flattens transparent apple-touch icons onto black, so the touch icon is generated flattened onto white.
- The design's approved choices: hero is README-styled (logo over an enlarged centered title over the tagline), the nav wordmark stays its current size, and every other page's headings are untouched.

---

### Task 1: Favicon assets, head links, and the nav brand logo

**Files:**
- Create: `src/skipjack/web/static/img/favicon.ico`, `src/skipjack/web/static/img/apple-touch-icon.png`
- Modify: `src/skipjack/web/templates/base.html`, `src/skipjack/web/static/style.css`
- Test: `tests/test_web.py`

**Step 1: Write the failing tests** — append to `tests/test_web.py`:

```python
def test_every_page_carries_the_favicon_and_brand_logo():
    html = client.get("/about").text
    assert '<link rel="icon" href="/static/img/favicon.ico"' in html
    assert '<link rel="apple-touch-icon" href="/static/img/apple-touch-icon.png">' in html
    assert 'class="brand-logo"' in html


def test_the_favicon_assets_derive_from_the_logo():
    from PIL import Image

    from skipjack.web.paths import STATIC_DIR

    ico = Image.open(STATIC_DIR / "img" / "favicon.ico")
    assert ico.info["sizes"] == {(48, 48), (32, 32), (16, 16)}
    touch = Image.open(STATIC_DIR / "img" / "apple-touch-icon.png")
    assert touch.size == (180, 180)
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web.py::test_every_page_carries_the_favicon_and_brand_logo tests/test_web.py::test_the_favicon_assets_derive_from_the_logo -q`
Expected: FAIL — missing link tags and `FileNotFoundError` on `favicon.ico`.

**Step 3: Generate the two derived assets (one-off; commit the outputs)**

Run from the repo root:

```bash
uv run python - <<'PY'
from PIL import Image

src = Image.open("src/skipjack/web/static/img/skipjack.webp").convert("RGBA")

# iOS renders transparent apple-touch icons on black, so flatten onto white.
touch = Image.new("RGB", src.size, "white")
touch.paste(src, mask=src.split()[3])
touch.resize((180, 180)).save("src/skipjack/web/static/img/apple-touch-icon.png")

src.save("src/skipjack/web/static/img/favicon.ico", sizes=[(48, 48), (32, 32), (16, 16)])
print("derived: apple-touch-icon.png 180px, favicon.ico 48/32/16")
PY
```

**Step 4: Wire the templates**

`src/skipjack/web/templates/base.html` — in `<head>`, immediately after the htmx script tag (line 9) and before `{% block head %}`:

```html
    <link rel="icon" href="{{ base }}/static/img/favicon.ico" sizes="48x48 32x32 16x16">
    <link rel="apple-touch-icon" href="{{ base }}/static/img/apple-touch-icon.png">
```

and the brand link (line 16) becomes:

```html
                <li><a href="{{ base }}/" class="brand"><img class="brand-logo" src="{{ base }}/static/img/skipjack.webp" alt="" width="24" height="24"><strong>Skipjack</strong></a></li>
```

(the nav instance is decorative: the wordmark is the anchor's text, so the image's alt is empty.)

**Step 5: The brand CSS** — in `src/skipjack/web/static/style.css`, replace the existing `.brand` rule (lines 16-19) with:

```css
.brand {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: #1B3A4B !important;
    text-decoration: none;
}

.brand-logo {
    width: 1.5rem;
    height: auto;
    border-radius: 50%;
}
```

**Step 6: Run the tests**

Run: `uv run pytest tests/test_web.py -q`
Expected: all pass (the two new ones green; the pre-existing smoke tests untouched).

**Step 7: Commit**

```bash
git add src/skipjack/web/static/img/favicon.ico src/skipjack/web/static/img/apple-touch-icon.png src/skipjack/web/templates/base.html src/skipjack/web/static/style.css tests/test_web.py
git commit -m "the logo in the nav and the tab: favicon and brand mark

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: The homepage hero — logo, centered, enlarged title

**Files:**
- Modify: `src/skipjack/web/templates/home.html` (the hgroup, lines 10-13), `src/skipjack/web/static/style.css`
- Test: `tests/test_web.py`

**Step 1: Write the failing test** — append:

```python
def test_the_home_page_carries_the_hero_logo_and_enlarged_title():
    html = client.get("/").text
    assert 'class="hero-logo"' in html
    assert (
        '<img class="hero-logo" src="/static/img/skipjack.webp" width="128" height="128"' in html
    )
    assert 'class="hero"' in html
    assert "Navigating the Maryland electorate through data" in html
    # the nav brand keeps its own size: the hero class comes from the hgroup alone
    assert html.count('class="hero"') == 1
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web.py::test_the_home_page_carries_the_hero_logo_and_enlarged_title -q`
Expected: FAIL (no hero-logo markup yet).

**Step 3: The hero markup** — `src/skipjack/web/templates/home.html`, replace the hgroup:

```html
<hgroup>
    <h1>Skipjack</h1>
    <p>Navigating the Maryland electorate through data</p>
</hgroup>
```

with:

```html
<hgroup class="hero">
    <img class="hero-logo" src="{{ base }}/static/img/skipjack.webp" width="128" height="128"
        alt="Line drawing of a Chesapeake Bay skipjack sailing on blue waves, inside a circle">
    <h1>Skipjack</h1>
    <p>Navigating the Maryland electorate through data</p>
</hgroup>
```

**Step 4: The hero CSS** — in `style.css`, directly after the `.brand-logo` rule:

```css
/* The homepage's brand block: the README's logo over the title over the tagline. */
hgroup.hero {
    text-align: center;
}

hgroup.hero h1 {
    font-size: 2.75rem;
    margin-bottom: 0;
}

.hero-logo {
    display: block;
    width: 8rem;
    height: auto;
    margin: 0 auto 1rem;
}
```

**Step 5: Run the tests**

Run: `uv run pytest tests/test_web.py -q`
Expected: all pass.

**Step 6: Commit**

```bash
git add src/skipjack/web/templates/home.html src/skipjack/web/static/style.css tests/test_web.py
git commit -m "the homepage hero: the logo above a bigger Skipjack title

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Full verification and the local rebuild

**Step 1: Full suite**

Run: `uv run pytest -q`
Expected: 485 passed (482 + 3 new), same warnings as today.

**Step 2: Rebuild the local site and spot-check**

Run: `make site SITE_BASE=/skipjack`
Expected: pages + files count grows slightly (two new icons); link check passes.

```bash
grep -c "favicon.ico" site/index.html && grep -c "hero-logo" site/index.html && ls site/static/img/
```
Expected: both greps ≥ 1; `apple-touch-icon.png` and `favicon.ico` in the listing.

**Step 3: No commit needed** (site/ is gitignored). Report the hero's rendered look via one final assertion: the built index contains the logo above the title (grep order check).

---

## Verification checklist (before declaring done)

- [ ] `uv run pytest -q` all pass (485).
- [ ] Favicon + apple-touch-icon resolve at `/static/img/` in the live app and in the built site.
- [ ] The hero: logo (8rem, centered) → `Skipjack` (~2.75rem, centered) → tagline, on `/` only.
- [ ] The nav wordmark keeps its size; other pages' h1s unchanged (hero class scoped).
- [ ] `check_links` passes; the new static paths exist in `site/static/img/`.