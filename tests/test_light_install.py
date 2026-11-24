"""skipjack get must work in a light install: no web stack, no PDF stack."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from skipjack import cli

TESTS = Path(__file__).resolve().parent

# The extras' own modules, and what they pull in that the light path must not import either
BLOCKED = sorted(
    {*cli._EXTRAS, "starlette", "pdfplumber", "pypdfium2", "PIL", "pandas", "numpy", "onnxruntime"}
)

# Blocking the heavy modules in a subprocess proves it without a second virtualenv.
BLOCK = """
import sys
for name in {blocked!r}:
    sys.modules[name] = None  # makes `import name` raise ImportError
sys.path.insert(0, {tests!r})
import pathlib
from skipjack.cli import main
from client_fixture import write_downloads
base = write_downloads(pathlib.Path({tmp!r}) / "downloads")
main(["get", "registration", "--base-url", str(base), "--format", "csv"])
main(["list", "counties", "--base-url", str(base), "--format", "csv"])
import skipjack.client.sbe, skipjack.pipeline.results, skipjack.pipeline.turnout
"""


def test_the_data_cli_runs_without_the_heavy_dependencies(tmp_path):
    # another project's `tests` package on PYTHONPATH would shadow client_fixture's folder
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, "-c", BLOCK.format(blocked=BLOCKED, tests=str(TESTS), tmp=str(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert "report_date,county" in done.stdout


def test_a_pipeline_command_without_its_extra_says_what_to_install(capsys, monkeypatch):

    def needs_the_web_stack(args):
        raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")

    monkeypatch.setattr(cli, "cmd_build_db", needs_the_web_stack)
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["build-db"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "`skipjack build-db` needs fastapi, which is not installed" in err
    assert "In a checkout: uv sync. Otherwise:" in err and "skipjack[web]" in err


def test_a_pdf_submodule_names_the_pdf_extra(capsys, monkeypatch):

    def needs_the_pdf_reader(args):
        raise ModuleNotFoundError("No module named 'natural_pdf.ocr'", name="natural_pdf.ocr")

    monkeypatch.setattr(cli, "cmd_parse", needs_the_pdf_reader)
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["parse"])
    err = capsys.readouterr().err
    assert "needs natural_pdf" in err and "skipjack[pdf]" in err


def test_a_missing_module_that_is_not_an_extra_is_a_bug_and_keeps_its_traceback(monkeypatch):

    def broken(args):
        raise ModuleNotFoundError("No module named 'skipjack.nope'", name="skipjack.nope")

    monkeypatch.setattr(cli, "cmd_build_db", broken)
    with pytest.raises(ModuleNotFoundError, match="skipjack.nope"):
        cli.main(["build-db"])


def test_a_moved_submodule_of_an_installed_extra_is_a_bug_and_keeps_its_traceback(monkeypatch):
    # natural_pdf is installed in the dev environment; only its submodule is missing

    def moved(args):
        raise ModuleNotFoundError("No module named 'natural_pdf.gone'", name="natural_pdf.gone")

    monkeypatch.setattr(cli, "cmd_parse", moved)
    with pytest.raises(ModuleNotFoundError, match="natural_pdf.gone"):
        cli.main(["parse"])
