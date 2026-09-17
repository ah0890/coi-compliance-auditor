"""Optional OCR fallback for scanned certificates.

Both the Python packages (`pytesseract`, `pdf2image`) and the native binaries
(Tesseract, Poppler) are optional. When they are missing the auditor keeps
running and flags the file as `needs_ocr`.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = (
    "OCR is not available. Install the extras and the Tesseract binary to read "
    "scanned certificates:  pip install pytesseract pdf2image  "
    "(plus Tesseract OCR and Poppler on PATH). Until then this file is marked "
    "needs_ocr and skipped."
)

OCR_DPI = 300


def ocr_available() -> bool:
    """True when both optional packages import."""
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_pdf(pdf_path: Path) -> tuple[str, bool]:
    """OCR a PDF. Returns (text, available); ("", False) when OCR is absent."""
    if not ocr_available():
        log.warning("%s: %s", Path(pdf_path).name, UNAVAILABLE_MESSAGE)
        return "", False
    try:
        import pdf2image
        import pytesseract

        pages = pdf2image.convert_from_path(str(pdf_path), dpi=OCR_DPI)
        text = "\n".join(pytesseract.image_to_string(page) for page in pages)
        return text, True
    except Exception as exc:  # noqa: BLE001 - a broken OCR install must not stop the run
        log.warning("%s: OCR failed (%s: %s)", Path(pdf_path).name, type(exc).__name__, exc)
        return "", False
