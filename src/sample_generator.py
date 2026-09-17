"""Generate synthetic ACORD-25-style certificate PDFs for demos and tests.

The layout here is original: it borrows the ACORD 25 *field labels* (which are
what the extractor anchors on) but none of the form's artwork. Every page is
stamped SAMPLE.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib.colors import Color, black, grey
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdfcanvas

PAGE_W, PAGE_H = letter
MARGIN = 36.0
CONTENT_W = PAGE_W - 2 * MARGIN
LABEL_FONT = "Helvetica-Bold"
VALUE_FONT = "Helvetica"

HOLDER_GOOD = "Northwind Property Management\n500 Harbor Point Drive, Suite 400\nSeattle, WA 98104"
HOLDER_WRONG = "Eastvale Realty Holdings LLC\n88 Cannery Row, Portland, OR 97209"


def _d(offset_days: int) -> str:
    """A date `offset_days` from today, formatted MM/DD/YYYY."""
    return (date.today() + timedelta(days=offset_days)).strftime("%m/%d/%Y")


def sample_specs() -> list[dict[str, Any]]:
    """The eight synthetic certificates, defined relative to today."""
    return [
        {
            "slug": "apex_roofing",
            "scenario": "compliant",
            "insured": "APEX ROOFING & SHEET METAL LLC\n1420 Industrial Way, Tacoma, WA 98421",
            "vendor_name": "Apex Roofing and Sheet Metal, LLC",
            "category": "roofing",
            "email": "certs@apexroofing.example.com",
            "property": "Harbor Point Tower",
            "producer": "Cascade Risk Partners\n900 Second Ave, Seattle, WA 98104\n(206) 555-0142",
            "insurers": ["Summit Casualty Insurance Co.", "Pacific Indemnity Group", "Cascade Excess Ltd."],
            "eff": _d(-160),
            "exp": _d(205),
            "gl_occurrence": 2_000_000,
            "gl_aggregate": 4_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 2_000_000,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "Y",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "clearview_janitorial",
            "scenario": "compliant",
            "insured": "CLEARVIEW JANITORIAL SERVICES INC.\n77 Rainier Blvd, Renton, WA 98057",
            "vendor_name": "Clearview Janitorial Services Inc",
            "category": "janitorial",
            "email": "insurance@clearviewjan.example.com",
            "property": "Northgate Commons",
            "producer": "Emerald Insurance Brokers\n310 Pike St, Seattle, WA 98101\n(206) 555-0177",
            "insurers": ["Evergreen Mutual Insurance", "Northwest Fidelity Casualty", "Puget Surplus Lines"],
            "eff": _d(-200),
            "exp": _d(160),
            "gl_occurrence": 1_000_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 500_000,
            "umbrella": 0,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "N",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "summit_hvac",
            "scenario": "expiring_soon",
            "insured": "SUMMIT HVAC SOLUTIONS LLC\n2200 Mercer St, Seattle, WA 98109",
            "vendor_name": "Summit HVAC Solutions LLC",
            "category": "hvac",
            "email": "ops@summithvac.example.com",
            "property": "Harbor Point Tower",
            "producer": "Rainier Commercial Insurance\n45 Boren Ave N, Seattle, WA 98109\n(206) 555-0198",
            "insurers": ["Evergreen Mutual Insurance", "Summit Casualty Insurance Co.", "Cascade Excess Ltd."],
            "eff": _d(-345),
            "exp": _d(20),
            "gl_occurrence": 1_000_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 1_000_000,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "N",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "ironclad_security",
            "scenario": "expiring_soon",
            "insured": "IRONCLAD SECURITY GROUP INC\n610 Denny Way, Seattle, WA 98109",
            "vendor_name": "Ironclad Security Group, Inc.",
            "category": "security",
            "email": "compliance@ironcladsec.example.com",
            "property": "Northgate Commons",
            "producer": "Cascade Risk Partners\n900 Second Ave, Seattle, WA 98104\n(206) 555-0142",
            "insurers": ["Pacific Indemnity Group", "Evergreen Mutual Insurance", "Puget Surplus Lines"],
            "eff": _d(-338),
            "exp": _d(27),
            "gl_occurrence": 1_000_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 0,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "Y",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "greenline_landscaping",
            "scenario": "expired",
            "insured": "GREENLINE LANDSCAPING CO\n8800 Aurora Ave N, Shoreline, WA 98133",
            "vendor_name": "Greenline Landscaping Co.",
            "category": "landscaping",
            "email": "admin@greenlinelandscape.example.com",
            "property": "Willow Creek Apartments",
            "producer": "Emerald Insurance Brokers\n310 Pike St, Seattle, WA 98101\n(206) 555-0177",
            "insurers": ["Northwest Fidelity Casualty", "Evergreen Mutual Insurance", "Cascade Excess Ltd."],
            "eff": _d(-425),
            "exp": _d(-60),
            "gl_occurrence": 1_000_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 0,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "N",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "metro_plumbing",
            "scenario": "gl_below_minimum",
            "insured": "METRO PLUMBING PARTNERS LLC\n1501 Airport Way S, Seattle, WA 98134",
            "vendor_name": "Metro Plumbing Partners LLC",
            "category": "plumbing",
            "email": "billing@metroplumbing.example.com",
            "property": "Willow Creek Apartments",
            "producer": "Rainier Commercial Insurance\n45 Boren Ave N, Seattle, WA 98109\n(206) 555-0198",
            "insurers": ["Puget Surplus Lines", "Northwest Fidelity Casualty", "Summit Casualty Insurance Co."],
            "eff": _d(-120),
            "exp": _d(245),
            "gl_occurrence": 500_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 0,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "Y",
            "subr_wvd": "N",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "voltaic_electric",
            "scenario": "missing_workers_comp",
            "insured": "VOLTAIC ELECTRIC CORP\n3320 Fourth Ave S, Seattle, WA 98134",
            "vendor_name": "Voltaic Electric Corp",
            "category": "electrical",
            "email": "certificates@voltaicelectric.example.com",
            "property": "Harbor Point Tower",
            "producer": "Cascade Risk Partners\n900 Second Ave, Seattle, WA 98104\n(206) 555-0142",
            "insurers": ["Summit Casualty Insurance Co.", "Pacific Indemnity Group", "Cascade Excess Ltd."],
            "eff": _d(-90),
            "exp": _d(275),
            "gl_occurrence": 2_000_000,
            "gl_aggregate": 4_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 1_000_000,
            "wc": False,
            "el_each_accident": 0,
            "addl_insd": "Y",
            "subr_wvd": "N",
            "holder": HOLDER_GOOD,
        },
        {
            "slug": "precision_painting",
            "scenario": "wrong_certificate_holder",
            "insured": "PRECISION PAINTING & DRYWALL LLC\n455 Andover Park E, Tukwila, WA 98188",
            "vendor_name": "Precision Painting & Drywall LLC",
            "category": "painting",
            "email": "office@precisionpaint.example.com",
            "property": "Northgate Commons",
            "producer": "Emerald Insurance Brokers\n310 Pike St, Seattle, WA 98101\n(206) 555-0177",
            "insurers": ["Evergreen Mutual Insurance", "Puget Surplus Lines", "Northwest Fidelity Casualty"],
            "eff": _d(-150),
            "exp": _d(215),
            "gl_occurrence": 1_000_000,
            "gl_aggregate": 2_000_000,
            "auto_csl": 1_000_000,
            "umbrella": 0,
            "wc": True,
            "el_each_accident": 1_000_000,
            "addl_insd": "N",
            "subr_wvd": "N",
            "holder": HOLDER_WRONG,
        },
    ]


def _money(amount: int) -> str:
    return f"${amount:,}"


def _policy_no(slug: str, line: str) -> str:
    """Stable pseudo-policy number derived from the slug (no randomness)."""
    stem = "".join(part[0] for part in slug.split("_")).upper()
    digits = sum(ord(ch) for ch in slug + line) * 7919 % 9_000_000 + 1_000_000
    return f"{line}-{stem}-{digits}"


def _watermark(c: pdfcanvas.Canvas) -> None:
    c.saveState()
    c.setFillColor(Color(0.88, 0.88, 0.90))
    c.setFont("Helvetica-Bold", 96)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(38)
    c.drawCentredString(0, 0, "SAMPLE")
    c.restoreState()


def _section(c: pdfcanvas.Canvas, y: float, title: str, height: float) -> float:
    """Draw a bordered section box with a title bar. Returns the first body y."""
    c.setStrokeColor(grey)
    c.setLineWidth(0.7)
    c.rect(MARGIN, y - height, CONTENT_W, height, stroke=1, fill=0)
    c.setFillColor(Color(0.93, 0.93, 0.95))
    c.rect(MARGIN, y - 16, CONTENT_W, 16, stroke=1, fill=1)
    c.setFillColor(black)
    c.setFont(LABEL_FONT, 8)
    c.drawString(MARGIN + 5, y - 12, title)
    return y - 30


def _field(c: pdfcanvas.Canvas, x: float, y: float, label: str, value: str, value_dx: float = 150) -> None:
    """One label-anchored field: bold label at x, value at x + value_dx."""
    c.setFillColor(black)
    c.setFont(LABEL_FONT, 7.5)
    c.drawString(x, y, label)
    c.setFont(VALUE_FONT, 8.5)
    c.drawString(x + value_dx, y, value)


def _block(c: pdfcanvas.Canvas, x: float, y: float, label: str, text: str) -> None:
    """A label with a multi-line address-style value underneath it."""
    c.setFillColor(black)
    c.setFont(LABEL_FONT, 7.5)
    c.drawString(x, y, label)
    c.setFont(VALUE_FONT, 8.5)
    for i, line in enumerate(text.split("\n")):
        c.drawString(x, y - 11 - i * 10, line)


def _wrap(text: str, width: int) -> str:
    """Greedy wrap into newline-separated lines of at most `width` characters."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def render_certificate(spec: dict[str, Any], out_path: Path) -> Path:
    """Render one synthetic certificate PDF."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_path), pagesize=letter)
    c.setTitle(f"SAMPLE Certificate of Liability Insurance - {spec['vendor_name']}")
    _watermark(c)

    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, PAGE_H - MARGIN - 4, "CERTIFICATE OF LIABILITY INSURANCE")
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawCentredString(
        PAGE_W / 2,
        PAGE_H - MARGIN - 16,
        "SAMPLE - SYNTHETIC TRAINING DOCUMENT. NOT A REAL CERTIFICATE AND NOT EVIDENCE OF INSURANCE.",
    )
    c.setFont(VALUE_FONT, 7.5)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - 28, f"DATE (MM/DD/YYYY)  {_d(-2)}")

    y = PAGE_H - MARGIN - 36

    # --- Producer / insured -------------------------------------------------
    body = _section(c, y, "PRODUCER AND INSURED", 96)
    _block(c, MARGIN + 6, body, "PRODUCER", spec["producer"])
    _block(c, MARGIN + CONTENT_W / 2, body, "INSURED", spec["insured"])
    y -= 104

    # --- Insurers -----------------------------------------------------------
    body = _section(c, y, "INSURER(S) AFFORDING COVERAGE", 62)
    for i, (letter_, name) in enumerate(zip("ABC", spec["insurers"])):
        _field(c, MARGIN + 6, body - i * 12, f"INSURER {letter_}", name, value_dx=70)
    y -= 70

    # --- General liability --------------------------------------------------
    body = _section(c, y, "COMMERCIAL GENERAL LIABILITY", 76)
    _field(c, MARGIN + 6, body, "POLICY NUMBER", _policy_no(spec["slug"], "GL"))
    _field(c, MARGIN + 300, body, "ADDL INSD", spec["addl_insd"], value_dx=80)
    _field(c, MARGIN + 6, body - 12, "POLICY EFF", spec["eff"])
    _field(c, MARGIN + 300, body - 12, "SUBR WVD", spec["subr_wvd"], value_dx=80)
    _field(c, MARGIN + 6, body - 24, "POLICY EXP", spec["exp"])
    _field(c, MARGIN + 6, body - 40, "GL EACH OCCURRENCE", _money(spec["gl_occurrence"]))
    _field(c, MARGIN + 300, body - 40, "GENERAL AGGREGATE", _money(spec["gl_aggregate"]), value_dx=110)
    y -= 84

    # --- Automobile ---------------------------------------------------------
    body = _section(c, y, "AUTOMOBILE LIABILITY", 56)
    _field(c, MARGIN + 6, body, "POLICY NUMBER", _policy_no(spec["slug"], "AU"))
    _field(c, MARGIN + 6, body - 12, "POLICY EXP", spec["exp"])
    _field(c, MARGIN + 6, body - 28, "AUTO COMBINED SINGLE LIMIT", _money(spec["auto_csl"]), value_dx=175)
    y -= 64

    # --- Umbrella -----------------------------------------------------------
    body = _section(c, y, "UMBRELLA LIABILITY", 44)
    if spec["umbrella"] > 0:
        _field(c, MARGIN + 6, body, "POLICY NUMBER", _policy_no(spec["slug"], "UM"))
        _field(c, MARGIN + 6, body - 16, "UMBRELLA EACH OCCURRENCE", _money(spec["umbrella"]), value_dx=175)
    else:
        c.setFont(VALUE_FONT, 8.5)
        c.drawString(MARGIN + 6, body, "NOT COVERED UNDER THIS CERTIFICATE")
    y -= 52

    # --- Workers compensation ------------------------------------------------
    body = _section(c, y, "WORKERS COMPENSATION AND EMPLOYERS LIABILITY", 56)
    if spec["wc"]:
        _field(c, MARGIN + 6, body, "POLICY NUMBER", _policy_no(spec["slug"], "WC"))
        _field(c, MARGIN + 6, body - 14, "WORKERS COMP", "PER STATUTE")
        _field(c, MARGIN + 6, body - 28, "E.L. EACH ACCIDENT", _money(spec["el_each_accident"]))
    else:
        c.setFont(VALUE_FONT, 8.5)
        c.drawString(MARGIN + 6, body, "NO COVERAGE PROVIDED - SEE DESCRIPTION OF OPERATIONS")
    y -= 64

    # --- Description of operations -------------------------------------------
    body = _section(c, y, "DESCRIPTION OF OPERATIONS", 58)
    desc = f"{spec['category'].title()} services performed at {spec['property']}."
    if spec["addl_insd"] == "Y":
        desc += " Certificate holder is included as additional insured where required by written contract."
    if not spec["wc"]:
        desc += " Workers compensation coverage is not carried; sole proprietor exemption claimed."
    _block(c, MARGIN + 6, body, "DESCRIPTION OF OPERATIONS", _wrap(desc, 105))
    y -= 66

    # --- Certificate holder ---------------------------------------------------
    body = _section(c, y, "CERTIFICATE HOLDER", 62)
    _block(c, MARGIN + 6, body, "CERTIFICATE HOLDER", spec["holder"])

    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColor(grey)
    c.drawCentredString(
        PAGE_W / 2, MARGIN - 8, "SAMPLE DOCUMENT - generated by COI Compliance Auditor for testing."
    )
    c.showPage()
    c.save()
    return out_path


def write_vendors_csv(specs: list[dict[str, Any]], path: Path) -> Path:
    """Write the vendor roster the matcher resolves insured names against."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["vendor_name", "category", "contact_email", "property", "notes"]
        )
        writer.writeheader()
        for spec in specs:
            writer.writerow(
                {
                    "vendor_name": spec["vendor_name"],
                    "category": spec["category"],
                    "contact_email": spec["email"],
                    "property": spec["property"],
                    "notes": f"sample scenario: {spec['scenario']}",
                }
            )
    return path


def generate_samples(inbox: Path, vendors_csv: Path) -> list[Path]:
    """Write all eight sample PDFs plus the matching vendors.csv."""
    inbox.mkdir(parents=True, exist_ok=True)
    specs = sample_specs()
    written = [
        render_certificate(spec, inbox / f"{i:02d}_{spec['slug']}_coi.pdf")
        for i, spec in enumerate(specs, start=1)
    ]
    write_vendors_csv(specs, vendors_csv)
    return written
