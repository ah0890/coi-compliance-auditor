"""Build the multi-tab Excel compliance report from whatever is in the DB."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from config import CONFIDENCE_THRESHOLD
from src import storage
from src.extractor import FIELD_KINDS
from src.validator import CONFIDENCE_CHECKED, days_until_expiry

log = logging.getLogger(__name__)

DISCLAIMER = "Automated extraction — verify before relying on it for compliance decisions."

FILL_FAIL = PatternFill("solid", start_color="FFF8CECC", end_color="FFF8CECC")
FILL_WARN = PatternFill("solid", start_color="FFFFF2CC", end_color="FFFFF2CC")
FILL_PASS = PatternFill("solid", start_color="FFD5E8D4", end_color="FFD5E8D4")
STATUS_FILLS = {"FAIL": FILL_FAIL, "WARN": FILL_WARN, "PASS": FILL_PASS}

HEADER_FILL = PatternFill("solid", start_color="FF33383D", end_color="FF33383D")
HEADER_FONT = Font(color="FFFFFFFF", bold=True)

MAX_COL_WIDTH = 58


def report_path(output_dir: Path, today: date | None = None) -> Path:
    stamp = (today or date.today()).isoformat()
    return Path(output_dir) / f"coi_report_{stamp}.xlsx"


# --------------------------------------------------------------------------
# frame building
# --------------------------------------------------------------------------

def _expiry_bucket(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    for bucket in (30, 60, 90):
        if days <= bucket:
            return f"{bucket} days"
    return "over 90 days"


def build_frames(conn: sqlite3.Connection, today: date | None = None) -> dict[str, pd.DataFrame]:
    """Assemble every tab as a DataFrame."""
    today = today or date.today()
    findings_map = storage.findings_by_certificate(conn)
    contacts = storage.vendor_contacts(conn)
    current = storage.current_certificates(conn)

    vendor_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for row in current:
        findings = findings_map.get(int(row["id"]), [])
        fields = storage.row_to_fields(row)
        expiry = fields.get("policy_exp")
        days = days_until_expiry(expiry, today)
        vendor = contacts.get(row.get("vendor_name") or "", {})
        vendor_rows.append(
            {
                "cert_id": row["id"],
                "vendor_name": row.get("vendor_name") or "(unmatched)",
                "category": row.get("category") or "",
                "property": vendor.get("property", ""),
                "contact_email": vendor.get("contact_email", ""),
                "status": row.get("status") or "",
                "earliest_expiry": expiry.isoformat() if expiry else "",
                "days_remaining": "" if days is None else days,
                "expiry_bucket": _expiry_bucket(days),
                "gl_each_occurrence": fields.get("gl_each_occurrence"),
                "gl_aggregate": fields.get("gl_aggregate"),
                "auto_csl": fields.get("auto_csl"),
                "umbrella": fields.get("umbrella_each_occurrence"),
                "workers_comp": fields.get("workers_comp") or "",
                "addl_insd": _yn(fields.get("addl_insd")),
                "subr_wvd": _yn(fields.get("subr_wvd")),
                "findings": "; ".join(f["code"] for f in findings) or "OK",
                "finding_detail": " | ".join(f["message"] for f in findings),
                "match": f'{row.get("match_method")} ({row.get("match_score")})',
                "file_name": row["file_name"],
            }
        )

        confidences = storage.row_confidences(row)
        snippets = storage.row_snippets(row)
        # Only the fields the rules actually depend on: an absent umbrella
        # limit is a legitimate zero, not something to hand back for review.
        low = sorted(
            name
            for name in CONFIDENCE_CHECKED
            if float(confidences.get(name, 0.0)) < CONFIDENCE_THRESHOLD
        )
        unmatched = (row.get("match_method") or "unmatched") == "unmatched"
        if low or unmatched or row.get("needs_ocr") or row.get("error"):
            review_rows.append(
                {
                    "cert_id": row["id"],
                    "file_name": row["file_name"],
                    "vendor_name": row.get("vendor_name") or "(unmatched)",
                    "reason": ", ".join(
                        filter(
                            None,
                            [
                                "unmatched vendor" if unmatched else "",
                                "needs OCR" if row.get("needs_ocr") else "",
                                "extraction error" if row.get("error") else "",
                                f"low confidence: {', '.join(low)}" if low else "",
                            ],
                        )
                    ),
                    "top_candidates": ", ".join(
                        f'{c["vendor_name"]} ({c["score"]})'
                        for c in json.loads(row.get("candidates") or "[]")
                    ),
                    "insured_snippet": snippets.get("insured", ""),
                    "holder_snippet": snippets.get("certificate_holder", ""),
                    "error": row.get("error") or "",
                }
            )

    vendors_df = pd.DataFrame(vendor_rows)
    review_df = pd.DataFrame(review_rows)

    if vendors_df.empty:
        expiring_df = pd.DataFrame()
        noncompliant_df = pd.DataFrame()
    else:
        expiring_df = vendors_df[vendors_df["expiry_bucket"].isin(["30 days", "60 days", "90 days"])].copy()
        if not expiring_df.empty:
            expiring_df = expiring_df.sort_values("days_remaining")
        noncompliant_df = vendors_df[vendors_df["status"] == "FAIL"].copy()

    raw_rows = []
    for row in storage.all_certificates(conn):
        confidences = storage.row_confidences(row)
        entry = {
            "cert_id": row["id"],
            "file_name": row["file_name"],
            "vendor_name": row.get("vendor_name") or "",
            "status": row.get("status") or "",
            "processed_at": row.get("processed_at"),
            "file_hash": (row.get("file_hash") or "")[:12],
        }
        for name in FIELD_KINDS:
            entry[name] = row.get(name)
            entry[f"{name}__conf"] = confidences.get(name)
        raw_rows.append(entry)
    raw_df = pd.DataFrame(raw_rows)

    return {
        "Summary": _summary_frame(vendors_df, today),
        "All Vendors": vendors_df,
        "Expiring": expiring_df,
        "Non-Compliant": noncompliant_df,
        "Needs Review": review_df,
        "Raw Extractions": raw_df,
    }


def _yn(value: Any) -> str:
    if value is True:
        return "Y"
    if value is False:
        return "N"
    return ""


def _summary_frame(vendors_df: pd.DataFrame, today: date) -> pd.DataFrame:
    total = len(vendors_df)
    counts = vendors_df["status"].value_counts().to_dict() if total else {}
    passed = int(counts.get("PASS", 0))
    warned = int(counts.get("WARN", 0))
    failed = int(counts.get("FAIL", 0))
    buckets = vendors_df["expiry_bucket"].value_counts().to_dict() if total else {}
    rows = [
        ("Report date", today.isoformat()),
        ("Vendors with a certificate on file", total),
        ("PASS", passed),
        ("WARN", warned),
        ("FAIL", failed),
        ("Compliance %", f"{(passed / total * 100):.1f}%" if total else "n/a"),
        ("Expired", int(buckets.get("expired", 0))),
        ("Expiring within 30 days", int(buckets.get("30 days", 0))),
        ("Expiring within 60 days", int(buckets.get("60 days", 0))),
        ("Expiring within 90 days", int(buckets.get("90 days", 0))),
        ("", ""),
        ("Note", DISCLAIMER),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


# --------------------------------------------------------------------------
# workbook formatting
# --------------------------------------------------------------------------

def _style_sheet(sheet: Worksheet, frame: pd.DataFrame) -> None:
    if frame.empty:
        sheet["A1"] = "No rows for this tab."
        sheet.column_dimensions["A"].width = 40
        return

    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    for index, column in enumerate(frame.columns, start=1):
        longest = max(
            [len(str(column))] + [len(str(value)) for value in frame[column].head(200).tolist()]
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 10), MAX_COL_WIDTH)

    if "status" in frame.columns:
        status_col = list(frame.columns).index("status") + 1
        for row_index in range(2, len(frame) + 2):
            status = sheet.cell(row=row_index, column=status_col).value
            fill = STATUS_FILLS.get(str(status))
            if not fill:
                continue
            for col_index in range(1, len(frame.columns) + 1):
                sheet.cell(row=row_index, column=col_index).fill = fill


def _style_summary(sheet: Worksheet, frame: pd.DataFrame) -> None:
    sheet.column_dimensions["A"].width = 36
    sheet.column_dimensions["B"].width = 70
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    sheet.freeze_panes = "A2"
    for row_index in range(2, len(frame) + 2):
        label = sheet.cell(row=row_index, column=1)
        value = sheet.cell(row=row_index, column=2)
        if label.value in STATUS_FILLS:
            for cell in (label, value):
                cell.fill = STATUS_FILLS[str(label.value)]
        if label.value == "Note":
            value.font = Font(italic=True)
            value.alignment = Alignment(wrap_text=True)


def write_report(conn: sqlite3.Connection, output_dir: Path, today: date | None = None) -> Path:
    """Write the Excel workbook and return its path."""
    today = today or date.today()
    frames = build_frames(conn, today)
    out_path = report_path(output_dir, today)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for tab, frame in frames.items():
            to_write = frame if not frame.empty else pd.DataFrame()
            to_write.to_excel(writer, sheet_name=tab, index=False)
            sheet = writer.sheets[tab]
            if tab == "Summary":
                _style_summary(sheet, frame)
            else:
                _style_sheet(sheet, frame)

    log.info("wrote %s", out_path)
    return out_path
