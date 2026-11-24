"""Shared fixtures."""

from __future__ import annotations

import pytest

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import write_downloads


@pytest.fixture
def base(tmp_path):
    """The data CLI tests' miniature downloads/ folder, as a --base-url."""
    return str(write_downloads(tmp_path / "downloads"))
