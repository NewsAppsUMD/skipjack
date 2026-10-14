# Site brand: logo, hero title, favicon — design

Date: 2026-10-14
Approved: README-mirrored centered hero; logo in the nav wordmark; derived favicon assets;
hero-only title enlargement.

## Context

The logo already ships as `src/skipjack/web/static/img/skipjack.webp` (640×640 line drawing
of a skipjack in a circle; the README's image). It is copied into every build by
`static_site.py`'s `shutil.copytree(STATIC_DIR, ...)` at `site/static/img/` but is never
referenced by a template. No favicon exists. "Skipjack" appears twice: the header nav
wordmark (`.brand`, small) and the homepage hero `<hgroup><h1>Skipjack</h1><p>…</p></hgroup>`
at Pico's default h1 size. Pillow is available in the project environment; `hgroup` is used
only by `home.html`.

## Changes

### 1. Homepage hero (mirrors the README)

`home.html`'s hero: the logo image above the title, both centered, tagline under them — a
`div.hero` wrapper holding the logo img and an inner, plain hgroup (amended during
implementation: the img must not be a child of hgroup — the wrapper div carries the hero
class). Sizes: logo display block ~8rem; the h1 ~2.75rem (from Pico's hgroup default) via a
scoped class on the wrapper so no other page's headings move.

### 2. Header nav brand (every page)

`base.html`'s brand link gains a small logo (~1.5rem, rounded) before the existing
`<strong>Skipjack</strong>` wordmark; the nav's flex layout takes the inline image.

### 3. Favicon (derived once, committed)

`static/img/favicon.ico` (48/32/16) and `static/img/apple-touch-icon.png` (180px)
generated from `skipjack.webp` by a one-off `uv run --with pillow` command; committed next
to the webp. `base.html` `<head>` gains `<link rel="icon">` and
`<link rel="apple-touch-icon">` carrying `{{ base }}` so the live app and the
`/skipjack`-prefixed Pages deploy both resolve. Linking the webp directly was rejected
(Safari ignores webp favicons); build-time image processing and data-URI inlining were
rejected (machinery/bloat for two committed assets).

### Implementation shape

Template + CSS only for the markup: no Python changes, no build changes — the existing
`copytree` ships the new committed icons. All styling lives in `style.css`
(`.hero-logo`, the scoped hero size, `.brand-logo`). (Amended during implementation: the
new tests import Pillow directly, so pillow was promoted to an explicit dev dependency in
`pyproject.toml`/`uv.lock`.)

## Verification

- New tests in `tests/test_web.py`: the home page renders the hero logo and hero class;
  a rendered page carries the favicon and apple-touch-icon links plus the brand logo;
  the two derived icon files exist with the right dimensions (Pillow).
- `uv run pytest -q` full suite green; local `make site SITE_BASE=/skipjack` rebuild;
  spot-check the built index for the asset paths and favicon links.

## Out of scope

Changing other pages' headings or the nav wordmark's size; dark-theme logo variants
(the app is fixed `data-theme="light"`); og:image/social cards.