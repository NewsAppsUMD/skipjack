"""FastAPI application factory with Datasette mount."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from skipjack.web.downloads import DOWNLOAD_ROOTS
from skipjack.web.dates import month_label
from skipjack.web.paths import DATA_DIR, PROJECT_ROOT, STATIC_DIR, TEMPLATES_DIR
from skipjack.web.routes import (
    about,
    compare,
    data,
    errata,
    home,
    trends,
    voterfile,
    voters,
    young,
)
from skipjack.web.summary import signed
from skipjack.web.urls import slugify


def configure_templates(
    templates: Jinja2Templates,
    base: str = "",
    static_site: bool = False,
    db_download: bool = False,
) -> None:
    """Set what every template can see.

    base: prefix for internal links ("" on the live server, "/skipjack" for a project site).
    static_site: True when pages are pre-rendered; hides the live-only Data Explorer.
    db_download: True when the SQLite file is offered for download.
    """
    templates.env.filters["slug"] = slugify
    templates.env.filters["signed"] = signed
    templates.env.filters["month_label"] = month_label
    templates.env.globals.update(base=base, static_site=static_site, db_download=db_download)


def create_app() -> FastAPI:
    application = FastAPI(title="Skipjack", docs_url=None, redoc_url=None)

    application.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    configure_templates(application.state.templates)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for folder in DOWNLOAD_ROOTS:
        if (DATA_DIR / folder).exists():
            application.mount(
                f"/downloads/{folder}",
                StaticFiles(directory=str(DATA_DIR / folder)),
                name=f"downloads-{folder}",
            )

    application.include_router(home.router)
    application.include_router(voters.router)
    application.include_router(trends.router)
    application.include_router(compare.router)
    application.include_router(young.router)
    application.include_router(voterfile.router)
    application.include_router(data.router)
    application.include_router(errata.router)
    application.include_router(about.router)

    db_path = PROJECT_ROOT / "skipjack.db"
    if db_path.exists():
        try:
            from datasette.app import Datasette

            dbs = [str(db_path)]
            cvr_path = PROJECT_ROOT / "skipjack_cvr.db"
            if cvr_path.exists():
                dbs.append(str(cvr_path))

            ds = Datasette(
                dbs,
                metadata={
                    "title": "Skipjack Data Explorer",
                    "description": "Browse and query Maryland elections data",
                    "license": "CC-BY 4.0",
                    "source": "Maryland State Board of Elections",
                    "source_url": "https://results.elections.maryland.gov",
                },
            )
            application.mount("/explorer", ds.app())
        except Exception:
            pass

    return application


app = create_app()
