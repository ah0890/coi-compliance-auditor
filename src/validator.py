"""Compliance rules. Pure functions: no I/O, no globals, no clock reads.

`validate` takes already-extracted values plus the merged requirement block for
the vendor's category and returns a list of findings. Callers pass `today`
explicitly so results are reproducible and testable.
"""

from __future__ import annotations

from datetime import date
from typing import Any

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"

SEVERITY_ORDER = {FAIL: 3, WARN: 2, INFO: 1}

# Required fields whose confidence is checked for LOW_CONFIDENCE.
CONFIDENCE_CHECKED = (
    "insured",
    "policy_exp",
    "gl_each_occurrence",
    "gl_aggregate",
    "certificate_holder",
)


def _finding(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _money(amount: int | None) -> str:
    return f"${amount:,}" if isinstance(amount, int) else "not found"


def days_until_expiry(expiry: date | None, today: date) -> int | None:
    """Whole days from `today` to `expiry`; negative once expired."""
    if not isinstance(expiry, date):
        return None
    return (expiry - today).days


def check_expiry(expiry: date | None, today: date, warning_days: list[int]) -> list[dict[str, str]]:
    """EXPIRED, or the tightest EXPIRING_<n> bucket that applies."""
    days = days_until_expiry(expiry, today)
    if days is None:
        return []
    if days < 0:
        return [
            _finding(
                "EXPIRED",
                FAIL,
                f"Coverage expired on {expiry:%m/%d/%Y} ({abs(days)} days ago).",
            )
        ]
    for bucket in sorted(int(b) for b in warning_days):
        if days <= bucket:
            return [
                _finding(
                    f"EXPIRING_{bucket}",
                    WARN,
                    f"Coverage expires {expiry:%m/%d/%Y} - {days} days remaining.",
                )
            ]
    return []


def _check_minimum(
    code: str, label: str, actual: int | None, minimum: int | None, treat_missing_as_zero: bool = True
) -> list[dict[str, str]]:
    """One 'limit at least N' rule. A minimum of 0 disables the rule."""
    required = int(minimum or 0)
    if required <= 0:
        return []
    value = actual if isinstance(actual, int) else (0 if treat_missing_as_zero else None)
    if value is None or value >= required:
        return []
    return [
        _finding(
            code,
            FAIL,
            f"{label} is {_money(actual)}; {_money(required)} required.",
        )
    ]


def check_workers_comp(workers_comp: Any, required: bool) -> list[dict[str, str]]:
    if not required:
        return []
    if workers_comp:
        return []
    return [_finding("WC_MISSING", FAIL, "No workers compensation coverage shown on the certificate.")]


def check_additional_insured(marked: Any, required: bool) -> list[dict[str, str]]:
    if not required or marked is True:
        return []
    return [
        _finding(
            "ADDL_INSURED_NOT_MARKED",
            FAIL,
            "ADDL INSD is not marked Y; the certificate holder is not shown as additional insured.",
        )
    ]


def check_waiver(marked: Any, required: bool) -> list[dict[str, str]]:
    if not required or marked is True:
        return []
    return [
        _finding(
            "WAIVER_NOT_MARKED",
            FAIL,
            "SUBR WVD is not marked Y; no waiver of subrogation in favour of the holder.",
        )
    ]


def check_holder(holder: str | None, must_contain: str | None) -> list[dict[str, str]]:
    """The certificate holder block must name us."""
    needle = (must_contain or "").strip().casefold()
    if not needle:
        return []
    haystack = (holder or "").casefold()
    if needle in haystack:
        return []
    shown = (holder or "").strip() or "nothing readable"
    return [
        _finding(
            "HOLDER_MISMATCH",
            FAIL,
            f'Certificate holder must contain "{must_contain}"; certificate shows: {shown[:120]}',
        )
    ]


def check_confidence(
    confidences: dict[str, float], threshold: float, names: tuple[str, ...] = CONFIDENCE_CHECKED
) -> list[dict[str, str]]:
    low = sorted(name for name in names if float(confidences.get(name, 0.0)) < threshold)
    if not low:
        return []
    return [
        _finding(
            "LOW_CONFIDENCE",
            WARN,
            "Low-confidence or missing extraction for: " + ", ".join(low) + ". Verify against the PDF.",
        )
    ]


def validate(
    fields: dict[str, Any],
    confidences: dict[str, float],
    requirements: dict[str, Any],
    today: date,
    confidence_threshold: float = 0.6,
) -> list[dict[str, str]]:
    """Run every rule and return the findings, most severe first."""
    findings: list[dict[str, str]] = []

    findings += check_expiry(
        fields.get("policy_exp"), today, list(requirements.get("expiring_warning_days") or [30, 60, 90])
    )
    findings += _check_minimum(
        "GL_OCCURRENCE_BELOW_MIN",
        "General liability each occurrence",
        fields.get("gl_each_occurrence"),
        requirements.get("gl_each_occurrence_min"),
    )
    findings += _check_minimum(
        "GL_AGGREGATE_BELOW_MIN",
        "General aggregate",
        fields.get("gl_aggregate"),
        requirements.get("gl_aggregate_min"),
    )
    findings += _check_minimum(
        "AUTO_BELOW_MIN",
        "Automobile combined single limit",
        fields.get("auto_csl"),
        requirements.get("auto_csl_min"),
    )
    findings += _check_minimum(
        "UMBRELLA_BELOW_MIN",
        "Umbrella each occurrence",
        fields.get("umbrella_each_occurrence"),
        requirements.get("umbrella_min"),
    )
    findings += check_workers_comp(
        fields.get("workers_comp"), bool(requirements.get("workers_comp_required"))
    )
    findings += check_additional_insured(
        fields.get("addl_insd"), bool(requirements.get("additional_insured_required"))
    )
    findings += check_waiver(
        fields.get("subr_wvd"), bool(requirements.get("waiver_of_subrogation_required"))
    )
    findings += check_holder(
        fields.get("certificate_holder"), requirements.get("certificate_holder_must_contain")
    )
    findings += check_confidence(confidences, confidence_threshold)

    return sorted(findings, key=lambda f: -SEVERITY_ORDER.get(f["severity"], 0))


def overall_status(findings: list[dict[str, str]]) -> str:
    """FAIL if any finding fails, else WARN if any warns, else PASS."""
    severities = {finding["severity"] for finding in findings}
    if FAIL in severities:
        return FAIL
    if WARN in severities:
        return WARN
    return "PASS"


def findings_summary(findings: list[dict[str, str]], limit: int = 6) -> str:
    """One-cell summary of the findings for the spreadsheet."""
    if not findings:
        return "OK"
    codes = [f"{finding['code']}" for finding in findings[:limit]]
    extra = len(findings) - len(codes)
    return "; ".join(codes) + (f"; +{extra} more" if extra > 0 else "")
