"""Extraction tests against generated fixture PDFs."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.extractor import (
    coerce_value,
    extract_certificate,
    field_values,
    parse_date,
    parse_flag,
    parse_money,
)
from src.matcher import match_vendor
from src.normalizer import company_name, normalize_name, slugify, strip_suffixes


def _as_date(mmddyyyy: str) -> date:
    return datetime.strptime(mmddyyyy, "%m/%d/%Y").date()


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------

def test_parse_money_handles_currency_formatting():
    assert parse_money("$2,000,000") == 2_000_000
    assert parse_money("1000000") == 1_000_000
    assert parse_money("") is None
    assert parse_money("PER STATUTE") is None


def test_parse_date_reads_us_format():
    assert parse_date("POLICY EXP 03/09/2027") == date(2027, 3, 9)
    assert parse_date("no date here") is None


def test_parse_flag():
    assert parse_flag("Y") is True
    assert parse_flag("N") is False
    assert parse_flag("") is None


def test_coerce_value_dispatches_on_field_kind():
    assert coerce_value("gl_each_occurrence", "$2,500,000") == 2_500_000
    assert coerce_value("policy_exp", "12/31/2027") == date(2027, 12, 31)
    assert coerce_value("addl_insd", "Y") is True
    with pytest.raises(KeyError):
        coerce_value("not_a_field", "x")


# --------------------------------------------------------------------------
# normalizer
# --------------------------------------------------------------------------

def test_company_name_drops_trailing_address():
    block = "APEX ROOFING & SHEET METAL LLC 1420 Industrial Way, Tacoma, WA 98421"
    assert company_name(block) == "APEX ROOFING & SHEET METAL LLC"


def test_strip_suffixes_and_normalize():
    assert strip_suffixes("Metro Plumbing Partners LLC") == "Metro Plumbing Partners"
    assert normalize_name("Apex Roofing and Sheet Metal, LLC") == "apex roofing and sheet metal"
    assert normalize_name("APEX ROOFING & SHEET METAL LLC") == "apex roofing and sheet metal"


def test_slugify():
    assert slugify("Precision Painting & Drywall LLC") == "precision_painting_drywall_llc"
    assert slugify("") == "unknown"


# --------------------------------------------------------------------------
# extraction from real generated PDFs
# --------------------------------------------------------------------------

def test_extracts_every_key_field_from_a_compliant_certificate(fixture_pdfs, specs_by_slug):
    spec = specs_by_slug["apex_roofing"]
    result = extract_certificate(fixture_pdfs["apex_roofing"])

    assert result["error"] is None
    assert result["needs_ocr"] is False
    assert len(result["file_hash"]) == 64

    values = field_values(result)
    assert values["insured"].startswith("APEX ROOFING & SHEET METAL LLC")
    assert values["producer"].startswith("Cascade Risk Partners")
    assert values["insurer_a"] == "Summit Casualty Insurance Co."
    assert values["policy_eff"] == _as_date(spec["eff"])
    assert values["policy_exp"] == _as_date(spec["exp"])
    assert values["gl_each_occurrence"] == spec["gl_occurrence"]
    assert values["gl_aggregate"] == spec["gl_aggregate"]
    assert values["auto_csl"] == spec["auto_csl"]
    assert values["umbrella_each_occurrence"] == spec["umbrella"]
    assert values["workers_comp"] == "PER STATUTE"
    assert values["el_each_accident"] == spec["el_each_accident"]
    assert values["addl_insd"] is True
    assert values["subr_wvd"] is True
    assert values["certificate_holder"].startswith("Northwind Property Management")
    assert values["policy_number"].startswith("GL-")


def test_required_fields_are_extracted_with_high_confidence(fixture_pdfs):
    result = extract_certificate(fixture_pdfs["clearview_janitorial"])
    for name in ("insured", "policy_exp", "gl_each_occurrence", "gl_aggregate", "certificate_holder"):
        assert result["fields"][name]["confidence"] >= 0.6, name
        assert result["fields"][name]["source_snippet"]


def test_watermark_glyphs_do_not_corrupt_values(fixture_pdfs):
    """The SAMPLE stamp overlays the form; oversized glyphs must be dropped."""
    values = field_values(extract_certificate(fixture_pdfs["apex_roofing"]))
    assert "SAMPLE" not in values["insured"].upper()
    assert values["policy_number"].count("-") == 2


@pytest.mark.parametrize(
    "slug, field, expected",
    [
        ("metro_plumbing", "gl_each_occurrence", 500_000),
        ("voltaic_electric", "workers_comp", None),
        ("precision_painting", "addl_insd", False),
        ("clearview_janitorial", "auto_csl", 500_000),
        ("summit_hvac", "umbrella_each_occurrence", 1_000_000),
    ],
)
def test_scenario_specific_fields(fixture_pdfs, slug, field, expected):
    assert field_values(extract_certificate(fixture_pdfs[slug]))[field] == expected


def test_wrong_holder_is_read_verbatim(fixture_pdfs):
    values = field_values(extract_certificate(fixture_pdfs["precision_painting"]))
    assert "Eastvale Realty Holdings" in values["certificate_holder"]
    assert "Northwind" not in values["certificate_holder"]


def test_expiry_is_the_earliest_of_the_coverage_lines(fixture_pdfs, specs_by_slug):
    for slug in ("greenline_landscaping", "summit_hvac"):
        values = field_values(extract_certificate(fixture_pdfs[slug]))
        assert values["policy_exp"] == _as_date(specs_by_slug[slug]["exp"])


def test_missing_file_is_reported_not_raised(tmp_path):
    result = extract_certificate(tmp_path / "does_not_exist.pdf")
    assert result["error"]
    assert result["fields"] == {}


def test_corrupt_pdf_is_reported_not_raised(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not really a pdf")
    result = extract_certificate(bad)
    assert result["error"]


# --------------------------------------------------------------------------
# matcher
# --------------------------------------------------------------------------

VENDORS = [
    {"vendor_name": "Apex Roofing and Sheet Metal, LLC", "category": "roofing",
     "contact_email": "a@example.com", "property": "P", "notes": ""},
    {"vendor_name": "Metro Plumbing Partners LLC", "category": "plumbing",
     "contact_email": "m@example.com", "property": "P", "notes": ""},
]


def test_match_exact_and_normalized():
    exact = match_vendor("Metro Plumbing Partners LLC", VENDORS)
    assert exact["method"] == "exact"
    assert exact["category"] == "plumbing"

    normalized = match_vendor("APEX ROOFING & SHEET METAL LLC 1420 Industrial Way, Tacoma", VENDORS)
    assert normalized["method"] == "normalized"
    assert normalized["vendor_name"] == "Apex Roofing and Sheet Metal, LLC"


def test_match_fuzzy_above_threshold():
    result = match_vendor("Metro Plumbing Partners", VENDORS)
    assert result["method"] in {"normalized", "fuzzy"}
    assert result["score"] >= 88


def test_unmatched_returns_top_candidates():
    result = match_vendor("Completely Different Business Name", VENDORS)
    assert result["method"] == "unmatched"
    assert result["vendor_name"] is None
    assert 1 <= len(result["candidates"]) <= 3
