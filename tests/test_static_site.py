"""Tests for the static site generator, against the built skipjack.db."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from skipjack.web import static_site
from skipjack.web.bulk import BULK_FILES
from skipjack.web.db import DB_PATH
from skipjack.web.downloads import DOWNLOAD_SUFFIXES, download_files
from skipjack.web.paths import DATA_DIR
from skipjack.web.static_site import SiteBuildError, build_site, check_links, page_urls
from skipjack.web.urls import normalize_base_path, resolve_slug, slugify


def _built() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM voter_registration").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


needs_db = pytest.mark.skipif(not _built(), reason="skipjack.db not built")


# --- helpers (no database needed) ----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Prince George's", "prince-georges"),
        ("St. Mary's", "st-marys"),
        ("Queen Anne's", "queen-annes"),
        ("Baltimore City", "baltimore-city"),
        ("Baltimore County", "baltimore-county"),
        ("Queen Anne’s", "queen-annes"),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_resolve_slug():
    names = ["Baltimore City", "Baltimore County", "St. Mary's"]
    assert resolve_slug("st-marys", names) == "St. Mary's"
    assert resolve_slug("atlantis", names) is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, ""), ("", ""), ("/", ""), ("skipjack", "/skipjack"), ("/skipjack/", "/skipjack"),
     (" /a/b/ ", "/a/b")],
)  # fmt: skip
def test_normalize_base_path(given, expected):
    assert normalize_base_path(given) == expected


def test_check_links_finds_missing_files_and_missing_prefix(tmp_path):
    (tmp_path / "voters").mkdir()
    (tmp_path / "voters" / "index.html").write_text("ok")
    (tmp_path / "index.html").write_text(
        '<a href="/site/voters/">ok</a> <a href="/site/nowhere/">bad</a> '
        '<a href="/voters/">unprefixed</a> <a href="https://example.com/">external</a> '
        '<a href="#top">anchor</a> <a href="relative.html">relative</a> '
        '<script>import x from "/site/static/missing.js"</script>'
    )
    problems = check_links(tmp_path, "/site")
    assert len(problems) == 4
    joined = "\n".join(problems)
    assert "/site/nowhere/" in joined and "missing the base path" in joined
    assert "relative" in joined and "missing.js" in joined


def test_prepare_output_refuses_a_folder_that_is_not_a_built_site(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(SiteBuildError, match="not empty"):
        static_site.prepare_output(tmp_path)
    assert (tmp_path / "precious.txt").exists()
    with pytest.raises(SiteBuildError, match="Refusing"):
        static_site.prepare_output(static_site.PROJECT_ROOT)


def test_downloads_are_only_aggregates():
    files = download_files(DATA_DIR)
    assert all(f.suffix in DOWNLOAD_SUFFIXES for f in files)
    assert all(f.parts[0] in {"voter_registration", "voter_file"} for f in files)


# --- full build ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def root_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site-root")
    return out, build_site(out)


@pytest.fixture(scope="module")
def project_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site-project") / "skipjack"
    return out, build_site(out, base_path="/skipjack", include_db=True)


@needs_db
def test_every_page_in_the_plan_is_written(root_site):
    out, report = root_site
    urls = list(dict.fromkeys(page_urls()))
    assert report.pages == len(urls) > 250
    for url in urls:
        rel = url.strip("/")
        assert (out / rel / "index.html").is_file(), url
    assert (out / "voters" / "month" / "2019-06" / "index.html").is_file()
    assert (out / "voters" / "county" / "prince-georges" / "index.html").is_file()
    assert (out / "voters" / "file" / "districts" / "legislative" / "index.html").is_file()


@needs_db
def test_trend_pages_scripts_and_combined_downloads_are_published(root_site, project_site):
    for out, _ in (root_site, project_site):
        for page in ("voters/methods", "voters/removals", "voters/party-switching", "errata"):
            assert (out / page / "index.html").is_file(), page
        for script in ("breakdown.js", "switching.js", "home.js", "charthelpers.js", "charts.css"):
            assert (out / "static" / script).is_file(), script
        for name in BULK_FILES:
            assert (out / "downloads" / "combined" / name).stat().st_size > 100_000, name
    # Module scripts and downloads carry the base path, like every other internal link.
    html = (project_site[0] / "voters" / "methods" / "index.html").read_text()
    assert 'src="/skipjack/static/breakdown.js"' in html
    assert 'href="/skipjack/downloads/combined/registration_summary_all.csv"' in html


@needs_db
def test_the_snapshot_comparison_is_published_once_per_newest_pair(root_site, project_site):
    urls = list(dict.fromkeys(page_urls()))
    assert urls.count("/voters/file/compare/") == 1
    for out, _ in (root_site, project_site):
        html = (out / "voters" / "file" / "compare" / "index.html").read_text()
        assert "This compares totals, not people." in html
        assert (out / "static" / "compare.js").is_file()
    # With only two snapshots there is no separate address for the one pair.
    assert not [u for u in urls if re.match(r"/voters/file/compare/\d{4}-", u)]
    html = (project_site[0] / "voters" / "file" / "compare" / "index.html").read_text()
    assert 'src="/skipjack/static/compare.js"' in html
    assert 'href="/skipjack/voters/file/county/kent/"' in html


@needs_db
def test_the_young_voters_page_is_published_with_its_script(root_site, project_site):
    assert "/voters/young/" in page_urls()
    for out, _ in (root_site, project_site):
        assert (out / "voters" / "young" / "index.html").is_file()
        assert (out / "static" / "young.js").is_file()
        assert (out / "downloads" / "voter_file" / "2026-08-12" / "new_registrants.json").is_file()
    html = (project_site[0] / "voters" / "young" / "index.html").read_text()
    assert 'src="/skipjack/static/young.js"' in html
    assert 'href="/skipjack/voters/file/county/calvert/"' in html
    assert "Young Voters" in (root_site[0] / "index.html").read_text()


@needs_db
def test_build_has_no_broken_links(root_site, project_site):
    assert root_site[1].problems == []
    assert project_site[1].problems == []


@needs_db
def test_support_files_for_github_pages(root_site):
    out, _ = root_site
    assert (out / ".nojekyll").is_file()
    assert "Page not found" in (out / "404.html").read_text()
    assert (out / "static" / "linechart.js").is_file()
    assert (out / "static" / "sortable.js").is_file()
    assert (out / "downloads" / "voter_registration" / "monthly" / "2026-08.csv").is_file()


@needs_db
def test_static_site_hides_the_live_only_explorer(root_site):
    out, _ = root_site
    for page in ("index.html", "voters/index.html", "voters/file/index.html"):
        html = (out / page).read_text()
        assert "/explorer/" not in html
        assert 'href="/data/"' in html
    assert "Downloads" in (out / "index.html").read_text()


@needs_db
def test_base_path_prefixes_every_internal_link(project_site):
    out, report = project_site
    assert report.base_path == "/skipjack"
    html = (out / "voters" / "file" / "index.html").read_text()
    assert 'href="/skipjack/static/style.css"' in html
    assert 'from "/skipjack/static/linechart.js"' in html
    assert 'href="/skipjack/voters/file/county/kent/"' in html
    assert 'value="/skipjack/voters/file/county/kent/"' in html
    unprefixed = re.findall(r'(?:href|src)="(/(?!skipjack/)[^"]*)"', html)
    assert unprefixed == []


@needs_db
def test_database_is_only_included_when_asked(root_site, project_site):
    assert not (root_site[0] / "downloads" / "skipjack.db").exists()
    assert (project_site[0] / "downloads" / "skipjack.db").is_file()
    assert "skipjack.db" in (project_site[0] / "data" / "index.html").read_text()
    assert "skipjack.db" not in (root_site[0] / "data" / "index.html").read_text()


@needs_db
def test_no_raw_or_personal_files_are_published(root_site):
    out, _ = root_site
    suffixes = {p.suffix for p in out.rglob("*") if p.is_file()}
    # '' is .nojekyll; .webp is the logo in static/img.
    assert suffixes <= {".html", ".js", ".css", ".csv", ".json", ".webp", ""}
    published = "".join(p.read_text() for p in (out / "downloads" / "voter_file").rglob("*.json"))
    for forbidden in ("VTR_ID", "LastName", "FirstName", "BirthDate"):
        assert forbidden not in published
    assert not list(out.rglob("*.pdf")) and not list(out.rglob("*.txt"))


@needs_db
def test_pages_keep_their_content_and_the_turnout_notice(root_site):
    out, _ = root_site
    html = (out / "voters" / "file" / "index.html").read_text()
    assert "These are not official turnout figures" in html
    assert "Participation among current voters" in html
    month = (out / "voters" / "month" / "2026-08" / "index.html").read_text()
    assert "as of 2026-08" in month and "data-sortable" in month
    data = json.loads(
        re.search(
            r'<script id="county-data" type="application/json">(.*?)</script>',
            (out / "voters" / "county" / "index.html").read_text(),
            re.S,
        ).group(1)
    )
    assert data["totals"][data["dates"].index("2026-08")] == 4_322_671


@needs_db
def test_rebuilding_replaces_the_previous_build_and_restores_the_live_app(tmp_path):
    from skipjack.web.app import app

    out = tmp_path / "site"
    build_site(out, base_path="/x")
    (out / "stale.html").write_text("old")
    build_site(out)
    assert not (out / "stale.html").exists()
    # The shared app is left exactly as the live server expects it.
    assert app.state.templates.env.globals["base"] == ""
    assert app.state.templates.env.globals["static_site"] is False
    from fastapi.testclient import TestClient

    assert 'href="/explorer/"' in TestClient(app).get("/").text
