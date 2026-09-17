"""Text normalization shared by the matcher, storage and reporting."""

from __future__ import annotations

import re
import unicodedata

# Legal-entity suffixes that carry no matching signal.
SUFFIXES = frozenset(
    {
        "llc", "l l c", "inc", "incorporated", "corp", "corporation", "co",
        "company", "ltd", "limited", "lp", "llp", "pllc", "pc", "plc", "group",
    }
)

# A street address inside an extracted block starts at a house number.
_ADDRESS_START = re.compile(r"\s\d+\s+[A-Za-z]")
_PUNCT = re.compile(r"[^\w\s&]")
_WS = re.compile(r"\s+")


def normalize_whitespace(text: str | None) -> str:
    return _WS.sub(" ", (text or "").strip())


def company_name(text: str | None) -> str:
    """Pull the company name out of an extracted INSURED block.

    The block usually reads `ACME ROOFING LLC 1420 Industrial Way, Tacoma WA`,
    so everything from the first street number onward is dropped.
    """
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return ""
    match = _ADDRESS_START.search(cleaned)
    if match:
        cleaned = cleaned[: match.start()]
    return cleaned.strip(" ,.-")


def strip_suffixes(name: str) -> str:
    """Remove trailing legal-entity suffixes (LLC, Inc., Corp, ...)."""
    tokens = name.split()
    while tokens and tokens[-1].lower().strip(".,") in SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_name(name: str | None) -> str:
    """Casefold, drop punctuation and entity suffixes, collapse whitespace."""
    text = unicodedata.normalize("NFKD", company_name(name)).encode("ascii", "ignore").decode()
    text = text.replace("&", " and ")
    text = _PUNCT.sub(" ", text).lower()
    text = normalize_whitespace(text)
    return normalize_whitespace(strip_suffixes(text))


def coerce_flag(value: object) -> bool | None:
    """Coerce Y/N/true/false/1/0 to a bool, or None when unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    token = str(value).strip().upper()
    if token in {"Y", "YES", "TRUE", "1", "X"}:
        return True
    if token in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def slugify(text: str | None, fallback: str = "unknown") -> str:
    """Filesystem-safe slug used for processed/ subfolders and .eml names."""
    slug = _PUNCT.sub(" ", normalize_whitespace(text).lower()).strip()
    slug = re.sub(r"[\s&]+", "_", slug).strip("_")
    return slug or fallback
