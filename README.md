# COI Compliance Auditor

A command-line tool that reads a folder of vendor Certificates of Insurance
(ACORD 25 layout), pulls out the coverage fields, checks them against
per-category requirements you define in YAML, stores everything in SQLite, and
produces:

1. a multi-tab Excel compliance report, and
2. drafted `.eml` notices for vendors whose coverage is expiring or deficient.

**It never sends email.** Drafts are written to `output/emails/` for you to
review and send yourself. Everything runs locally — no cloud services, no API
calls, no LLMs.

---

## What it does, end to end

```
data/inbox/*.pdf
      │
      ├─ extract     label-anchored field extraction (pdfplumber), per-field confidence
      ├─ normalize   strip entity suffixes, coerce Y/N, cut addresses off company names
      ├─ match       insured name → vendors.csv (exact → normalized → fuzzy ≥ 88)
      ├─ validate    pure rule functions → findings (FAIL / WARN / INFO)
      ├─ store       SQLite at data/coi.db, deduplicated by file hash
      ├─ report      output/coi_report_YYYY-MM-DD.xlsx (6 tabs, colour-coded)
      └─ draft       output/emails/<vendor>_<date>.eml + mail_merge.csv
      │
data/processed/<vendor_slug>/   (or data/failed/ if the PDF could not be read)
```

## Install

Requires Python 3.11 or newer (developed and tested on 3.14).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Optional: OCR for scanned certificates

OCR is **not required**. If a PDF has fewer than 200 characters of text it is
assumed to be a scan; the tool will use OCR when it is available and otherwise
flag the file `needs_ocr`, print a clear message, and carry on with the rest of
the batch.

```bash
pip install pytesseract pdf2image
```

You also need the native binaries on your `PATH`: **Tesseract OCR** and
**Poppler** (`pdftoppm`). On Windows the usual route is the UB-Mannheim
Tesseract installer plus a Poppler release archive.

## Quick start

```powershell
python main.py generate-samples   # 8 synthetic SAMPLE PDFs + data/vendors.csv
python main.py audit              # extract → validate → report → draft emails
start output\coi_report_2026-09-17.xlsx
```

On macOS/Linux use `open` instead of `start`.

The samples cover eight scenarios: two fully compliant, two expiring within 30
days, one expired, one with a general-liability limit below the minimum, one
with no workers compensation, and one naming the wrong certificate holder. Their
dates are generated relative to today, so the demo always produces a live mix of
statuses.

## Commands

| Command | What it does |
| --- | --- |
| `python main.py generate-samples` | Writes eight synthetic SAMPLE certificates to `data/inbox/` and a matching `data/vendors.csv`. |
| `python main.py audit` | Audits every PDF in `data/inbox/`, writes the report and the email drafts. `--inbox PATH` to point elsewhere, `--keep-inbox` to leave files where they are, `-v` for debug output. |
| `python main.py fix <cert_id> --field NAME --value VALUE` | Corrects one extracted field by hand, sets its confidence to 1.0 and re-runs the rules for that certificate. |
| `python main.py report` | Rebuilds the Excel workbook from the database without re-reading any PDFs. |
| `python main.py status` | Prints the current status of every vendor as a console table. |

Example correction:

```bash
python main.py fix 6 --field gl_each_occurrence --value 2000000
python main.py report
```

Dates take `MM/DD/YYYY`, money takes `2000000` or `$2,000,000`, flags take `Y`/`N`.

## Configuration

### `requirements.yaml`

```yaml
certificate_holder_must_contain: "Northwind Property Management"
expiring_warning_days: [30, 60, 90]

default:
  gl_each_occurrence_min: 1000000
  gl_aggregate_min: 2000000
  auto_csl_min: 1000000
  umbrella_min: 0                      # 0 disables the rule
  workers_comp_required: true
  additional_insured_required: true
  waiver_of_subrogation_required: false

categories:
  roofing:     { gl_each_occurrence_min: 2000000, umbrella_min: 1000000 }
  janitorial:  { auto_csl_min: 500000 }
```

A category block overrides the `default` block key by key. A vendor whose
category is absent from `categories` (or who has no category at all) is checked
against `default` alone.

### `data/vendors.csv`

| column | purpose |
| --- | --- |
| `vendor_name` | Matched against the certificate's INSURED name. |
| `category` | Selects the requirement block. |
| `contact_email` | The `To:` address on drafted emails. |
| `property` | Shown in the report. |
| `notes` | Free text. |

### `.env`

Nothing in `.env` is required. `.env.example` holds commented-out `SMTP_*` keys
for a future send feature, plus an optional `SENDER_NAME` used to sign drafts
(defaults to "Risk Management").

## How findings are decided

Each certificate produces a list of findings; the overall status is **FAIL** if
any finding is FAIL, otherwise **WARN** if any is WARN, otherwise **PASS**.

| Code | Severity | Raised when |
| --- | --- | --- |
| `EXPIRED` | FAIL | The earliest coverage expiry is in the past. |
| `EXPIRING_30` / `_60` / `_90` | WARN | Expiry falls inside that window. Only the tightest bucket is reported. |
| `GL_OCCURRENCE_BELOW_MIN` | FAIL | GL each occurrence < `gl_each_occurrence_min`. |
| `GL_AGGREGATE_BELOW_MIN` | FAIL | General aggregate < `gl_aggregate_min`. |
| `AUTO_BELOW_MIN` | FAIL | Auto combined single limit < `auto_csl_min`. |
| `UMBRELLA_BELOW_MIN` | FAIL | Umbrella each occurrence < `umbrella_min`. |
| `WC_MISSING` | FAIL | Workers comp required but no coverage shown. |
| `ADDL_INSURED_NOT_MARKED` | FAIL | Required but ADDL INSD is not `Y`. |
| `WAIVER_NOT_MARKED` | FAIL | Required but SUBR WVD is not `Y`. |
| `HOLDER_MISMATCH` | FAIL | Certificate holder block does not contain the configured string. |
| `LOW_CONFIDENCE` | WARN | A rule-relevant field was extracted below 0.6 confidence. |
| `NEEDS_OCR` | WARN | The PDF has no usable text layer. |

Expiry uses the **earliest** expiry date found across the coverage lines, so a
lapsed auto policy is not hidden behind a current GL policy. A minimum of `0`
disables that limit rule. A missing limit is treated as zero, so an absent
required coverage fails rather than passing silently.

Confidence is assigned per field: `0.95` for a clean label-anchored read, `0.75`
when the captured cell looks unusually long or noisy, `0.6` for a regex fallback
over the page text, and `0.0` when nothing was found. `fix` sets it to `1.0`.
Only the fields the rules actually depend on (insured, expiry, GL limits,
certificate holder) can trigger `LOW_CONFIDENCE` — an absent umbrella limit is a
legitimate zero, not a review item.

## The report

`output/coi_report_YYYY-MM-DD.xlsx`, six tabs:

- **Summary** — counts by status, compliance %, expiry buckets, disclaimer.
- **All Vendors** — one row per vendor's current certificate, colour-coded
  (red FAIL, amber WARN, green PASS), with expiry, days remaining and findings.
- **Expiring** — the 30/60/90-day buckets, soonest first.
- **Non-Compliant** — the FAIL rows.
- **Needs Review** — low-confidence, unmatched, needs-OCR or errored files,
  with the source snippets and the top three vendor-name candidates.
- **Raw Extractions** — every processed file with every field and its confidence.

Compliance % is `PASS ÷ vendors with a certificate on file`; WARN rows count
against it. Header rows are frozen and filtered, and columns are autosized.

## Assumptions

Simplest-option choices made while building this:

- **One certificate per vendor governs.** The "current" certificate is the one
  with the latest expiry date. Older ones stay in the database and the Raw
  Extractions tab.
- **The company name ends where the street address begins.** The INSURED block
  is cut at the first house number, so a company with a leading digit in its
  name (e.g. "7-Eleven") would need a manual `fix`.
- **Missing limits are zero**, so a required coverage that is absent fails.
- **Glyphs 24pt or larger are page furniture** (watermarks, stamps) and are
  dropped before words are assembled, which stops a stamp letter from fusing
  onto a value.
- **An already-expired certificate gets the deficiency template**, not the
  renewal-reminder template.
- **Unmatched certificates are still audited** against the `default`
  requirements and listed individually in Needs Review.
- **Duplicates are decided by file hash**, so a re-sent identical PDF is skipped
  but a re-issued certificate is audited as a new row.
- Sample vendor categories beyond `roofing` and `janitorial` were added to
  `requirements.yaml`; `painting` was deliberately left out to exercise the
  fallback to `default`.

## Limitations

- Built for **digital ACORD-25-style PDFs with a text layer** first. Other
  carriers' layouts will extract less reliably; check the Needs Review tab.
- **Scanned certificates need the OCR extras.** Without them they are flagged,
  not read. OCR'd text also skips label-anchored extraction and relies on the
  regex fallback, so confidence is lower.
- Field labels are matched in English, upper-case ACORD wording.
- The tool reads the certificate, not the policy. A certificate is not proof of
  coverage and endorsements are not parsed.
- **Always verify before acting.** Every report carries that disclaimer; treat
  the output as a triage queue, not a compliance determination.

## Security notes

- Everything is local: no network calls, no telemetry, no third-party services.
- **Nothing is ever emailed.** `emailer.py` has no SMTP code at all; it only
  writes `.eml` files.
- Policy numbers are masked to their last four characters in both the console
  output and `output/run.log`.
- `.gitignore` excludes `.env`, `data/inbox/`, `data/processed/`, `data/failed/`,
  `data/*.db` and `output/` — real certificates, the database and generated
  reports never land in git.
- The sample PDFs are synthetic, watermarked SAMPLE, and use `example.com`
  addresses. They are not real certificates and contain no real vendor data.

## Tests

```bash
pytest -q
```

71 tests, fully offline. Fixture PDFs are generated into `tests/fixtures/` on
first run (and regenerated when stale, since sample dates are relative to
today). `test_extractor.py` asserts field-level extraction against those PDFs,
`test_validator.py` covers every rule with hand-built dicts, and
`test_reporter.py` runs the real pipeline into a temporary database and asserts
the workbook, its tabs, the colour fills and the email drafts.

## Project layout

```
main.py                 Typer CLI: generate-samples, audit, fix, report, status
config.py               paths, .env, requirements.yaml loading and merging
requirements.yaml       coverage minimums, per category
src/extractor.py        label-anchored PDF field extraction + confidence
src/ocr.py              optional Tesseract fallback
src/normalizer.py       name/flag/slug normalization
src/matcher.py          insured name → vendors.csv (rapidfuzz)
src/validator.py        pure rule functions → findings
src/storage.py          SQLite schema, dedup, current-certificate query
src/reporter.py         multi-tab Excel workbook
src/emailer.py          .eml drafting + mail_merge.csv (never sends)
src/sample_generator.py synthetic SAMPLE certificate PDFs
templates/              expiring.txt, deficient.txt
tests/                  pytest suite
```

## What to improve next

1. **Per-coverage-line detail.** Expiry and limits are currently flattened to
   one row per certificate; storing GL/auto/umbrella/WC as separate coverage
   rows would let findings point at the specific policy.
2. **More layouts.** Add a second and third real-world carrier layout to the
   fixture set and widen the label synonym table.
3. **Endorsement parsing.** Read the Description of Operations text for
   additional-insured and primary/non-contributory wording instead of trusting
   the ADDL INSD column.
4. **A `send` command** behind an explicit `--yes` flag, using the `.env` SMTP
   keys, with a dry-run default.
5. **History and trends.** The database already keeps every certificate; a
   "compliance over time" tab is a short step from there.
6. **Configurable severity.** Let `requirements.yaml` choose whether a given
   rule is FAIL or WARN per category.
