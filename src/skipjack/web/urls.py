"""URL helpers shared by the live server and the static site generator."""

from __future__ import annotations

import re
from collections.abc import Iterable


def slugify(name: str) -> str:
    """URL slug for a county or district name: "Prince George's" -> "prince-georges"."""
    plain = name.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def resolve_slug(slug: str, names: Iterable[str]) -> str | None:
    """The name whose slug is ``slug``, or None."""
    return next((n for n in names if slugify(n) == slug), None)


def normalize_base_path(base_path: str | None) -> str:
    """'' for the site root, otherwise '/name' with no trailing slash."""
    stripped = (base_path or "").strip().strip("/")
    return f"/{stripped}" if stripped else ""
