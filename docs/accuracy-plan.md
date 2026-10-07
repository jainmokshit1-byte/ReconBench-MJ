# Accuracy overhaul plan: extraction → mapping → reconciliation

Status: written 6 Oct 2026. **AC-0 done** (benchmark + baseline, D32); **AC-1 done** (deterministic guards, D33); **AC-2 done** (e-invoice QR, D34); **AC-3 done** (page split + classify, D35); **AC-4 done** (vendor hygiene, D36); **AC-5 done** (vision reader, suggestions only, D37; the owner-run probe and recorded bench answers are still to come); AC-6 is next. Run one phase per session, in order, with the benchmark (AC-0) after every phase.
Real sample values and ground truth are **not** kept in this repo. They live in the owner's local memory directory and in `work/` (git-ignored).

---

## 1. What "100% accurate" means here

No OCR or AI reader is 100% correct on arbitrary scans, so the product commits to this instead:

| Hard guarantee (must stay 0) | Measured as |
|---|---|
| **False-safe document**: a document marked verified that has a wrong critical value | per benchmark run |
| **False auto-link**: an automatic (non-suggested) link to the wrong PO or GRN | per benchmark run |
| **False Matched**: a reconciliation case marked Matched that should not be | per benchmark run |
| **Wrong auto-created vendor**: a vendor created from the buyer's own GSTIN, a label, or an unverified value | per benchmark run |

Everything else is a **throughput** target. The goal is the most documents verified automatically and the fewest fields a person has to touch:

| Target (proposed; owner may change) | Today (from the 6 Oct real-sample test) |
|---|---|
| Header fields correct before human touch: ≥ 98% | about 89% (D18 measurement on 8 scans; worse on the new sets) |
| Line items correct before human touch: ≥ 95% | about 84% |
| Documents fully auto-verified: ≥ 90% digital/QR, ≥ 60% scans | low; most scans need review |
| Fields a person must touch per document: ≤ 2 on average | not measured |
| Invoice → PO/GRN link found (linked or suggested) when the PO is in the data: ≥ 95% | 0 links in the real test (no complete set) |

A field reaches matching only when it is **verified** (by a deterministic check, an independent second reading, a signed source, or a person). This keeps D17/D18, and the plan extends what counts as verified.

---

## 2. Root causes from the real-sample test

| # | Symptom in the real test | Root cause | Fixed in |
|---|---|---|---|
| R1 | Handwritten PO/GRN numbers never read; ₹ glyph read as 1/2; a rotated page misread; garbled multi-column names | Tesseract reads text only; it cannot read handwriting and loses layout | AC-5 vision, AC-1 glyph rule |
| R2 | The buyer's own GSTIN extracted as the vendor; the label "(Bill from)" used as the vendor name; the AI picked the buyer as the vendor | No party-role model; `buyer_entities` is used only by the reconciliation engine, not by extraction, AI validation or vendor creation | AC-1, AC-4 |
| R3 | O/0 and I/1 in document numbers; the same scan read differently on each re-scan | No cross-reader agreement; no vendor number-pattern knowledge | AC-6, AC-10 |
| R4 | Totals wrong on non-standard layouts (typo "TOTEL" row, line amount taken as CGST) | Heuristic summary detection; only one reader | AC-1 validators, AC-6 consensus |
| R5 | A 6-page bundle (PO+GRN+invoice) treated as one invoice → wrong link and a false duplicate | One file = one document in the layout path; no page classification | AC-3 |
| R6 | Tally PO/GRN PDFs: number, date, totals and "Reference No." not captured; no AI help | Tally labels not wired; H1 is invoice-only (D13) | AC-1, AC-5 |
| R7 | The AI invented IGST, kept GSTIN typos, and used the buyer as the vendor | AI output is checked only for type and grounding, not against business rules | AC-1 validators, AC-6 |
| R8 | Correct AI values that the OCR text misread are rejected | H1 grounding requires the quote to appear in the OCR text | AC-5/AC-6 grounding by bounding-box crop |
| R9 | Re-scans create new documents; real duplicates are missed | The natural key and duplicate rules use unstable OCR fields (vendor GSTIN/name, document number) | AC-8 |
| R10 | Vendor master polluted (label names, GSTIN-only names, the buyer as a vendor) | `_ensure_gstin_anchors` creates vendors from any checksum-valid GSTIN, even unverified ones | AC-4 |
| R11 | No PO number means no link | The content step needs the same `vendor_id` and good lines; there is no invoice↔GRN anchor, and handwritten refs, challan and supplier-invoice refs are unused | AC-9 |
| R12 | XML lines show the category ("CABINET") instead of the item; GRN serials dropped | XML field mapping | AC-1 |

---

## 3. Target extraction architecture

```
file ─► page split + classify (AC-3) ─► per-document page set
          │
          ├─ Reader A: local layout/OCR (existing local_hybrid + legacy text layer)
          ├─ Reader B: e-invoice signed QR / IRN (AC-2)              ← exact values
          ├─ Reader C: vision model on page images (AC-5, async, consented)
          └─ Reader D: person (verify screen, AC-7)
                 │
                 ▼
      validators (deterministic, authoritative)   ── AC-1
                 │
                 ▼
      consensus + trust level per field (AC-6) ── verify loop: targeted re-read of failing regions (max 2)
                 │
        all critical fields L0/L1 ──► verified version ──► vendor, identity, matching
        otherwise ──► verify queue (only the uncertain fields are shown)
```

### 3.1 Trust levels (extends D18)

| Level | Meaning | Examples | Counts as verified |
|---|---|---|---|
| **L0 certain** | Value from a digital or signed source, or entered by a person | XML, text-layer value that also passes its check, signed e-invoice QR, human save | yes |
| **L1 confirmed** | Two **independent** readers agree exactly **and** format checks pass, **or** a deterministic relation holds (D18 "confirmed/corrected") | vision value = Tesseract re-read of the same crop; qty × rate = amount; GSTIN checksum valid and not a buyer entity; amount in words = grand total | yes |
| **L2 single** | One reader, passes format checks | only the vision model read the date | no; shown prefilled |
| **L3 conflict** | Readers disagree and no check settles it | OCR "PAPLO826", vision "PAPL0826" | no; person picks from the options |
| **Rejected** | Fails a hard rule | supplier GSTIN = buyer entity; invented IGST on an intra-state invoice; invalid checksum | never used; shown as an error |

Two rules keep this honest:
- **Two answers from the same model never confirm each other** (their errors are correlated). Agreement counts only between different readers (OCR vs vision vs QR), plus a check.
- The D18 look-alike guard stays for the OCR reader. A look-alike field can reach L1 only when an independent reader (vision or QR) agrees and the vendor's known number pattern accepts it (AC-10).

### 3.2 Deterministic validators (AC-1; the core stays pure)

- **Arithmetic tree**, as today (D18): line qty × rate, line sums to taxable, taxable + tax = grand (round-off), amount in words.
- **GST structure**: supplier state (GSTIN digits 1–2) vs place of supply/buyer state. Intra-state means CGST = SGST and IGST = 0; inter-state means IGST only. The tax-to-taxable ratio must be a configured slab (`_STANDARD_RATES`) or a mix of slabs that the lines explain.
- **Parties**: supplier GSTIN ∉ `buyer_entities`; the buyer GSTIN should be in `buyer_entities` (warning otherwise); PAN consistency; checksum.
- **Names**: reject label text ("Bill from", "Supplier", "Consignee", "Buyer", "M/s" alone), GSTIN-shaped names and line text as vendor names.
- **Dates**: not in the future, within a sane window of the upload, invoice date ≥ PO date (warning, not reject).
- **References**: a PO/GRN reference must look like a document number. Address digits ("501"), seller sales-order numbers and marketplace order numbers are typed separately, never as `po_refs`.

### 3.3 Verify loop (AC-6)

When a check fails, re-read **only the failing region** instead of sending the document to a person straight away:
- Arithmetic fails on a line: re-OCR that row at 2×/600 DPI, then (if consented) ask the vision reader for that crop only.
- Total vs lines mismatch: re-read the summary block.
- GSTIN checksum fails: re-read the GSTIN crop with a character whitelist.

Stop after 2 rounds; anything left goes to the verify screen. Every round is logged as evidence.

---

## 4. Matching without a PO number (AC-9)

The current ladder (`reconbench_core/mapping/links.py`) has five steps: labelled ref → reverse text lookup → via GRN → manual → content. The content step only considers POs with the **same `vendor_id`**, needs aligned lines, and always ends as a suggestion (confidence capped at 0.90). So matching without a PO number needs three things: (a) a correct vendor (AC-4), (b) correct lines (AC-5/6), and (c) more anchors and a better scorer.

**New anchors** (each one is evidence, not truth):
1. **Handwritten PO/GRN numbers** (vision, stored separately as `annotation_refs`). They are a hint only and must exist in the data and agree with the vendor and amount.
2. **GRN → invoice**: the GRN "Reference No." / "Supplier Invoice No." / challan number equals the invoice number (after look-alike folding) for the same vendor. This gives a direct invoice↔GRN edge, and the PO follows from GRN→PO (which already works for XML).
3. **Challan / delivery-note number** on the invoice equals the GRN challan.
4. **Marketplace order number**, stored as `marketplace_order_no` and matched against PO narration or notes when present.

**Scoring**: one explainable score per invoice × candidate (PO or GRN):

| Signal | Notes |
|---|---|
| Vendor | Verified GSTIN equal (strong); same PAN; alias; none |
| Amount | Invoice grand/taxable vs GRN value or PO open balance, within tolerance |
| Date window | PO date ≤ GRN date ≤ invoice date + N days (configurable) |
| Lines | Existing `align_lines` (description, code, HSN, UoM) plus qty equal to GRN accepted qty and rate equal to PO rate |
| Strong tokens | Model and part numbers shared between invoice and PO lines |
| Anchors | Handwritten or challan or GRN reference matches |

**Assignment**: instead of greedy per-document reservation, match invoices against GRN/PO capacity for the whole period. Use a deterministic maximum-weight assignment with quantity/value capacities. When the top two candidates are within the lead margin, the result is "ambiguous" and goes to a person with both options.

**Policy** (owner decision D-6): automatic links come only from verified deterministic anchors (as today). A multi-signal match is a **suggestion with a one-click confirm** until the benchmark shows zero false links over an agreed number of documents. After that, an auto-link rule for "unique + vendor verified + exact amount + qty equal" can be switched on per workspace.

---

## 5. Vendor master hygiene (AC-4)

- `buyer_entities` (owner's own GSTINs and names) becomes a **prerequisite setting**. It is passed into extraction hints, AI validation, and vendor resolution.
- Vendors are created **only from a verified supplier identity**: an L0/L1 GSTIN that is not a buyer entity. A document without one gets the state **vendor pending**, with ranked suggestions (GSTIN, PAN, alias, fuzzy name). No new vendor is created.
- Display-name quality: never a label, a GSTIN, or line text. The name comes from QR/verified text or a person; otherwise the vendor shows as "(name pending)".
- A supplier without a GSTIN (unregistered, e.g. a small service vendor) is created only by a person or from a confirmed PO party, keyed by verified name plus PO.
- **Marketplace rule** (owner decision D-5): on marketplace invoices, the legal supplier is the **seller GSTIN** printed on the invoice, not the marketplace. If POs are raised to the seller (as in the samples), no alias is needed. If some POs are raised to the marketplace itself, the marketplace order number becomes the anchor.
- A one-time **cleanup**: list junk vendors (label names, buyer GSTIN, GSTIN-as-name), merge or delete them with an audit trail, then re-run resolution and mapping. There is a merge/assign UI on the Mapping page (G-13 endpoints).

---

## 6. Identity, re-scans, duplicates, bundles

- **Bundles (AC-3)**: classify every page (invoice, PO, GRN, e-way bill, annexure/terms, other) by title keywords plus layout, and by vision when consented. Split pages into documents at type changes or document-number changes, using "Page x of y" continuity. A file uploaded to the invoice zone that contains PO/GRN pages gets a "split this file?" prompt (default: suggest split, never silently merge). An e-way bill for another document stays attached as evidence only (existing `ATTACHED_EWAY_BILL_OTHER_DOCUMENT`).
- **Document identity (AC-8)**: the natural key uses **verified** fields only. Before verification, identity comes from the file SHA plus a **page image perceptual hash** plus a **content fingerprint** (grand total, date, multiset of line amounts, supplier PAN if verified). A re-scan whose fingerprint matches an existing document becomes a new version of that document, not a new document. A conflict check makes sure it never overwrites a human-verified version silently (the open "rescan overwrites clean version" policy).
- **Duplicates**: add fingerprint-based suspicion that works even when the vendor or number was misread: same total ± ₹1, date within N days, and the document number equal after look-alike folding, or the same line-amount multiset. Duplicates go to `DUP_SUSPECTED`, so a person decides, and they keep consuming balance (OQ-5).

---

## 7. AI guardrails and privacy

- **Vision reader contract**: the model returns each field with `page`, `bbox`, `as_printed` text (no normalisation by the model) and `role` for parties (supplier / buyer / ship-to). Handwritten marks go in a separate `annotations[]` list, never into printed fields. `null` means not visible. The model never computes totals.
- **Grounding** moves from "quote appears in OCR text" (H1) to: the bbox lies on the page, a Tesseract re-read of that crop agrees (gives L1) or does not (gives L2/L3), and all validators pass.
- **Hard rejects**: buyer GSTIN as supplier, invalid checksum, tax type against the state rule, numbers that break arithmetic that the OCR numbers satisfy, values on a page that does not exist.
- **Determinism and cost**: responses are cached by `(page image hash, schema version, model)`, so re-runs and the benchmark are stable and free. A per-document page cap and a monthly budget are owner-set (D-8).
- **Privacy**: before sending a page image, black out regions that OCR word boxes identify as bank account, IFSC, phone, e-mail or signatures (reusing the H1 redaction categories on word positions). GSTINs stay visible. This is best-effort, and the residual risk is stated in the consent text. Only the paid API tier with `store=False` is used, and calls go through the existing gate, consent, limits and ledger.
- The PDF sandbox has no network access, so vision runs **asynchronously in the ai-worker**: parse locally → version v1 (unverified) → vision job → consensus job → version v2 (verified, or still in the verify queue). This is the same queue pattern as H1–H4.
- **Built in AC-5 (D37):** hook `h6_vision`, chosen per workspace in the AI consent flow (it replaces H1 for that workspace); pages rendered and masked in the PDF sandbox by the general worker and handed to the AI worker through the transient `document_page_image` bytes; images only (no OCR text) so the reading stays independent; one box per field and per line row; every value checked by `reconbench_core/extraction/vision.py` with the hard rejects above; suggestions only. The crop re-read and L1/L2/L3 levels are AC-6.
- **AC-5b follow-up (D38):** first owner bench on `gemini-3.5-flash-lite` read headers well (141/166, no OCR × vision agreement on a wrong value) but almost no line rows (1/77). `vision.schema.v2` makes the line description required and prompt v2 asks for each row's amount and quantity as printed; the bench and probe now show why a call failed (Google's error text, or status + tokens + row/loop counts, never values) and score handwritten-reference recall (a truth-loader bug had kept it at 0/0). The output cap stays 16,384 until the next run shows whether answers are cut by it.

---

## 8. Benchmark harness (AC-0, used by every phase)

- **Truth v2 format** (local only, `work/accuracy-truth/*.json`): a list of **sets**, each with documents (file, page range, type, header fields, lines) plus expected vendor, expected links (invoice→PO/GRN, GRN→PO), expected duplicates and expected recon status. It extends `work/extraction-truth/invoices.json`.
- `scripts/accuracy_bench.py` builds on `scripts/measure_extraction.py`. It runs extraction offline (`parse_pdf_isolated`), then vendor resolution, linking (`resolve_links`), duplicates and the recon engine on the pure core, with no database needed. Reader C uses only the local response cache unless the owner passes `--allow-provider`.
- **Report**: per field and doc type (correct / wrong / missing / flagged), false-safe count, auto-verified rate, fields-to-touch per document, link precision and recall, vendor correctness, duplicate recall, recon status agreement. It prints summary metrics only, never document text.
- **Gate**: any non-zero hard metric (§1) fails the phase. A decision-log entry records the before/after numbers (no real values).
- **CI**: synthetic only. Add rendered "scan-like" synthetic invoices (rotation, noise, look-alikes, bundles, no-PO sets) and recorded fake vision responses, so the logic is regression-tested without real documents or provider calls.
- **Truth set needed from the owner (D-7)**: there is currently **no complete PO + GRN + invoice set**. At least 15–20 complete sets are needed, including sets without a PO number, handwritten refs, multi-page invoices, a bundle and a re-scan pair. To reduce effort, the bench can prefill truth from the extraction output and the owner only corrects it.

### 8.1 Running the bench (built in AC-0)

- Samples go in `work/accuracy-samples/` and the truth file in `work/accuracy-truth/truth.json` (both git-ignored).
- `make accuracy-bench` runs `scripts/accuracy_bench.py` in the worker image (same Tesseract/OCRmyPDF as the app) with the working tree mounted and `--network none`. Extra flags go in `BENCH_ARGS`, for example `BENCH_ARGS="--details"` (per-document outcome labels), `--refresh` (ignore the reader cache), `--prefill work/accuracy-truth/draft.json` (draft truth for files that have none yet; the owner corrects it), `--out work/accuracy-reports/<name>.json`.
- Reader outputs are cached in `work/accuracy-cache/` by file bytes, parser hints, extraction code, engine version and OCR tool versions. A change to extraction code re-reads the files; a change to vendor, mapping or reconciliation code re-uses the cache.
- One run = one fresh workspace in truth-file order: real parsers → `document_natural_key` identity (a re-upload with the same key is a new version) → vendor anchors as `_ensure_gstin_anchors` does them, then `resolve_vendors` → `resolve_links` (GRNs first) → `evaluate_components` with the app's config and the truth file's `buyer_entities`.
- Exit code 1 when any §1 hard metric is non-zero. The report hash is stable across re-runs.

Truth v2 rules: a field key that is absent is **unknown** and not scored; `null` means **known to be absent** (an extracted value then counts as wrong). `po_refs` holds printed PO references only; marketplace order numbers and handwritten refs are kept as `marketplace_order_no` / `annotation_refs` (not scored until AC-5/AC-9). A file can be a source of several truth documents (a bundle), and a truth document can have several source files (copies and re-scans).

Metric definitions:

| Metric | Definition |
|---|---|
| False-safe document | Not flagged for verification, and a critical field is wrong or unexpected (invoice: number, date, supplier GSTIN, grand total, PO refs; PO: number, date, supplier GSTIN, grand total; GRN: number, date, supplier GSTIN, PO refs), or number/date/grand total is missing, or lines are incomplete or spurious |
| Trusted-but-wrong field | A wrong value whose confidence is at the verified level (≥ 0.98); diagnostic, feeds AC-1/AC-6 |
| Fields to touch / to confirm | Touch: wrong, unexpected or missing header values plus wrong/missing line cells and spurious lines. Confirm: correct values below the verified level |
| Wrong auto-created vendor | A vendor whose GSTIN is a buyer entity or not any truth supplier's, or whose name is a label, GSTIN-shaped, or < 0.80 similar to the truth supplier name |
| False auto-link | An automatic or confirmed link whose (source, PO) truth pair is not expected; suggestions are counted separately |
| False Matched | A Matched / Matched-with-flags case whose truth status is different, that belongs to a duplicate copy, or whose invoice has a wrong critical field |
| Missed / spurious documents | Truth documents in a file that no parsed document was assigned to (for example the PO and GRN pages of a bundle) / parsed documents with no truth document |
| Extra documents, duplicates | Extra = system documents beyond one per truth document (re-scans that did not collapse into versions); each extra invoice copy should be flagged `duplicate` of the same truth invoice |

---

## 9. Phases (one per session, in this order)

Each phase ends with: unit, property, scenario and golden suites green; the benchmark run; a decision-log entry; and a memory update. Effort: **high** for AC-5, AC-6 and AC-9; medium or high for the rest.

| Phase | Scope | Main files | Exit criteria |
|---|---|---|---|
| **AC-0** Benchmark + baseline | Truth v2 format, `accuracy_bench.py`, reader cache, baseline report. Owner sets `buyer_entities` (D-4). | `scripts/`, `work/accuracy-truth/` | Baseline numbers recorded; the bench re-runs deterministically |
| **AC-1** Deterministic guards | Party roles and the buyer-entity guard in extraction; label-as-value reject; ₹-glyph rule; Tally PO/GRN labels (Voucher No., Dated, Reference No. & Date, Supplier (Bill from), Order No.); GRN reference capture; GST state/slab validator; amount in words everywhere; XML item name (not category) + GRN serials; `buyer_entities` in `ParserHints` | `reconbench_core/extraction/{extract,values,verify}.py`, `parsers/{pdf,xml}.py`, `ingestion._parser_hints`, `vendor_resolution._ensure_gstin_anchors` | Header accuracy up; false-safe 0; buyer GSTIN never a vendor |
| **AC-2** e-invoice QR | Decode the signed QR from page images (seller/buyer GSTIN, number, date, total, IRN, item count, main HSN). Verify the signature if the NIC public key is configured; otherwise treat the result as L1 together with the OCR reading. Dependency review (G-24) for the decoder library | new `reconbench_core/extraction/qr.py` (pure payload parsing), `extraction/local_hybrid.py` (image decode), `pipeline.py` | Invoices with a QR get L0/L1 header fields |
| **AC-3** Page split + classify | Page types and document boundaries; multi-document `ParseResult` from the layout path; split prompt for wrong-zone files | new `reconbench_core/extraction/pages.py`, `pipeline.layout_parse`, `ingestion` type-mismatch flow, upload UI | A bundle gives correct separate documents; no document mixes pages |
| **AC-4** Vendor hygiene | Verified-only vendor creation; vendor-pending state; name quality; marketplace rule (D-5); cleanup script + merge/assign UI | `vendor_resolution.py`, `reconbench_core/mapping/vendor.py`, `api/mapping.py`, `MappingPage.tsx` | 0 junk vendors after re-ingesting the real set |
| **AC-5** Vision reader | Engine adapter `vision_gemini` behind the D17 port (async in the ai-worker); image input to Gemini (first an owner-run probe for the SDK image form and cost per page, like D31); schema with bbox, as-printed text, roles and annotations; image masking; invoice + PO + GRN (D-3); consent text update | `extraction/engine.py`, `ai/contracts.py`, `ai/worker/gemini.py`, `ai/redaction.py`, new hook, `ai/consent.py`, `ai/limits.py` | Benchmark with cached responses; vision alone measured per field |
| **AC-6** Consensus + verify loop | Pure consensus module, trust levels L0–L3, crop re-read cross-check, targeted re-read loop, new verified version, per-field provenance (migration on evidence), D18 → D18b | new `reconbench_core/extraction/consensus.py`, `pipeline.py`, `ingestion._persist_parse`, `db/metadata.py` + migration | Auto-verified rate up, false-safe 0, H1 "suggestion only" replaced for L0/L1 fields (D-2) |
| **AC-7** Verify screen v2 | Show only unverified fields, with the source crop beside each value; A/B pick for conflicts; keyboard-first flow; split confirm; vendor-pending assign; local export of corrections into truth | `IngestionPreview.tsx`, `PdfPreview.tsx`, `api/review.py`, Playwright spec | Owner can verify a document in under 30 s; corrections reach the bench |
| **AC-8** Identity + duplicates | Verified-only natural key; perceptual page hash + content fingerprint; re-scan = new version with a conflict guard; fingerprint duplicate suspicion | `ingestion._upsert_document`, `recon/engine._duplicate_map`, migration | Re-scans make 0 new documents; real duplicates flagged |
| **AC-9** Matching v2 | New anchors (handwritten, GRN↔invoice no, challan, marketplace order); multi-signal scorer with explanation; invoice↔GRN edge; period-wide assignment; one-click confirm (D-6) | `reconbench_core/mapping/links.py`, `align/`, `mapping_service.py`, `MappingPage.tsx` | Link recall ≥ 95%, false auto-links 0 on the benchmark |
| **AC-10** Learning loop | Vendor profile from verified documents: number pattern, where the PO ref sits, tax type, usual items. Used as validators and extraction hints; aliases learned from confirmations | new `vendor_profile` table + core module | Repeat-vendor documents need fewer touches |
| **AC-11** Real-set E2E + exit gate | Full upload → verify → map → recon on the truth sets; then the Phase 4 exit gate | (none) | §1 hard metrics 0; targets met or the gap reported |

AC-0 to AC-4 are **local only** and need no AI approval, so they can start immediately. AC-5 onward needs D-1/D-2/D-3 (D-1 and D-3 decided 7 Oct 2026; D-2 is AC-6's).

Optional AC-12, **GSTR-2B cross-check** (D-10): import the monthly GSTR-2B JSON downloaded from the GST portal (no external call). It lists supplier GSTIN, invoice number, date and values filed by the supplier, so a matching row turns those header fields into L0 and also exposes invoices that were never received. It arrives late (around the 14th of the next month), so it serves the month-end check, not day-one entry.

---

## 10. Owner decisions needed

| ID | Decision | Needed before | Recommendation |
|---|---|---|---|
| D-1 | Standing approval to send masked **page images** of real documents to Gemini (paid tier, `store=False`), or use a local vision model instead | AC-5 | **Decided 7 Oct 2026:** yes, Gemini with masking; the key comes through the existing AI settings flow; the port stays open for a local model (D37) |
| D-2 | AI-read values may become **verified without a person** when an independent reader or a deterministic check confirms them (changes D11/D21 "suggestion only") | AC-6 | **Decided 8 Oct 2026 (D42):** yes per field, only with two independent readers agreeing, all deterministic validators passing and the crop re-read agreeing; never for handwritten marks, new vendor names or corrected fields; switched on per field type only after the bench shows 0 false-safe, behind a workspace setting (off by default) |
| D-3 | AI extraction for **PO/GRN PDFs** (closes D13) | AC-5 | **Decided 7 Oct 2026:** yes for the vision reader (read-only suggestions until AC-7 adds PO/GRN corrections); XML stays the preferred source (D37) |
| D-4 | `buyer_entities`: all own GSTINs and legal names | AC-1 | Set it in Settings before AC-1 |
| D-5 | Marketplace vendor rule (seller GSTIN = vendor?) | AC-4 | **Decided 7 Oct 2026:** the seller GSTIN printed on the invoice is the vendor (D36) |
| D-6 | Non-PO matching: suggestion-only or automatic after proof | AC-9 | Suggestion-only first |
| D-7 | Truth set: at least 15–20 complete PO+GRN+invoice sets, labelled | AC-0 (can grow later) | Bench prefills; owner corrects |
| D-8 | Vision cost cap (pages per document, monthly budget, model) | AC-5 | Open: probe measured about 1,100 input tokens per page (`image_tokens_estimate` now 1,200, D38); the proposal follows the AC-5b bench run (output tokens per document) and the owner's price and monthly volume. The limits file already takes `h6_vision.max_pages` and `per_month_calls/tokens` (D37) |
| D-9 | Bundles: auto-split or ask | AC-3 | **Decided 7 Oct 2026:** suggest the split, person confirms (D35) |
| D-10 | Use GSTR-2B as a verifier (AC-12) | optional | Yes, if 2B JSON is available |

---

## 11. Non-goals and risks

- Reading arbitrary handwriting as **verified** is a non-goal. Handwritten refs remain hints that must agree with data.
- Vision models can be confidently wrong. The validators, independence rule and benchmark gate are the defence, not the model's own confidence.
- A small truth set makes the percentages noisy. Report counts as well as rates, and grow the set with every verify-screen correction.
- Open items found earlier that are outside this plan but should not be lost: the D22 `recon_case_aged_ck` violation (aged + GRN_PENDING), the `ai_request.ai_cache_id` NULL linkage seen in the AI-first test, and payment terms not wired to the due date (D27).
