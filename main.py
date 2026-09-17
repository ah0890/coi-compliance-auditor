"""COI Compliance Auditor - command line entry point.

    python main.py generate-samples
    python main.py audit
    python main.py fix 3 --field gl_each_occurrence --value 2000000
    python main.py report
    python main.py status
"""

import logging
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

import typer

import config
from src import emailer, reporter, storage
from src.extractor import (
    FIELD_KINDS,
    coerce_value,
    extract_certificate,
    field_confidences,
    field_values,
)
from src.matcher import load_vendors, match_vendor
from src.normalizer import slugify
from src.sample_generator import generate_samples
from src.validator import findings_summary, overall_status, validate

app = typer.Typer(add_completion=False, help="Audit vendor certificates of insurance.")
log = logging.getLogger("coi")

STATUS_ERROR = "ERROR"
STATUS_NEEDS_OCR = "NEEDS_OCR"

# Tokens that look like policy numbers: 8+ chars, upper-case, letters and digits.
_POLICY_LIKE = re.compile(r"\b(?=[A-Z0-9-]{8,}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9-]+\b")


def mask_policy(value: Optional[str]) -> str:
    """Reduce a policy number to its last four characters."""
    text = str(value or "")
    tail = re.sub(r"[^A-Za-z0-9]", "", text)[-4:]
    return f"****{tail}" if tail else "****"


class _MaskPolicyNumbers(logging.Filter):
    """Keep full policy numbers out of the console and the log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _POLICY_LIKE.sub(lambda m: mask_policy(m.group(0)), str(record.getMessage()))
        record.args = ()
        return True


def setup_logging(verbose: bool = False) -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    mask = _MaskPolicyNumbers()
    for handler in (console, file_handler):
        handler.addFilter(mask)
        root.addHandler(handler)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _holder_name() -> str:
    return config.load_requirements().get("certificate_holder_must_contain", "the certificate holder")


def _revalidate_row(conn: sqlite3.Connection, row: dict[str, Any], today: date) -> tuple[str, list]:
    """Re-run the rules against a stored certificate row."""
    reqs = config.requirements_for(row.get("category"))
    findings = validate(
        storage.row_to_fields(row),
        storage.row_confidences(row),
        reqs,
        today,
        config.CONFIDENCE_THRESHOLD,
    )
    status = overall_status(findings)
    storage.replace_findings(conn, int(row["id"]), findings)
    storage.set_status(conn, int(row["id"]), status)
    return status, findings


def _move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        dest = dest_dir / f"{src.stem}_{date.today().isoformat()}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def _print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str, int]]) -> None:
    """Minimal fixed-width console table (no extra dependency)."""
    header = "  ".join(title.ljust(width)[:width] for _key, title, width in columns)
    typer.echo(header)
    typer.echo("  ".join("-" * width for _key, _title, width in columns))
    for row in rows:
        typer.echo(
            "  ".join(str(row.get(key, "") or "").ljust(width)[:width] for key, _title, width in columns)
        )


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

@app.command("generate-samples")
def cmd_generate_samples() -> None:
    """Write eight synthetic SAMPLE certificate PDFs and a matching vendors.csv."""
    setup_logging()
    config.ensure_dirs()
    written = generate_samples(config.INBOX_DIR, config.VENDORS_CSV)
    for path in written:
        log.info("wrote %s", path.relative_to(config.ROOT))
    log.info("wrote %s", config.VENDORS_CSV.relative_to(config.ROOT))
    log.info("%d sample certificates ready. Next: python main.py audit", len(written))


@app.command("audit")
def cmd_audit(
    inbox: Optional[Path] = typer.Option(None, "--inbox", help="Folder of PDFs to audit."),
    keep_inbox: bool = typer.Option(False, "--keep-inbox", help="Do not move files out of the inbox."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging on the console."),
) -> None:
    """Extract, validate and report on every PDF in the inbox."""
    setup_logging(verbose)
    config.ensure_dirs()
    today = date.today()
    inbox_dir = Path(inbox) if inbox else config.INBOX_DIR

    pdfs = sorted(p for p in inbox_dir.glob("*.pdf") if p.is_file())
    if not pdfs:
        log.warning("No PDFs found in %s. Run: python main.py generate-samples", inbox_dir)
        raise typer.Exit(code=1)

    conn = storage.connect(config.DB_PATH)
    vendors = load_vendors(config.VENDORS_CSV)
    storage.upsert_vendors(conn, vendors)
    log.info("Auditing %d certificate(s) from %s against %d vendor(s)", len(pdfs), inbox_dir, len(vendors))

    processed, skipped, failed, results = 0, 0, 0, []
    for pdf in pdfs:
        extraction = extract_certificate(pdf)

        if extraction["error"]:
            log.error("%s: %s -> data/failed/", pdf.name, extraction["error"])
            if not keep_inbox:
                _move(pdf, config.FAILED_DIR)
            failed += 1
            continue

        if extraction["file_hash"] and storage.find_by_hash(conn, extraction["file_hash"]):
            log.info("%s: already audited (same file hash), skipping", pdf.name)
            if not keep_inbox:
                _move(pdf, config.PROCESSED_DIR / "duplicates")
            skipped += 1
            continue

        match = match_vendor(extraction["fields"]["insured"]["value"], vendors)
        reqs = config.requirements_for(match["category"])

        if extraction["needs_ocr"]:
            status, findings = STATUS_NEEDS_OCR, [
                {
                    "code": "NEEDS_OCR",
                    "severity": "WARN",
                    "message": "Scanned certificate; no text layer. Install the OCR extras or key it in by hand.",
                }
            ]
        else:
            findings = validate(
                field_values(extraction),
                field_confidences(extraction),
                reqs,
                today,
                config.CONFIDENCE_THRESHOLD,
            )
            status = overall_status(findings)

        cert_id = storage.save_certificate(conn, extraction, match, status)
        storage.replace_findings(conn, cert_id, findings)
        log.debug(
            "%s: vendor=%s policy=%s status=%s",
            pdf.name,
            match["vendor_name"],
            mask_policy(extraction["fields"]["policy_number"]["value"]),
            status,
        )

        vendor_slug = slugify(match["vendor_name"] or "unmatched", fallback="unmatched")
        if not keep_inbox:
            _move(pdf, config.PROCESSED_DIR / vendor_slug)
        processed += 1
        results.append(
            {
                "cert_id": cert_id,
                "file": pdf.name,
                "vendor": match["vendor_name"] or "(unmatched)",
                "status": status,
                "findings": findings_summary(findings),
            }
        )

    report_file = reporter.write_report(conn, config.OUTPUT_DIR, today)
    drafts = emailer.draft_emails(
        conn, config.TEMPLATES_DIR, config.EMAILS_DIR, _holder_name(), config.SENDER_NAME, today
    )
    conn.close()

    typer.echo("")
    _print_table(
        results,
        [("cert_id", "ID", 4), ("vendor", "VENDOR", 34), ("status", "STATUS", 9), ("findings", "FINDINGS", 46)],
    )
    typer.echo("")
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    log.info(
        "Processed %d, skipped %d duplicate(s), %d failed. %s",
        processed,
        skipped,
        failed,
        " ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no results",
    )
    log.info("Report:  %s", report_file)
    log.info("Emails:  %d draft(s) in %s (nothing sent)", len(drafts), config.EMAILS_DIR)


@app.command("fix")
def cmd_fix(
    cert_id: int = typer.Argument(..., help="Certificate id from the audit table."),
    field: str = typer.Option(..., "--field", help=f"One of: {', '.join(FIELD_KINDS)}"),
    value: str = typer.Option(..., "--value", help="Corrected value (dates MM/DD/YYYY, money 1000000, flags Y/N)."),
) -> None:
    """Correct one extracted field by hand, then re-run the rules."""
    setup_logging()
    if field not in FIELD_KINDS:
        typer.echo(f"Unknown field {field!r}. Known fields: {', '.join(FIELD_KINDS)}")
        raise typer.Exit(code=2)

    parsed = coerce_value(field, value)
    if parsed is None:
        typer.echo(f"Could not parse {value!r} as a {FIELD_KINDS[field]} value.")
        raise typer.Exit(code=2)

    conn = storage.connect(config.DB_PATH)
    if storage.get_certificate(conn, cert_id) is None:
        typer.echo(f"No certificate with id {cert_id}.")
        raise typer.Exit(code=2)

    storage.update_field(conn, cert_id, field, parsed)
    row = storage.get_certificate(conn, cert_id)
    status, findings = _revalidate_row(conn, row, date.today())
    conn.close()

    shown = mask_policy(parsed) if field == "policy_number" else parsed
    log.info("cert %d: %s = %s (confidence set to 1.0)", cert_id, field, shown)
    log.info("cert %d: status is now %s - %s", cert_id, status, findings_summary(findings))
    log.info("Rebuild the workbook with: python main.py report")


@app.command("report")
def cmd_report() -> None:
    """Rebuild the Excel report from the database."""
    setup_logging()
    config.ensure_dirs()
    conn = storage.connect(config.DB_PATH)
    out_path = reporter.write_report(conn, config.OUTPUT_DIR, date.today())
    conn.close()
    log.info("Report: %s", out_path)


@app.command("status")
def cmd_status() -> None:
    """Show the current certificate status for every vendor."""
    setup_logging()
    conn = storage.connect(config.DB_PATH)
    today = date.today()
    findings_map = storage.findings_by_certificate(conn)
    rows = []
    for row in storage.current_certificates(conn):
        fields = storage.row_to_fields(row)
        expiry = fields.get("policy_exp")
        days = (expiry - today).days if expiry else None
        codes = "; ".join(f["code"] for f in findings_map.get(int(row["id"]), [])) or "OK"
        rows.append(
            {
                "cert_id": row["id"],
                "vendor": row.get("vendor_name") or "(unmatched)",
                "status": row.get("status") or "",
                "expiry": expiry.strftime("%m/%d/%Y") if expiry else "-",
                "days": "-" if days is None else str(days),
                "findings": codes,
            }
        )
    conn.close()

    if not rows:
        typer.echo("No certificates on file yet. Run: python main.py audit")
        raise typer.Exit()

    _print_table(
        rows,
        [
            ("cert_id", "ID", 4),
            ("vendor", "VENDOR", 34),
            ("status", "STATUS", 9),
            ("expiry", "EXPIRES", 11),
            ("days", "DAYS", 6),
            ("findings", "FINDINGS", 40),
        ],
    )
    typer.echo(f"\n{len(rows)} vendor(s). Automated extraction - verify before acting on it.")


if __name__ == "__main__":
    app()
