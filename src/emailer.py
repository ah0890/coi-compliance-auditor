"""Draft .eml notices for vendors that need to act.

This module never sends anything. It writes RFC-822 files you can open in any
mail client, plus a mail_merge.csv for bulk tools.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from src import storage
from src.normalizer import slugify

log = logging.getLogger(__name__)

# An already-expired certificate is a deficiency, not a renewal reminder, so
# EXPIRED is deliberately absent here.
EXPIRY_CODES = frozenset({"EXPIRING_30", "EXPIRING_60", "EXPIRING_90"})


def load_template(path: Path) -> tuple[str, str]:
    """Split a template file into (subject, body).

    The first line must be `Subject: ...`; everything after it is the body.
    """
    text = Path(path).read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    if not first.lower().startswith("subject:"):
        return "Certificate of insurance - action needed", text.strip()
    return first.split(":", 1)[1].strip(), rest.strip()


def choose_template(findings: list[dict[str, str]], templates_dir: Path) -> Path:
    """Expiry-only problems get the renewal note; anything else the deficiency note."""
    codes = {finding["code"] for finding in findings}
    substantive = codes - EXPIRY_CODES - {"LOW_CONFIDENCE"}
    name = "expiring.txt" if not substantive else "deficient.txt"
    return Path(templates_dir) / name


def _format_expiry(iso_date: str | None) -> str:
    """Show dates the way the certificates do (MM/DD/YYYY)."""
    if not iso_date:
        return "unknown"
    try:
        return date.fromisoformat(str(iso_date)).strftime("%m/%d/%Y")
    except ValueError:
        return str(iso_date)


def format_findings(findings: list[dict[str, str]]) -> str:
    return "\n".join(f"  - [{f['severity']}] {f['message']}" for f in findings) or "  - (none recorded)"


def render(template_path: Path, context: dict[str, str]) -> tuple[str, str]:
    subject, body = load_template(template_path)
    return subject.format(**context), body.format(**context)


def build_message(to_address: str, subject: str, body: str, sender_name: str) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to_address or "unknown@example.invalid"
    message["From"] = f"{sender_name} <do-not-reply@example.invalid>"
    message["Subject"] = subject
    message["X-COI-Auditor"] = "draft-only; not sent"
    message.set_content(body)
    return message


def draft_emails(
    conn: sqlite3.Connection,
    templates_dir: Path,
    emails_dir: Path,
    holder_name: str,
    sender_name: str,
    today: date | None = None,
) -> list[Path]:
    """Write one .eml per WARN/FAIL vendor, plus mail_merge.csv."""
    today = today or date.today()
    emails_dir = Path(emails_dir)
    emails_dir.mkdir(parents=True, exist_ok=True)

    findings_map = storage.findings_by_certificate(conn)
    contacts = storage.vendor_contacts(conn)
    written: list[Path] = []
    merge_rows: list[dict[str, Any]] = []

    for row in storage.current_certificates(conn):
        if row.get("status") not in {"WARN", "FAIL"}:
            continue
        findings = findings_map.get(int(row["id"]), [])
        vendor_name = row.get("vendor_name") or Path(row["file_name"]).stem
        vendor = contacts.get(vendor_name, {})
        expiry = _format_expiry(row.get("policy_exp"))
        context = {
            "vendor_name": vendor_name,
            "findings_list": format_findings(findings),
            "expiry_date": expiry,
            "holder_name": holder_name,
            "sender_name": sender_name,
        }
        template_path = choose_template(findings, templates_dir)
        subject, body = render(template_path, context)
        to_address = vendor.get("contact_email", "")
        message = build_message(to_address, subject, body, sender_name)

        out_path = emails_dir / f"{slugify(vendor_name)}_{today.isoformat()}.eml"
        out_path.write_text(message.as_string(), encoding="utf-8")
        written.append(out_path)

        merge_rows.append(
            {
                "vendor_name": vendor_name,
                "to": to_address,
                "status": row.get("status"),
                "template": template_path.name,
                "subject": subject,
                "expiry_date": expiry,
                "findings": "; ".join(f["code"] for f in findings),
                "eml_file": out_path.name,
            }
        )

    merge_path = emails_dir / "mail_merge.csv"
    with merge_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "vendor_name", "to", "status", "template", "subject",
                "expiry_date", "findings", "eml_file",
            ],
        )
        writer.writeheader()
        writer.writerows(merge_rows)

    log.info("drafted %d email(s) into %s (nothing was sent)", len(written), emails_dir)
    return written
