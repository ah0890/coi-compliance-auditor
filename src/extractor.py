"""Label-anchored field extraction from ACORD-25-style certificate PDFs.

Strategy, in order of preference per field:
  1. Find the label's word sequence on the page, then read the value to its
     right (same text line) or in the box directly below it.
  2. Fall back to a regex over the whole page text.

Nothing in here raises for a bad file: `extract_certificate` catches and
returns an `error` on the result instead.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pdfplumber
from dateutil import parser as dateparser

from config import MIN_TEXT_CHARS

log = logging.getLogger(__name__)

# Glyphs at least this tall are page furniture (the SAMPLE watermark, big
# stamps) rather than form content. Dropping them before pdfplumber groups
# characters into words stops a stray watermark letter from fusing onto a value.
WATERMARK_MIN_SIZE = 24.0

# A value ends when the next word sits further than this many points away:
# that is a new form column, not a continuation.
COLUMN_GAP = 25.0

# How far below a label to look for a block value, and how wide that column is.
BELOW_DEPTH = 46.0
BELOW_WIDTH = 250.0

CONFIDENCE_ANCHORED = 0.95
CONFIDENCE_ANCHORED_MESSY = 0.75
CONFIDENCE_REGEX = 0.6
CONFIDENCE_ABSENT = 0.0

# First tokens of every label we know: a value never runs into one of these.
LABEL_STOP_TOKENS = frozenset(
    {
        "PRODUCER", "INSURED", "INSURER", "POLICY", "ADDL", "SUBR", "GL",
        "GENERAL", "AUTO", "UMBRELLA", "WORKERS", "E.L.", "CERTIFICATE",
        "DESCRIPTION", "COMMERCIAL", "AUTOMOBILE", "DATE", "SAMPLE", "NOT",
    }
)


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------

def parse_date(text: str) -> date | None:
    """Parse MM/DD/YYYY (the ACORD convention) out of a fragment of text."""
    match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text or "")
    if not match:
        return None
    try:
        return dateparser.parse(match.group(1), dayfirst=False).date()
    except (ValueError, OverflowError):
        return None


def parse_money(text: str) -> int | None:
    """Parse `$1,000,000` (or `1000000`) to an int. Returns None if absent."""
    match = re.search(r"\$?\s*([\d][\d,]*)(?:\.\d{2})?", text or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_flag(text: str) -> bool | None:
    """Coerce a Y/N cell to a bool."""
    token = (text or "").strip().upper()
    if not token:
        return None
    if token[0] == "Y":
        return True
    if token[0] == "N":
        return False
    return None


def parse_text(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned or None


def _parse_per_statute(text: str) -> str | None:
    """Workers-comp limit cell: usually the words PER STATUTE."""
    cleaned = parse_text(text)
    if not cleaned:
        return None
    return "PER STATUTE" if "STATUT" in cleaned.upper() else cleaned


# --------------------------------------------------------------------------
# page word access
# --------------------------------------------------------------------------

def _keep_char(obj: dict[str, Any]) -> bool:
    if obj.get("object_type") != "char":
        return True
    return float(obj.get("size", 0) or 0) < WATERMARK_MIN_SIZE


def read_words(pdf_path: Path) -> tuple[list[dict[str, Any]], str]:
    """Return (words, full_text) with watermark-sized glyphs removed."""
    words: list[dict[str, Any]] = []
    text_parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, raw_page in enumerate(pdf.pages):
            page = raw_page.filter(_keep_char)
            for word in page.extract_words(keep_blank_chars=False, use_text_flow=False):
                word["page"] = page_no
                words.append(word)
            text_parts.append(page.extract_text() or "")
    return words, "\n".join(text_parts)


def _same_line(a: dict[str, Any], b: dict[str, Any], tol: float = 3.0) -> bool:
    if a["page"] != b["page"]:
        return False
    a_mid = (a["top"] + a["bottom"]) / 2
    return b["top"] - tol <= a_mid <= b["bottom"] + tol


def find_label(words: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    """Find every occurrence of a label, returned as its list of word dicts."""
    tokens = label.upper().split()
    hits: list[dict[str, Any]] = []
    for i, word in enumerate(words):
        if word["text"].upper().strip(":") != tokens[0]:
            continue
        run = [word]
        cursor = i
        for token in tokens[1:]:
            nxt = words[cursor + 1] if cursor + 1 < len(words) else None
            if nxt is None or nxt["text"].upper().strip(":") != token or not _same_line(word, nxt):
                run = []
                break
            run.append(nxt)
            cursor += 1
        if run:
            hits.append(
                {
                    "page": word["page"],
                    "x0": run[0]["x0"],
                    "x1": run[-1]["x1"],
                    "top": min(w["top"] for w in run),
                    "bottom": max(w["bottom"] for w in run),
                    "index": cursor,
                }
            )
    return hits


def value_right(words: list[dict[str, Any]], anchor: dict[str, Any]) -> str:
    """Read the cell to the right of a label, stopping at the next column."""
    collected: list[str] = []
    cursor_x = anchor["x1"]
    for word in words[anchor["index"] + 1 :]:
        if not _same_line(anchor, word):
            break
        if word["x0"] < anchor["x1"] - 1:
            continue
        gap = word["x0"] - cursor_x
        if collected and gap > COLUMN_GAP:
            break
        if word["text"].upper().strip(":") in LABEL_STOP_TOKENS:
            break
        collected.append(word["text"])
        cursor_x = word["x1"]
    return " ".join(collected).strip()


def _drop_heading_anchors(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a label occurrence that is only the section heading above the field.

    The layout prints e.g. `CERTIFICATE HOLDER` as a section title and again as
    the field label a line or two below it. Reading "below" from the title would
    just return the label, so keep the lowest occurrence of each cluster.
    """
    kept = []
    for anchor in anchors:
        shadowed = any(
            other is not anchor
            and other["page"] == anchor["page"]
            and anchor["bottom"] < other["top"] < anchor["bottom"] + 34
            for other in anchors
        )
        if not shadowed:
            kept.append(anchor)
    return kept or anchors


def value_below(words: list[dict[str, Any]], anchor: dict[str, Any], max_lines: int = 4) -> str:
    """Read the block underneath a label, confined to the label's column."""
    candidates = [
        w
        for w in words
        if w["page"] == anchor["page"]
        and w["top"] > anchor["bottom"] - 1
        and w["top"] < anchor["bottom"] + BELOW_DEPTH
        and anchor["x0"] - 6 <= w["x0"] <= anchor["x0"] + BELOW_WIDTH
    ]
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in sorted(candidates, key=lambda w: (round(w["top"]), w["x0"])):
        lines.setdefault(round(word["top"] / 3), []).append(word)
    out: list[str] = []
    for key in sorted(lines)[:max_lines]:
        row = [w["text"] for w in sorted(lines[key], key=lambda w: w["x0"])]
        if row and row[0].upper().strip(":") in LABEL_STOP_TOKENS and out:
            break
        out.append(" ".join(row))
    return " ".join(out).strip()


# --------------------------------------------------------------------------
# field specification
# --------------------------------------------------------------------------

# name -> (label, strategy, parser, regex fallback, required?)
FIELD_SPECS: list[tuple[str, str, str, Callable[[str], Any], str | None, bool]] = [
    ("producer", "PRODUCER", "below", parse_text, None, False),
    ("insured", "INSURED", "below", parse_text, None, True),
    ("insurer_a", "INSURER A", "right", parse_text, r"INSURER A\s+(.+)", False),
    ("insurer_b", "INSURER B", "right", parse_text, r"INSURER B\s+(.+)", False),
    ("insurer_c", "INSURER C", "right", parse_text, r"INSURER C\s+(.+)", False),
    ("policy_number", "POLICY NUMBER", "right", parse_text, r"POLICY NUMBER\s*([A-Z]{2}-[A-Z0-9-]+)", True),
    ("policy_eff", "POLICY EFF", "right", parse_date, r"POLICY EFF\s+(\d{2}/\d{2}/\d{4})", True),
    ("policy_exp", "POLICY EXP", "right", parse_date, r"POLICY EXP\s+(\d{2}/\d{2}/\d{4})", True),
    ("gl_each_occurrence", "GL EACH OCCURRENCE", "right", parse_money, r"GL EACH OCCURRENCE\s+\$?([\d,]+)", True),
    ("gl_aggregate", "GENERAL AGGREGATE", "right", parse_money, r"GENERAL AGGREGATE\s+\$?([\d,]+)", True),
    ("auto_csl", "AUTO COMBINED SINGLE LIMIT", "right", parse_money, r"AUTO COMBINED SINGLE LIMIT\s+\$?([\d,]+)", False),
    ("umbrella_each_occurrence", "UMBRELLA EACH OCCURRENCE", "right", parse_money, r"UMBRELLA EACH OCCURRENCE\s+\$?([\d,]+)", False),
    ("workers_comp", "WORKERS COMP", "right", _parse_per_statute, r"WORKERS COMP\s+(PER STATUTE)", False),
    ("el_each_accident", "E.L. EACH ACCIDENT", "right", parse_money, r"E\.L\. EACH ACCIDENT\s+\$?([\d,]+)", False),
    ("addl_insd", "ADDL INSD", "right", parse_flag, r"ADDL INSD\s+([YN])\b", True),
    ("subr_wvd", "SUBR WVD", "right", parse_flag, r"SUBR WVD\s+([YN])\b", True),
    ("description_of_operations", "DESCRIPTION OF OPERATIONS", "below", parse_text, None, False),
    ("certificate_holder", "CERTIFICATE HOLDER", "below", parse_text, None, True),
]

REQUIRED_FIELDS = tuple(name for name, *_rest, required in FIELD_SPECS if required)

# One source of truth for how each field is typed, used by storage, the
# reporter and the `fix` command.
_PARSER_KINDS = {
    parse_money: "money",
    parse_date: "date",
    parse_flag: "flag",
    parse_text: "text",
    _parse_per_statute: "text",
}
FIELD_KINDS: dict[str, str] = {
    name: _PARSER_KINDS[parser] for name, _label, _strategy, parser, _pattern, _req in FIELD_SPECS
}
FIELD_NAMES = tuple(FIELD_KINDS)

_KIND_PARSERS = {"money": parse_money, "date": parse_date, "flag": parse_flag, "text": parse_text}


def coerce_value(field_name: str, raw: str) -> Any:
    """Parse a user-supplied string for `fix --field NAME --value VALUE`."""
    kind = FIELD_KINDS.get(field_name)
    if kind is None:
        raise KeyError(field_name)
    return _KIND_PARSERS[kind](raw)

# Labels that legitimately repeat once per coverage line.
MULTI_VALUE_FIELDS = {"policy_exp", "policy_eff", "policy_number"}


def _field(value: Any, confidence: float, snippet: str) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "source_snippet": snippet}


def _extract_one(
    words: list[dict[str, Any]],
    text: str,
    name: str,
    label: str,
    strategy: str,
    parser: Callable[[str], Any],
    pattern: str | None,
) -> dict[str, Any]:
    """Run anchored extraction for one field, then the regex fallback."""
    anchors = find_label(words, label)
    if strategy == "below":
        anchors = _drop_heading_anchors(anchors)
    raw_values: list[str] = []
    for anchor in anchors:
        raw = value_right(words, anchor) if strategy == "right" else value_below(words, anchor)
        if raw:
            raw_values.append(raw)
        if name not in MULTI_VALUE_FIELDS and raw:
            break

    parsed = [(parser(raw), raw) for raw in raw_values]
    parsed = [(value, raw) for value, raw in parsed if value is not None]

    if parsed:
        if name in MULTI_VALUE_FIELDS and len(parsed) > 1 and isinstance(parsed[0][0], date):
            # Several coverage lines: the earliest expiry is what governs.
            value, raw = min(parsed, key=lambda pair: pair[0])
        else:
            value, raw = parsed[0]
        messy = len(raw) > 90 or (strategy == "right" and len(raw.split()) > 8)
        confidence = CONFIDENCE_ANCHORED_MESSY if messy else CONFIDENCE_ANCHORED
        return _field(value, confidence, raw[:200])

    if pattern:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = parser(match.group(1))
            if value is not None:
                return _field(value, CONFIDENCE_REGEX, match.group(0)[:200])

    snippet = raw_values[0][:200] if raw_values else ""
    return _field(None, CONFIDENCE_ABSENT, snippet)


def file_hash(path: Path) -> str:
    """SHA-256 of the file's bytes, used for deduplication."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_certificate(pdf_path: Path) -> dict[str, Any]:
    """Extract every known field from one certificate PDF.

    Never raises: a failure is reported as `result["error"]`.
    """
    pdf_path = Path(pdf_path)
    result: dict[str, Any] = {
        "file_name": pdf_path.name,
        "file_path": str(pdf_path),
        "file_hash": "",
        "needs_ocr": False,
        "error": None,
        "fields": {},
        "raw_text": "",
    }
    try:
        result["file_hash"] = file_hash(pdf_path)
        words, text = read_words(pdf_path)

        if len(text.strip()) < MIN_TEXT_CHARS:
            from src import ocr  # imported lazily: OCR extras are optional

            ocr_text, available = ocr.ocr_pdf(pdf_path)
            if available and len(ocr_text.strip()) >= MIN_TEXT_CHARS:
                text = ocr_text
                words = []  # OCR gives text only; anchored extraction is skipped
                log.info("%s: used OCR (%d chars)", pdf_path.name, len(text))
            else:
                result["needs_ocr"] = True
                log.warning(
                    "%s: only %d characters of text - this looks scanned. %s",
                    pdf_path.name,
                    len(text.strip()),
                    ocr.UNAVAILABLE_MESSAGE if not available else "OCR produced too little text.",
                )

        result["raw_text"] = text
        for name, label, strategy, parser, pattern, _required in FIELD_SPECS:
            result["fields"][name] = _extract_one(words, text, name, label, strategy, parser, pattern)
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
        result["error"] = f"{type(exc).__name__}: {exc}"
        # The caller prints a one-line error; the traceback goes to run.log.
        log.debug("failed to extract %s", pdf_path.name, exc_info=True)
    return result


def field_values(extraction: dict[str, Any]) -> dict[str, Any]:
    """Flatten {name: {value: ...}} down to {name: value} for the validator."""
    return {name: field["value"] for name, field in extraction.get("fields", {}).items()}


def field_confidences(extraction: dict[str, Any]) -> dict[str, float]:
    return {name: float(field["confidence"]) for name, field in extraction.get("fields", {}).items()}


def low_confidence_fields(
    extraction: dict[str, Any], threshold: float, names: Iterable[str] = REQUIRED_FIELDS
) -> list[str]:
    confidences = field_confidences(extraction)
    return [name for name in names if confidences.get(name, 0.0) < threshold]
