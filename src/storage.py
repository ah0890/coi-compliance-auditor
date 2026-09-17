"""SQLite persistence for vendors, certificates and findings.

The certificates table gets one real column per extracted field (derived from
`extractor.FIELD_KINDS`, so the two cannot drift) plus a JSON blob of the
per-field confidences and source snippets.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.extractor import FIELD_KINDS, FIELD_NAMES

log = logging.getLogger(__name__)

_SQL_TYPES = {"money": "INTEGER", "date": "TEXT", "flag": "INTEGER", "text": "TEXT"}

# A vendor with no match is still worth reporting, keyed by its file.
UNMATCHED_KEY = "UNMATCHED"


def _field_columns() -> str:
    return ",\n            ".join(f'"{name}" {_SQL_TYPES[kind]}' for name, kind in FIELD_KINDS.items())


def _schema() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS vendors (
            vendor_name   TEXT PRIMARY KEY,
            category      TEXT,
            contact_email TEXT,
            property      TEXT,
            notes         TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS certificates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash     TEXT NOT NULL UNIQUE,
            file_name     TEXT NOT NULL,
            file_path     TEXT,
            vendor_name   TEXT,
            category      TEXT,
            match_method  TEXT,
            match_score   REAL,
            candidates    TEXT,
            status        TEXT,
            needs_ocr     INTEGER DEFAULT 0,
            error         TEXT,
            processed_at  TEXT NOT NULL,
            confidences   TEXT,
            snippets      TEXT,
            {_field_columns()}
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS findings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            certificate_id INTEGER NOT NULL REFERENCES certificates(id) ON DELETE CASCADE,
            code           TEXT NOT NULL,
            severity       TEXT NOT NULL,
            message        TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_findings_cert ON findings(certificate_id)",
        "CREATE INDEX IF NOT EXISTS idx_certs_vendor ON certificates(vendor_name)",
    ]


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, creating the schema if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for statement in _schema():
        conn.execute(statement)
    conn.commit()
    return conn


def upsert_vendors(conn: sqlite3.Connection, vendors: list[dict[str, str]]) -> int:
    conn.executemany(
        """
        INSERT INTO vendors (vendor_name, category, contact_email, property, notes)
        VALUES (:vendor_name, :category, :contact_email, :property, :notes)
        ON CONFLICT(vendor_name) DO UPDATE SET
            category=excluded.category,
            contact_email=excluded.contact_email,
            property=excluded.property,
            notes=excluded.notes
        """,
        vendors,
    )
    conn.commit()
    return len(vendors)


def get_vendors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM vendors ORDER BY vendor_name")]


def find_by_hash(conn: sqlite3.Connection, file_hash: str) -> int | None:
    row = conn.execute("SELECT id FROM certificates WHERE file_hash = ?", (file_hash,)).fetchone()
    return int(row["id"]) if row else None


def _to_sql(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "date":
        return value.isoformat() if isinstance(value, date) else str(value)
    if kind == "flag":
        return 1 if value else 0
    if kind == "money":
        return int(value)
    return str(value)


def save_certificate(
    conn: sqlite3.Connection, extraction: dict[str, Any], match: dict[str, Any], status: str
) -> int:
    """Insert one processed certificate. Returns its row id."""
    fields = extraction.get("fields", {})
    row: dict[str, Any] = {
        "file_hash": extraction["file_hash"],
        "file_name": extraction["file_name"],
        "file_path": extraction.get("file_path"),
        "vendor_name": match.get("vendor_name"),
        "category": match.get("category"),
        "match_method": match.get("method"),
        "match_score": float(match.get("score") or 0.0),
        "candidates": json.dumps(match.get("candidates") or []),
        "status": status,
        "needs_ocr": 1 if extraction.get("needs_ocr") else 0,
        "error": extraction.get("error"),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "confidences": json.dumps({k: v["confidence"] for k, v in fields.items()}),
        "snippets": json.dumps({k: v["source_snippet"] for k, v in fields.items()}),
    }
    for name, kind in FIELD_KINDS.items():
        row[name] = _to_sql(fields.get(name, {}).get("value"), kind)

    columns = ", ".join(f'"{key}"' for key in row)
    placeholders = ", ".join(f":{key}" for key in row)
    cursor = conn.execute(f"INSERT INTO certificates ({columns}) VALUES ({placeholders})", row)
    conn.commit()
    return int(cursor.lastrowid)


def replace_findings(conn: sqlite3.Connection, certificate_id: int, findings: list[dict[str, str]]) -> None:
    conn.execute("DELETE FROM findings WHERE certificate_id = ?", (certificate_id,))
    conn.executemany(
        "INSERT INTO findings (certificate_id, code, severity, message) VALUES (?, ?, ?, ?)",
        [(certificate_id, f["code"], f["severity"], f["message"]) for f in findings],
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, certificate_id: int, status: str) -> None:
    conn.execute("UPDATE certificates SET status = ? WHERE id = ?", (status, certificate_id))
    conn.commit()


def update_field(conn: sqlite3.Connection, certificate_id: int, field: str, value: Any) -> None:
    """Overwrite one extracted field and mark it as human-verified (conf 1.0)."""
    if field not in FIELD_KINDS:
        raise KeyError(field)
    row = conn.execute("SELECT confidences FROM certificates WHERE id = ?", (certificate_id,)).fetchone()
    if row is None:
        raise LookupError(f"no certificate with id {certificate_id}")
    confidences = json.loads(row["confidences"] or "{}")
    confidences[field] = 1.0
    conn.execute(
        f'UPDATE certificates SET "{field}" = ?, confidences = ? WHERE id = ?',
        (_to_sql(value, FIELD_KINDS[field]), json.dumps(confidences), certificate_id),
    )
    conn.commit()


def get_certificate(conn: sqlite3.Connection, certificate_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM certificates WHERE id = ?", (certificate_id,)).fetchone()
    return dict(row) if row else None


def all_certificates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM certificates ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def current_certificates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The governing certificate per vendor: the one with the latest expiry.

    Unmatched certificates are each treated as their own group so they still
    show up for review.
    """
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT c.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(c.vendor_name, ? || ':' || c.file_name)
                       ORDER BY COALESCE(c.policy_exp, '0000-00-00') DESC, c.processed_at DESC
                   ) AS rn
            FROM certificates c
        )
        WHERE rn = 1
        ORDER BY COALESCE(vendor_name, file_name)
        """,
        (UNMATCHED_KEY,),
    ).fetchall()
    return [dict(row) for row in rows]


def findings_for(conn: sqlite3.Connection, certificate_id: int) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT code, severity, message FROM findings WHERE certificate_id = ? ORDER BY id",
        (certificate_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def findings_by_certificate(conn: sqlite3.Connection) -> dict[int, list[dict[str, str]]]:
    """All findings at once, keyed by certificate id."""
    grouped: dict[int, list[dict[str, str]]] = {}
    for row in conn.execute(
        "SELECT certificate_id, code, severity, message FROM findings ORDER BY certificate_id, id"
    ):
        grouped.setdefault(int(row["certificate_id"]), []).append(
            {"code": row["code"], "severity": row["severity"], "message": row["message"]}
        )
    return grouped


def vendor_contacts(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {vendor["vendor_name"]: vendor for vendor in get_vendors(conn)}


def row_to_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Turn a certificates row back into validator-shaped field values."""
    from src.extractor import parse_date

    values: dict[str, Any] = {}
    for name in FIELD_NAMES:
        raw = row.get(name)
        kind = FIELD_KINDS[name]
        if raw is None:
            values[name] = None
        elif kind == "date":
            values[name] = _iso_to_date(str(raw)) or parse_date(str(raw))
        elif kind == "flag":
            values[name] = bool(raw)
        else:
            values[name] = raw
    return values


def _iso_to_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def row_confidences(row: dict[str, Any]) -> dict[str, float]:
    return {k: float(v) for k, v in json.loads(row.get("confidences") or "{}").items()}


def row_snippets(row: dict[str, Any]) -> dict[str, str]:
    return json.loads(row.get("snippets") or "{}")
