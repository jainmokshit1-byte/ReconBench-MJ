# Diagnosis: why extraction from scanned documents is inaccurate

Date: 9 Oct 2026. Mode: read-only analysis. No source, config, migration or test file was changed. No external AI or OCR service was called; `RB_AI_EXTERNAL_CALLS` stayed `disabled`.

**Evidence tags.** **verified**: confirmed in code (path:line) or by command output in this session. **inferred**: follows from verified facts but was not executed end to end. **UNKNOWN**: not verifiable here. The reason is given each time.

**Privacy.** Private samples and truth live under `work/` (git-ignored). This document holds only counts, booleans, truth-set ids (`S01`…`S16`) and masked patterns (`L` = letter, `D` = digit). File ids whose names hint at a supplier are aliased: `BUNDLE-A`, `BUNDLE-B`, `INV-COPY`, `INV-P3`.

---

## 0. Summary

The scanned pages in the truth set are good quality: 200–240 DPI native, at most 1.1° of skew, and three pages rotated by 90° that orientation detection already fixes. Most errors happen **after OCR**. In 45 of 59 header misses and 166 of 194 line-cell misses on scanned pages, the correct value **is present in the OCR text** and the rules did not extract it.

| # | Root cause (stage) | Evidence (scanned-page documents, n = 33) |
|---|---|---|
| 1 | **Line-table logic (B).** No table heading is found, heading columns are misclassified, or a heading is found but no item rows are produced. | Line amount 28 B vs 5 A misses; qty 37 B; rate 29 B vs 14 A; description 52 B vs 3 A. Probe: no heading on 2 docs, wrong column kinds on 3 docs, heading with 0 items on 2 pages. |
| 2 | **Header rules (B): vendor name and totals.** The vendor name is extracted only from "for <Company>" or supplier-labelled blocks. Totals come only from a fixed label list or table summary rows. | Vendor name missing although printed: 16 scan docs (+6 on digital pages). Totals/tax: 23 B misses on scans (+6 on digital pages). |
| 3 | **Bench vs upload-path differences, and config ignored on the scan path.** | The bench injects `buyer_entities` from truth, but the seed config has none (`seed_data.py:254`). Workspace `synonyms`, UOM conversions and GST slab dates are not used by the layout (scan) extractor. `RB_EXTRACTION_ENGINE` is read only by the bench. |

Pure OCR failures (A) are the minority: 9 of 59 header misses and 24 of 194 line-cell misses. They sit on handwritten or low-contrast pages, and include handwritten PO references that Tesseract cannot read. No role error (C) and no page-split error (D) remains on this truth set, **with** `buyer_entities` configured. Link recall is 3/10, and 4 of the 7 missed links are a **matching** regression, not an extraction problem (§5.7).

---

## 1. Repository state (verified)

- `git rev-parse HEAD` → `0a0521071d301dee6e805d1be9d050e8f546c130` (branch `phase2-hosted-ci`).
- `git status --short` → 84 entries: 61 modified tracked files (backend AI, extraction, ingestion, mapping, recon, tests, web, docs, scripts) and 23 untracked, including `CODEBASE_CONTEXT.md`, migrations `0021`–`0024`, `reconbench_app/ai/consensus.py`, `reconbench_app/document_identity.py`, `reconbench_core/extraction/consensus.py`, and new tests and scripts. All findings describe this **working tree**, not `HEAD`.
- `CODEBASE_CONTEXT.md` (dated 9 Oct 2026, static) was read first. Its claims were re-checked below. Corrections: the scan trigger has **two** different definitions (§3), and the seed GST slabs lack 40% while the parser default includes it (§6).

## 2. Wiring map

### 2.1 Upload path for a PDF (verified)

```
POST /api/v1/files → IngestionService (ingestion.py) → Procrastinate ingest_file → parse_file
  ingestion.py:1720-1727  parse_pdf_isolated(content, _parser_hints(...), languages=RB_PDF_OCR_LANGUAGES, einvoice_keys)
    pdf_sandbox.py:668  parse_pdf_isolated
      pdf_sandbox.py:715  _extract_isolated
        [child 1, 8 CPU-s / 12 s wall]  core parsers/pdf.py:1142 parse_pdf  (pdfplumber text layer; a page with <20 alnum chars is "scanned" and skipped)
        if any scanned page OR not fully verified:
          pdf_sandbox.py:565 _prefer_layout   (skipped when >20 pages, :582)
            [child 2, 150 CPU-s / 180 s wall] app extraction/pipeline.py:137 layout_parse
               engine.py:23 engine_for("local_hybrid") → local_hybrid.py:303 LocalHybridEngine.layout
                  text-layer words for pages without a ≥50% image; Tesseract for pages with one
               core extraction/pages.py (page classify + split) → core extraction/extract.py:787 extract_document
               pipeline.py:61 _dual_read (2× crop, PSM 7) for doc_no/doc_date/po_refs
               parsers/parties.py guard_result
            layout wins if it split the file, scores higher, or (scanned and) scores equal (:592-601)
        else if scanned pages: [child 3, 90 CPU-s / 120 s] OCRmyPDF force-OCR + core parse_pdf (:270-327, :777-808)
        else if critical header gaps and no complete lines: OCRmyPDF on all pages (:813-843)
      invoice only: QR decode child (first 5 pages, qr_decode.py:17) → apply_einvoice
      page dHash child → page_image_hashes
  _persist_parse → _upsert_document (identity/versioning) → vendor → links → recon (unchanged by this diagnosis)
```

### 2.2 Every parser path for PDFs

| Path | Code | Used on upload? | Notes |
|---|---|---|---|
| Core text parser | `reconbench_core/parsers/pdf.py:1142` | Yes, always runs first | Uses workspace `synonyms`. Never OCRs. "Scanned" = fewer than 20 alnum chars (`pdf.py:41`, `:1207`). |
| Layout engine (local hybrid) | `reconbench_app/extraction/pipeline.py:137`, `local_hybrid.py` | Yes, when scanned/unverified and ≤20 pages | **The final reader for all 39 PDF documents in the truth set** (verified, §5.1). Own fixed label lists, no `synonyms`. |
| OCRmyPDF + core parser | `pdf_sandbox.py:270`, `:777`, `:813` | Fallback only | Different OCR settings (OCRmyPDF defaults, no rule removal, no deskew search). Never the final reader on the truth set. Works in the worker image: **UNKNOWN**. OCRmyPDF needs a rasterizer, and the Dockerfile installs no Ghostscript (`deploy/backend.Dockerfile:4-11`); see §9. |
| QR e-invoice | `reconbench_app/extraction/qr_decode.py`, `reconbench_core/extraction/qr.py` | Invoices | 11 truth docs carry QR fields; signed = 0 (no NIC key configured in the bench). |
| `scripts/accuracy_bench.py` | `:382-431` `read_file` → `parse_pdf_isolated` | — | Same sandbox entry point as upload. |
| `scripts/measure_extraction.py` | `:174` → `parse_pdf_isolated` | — | Legacy. Uses an older truth format. |
| `scripts/accuracy_vision.py` | `vision_pages_isolated` + `GeminiProvider` | — | H6 bench only (cached by default). |

### 2.3 Benchmark vs upload-path differences (verified unless tagged)

| # | Difference | Evidence | Effect |
|---|---|---|---|
| M1 | The bench sets `buyer_entities` from the truth file. Upload reads the workspace config, which is **empty in the seed**. | `accuracy_bench.py:1608` vs `ingestion.py:1807-1852`, `seed_data.py:254` | The bench hides role errors (stage C) that occur when a workspace has no buyer entities. The live workspace value is UNKNOWN (no DB access in this session). |
| M2 | The bench builds hints from `DEFAULT_CONFIG`, not the workspace's active config. | `accuracy_bench.py:1607`, `:314-330` | Any workspace edit (UOM aliases, charge keywords, slabs) is not benchmarked. |
| M3 | The bench passes no `einvoice_keys`. | `accuracy_bench.py:415` vs `ingestion.py:1727` | QR is never "signed" in the bench. Whether the app has `RB_EINVOICE_PUBLIC_KEY_FILE` set is UNKNOWN. |
| M4 | The bench reads `RB_EXTRACTION_ENGINE`. The app has no such setting and always uses the default `local_hybrid`. | `accuracy_bench.py:1816`; `settings.py` has no engine field; `ingestion.py:1723` passes no `engine_name` | The env var documented in `extraction/__init__.py:3` has no effect on upload. |
| M5 | The bench reads files in a thread pool (`--workers`, default cpu/3), so sandbox children run in parallel. | `accuracy_bench.py:1613-1628`, `:1817` | A cold run can time out the 12 s text child (recorded in earlier sessions). Upload-path concurrency is UNKNOWN. |
| M6 | `measure_extraction.py` uses bare `ParserHints` (no buyer entities, no config), scores only `documents[0]` (ignores page splits), and uses the old truth format. | `measure_extraction.py:167-191` | Its numbers are not comparable with the bench or the upload path. |
| M7 | The bench runs vision on every PDF doc. The app runs it only in `ai_first` mode, or in `ai_assisted` mode when `needs_verification`, and only up to 8 pages. | `ai/vision.py:105-123`, `ai/limits.py:48-56` | Vision numbers are an upper bound on coverage. |

## 3. Values confirmed from code (verified)

| Item | Value | Location |
|---|---|---|
| Layout scan trigger | One image covering ≥50% of the page area (per image, not summed) | `local_hybrid.py:29`, `:54-61` |
| Core "scanned" trigger | Fewer than 20 alphanumeric chars of text layer | `parsers/pdf.py:41`, `:1205-1207` |
| Render DPI | 300 (pypdfium2) | `local_hybrid.py:27`, `:326` |
| OCR | Tesseract `--psm 11`, `-l eng` (default), `preserve_interword_spaces=1`, TSV, 60 s timeout | `local_hybrid.py:44-47`, `:223-235`; `settings.py:45-49` |
| Orientation | Tesseract OSD (`--psm 0`); rotate only if confidence ≥2.0 | `local_hybrid.py:186-200` |
| Deskew | Projection profile, ±5° coarse 0.5° then 0.1°; under 0.1° ignored | `local_hybrid.py:32`, `:155-172` |
| Grid removal | Dark < 160; horizontal runs ≥6% of width; vertical ≥60 px if touching a rule | `local_hybrid.py:30-36`, `:89-113` |
| Binarisation / denoise / upscale | None. A grayscale PNG goes to Tesseract (its internal Otsu). | `local_hybrid.py:213-222` |
| Dual read | doc_no, doc_date, po_refs; 2× crop, `--psm 7`; confirmed only if equal and no look-alikes | `pipeline.py:37-101` |
| Page caps | Layout: 20 (`pdf_sandbox.py:37`, `:423`; `pipeline.py:143`). Over 20 pages, the layout path is **skipped** (`pdf_sandbox.py:582`). Core: `ParserHints.max_pages` = 100 (`types.py:50`, seed `seed_data.py:295`), fails `PDF_PAGE_LIMIT` (`pdf.py:1200`). QR: first 5 pages (`qr_decode.py:17`). Vision: 8 pages (`ai/limits.py:54`). | |
| Sandbox limits | Text child 8 CPU-s/12 s; layout 150 CPU-s/180 s; OCRmyPDF 90/120 s; memory 2 GiB | `pdf_sandbox.py:25-42` |
| Parser version | `PARSER_VERSION = "phase4-parser-6"` (was `-4` at the previous commit) | `ingestion.py:87`; `git diff HEAD` |
| What makes old files re-parse | Only a **per-file manual retry**. A parsed file whose `parser_version` ≠ current is listed with `retry_available=True, retry_reason=PARSER_VERSION_OUTDATED`; `POST …/retry` re-queues it. No bulk or automatic re-parse. `PARSER_VERSION` is independent of `ENGINE_VERSION` (0.1.7) and of the extraction-code hash, so it must be bumped by hand. | `ingestion.py:267-270`, `:753-786`, `:3250-3320` |

**Routes where a poor or sparse text layer skips our OCR (verified in code; impact on the truth set measured in §5.1):**

1. **Tiled scans.** A page whose scan is stored as several images, each under 50% of the page, is "digital" for the layout engine (`local_hybrid.py:56-60`). Only its text layer is read; Tesseract never runs. Truth set: 0 such pages.
2. **Scanner (e.g. Epson) invisible OCR layer.** The core parser sees ≥20 chars and treats the page as text. If the core result is fully verified, `_extract_isolated` never tries the layout engine (`pdf_sandbox.py:763-776`). If the layout result does not score strictly higher, the poor layer wins (`:599-601`; equality wins only when the core marked a page scanned). Truth set: 17 scan pages carry such a layer; the layout engine won on all of them.
3. **More than 20 pages.** The layout path is skipped entirely (`pdf_sandbox.py:582`). Only the text layer or OCRmyPDF is used, which is inferred to be worse on tables (no rule removal).
4. **Layout child timeout** (150 CPU-s / 180 s). Falls back to OCRmyPDF. Each scanned page costs OSD + a 32-angle skew sweep + full-page OCR. A long scan can hit the limit (inferred; not timed here).
5. **Text child timeout** (12 s). The whole file fails with `PDF_PROCESS_TIMEOUT` before any OCR (`pdf_sandbox.py:742-747`).

## 4. Baseline confirmation

`make accuracy-bench` could not run: Docker Desktop was down (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`). Instead, `work/diag-scan/replay_bench.py` runs `scripts/accuracy_bench.py` **from its reader cache only**. It pins the tool versions to the last report's `meta.reader`, and fresh reads, page renders and provider sends raise an error. The extraction-code hash of the working tree **matches** the reference report.

Command (PowerShell): `backend\.venv\Scripts\python.exe work\diag-scan\replay_bench.py work\accuracy-reports\after-ac9a-grn-anchor-final-20261009.json --out work\diag-scan\replay-baseline.json --workers 1`

Result (verified, report hash `9cd8137fd7c1`, identical to D58; engine 0.1.7; Tesseract 5.5.0 in the cached reader key):

| Metric | Value |
|---|---|
| Files / parsed docs / missed / spurious | 35 / 51 / 0 / 0 |
| Header fields (all docs incl. XML) | 302/373 (81.0%): invoice 207/263, PO 49/56, GRN 46/54 |
| Line recall (all) / spurious lines | 96/141 (68.1%) / 33 |
| Auto-verified docs | 18/51 |
| Links expected / auto / suggested / missed | 10 / 0 / 3 / 7 → recall 3/10 |
| Identity extra docs / wrong merges / duplicate copies caught | 8 / 0 / 0 of 2 |
| **FALSE_SAFE / FALSE_AUTO_LINKS / FALSE_MATCHED / WRONG_VENDORS_CREATED** | **0 / 0 / 0 / 0** (gate PASS) |

Cached flash-lite vision answers (recorded 8 Oct, D41; re-scored offline with `--vision-model gemini-3.5-flash-lite`; no call made): header 271/313 (86.6%), lines 80/123 (65.0%), handwritten refs 6/18, marketplace order 5/5. OCR×vision: agree-correct 211, **agree-wrong 1**, vision right where they disagree 19, OCR right where they disagree 5, vision-only correct 41, OCR-only correct 26. Tokens: 102,470 in / 81,104 out for 39 docs; max output 4,399.

The "~89% headers / ~84% lines on 8 scans" figure from `docs/accuracy-plan.md` was **not reproduced**. The current scan-only figures are in §5.2.

## 5. Per-stage error attribution

### 5.1 Method and population

- `work/diag-scan/scan_types.py` (local, counts only) labels each truth PDF from its pixels. It reports image coverage, text-layer chars, native DPI, a wide ±15° skew sweep (the app searches only ±5°) and local Tesseract OSD.
- `work/diag-scan/attribution.py` loads the **cached app outputs** (the exact upload-path results for this code) and scores every truth field with the bench's own rules. For each miss it checks whether the true value is in the document's page text, either exactly after normalisation or only after look-alike folding (O/0, I/1, S/5, B/8, Z/2, G/6), and assigns a stage:
  - **A** = OCR/image (value absent from text; *A-glyph* = present only after folding)
  - **B** = rules/table (value present, not or wrongly extracted)
  - **C** = party role (extracted value is a buyer entity, a label, or another party's GSTIN)
  - **D** = page split/identity (value only on pages outside the document)
  - **E** = normalisation (value present; extracted value equals truth after a format or look-alike change)

| Measurement (verified) | Result |
|---|---|
| Scan pages / digital pages in truth PDFs | 61 / 6 (the 6 digital pages are PO/GRN pages inside two bundle files) |
| Files by type | scan-clean 19, scan-tilted (0.5–1.1°) 9, scan-rotated 90° 3, mixed bundle 2. **Phone photo: none detected; perspective: none.** |
| Native DPI of scan pages | 200 (19 pages), 225 (6), 240 (36). None under 200. |
| Skew | <0.5°: 48 pages; 0.5–2°: 13; 2–5°: 0; **>5°: 0** (max 1.1°) |
| Scan pages with a scanner text layer (≥20 chars) | 17 |
| Final reader of all 39 PDF docs | Layout engine. 33 docs on scan pages (Tesseract), 6 PO/GRN docs on digital bundle pages (text layer). Core text parser and OCRmyPDF: 0. |
| Handwritten/stamped refs (truth `annotation_refs`) | 5 truth docs (S02, S03, S06, S09-INV, S10-INV) |

### 5.2 Header fields, scan-page documents (n = 33, 283 scored fields)

Accuracy **224/283 = 79.2%**. Digital-page docs: 18/30. All PDF docs: 242/313 = 77.3%.

| Field group | OK | A-absent | A-glyph | B | C | D | E |
|---|---:|---:|---:|---:|---:|---:|---:|
| Doc no | 25 | 2 | 0 | 2 | 0 | 0 | 4 |
| Date | 32 | 1 | 0 | 0 | 0 | 0 | 0 |
| Vendor name / GSTIN / buyer GSTIN | 64 | 2 | 2 | 18 | 0 | 0 | 0 |
| PO/GRN refs (printed) | 12 | 0 | 0 | 2 | 0 | 0 | 1 |
| Totals / tax | 91 | 2 | 0 | 23 | 0 | 0 | 0 |
| **Misses** | — | **7** | **2** | **45** | **0** | **0** | **5** |

Digital-page docs (6 PO/GRN documents) add: vendor name B 6, totals B 6.

### 5.3 Line cells, scan-page documents

A truth line is matched to an extracted line by amount, then qty/rate (bench rule). When a row is not extracted, each of its cells counts as a miss.

| Cell | OK | A-absent | A-glyph | B | E | Accuracy |
|---|---:|---:|---:|---:|---:|---:|
| Amount (= line recall) | 78 | 5 | 0 | 28 | 0 | 78/111 = 70.3% |
| Quantity | 65 | 0 | 0 | 37 | 0 | 65/102 |
| Rate | 63 | 14 | 0 | 29 | 0 | 63/106 |
| Description (ratio ≥ 0.85) | 53 | 0 | 3 | 52 | 0 | 53/108 |
| HSN | 49 | 2 | 0 | 20 | 4 | 49/75 |
| UOM | — | — | — | — | — | **UNKNOWN**: the truth set has no UOM |

Digital-page docs add 10 amount B, 12 qty B, 10 rate B, 2 amount A and 2 rate A misses (0 lines found on each of the 6 bundle PO/GRN documents). These are not scan problems.

### 5.4 By scan type (header fields + line cells, scan pages)

| Scan type | OK | A | B | E | Note |
|---|---:|---:|---:|---:|---|
| clean | 238 | 13 | 70 | 3 | |
| tilted 0.5–1.1° | 96 | 17 | 84 | 2 | Dominated by two complex multi-page layouts (S05, S08); deskew is within range |
| clean + handwritten | 152 | 0 | 48 | 4 | Includes the two bundle invoices (scan pages) |
| rotated 90° + handwritten | 41 | 0 | 1 | 0 | OSD handles rotation |
| tilted + handwritten (S09-INV, S10-INV) | 5 | 3 | 8 | 0 | Single-page bills: no table heading found, low-confidence OCR (25–33% of words under 60) |

Tilt does **not** explain the errors (verified). Every page is within the ±5° the app already corrects, and tilted pages still fail mostly at B.

### 5.5 Top 5 failure patterns (masked)

1. **Line rows not produced or misaligned (B).** Rows missing with the amount present in the text: 38 qty, 38 amount, 31 rate, 30 description across all PDF docs. A local layout probe (`work/diag-scan/debug_tables.py`, local Tesseract 5.4 as a proxy for the image's 5.5) shows three modes:
   - **No heading found** → 0 lines: S09-INV, S10-INV. `find_heading` needs description + amount + one of qty/rate/HSN (`table.py:356-393`).
   - **Heading columns misclassified**: S03 (marketplace format) got `[description, rate, discount, amount, rate, tax]`, with no qty column and two rate columns. S08 (4-page invoice) had qty classified `other` on p1 and duplicated `quantity`/`total_incl` bands on p3. S05 p1 had two `quantity` bands. Result: S08 gets 5/10 lines + 5 spurious on each of 3 copies; S03 gets 0/2 on each of 4 copies. Classifier: `table.py:146-185`; band edges: `:395-419`.
   - **Heading found but 0 items**: S10-PO (all columns correct, 0 items), and S05 p2 (continuation page). The row walk keeps a row only if a number lands in the amount band and the description has ≥3 alnum chars (`table.py:631-650`).
2. **Vendor name not extracted although printed (B):** 22 docs (16 on scan pages + 6 on digital pages). Names come only from a "for <Company>" signatory row with every word at confidence ≥60 (`extract.py:689-700`), or from a supplier-labelled block (`:705-774`). The unlabelled company name at the top of a document, or the name above the vendor GSTIN, is never used.
3. **Header totals missing or wrong although printed (B), all PDF docs:** missing 8 grand, 5 taxable, 4 CGST. Wrong: 4 SGST (truth `D.DD` → got `DD.DD`), 4 grand (`DDDDDD.DD` → `DDDDDD.DD`), 2 taxable, 2 CGST (`DDDDD.DD` → `DDDDDD.DD`). Labels are a fixed list (`extract.py:545-573`). A table summary row without CGST/SGST/IGST is fed to **both** taxable and grand (`:596-608`). Tax shown only per line (marketplace format, S03) is never summed into header tax. `accept_money` rejects any token containing `%`, which is a common OCR reading of "₹" (`extract.py:263-266`).
4. **Wrong document number although the right one is printed (E/B):**
   - S02 on 4 copies: truth `LLLLDDDD/DDD`, got `LLLLLDDD/DDD` (O for 0). Both strings occur in the OCR text, and the look-alike variant was chosen. It is flagged for review, so it is safe but costs a touch.
   - S04 on 2 copies: truth `LLL/DD-DD/DDDD`, got `DD`. A short number beside the label was taken.
   - S05 on 2 copies: the PO ref `LL/DD-DD/DD-DDD` was replaced by another printed reference shaped `LLLL.DDDDD-DDD`.
   - S08 (1 copy): the PO ref was read with a look-alike (E).
5. **OCR cannot see the value (A):**
   - doc_no absent on S10-INV and on one re-scan of S04; GRN date absent on S10-GRN; vendor GSTIN absent on S09-GRN and the S04 re-scan; grand/IGST absent on S09-INV. These are the low-contrast bills with handwriting.
   - GSTIN look-alikes beyond the 2-swap repair on 2 copies of S07 (A-glyph). This is the same document whose re-scans became duplicate identities (inferred link to the "duplicates 0/2" metric).
   - 14 S08 line rates absent from the text. Whether this is OCR loss or a print format the presence check does not model is UNKNOWN; it needs a visual check.
   - Handwritten PO/GRN refs: Tesseract does not read handwriting at all (these refs are not header truth; vision found 6/18).

### 5.6 Role and page split

- **C = 0** and **D = 0** on this truth set, **with buyer entities injected by the bench** (verified). No page-split misses (missed docs 0, spurious 0).
- With empty `buyer_entities`, role assignment falls back to labels and repetition (`extract.py:439-522`). If the buyer GSTIN appears once without a "Bill to/Buyer" label, and the vendor GSTIN is missing or unreadable, the buyer GSTIN becomes the vendor GSTIN **and** is marked `CONFIRMED` because its checksum is valid (`:509-519`). That is a wrong vendor plus a false-safe candidate. This exact failure was observed before AC-1 (vendor GSTIN = buyer's own on the no-GSTIN supplier). Not re-measurable offline: changing the hints invalidates the cache, and Docker was down. Status: inferred.

### 5.7 Link misses (7 of 10), attributed

| Missed link | Earliest failing stage | Evidence |
|---|---|---|
| S14-GRN→PO, S15-GRN→PO, S16-GRN→PO, S09-GRN→PO | **Matching (AC-9a regression)**, not extraction | Same extraction output before and after AC-9a (header 302/373 and lines 96/141 in both `before-ac9a-session-20261008.json` and `after-ac9a-20261008-final.json`). Recall fell from 7/10 to 3/10, and these 4 links appear only after the change (verified). The cause inside `mapping/links.py` is UNKNOWN. Inferred: the XML PO plus the bundle PO copy (an extra identity) and the new verified-anchor / same-vendor gate. |
| S09-INV→PO, S10-INV→PO | A: handwritten PO ref, plus missing totals/doc_no | PO ref only handwritten; Tesseract cannot read it. Amount signal missing (grand absent or B). |
| S10-GRN→PO | Vendor identity (supplier has no GSTIN; vendor `pending`) | `vendors.name_status` shows 2 docs pending; same-vendor gate. Inferred. |

## 6. Config and hard-code audit (scan path)

| Item | Finding | Tag |
|---|---|---|
| `buyer_entities` | Empty in seed (`seed_data.py:254`). Consumed by `party_hints_from_config` (`parties.py:293-311`) on both paths. The bench overrides it from truth (M1). The live workspace value is UNKNOWN. Nothing blocks parsing when it is empty. | verified / UNKNOWN |
| `synonyms` | Used by the core text parser only. **Not passed** to `extract_document` (`pipeline.py:161-168`), which uses fixed lists (`extract.py:138-216`, `:545-573`, `:705-715`; `table.py:295-337`). Editing synonyms in Settings has **no effect** on scans. | verified |
| `uom.conversions` | Schema and seed only (`configuration.py:162`, `seed_data.py:243`); no reader in core or app. | verified |
| `gst_slabs` `from`/`to` | Ignored; only `rate` is read (`parties.py:302-307`). The seed lists 5/12/18/28 and **replaces** the parser default (0.25/3/5/12/18/28/40), so a 40% slab is treated as invalid in the GST check. Line tax rates above 28% are discarded outright (`extract.py:638`, `<= 28`). | verified |
| Date locale | Layout path: numeric dates are **always day-first**; a month-first date that is invalid as day-first is silently dropped (`values.py:155-166`). The core path uses same-file majority, then day-first (`normalize/dates.py:21-80`). No config. | verified |
| Charge keywords | Passed through (`pipeline.py:164`). Fixed seed list (freight, packing, insurance, handling, round off, discount). Unlisted charges (e.g. "installation", "labour") become goods lines. | verified / inferred |
| UOM | Aliases only (PCS/EA/UNIT→NOS, KGS→KG, M→MTR). Line UOM is dropped when it contains a digit (`extract.py:978-980`). | verified |
| 20-page cap | `PDF_LAYOUT_MAX_PAGES=20` vs config `max_pages=100`. Files with 21–100 pages pass the core check but never reach the layout/OCR engine. | verified |
| Confidence constants | OCR 0.65, text layer 0.90, verified 0.98, review floor 0.70 (`extract.py:73-78`). `verification_score` has a hard-coded 0.70 floor (`pipeline.py:262`) that does not read `extraction_gate`. | verified |
| OCR languages | `eng` default; `hin` installed in the image but unused (`settings.py:45-49`, Dockerfile). | verified |
| Windows dev portability | `_tesseract` decodes stdout with the locale codec (`local_hybrid.py:176-183`, `text=True`, no encoding). On this Windows host one file produced 0 words (cp1252 decode error) in the local probe. The Linux image is inferred unaffected (UTF-8 locale). | verified (host) / inferred (image) |

## 7. Vision (H6) readiness. Nothing was enabled or called.

**How it is wired today (verified):**
- **Switches:** deployment gate `RB_AI_EXTERNAL_CALLS` + approval ref; `RB_VISION_READER` = `vision_gemini|off` (`extraction/vision.py:66-75`); workspace `ai_setting.vision_enabled` (default false, `db/metadata.py:910`), mode `ai_assisted|ai_first`, model and key, consent fingerprint (includes schema/prompt versions).
- **Eligibility** (`ai/vision.py:105-123`): PDF invoice/PO/GRN, ≤8 pages. In `ai_assisted` mode, only when `needs_verification`.
- **Limits** (`ai/limits.py:48-56`): 16,384 output tokens, 12 MB images, 1,200 tokens/image estimate, 120 s timeout. 200 calls/day, 10/min, 2 in flight. Monthly caps off (D-8 open).
- **Rendering** (`extraction/vision.py:46-52`): 300 DPI, long side capped at 2,048 px (about 175 DPI for A4), JPEG q90, `media_resolution="high"`. Masking covers bank, IFSC, PAN, phone, e-mail, UPI and signatures; GSTIN stays visible.
- **Request** (`ai/worker/gemini.py:73-90`, `ai/provider.py:33-42`): structured JSON schema `vision.schema.v3`, `store=False`, **`thinking_level` typed as `Literal["low"]` only**, no temperature/top-p/top-k sent (provider defaults). Google SDK only in the AI worker. The bench uses the same `GeminiProvider` (`scripts/accuracy_vision.py:264-271`).
- **Prompt** `vision.prompt.v3`: values exactly as printed, "Leave out anything you cannot read clearly" (`prompts.py:163`), handwriting only in `annotations`.

**Validators in `reconbench_core/extraction/vision.py:534-700` (verified):** page sent; box shape; box not in a masked area; GSTIN checksum and buyer-entity role; tax type vs state; ref shape (no address digits, marketplace or sales-order numbers as PO); single-valued conflicts; UOM length. Gaps:
- Totals arithmetic is checked **only when the OCR totals are self-consistent** (`:620-632`). Line qty×rate=amount is checked only against an OCR line with the same amount (`:660-669`). So on exactly the scans where OCR fails, vision arithmetic is **not** checked on its own.
- No check that line amounts sum to the header taxable value.
- No explicit "unreadable" signal. Omission cannot be told apart from absence (no `null`-on-unreadable contract).
- No date-window check inside the vision checks (D42's window lives in consensus).
- The Tesseract cross-check exists only in consensus (`ai/consensus.py`), as a local crop re-read of stored vision answers.

**How the output is used (verified):**
- A checked answer becomes an unverified `Document` (source `ai`, below the review gate). It is shown as a **suggestion**; a person applies values through the normal correction save (`ai/vision.py:1-10`).
- Consensus (AC-6, `ai/consensus.py:802-860`) can only append a **same-value** version that changes verification state for opted-in field types (`doc_date`, `doc_no`, `gstin`, `line_amount`, `reference`; `ai/consent.py:25`). It never replaces an OCR value with a vision value.

**What would make H6 the primary reader on scans (inferred design, detailed in the fix plan WI-13):**
- A pure core merge (`ocr_doc + checked vision reading → candidate doc`), run by the general worker after the AI worker stores the answer (no SDK import outside the AI worker).
- Vision-sourced values capped below `extraction_gate`.
- Standalone validators (arithmetic, Σ lines, GSTIN/state/buyer, date window, ref shape).
- Verification only through the D42 rule: two independent readers + validators + crop re-read.
- A workspace switch, off by default.

## 8. Test gaps for scans (verified by search of `backend/tests`)

- **No unit tests** for `estimate_skew`, `remove_rules`, `is_scanned`, `find_heading`, `_supplier_block_name`, `_signatory_name` or `_prefer_layout`.
- **`backend/tests/parsers` is not run in CI at all** (verified). `make test` runs only `tests/unit`, `tests/property` and `tests/scenarios` (`Makefile`, `test:` target). CI calls `make test` plus named integration files (`.github/workflows/ci.yml:52`, `:84`, `:307-327`). The PDF/table parser tests, including `extract_tables` coverage in `tests/parsers/test_phase4_pdf.py`, therefore never gate a merge.
- **One** Tesseract-dependent test (`tests/parsers/test_phase4_pdf.py:484`). It is in the un-run `parsers` folder, it is skipped when `tesseract` is missing, and the CI workflow installs no Tesseract.
- No synthetic fixtures for: rotated or tilted (>5°) pages, low DPI, scanner text layers (poor), tiled images, >20 pages, multi-row GST headings, heading-less tables, continuation pages, marketplace line-tax tables, "TOTEL"-style typos, unlabelled vendor blocks, look-alike document numbers, handwriting.
- The golden set has 0 cases (`golden self-check … 0 cases (unverified_no_cases)`, D57/D58). Hard-zero metrics are guarded only by the owner-local bench.
- No test that the bench and the upload path build the same `ParserHints`.

## 9. Unknowns and what is needed

| UNKNOWN | Needed to resolve |
|---|---|
| Fresh-read benchmark in the worker image (only a cache replay was possible) | Docker Desktop running; `docker compose build worker`; `MSYS_NO_PATHCONV=1 make accuracy-bench` |
| Whether the OCRmyPDF fallback works in the image | `docker run --rm reconbench-worker sh -c "which gs; /app/backend/.venv/bin/ocrmypdf --version"` |
| Live workspace `buyer_entities`, `RB_EINVOICE_PUBLIC_KEY_FILE`, worker concurrency | Read-only DB/env check by the owner |
| Effect of empty `buyer_entities` on C errors | Bench run in the image with `buyer_entities` cleared (fix plan WI-1) |
| Cause of the AC-9a link regression | Per-pair candidate trace in `mapping/links.py` (fix plan WI-10) |
| Why 14 rates in S08 are absent from the text | Owner visual check of the masked crop |
| Gemini 3.5 Flash quality, latency and cost | The gated experiment (fix plan WI-12), only with owner approval and a paid key |

## Appendix: reproducible commands (local, counts only)

```
# cache replay of the bench (no OCR/provider)
backend\.venv\Scripts\python.exe work\diag-scan\replay_bench.py work\accuracy-reports\after-ac9a-grn-anchor-final-20261009.json --out work\diag-scan\replay-baseline.json --workers 1
# same, scoring the cached flash-lite vision answers
... replay_bench.py <same report> --out work\diag-scan\replay-vision-lite.json --workers 1 --vision-model gemini-3.5-flash-lite
# scan-type labels (pixels; writes work/diag-scan/scan_types.json)
backend\.venv\Scripts\python.exe work\diag-scan\scan_types.py
# per-stage attribution (needs scan_types.json)
backend\.venv\Scripts\python.exe work\diag-scan\attribution.py
# table probe (local Tesseract proxy)
backend\.venv\Scripts\python.exe work\diag-scan\debug_tables.py "INV-03,INV-08,INV-07,INV-05,INV-T1,INV-T2,PO-T1,INV-02"
```

**Measurement limits.** The presence check is string matching on reconstructed page text. A value can match another occurrence, so B may be slightly over-counted against A. Of the 242 correct header values, 2 were "absent" and 10 "folded only", so the absent-detection error is at least 0.8%. Line cells of a missing row are not independent. The truth set has 10 independent invoice documents and only 2 complete PO+GRN+invoice sets, so one document moves a rate by several points.
