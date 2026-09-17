"""Paths, .env and requirements.yaml loading. No side effects beyond mkdir."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
PROCESSED_DIR = DATA_DIR / "processed"
FAILED_DIR = DATA_DIR / "failed"
VENDORS_CSV = DATA_DIR / "vendors.csv"
DB_PATH = DATA_DIR / "coi.db"

OUTPUT_DIR = ROOT / "output"
EMAILS_DIR = OUTPUT_DIR / "emails"
LOG_PATH = OUTPUT_DIR / "run.log"

TEMPLATES_DIR = ROOT / "templates"
REQUIREMENTS_YAML = ROOT / "requirements.yaml"

# Fuzzy-match floor for token_set_ratio; below this a certificate is "unmatched".
MATCH_THRESHOLD = 88
# Any required field extracted below this confidence raises LOW_CONFIDENCE.
CONFIDENCE_THRESHOLD = 0.6
# Below this many characters of text, a PDF is assumed to be scanned.
MIN_TEXT_CHARS = 200

load_dotenv(ROOT / ".env")

SENDER_NAME = os.getenv("SENDER_NAME", "Risk Management")


def ensure_dirs() -> None:
    """Create the working directories the pipeline writes into."""
    for path in (INBOX_DIR, PROCESSED_DIR, FAILED_DIR, EMAILS_DIR):
        path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def load_requirements(path: str | None = None) -> dict[str, Any]:
    """Load requirements.yaml as a plain dict."""
    target = Path(path) if path else REQUIREMENTS_YAML
    with target.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def requirements_for(category: str | None, reqs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge the default requirement block with a category override."""
    reqs = reqs if reqs is not None else load_requirements()
    merged: dict[str, Any] = dict(reqs.get("default", {}))
    override = (reqs.get("categories") or {}).get((category or "").strip().lower())
    if isinstance(override, dict):
        merged.update(override)
    merged["certificate_holder_must_contain"] = reqs.get("certificate_holder_must_contain", "")
    merged["expiring_warning_days"] = reqs.get("expiring_warning_days", [30, 60, 90])
    return merged
