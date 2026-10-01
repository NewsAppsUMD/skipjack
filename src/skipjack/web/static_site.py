"""Pre-render the whole site into plain files for static hosting such as GitHub Pages.

The live app and the static site share the same routes and templates. This module walks
the list of pages the database implies, asks the app for each one, and writes the HTML to
``<out>/<path>/index.html``. It then copies the static assets and the downloadable data,
and checks that every internal link in the result points at a file that exists.

Nothing here reads the raw archives or the raw voter file. The site is built only from
``skipjack.db`` and the aggregated files under ``data/``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from skipjack.web.app import app, configure_templates
from skipjack.web.db import get_db
from skipjack.web.downloads import SQLITE_NAME, download_files
from skipjack.web.paths import DATA_DIR, PROJECT_ROOT, STATIC_DIR
from skipjack.web.urls import normalize_base_path, slugify

logger = logging.getLogger(__name__)

DISTRICT_TYPES = ("congressional", "legislative")
EXTERNAL_PREFIXES = ("#", "http://", "https://", "//", "mailto:", "javascript:", "data:")
LINK_RE = re.compile(r'(?:href|src|action)="([^"]*)"|from "(/[^"]*)"')


class SiteBuildError(RuntimeError):
    """The site could not be built."""


@dataclass
class SiteReport:
    out_dir: Path
    base_path: str
    pages: int = 0
    files: int = 0
    bytes: int = 0
    problems: list[str] = field(default_factory=list)


def page_urls() -> list[str]:
    """Every page of the site, as the URL the live app serves it at."""
    with get_db() as conn:
        dates = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT report_date FROM voter_registration ORDER BY report_date DESC"
            )
        ]
        counties = [
            r[0]
            for r in conn.execute("SELECT DISTINCT county FROM voter_registration ORDER BY county")
        ]
        latest_snapshot = conn.execute(
            "SELECT meta_json FROM voter_file_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
    if not dates:
        raise SiteBuildError("skipjack.db has no registration data. Run `skipjack build-db` first.")

    urls = ["/", "/about", "/data/", "/voters/"]
    urls += [f"/voters/month/{d}/" for d in dates]
    urls += ["/voters/county/"] + [f"/voters/county/{slugify(c)}/" for c in counties]
    if latest_snapshot:
        meta = json.loads(latest_snapshot[0])
        urls += ["/voters/file/"]
        urls += [f"/voters/file/county/{slugify(c)}/" for c in meta["counties"]]
        urls += [f"/voters/file/districts/{kind}/" for kind in DISTRICT_TYPES]
    return urls


def page_path(out_dir: Path, url: str) -> Path:
    rel = url.strip("/")
    return out_dir / rel / "index.html" if rel else out_dir / "index.html"


def prepare_output(out_dir: Path) -> None:
    """Create an empty output folder, clearing a previous build but nothing else."""
    if (
        out_dir == PROJECT_ROOT
        or out_dir in PROJECT_ROOT.parents
        or PROJECT_ROOT / "src"
        in (
            out_dir,
            *out_dir.parents,
        )
    ):
        raise SiteBuildError(f"Refusing to build the site into {out_dir}")
    if out_dir.exists():
        entries = list(out_dir.iterdir())
        if entries and not (out_dir / ".nojekyll").exists():
            raise SiteBuildError(
                f"{out_dir} is not empty and does not look like a built site; "
                "choose another folder or empty it yourself."
            )
        for entry in entries:
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)


def check_links(out_dir: Path, base_path: str) -> list[str]:
    """Internal links that do not lead to a file in the built site."""
    problems = []
    for page in sorted(out_dir.rglob("*.html")):
        shown = "/" + page.relative_to(out_dir).as_posix()
        for match in LINK_RE.finditer(page.read_text(encoding="utf-8")):
            link = match.group(1) if match.group(1) is not None else match.group(2)
            if not link or link.startswith(EXTERNAL_PREFIXES) or link.startswith("?"):
                continue
            if not link.startswith("/"):
                problems.append(f"{shown}: relative link {link!r} breaks at other depths")
                continue
            target = link.split("#")[0].split("?")[0]
            if base_path:
                if target != base_path and not target.startswith(base_path + "/"):
                    problems.append(f"{shown}: {link!r} is missing the base path {base_path!r}")
                    continue
                target = target[len(base_path) :] or "/"
            rel = target.lstrip("/")
            candidates = [out_dir / rel]
            if not rel or target.endswith("/") or "." not in Path(rel).name:
                candidates.append(out_dir / rel / "index.html")
            if not any(c.is_file() for c in candidates):
                problems.append(f"{shown}: {link!r} does not exist")
    return problems


def build_site(
    out_dir: Path | str,
    base_path: str = "",
    include_db: bool = False,
    data_dir: Path | None = None,
) -> SiteReport:
    """Render the site into ``out_dir``.

    base_path: URL prefix when the site is not served from the root, for example "/skipjack"
        for https://<user>.github.io/skipjack/.
    include_db: also offer skipjack.db as a download (about 16 MB).
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per page is just noise
    out_dir = Path(out_dir).expanduser().resolve()
    base = normalize_base_path(base_path)
    data_dir = data_dir or DATA_DIR
    urls = page_urls()
    prepare_output(out_dir)
    report = SiteReport(out_dir=out_dir, base_path=base)

    templates = app.state.templates
    previous = {
        "base": templates.env.globals.get("base", ""),
        "static_site": templates.env.globals.get("static_site", False),
        "db_download": templates.env.globals.get("db_download", False),
    }
    configure_templates(templates, base=base, static_site=True, db_download=include_db)
    try:
        client = TestClient(app, follow_redirects=False)
        failures = []
        for url in dict.fromkeys(urls):  # drop duplicates, keep order
            response = client.get(url)
            if response.status_code != 200:
                failures.append(f"{url}: HTTP {response.status_code}")
                continue
            path = page_path(out_dir, url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(response.text, encoding="utf-8")
            report.pages += 1
        if failures:
            raise SiteBuildError("Pages failed to render:\n  " + "\n  ".join(failures))
        (out_dir / "404.html").write_text(
            templates.env.get_template("not_found.html").render(), encoding="utf-8"
        )
    finally:
        configure_templates(templates, **previous)

    shutil.copytree(STATIC_DIR, out_dir / "static")
    for relative in download_files(data_dir):
        target = out_dir / "downloads" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(data_dir / relative, target)
    if include_db:
        database = PROJECT_ROOT / SQLITE_NAME
        if not database.exists():
            raise SiteBuildError(f"{database} does not exist")
        (out_dir / "downloads").mkdir(exist_ok=True)
        shutil.copy2(database, out_dir / "downloads" / SQLITE_NAME)
    (out_dir / ".nojekyll").write_text("")  # tell GitHub Pages not to run Jekyll

    report.problems = check_links(out_dir, base)
    files = [p for p in out_dir.rglob("*") if p.is_file()]
    report.files = len(files)
    report.bytes = sum(p.stat().st_size for p in files)
    return report
