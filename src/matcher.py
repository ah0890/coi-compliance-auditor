"""Match a certificate's INSURED name to a row in vendors.csv."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from config import MATCH_THRESHOLD
from src.normalizer import company_name, normalize_name

log = logging.getLogger(__name__)

VENDOR_COLUMNS = ("vendor_name", "category", "contact_email", "property", "notes")


def load_vendors(csv_path: Path) -> list[dict[str, str]]:
    """Read vendors.csv. Missing optional columns are filled with ''."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        log.warning("vendors.csv not found at %s - every certificate will be unmatched", csv_path)
        return []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [
            {col: (row.get(col) or "").strip() for col in VENDOR_COLUMNS}
            for row in csv.DictReader(fh)
            if (row.get("vendor_name") or "").strip()
        ]
    log.debug("loaded %d vendors from %s", len(rows), csv_path)
    return rows


def _score(insured_norm: str, vendor: dict[str, str]) -> float:
    return float(fuzz.token_set_ratio(insured_norm, normalize_name(vendor["vendor_name"])))


def match_vendor(
    insured: str | None, vendors: list[dict[str, str]], threshold: int = MATCH_THRESHOLD
) -> dict[str, Any]:
    """Resolve an insured name to a vendor row.

    Tiers: exact -> normalized-equal -> token_set_ratio >= threshold.
    Below the threshold the result is `unmatched` with the top three
    candidates so a human can adjudicate from the Needs Review tab.
    """
    result: dict[str, Any] = {
        "vendor": None,
        "vendor_name": None,
        "category": None,
        "contact_email": None,
        "score": 0.0,
        "method": "unmatched",
        "candidates": [],
    }
    raw = company_name(insured)
    if not raw or not vendors:
        return result

    scored = sorted(
        ((_score(normalize_name(raw), vendor), vendor) for vendor in vendors),
        key=lambda pair: pair[0],
        reverse=True,
    )
    result["candidates"] = [
        {"vendor_name": vendor["vendor_name"], "score": round(score, 1)} for score, vendor in scored[:3]
    ]

    insured_norm = normalize_name(raw)
    for vendor in vendors:
        if raw.casefold() == vendor["vendor_name"].casefold():
            return _matched(result, vendor, 100.0, "exact")
    for vendor in vendors:
        if insured_norm and insured_norm == normalize_name(vendor["vendor_name"]):
            return _matched(result, vendor, 100.0, "normalized")

    best_score, best_vendor = scored[0]
    if best_score >= threshold:
        return _matched(result, best_vendor, best_score, "fuzzy")

    log.info("unmatched insured %r (best %.1f)", raw, best_score)
    result["score"] = round(best_score, 1)
    return result


def _matched(result: dict[str, Any], vendor: dict[str, str], score: float, method: str) -> dict[str, Any]:
    result.update(
        vendor=vendor,
        vendor_name=vendor["vendor_name"],
        category=vendor.get("category") or "",
        contact_email=vendor.get("contact_email") or "",
        score=round(score, 1),
        method=method,
    )
    return result
