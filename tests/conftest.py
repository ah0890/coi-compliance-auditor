"""Shared fixtures. Everything here runs offline."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_pdfs() -> dict[str, Path]:
    """Generate the sample PDFs into tests/fixtures once, if they are absent.

    Dates inside the samples are relative to today, so the fixtures are
    regenerated whenever they were not built today.
    """
    from src.sample_generator import sample_specs, render_certificate

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = FIXTURE_DIR / f".generated_{date.today().isoformat()}"
    stale = not stamp.exists()
    if stale:
        for old in FIXTURE_DIR.glob(".generated_*"):
            old.unlink()

    paths: dict[str, Path] = {}
    for spec in sample_specs():
        target = FIXTURE_DIR / f"{spec['slug']}.pdf"
        if stale or not target.exists():
            render_certificate(spec, target)
        paths[spec["slug"]] = target

    stamp.touch()
    return paths


@pytest.fixture(scope="session")
def specs_by_slug() -> dict[str, dict]:
    from src.sample_generator import sample_specs

    return {spec["slug"]: spec for spec in sample_specs()}
