"""Every validation rule, driven by hand-built field dicts."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.validator import (
    FAIL,
    WARN,
    findings_summary,
    overall_status,
    validate,
)

TODAY = date(2026, 6, 1)

REQUIREMENTS = {
    "gl_each_occurrence_min": 1_000_000,
    "gl_aggregate_min": 2_000_000,
    "auto_csl_min": 1_000_000,
    "umbrella_min": 0,
    "workers_comp_required": True,
    "additional_insured_required": True,
    "waiver_of_subrogation_required": False,
    "certificate_holder_must_contain": "Northwind Property Management",
    "expiring_warning_days": [30, 60, 90],
}

GOOD_FIELDS = {
    "insured": "APEX ROOFING LLC",
    "policy_eff": TODAY - timedelta(days=200),
    "policy_exp": TODAY + timedelta(days=200),
    "gl_each_occurrence": 1_000_000,
    "gl_aggregate": 2_000_000,
    "auto_csl": 1_000_000,
    "umbrella_each_occurrence": 0,
    "workers_comp": "PER STATUTE",
    "el_each_accident": 1_000_000,
    "addl_insd": True,
    "subr_wvd": False,
    "certificate_holder": "Northwind Property Management, 500 Harbor Point Drive",
}

GOOD_CONFIDENCE = {name: 0.95 for name in GOOD_FIELDS}


def run(overrides=None, requirements=None, confidences=None, today=TODAY):
    fields = {**GOOD_FIELDS, **(overrides or {})}
    reqs = {**REQUIREMENTS, **(requirements or {})}
    confs = {**GOOD_CONFIDENCE, **(confidences or {})}
    return validate(fields, confs, reqs, today)


def codes(findings) -> set[str]:
    return {finding["code"] for finding in findings}


# --------------------------------------------------------------------------

def test_clean_certificate_has_no_findings():
    findings = run()
    assert findings == []
    assert overall_status(findings) == "PASS"
    assert findings_summary(findings) == "OK"


def test_expired():
    findings = run({"policy_exp": TODAY - timedelta(days=1)})
    assert "EXPIRED" in codes(findings)
    assert overall_status(findings) == FAIL


@pytest.mark.parametrize("days, expected", [(1, "EXPIRING_30"), (30, "EXPIRING_30"),
                                            (31, "EXPIRING_60"), (60, "EXPIRING_60"),
                                            (61, "EXPIRING_90"), (90, "EXPIRING_90")])
def test_expiring_buckets(days, expected):
    findings = run({"policy_exp": TODAY + timedelta(days=days)})
    assert codes(findings) == {expected}
    assert overall_status(findings) == WARN


def test_expiring_only_reports_the_tightest_bucket():
    findings = run({"policy_exp": TODAY + timedelta(days=10)})
    assert len(findings) == 1


def test_no_expiry_warning_beyond_the_widest_bucket():
    assert run({"policy_exp": TODAY + timedelta(days=91)}) == []


def test_gl_occurrence_below_min():
    findings = run({"gl_each_occurrence": 500_000})
    assert "GL_OCCURRENCE_BELOW_MIN" in codes(findings)
    assert "$500,000" in findings[0]["message"]


def test_gl_aggregate_below_min():
    assert "GL_AGGREGATE_BELOW_MIN" in codes(run({"gl_aggregate": 1_000_000}))


def test_auto_below_min():
    assert "AUTO_BELOW_MIN" in codes(run({"auto_csl": 500_000}))


def test_auto_minimum_can_be_relaxed_per_category():
    assert run({"auto_csl": 500_000}, {"auto_csl_min": 500_000}) == []


def test_umbrella_below_min_only_when_required():
    assert run({"umbrella_each_occurrence": None}) == []
    findings = run({"umbrella_each_occurrence": None}, {"umbrella_min": 1_000_000})
    assert "UMBRELLA_BELOW_MIN" in codes(findings)


def test_missing_limit_counts_as_zero():
    assert "GL_OCCURRENCE_BELOW_MIN" in codes(run({"gl_each_occurrence": None}))


def test_wc_missing():
    findings = run({"workers_comp": None})
    assert "WC_MISSING" in codes(findings)
    assert overall_status(findings) == FAIL


def test_wc_not_required_for_this_category():
    assert run({"workers_comp": None}, {"workers_comp_required": False}) == []


def test_additional_insured_not_marked():
    assert "ADDL_INSURED_NOT_MARKED" in codes(run({"addl_insd": False}))
    assert "ADDL_INSURED_NOT_MARKED" in codes(run({"addl_insd": None}))


def test_additional_insured_not_required():
    assert run({"addl_insd": False}, {"additional_insured_required": False}) == []


def test_waiver_not_marked_only_when_required():
    assert run({"subr_wvd": False}) == []
    findings = run({"subr_wvd": False}, {"waiver_of_subrogation_required": True})
    assert "WAIVER_NOT_MARKED" in codes(findings)


def test_holder_mismatch():
    findings = run({"certificate_holder": "Eastvale Realty Holdings LLC"})
    assert "HOLDER_MISMATCH" in codes(findings)
    assert "Eastvale" in findings[0]["message"]


def test_holder_match_is_case_insensitive_and_substring():
    assert run({"certificate_holder": "NORTHWIND PROPERTY MANAGEMENT INC, Seattle"}) == []


def test_holder_rule_skipped_when_unconfigured():
    assert run({"certificate_holder": "Anyone"}, {"certificate_holder_must_contain": ""}) == []


def test_low_confidence():
    findings = run(confidences={"gl_each_occurrence": 0.2})
    assert "LOW_CONFIDENCE" in codes(findings)
    assert overall_status(findings) == WARN
    assert "gl_each_occurrence" in findings[0]["message"]


def test_low_confidence_ignores_optional_fields():
    assert run(confidences={"el_each_accident": 0.0, "producer": 0.0}) == []


def test_overall_status_precedence():
    findings = run(
        {"policy_exp": TODAY + timedelta(days=10), "workers_comp": None},
        confidences={"insured": 0.1},
    )
    assert codes(findings) >= {"EXPIRING_30", "WC_MISSING", "LOW_CONFIDENCE"}
    assert overall_status(findings) == FAIL
    assert findings[0]["severity"] == FAIL


def test_findings_are_sorted_most_severe_first():
    findings = run({"policy_exp": TODAY + timedelta(days=10), "gl_each_occurrence": 1})
    severities = [finding["severity"] for finding in findings]
    assert severities == sorted(severities, key=lambda s: 0 if s == FAIL else 1)


def test_findings_summary_truncates():
    findings = run(
        {
            "policy_exp": TODAY - timedelta(days=1),
            "gl_each_occurrence": 1,
            "gl_aggregate": 1,
            "auto_csl": 1,
            "workers_comp": None,
            "addl_insd": False,
            "certificate_holder": "Someone Else",
        }
    )
    summary = findings_summary(findings, limit=3)
    assert summary.count(";") == 3
    assert "more" in summary


def test_validate_does_not_mutate_inputs():
    fields = dict(GOOD_FIELDS)
    confidences = dict(GOOD_CONFIDENCE)
    requirements = dict(REQUIREMENTS)
    validate(fields, confidences, requirements, TODAY)
    assert fields == GOOD_FIELDS
    assert confidences == GOOD_CONFIDENCE
    assert requirements == REQUIREMENTS
