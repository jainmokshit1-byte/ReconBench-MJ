# Fix plan: scanned-document extraction accuracy

Date: 9 Oct 2026. Companion to `docs/diagnosis-scan-extraction.md` (evidence; cited as **Dx §n**). This plan is self-contained: an implementer needs only this file, the repository, and the owner's local `work/` folder.

---

## 0. Read first: rules for whoever implements this

### 0.1 Invariants (must hold after every work item)

1. AI never decides status, payable amount, link or PO capacity. AI output is a suggestion or one reader among several.
2. `backend/reconbench_core` stays pure (`scripts/check_core_purity.py`, import-linter in `cd backend && uv run lint-imports`).
3. The Google SDK is imported only in the AI worker (`reconbench_app/ai/worker/`); import-linter contract.
4. The PDF sandbox has no network access. All PDF/image work runs in `pdf_sandbox.py` child processes.
5. Money is `Decimal`, never `float`.
6. Content-based links stay suggestion-only (decision D-6). Only verified deterministic anchors may auto-link.
7. Corrections are versioned and audited. `document_version` is append-only; a re-parse never silently overwrites a manual correction.
8. Hard-zero metrics stay zero on every bench run: `FALSE_SAFE`, `FALSE_AUTO_LINKS`, `FALSE_MATCHED`, `WRONG_VENDORS_CREATED`. The bench exits 1 if any is non-zero.

### 0.2 Privacy and safety

- Real samples and truth live only in `work/` (git-ignored). Never copy them into the repo, tests, fixtures, docs or commit messages.
- Scripts and test output print counts, booleans and masked patterns only (`L` letter, `D` digit).
- Do not call Gemini or any external service, and do not set `RB_AI_EXTERNAL_CALLS=enabled`, except in WI-12 after the owner's written approval.
- Do not commit, push or open PRs (owner rule). Leave changes in the working tree.
- Treat `docs/ReconBench_*.md` as read-only.

### 0.3 Environment notes (Windows host)

- Unit tests: `cd backend; .venv\Scripts\python.exe -m pytest tests/unit tests/parsers tests/property tests/scenarios -q` (PowerShell). Do not collect `tests/unit` and `tests/integration` in one run (duplicate basenames).
- Static checks: `make check-python` (ruff, `mypy --strict`, lint-imports), `uv run --project backend python scripts/check_core_purity.py`, `uv run --project backend python scripts/check_rules_hash.py`, `uv run --project backend python scripts/check_engine_version.py`.
- Bench (fresh reads in the worker image, same Tesseract 5.5.0 as production), from Git Bash at the repo root, Docker Desktop running:
  ```
  docker compose build worker
  MSYS_NO_PATHCONV=1 make accuracy-bench BENCH_ARGS="--workers 1 --out work/accuracy-reports/<name>.json"
  ```
- Attribution (counts only): until WI-1 lands, `backend\.venv\Scripts\python.exe work\diag-scan\attribution.py` (pinned to the D58 report). After WI-1: `make accuracy-attribution REPORT=work/accuracy-reports/<name>.json`.
- Git Bash heredocs mangle `\n` in Python snippets. Write helper scripts to files instead.

### 0.4 Frozen baseline (verified 9 Oct 2026; Dx §4–§5)

Bench report hash `9cd8137fd7c1`, engine 0.1.7, extraction code `4d4cefdaeee4`, truth `f25da17cbc5f`.

| Metric | Baseline |
|---|---|
| Bench header fields (all 51 docs incl. XML) | 302/373 (81.0%) |
| Bench line recall (all) / spurious lines | 96/141 (68.1%) / 33 |
| Per field (all docs) | doc_no 43/51, vendor_name 27/51, vendor_gstin 44/48, grand_total 36/49, taxable 22/29, cgst 21/27, sgst 23/27, po_refs 12/15, doc_date 50/51 |
| **Scan-page docs (33): header** | **224/283 (79.2%)**. Misses: A 9, B 45, C 0, D 0, E 5 |
| **Scan-page docs: line cells** | amount 78/111 (70.3%), qty 65/102, rate 63/106, description 53/108, HSN 49/75. Misses: A 24, B 166, E 4 |
| Digital bundle PO/GRN docs (6) | header 18/30; lines 0/12 |
| Links | 3/10 (0 auto, 3 suggested). Identity extra 8; duplicates caught 0/2 |
| Hard metrics | 0 / 0 / 0 / 0 |

**Ceiling note.** With Tesseract + rules only, if every B/E miss were fixed, scan-page header accuracy tops out at 274/283 = 96.8% and line amount recall at 106/111 = 95.5%. The ≥98% header target therefore needs WI-8 (OCR) and/or WI-13 (vision) for the A misses.

---

## 1. Work items, in order (cheapest high-impact first)

| WI | Title | Effort | Depends on |
|---|---|---|---|
| WI-1 | Bench/upload parity, attribution tooling, baseline freeze | S | — |
| WI-2 | Test infrastructure and hard-zero regression guards | M | WI-1 |
| WI-3 | `buyer_entities` setup and party-role guard | S | WI-1, WI-2 |
| WI-4 | Document-number and PO-reference candidate selection | S | WI-2 |
| WI-5 | Vendor-name extraction | M | WI-2, WI-3 |
| WI-6 | Header totals and tax | M | WI-2 |
| WI-7 | Line-table extraction | L | WI-2 (WI-6 shares summary rows) |
| WI-8 | Targeted OCR and preprocessing | M | WI-1, WI-2 |
| WI-9 | Parser-version guard and re-parse plan | S | the check can land with WI-1; bump after WI-3–WI-8 |
| WI-10 | Link-recall regression from AC-9a | M | WI-1 |
| WI-11 | Truth-set expansion (owner-led) | L | WI-1 |
| WI-12 | **Gated** experiment: Tesseract+rules vs Gemini via H6 | M + owner run | WI-1; owner approval |
| WI-13 | Vision as primary reader on scanned pages (only if WI-12 wins) | L | WI-12, WI-2, D-8 |

Run the bench and attribution after **each** WI and record before/after counts in `docs/decision-log.md` (new entry per WI; aggregate numbers only).

---

### WI-1: Bench/upload parity, attribution tooling, baseline freeze (S)

**Goal.** The bench measures exactly what upload does, attribution is reproducible from the repo, and the baseline is frozen.

**Acceptance**
- (a) A unit test proves the bench and `IngestionService` build identical `ParserHints` from the same config body.
- (b) A fresh worker-image run with `--buyer-entities truth` reproduces the §0.4 metric blocks exactly. Command: `MSYS_NO_PATHCONV=1 make accuracy-bench BENCH_ARGS="--workers 1 --buyer-entities truth --out work/accuracy-reports/baseline-scanfix.json"`. The extraction/links/recon/hard blocks must equal `after-ac9a-grn-anchor-final-20261009.json`.
- (c) `make accuracy-attribution REPORT=work/accuracy-reports/baseline-scanfix.json` prints the §0.4 scan-page counts (header 224/283, A 9 / B 45 / E 5; amount 78/111).
- (d) A run with `--buyer-entities config` (seed = empty) completes. Its hard metrics are recorded in the decision log (input to WI-3), not required to be zero yet.

**Root cause.** Dx §2.3, mismatches M1–M6.

**Files / functions**
- `backend/reconbench_app/ingestion.py`: `IngestionService._parser_hints` (~1807–1852), `IngestionLimits` (~273).
- `scripts/accuracy_bench.py`: `parser_hints` (314–330), `reader_fingerprint` (349–370), `read_file` (382–431), `run` (1605+), `main` (1804+).
- `backend/reconbench_app/settings.py`, `backend/reconbench_app/jobs.py` (~222, ~254), `backend/reconbench_app/extraction/engine.py`.
- `backend/reconbench_app/extraction/einvoice_keys.py`.
- New: `scripts/accuracy_attribution.py`, `Makefile` target `accuracy-attribution`.
- `scripts/measure_extraction.py` (deprecate).

**Steps**
1. Extract the body of `_parser_hints` into a module-level pure function in `ingestion.py`:
   `parser_hints_from_config(body: Mapping[str, Any], *, zone_type: DocType | None, file_format: FileFormat, limits: IngestionLimits, page_split: PageSplit = PageSplit.AUTO, column_mapping: Mapping[str, str] = {}) -> ParserHints`.
   `_parser_hints` keeps its DB lookups (active config, CSV profile) and delegates. Behaviour must not change.
2. In the bench, replace `parser_hints()` with a call to `parser_hints_from_config`, using `IngestionLimits.model_validate(body["app"]["limits"])`.
3. Add bench flag `--buyer-entities {truth,config,none}` (default `truth`, so history stays comparable):
   - `truth`: current behaviour (`accuracy_bench.py:1608`).
   - `config`: leave `DEFAULT_CONFIG` as is.
   - `none`: force an empty list.

   Record the mode in `report["meta"]`. Hints are already part of the cache key, so the cache stays correct.
4. Add bench flag `--einvoice-key-file` (default: env `RB_EINVOICE_PUBLIC_KEY_FILE`). Load keys with the same loader the app uses (`einvoice_keys.py`), pass `einvoice_keys=` to `parse_pdf_isolated`, and add a SHA-256 of the key bytes (or `none`) to `reader_fingerprint`.
5. Add `extraction_engine: str = Field(default="local_hybrid", validation_alias="RB_EXTRACTION_ENGINE")` to `Settings`. Validate it at startup with `engine_for` and thread it through `jobs.py` → `IngestionService(…, extraction_engine=…)` → `parse_pdf_isolated(engine_name=…)`. The bench keeps reading the same env var.
6. Bench reliability: change the default `--workers` to `1` for **fresh** reads; cached reads stay instant. Count `PDF_PROCESS_TIMEOUT` results in `report["timing"]["timeouts"]`. A non-zero count prints a warning and the run must be repeated.
7. Port `work/diag-scan/scan_types.py` and `work/diag-scan/attribution.py` into **one** repo script `scripts/accuracy_attribution.py`:
   - `--report PATH` pins `tesseract`/`ocrmypdf`/`qr_decoder` from that report's `meta.reader` and asserts that `extraction_code` matches the working tree; otherwise exit 2 with "re-run the bench".
   - Read **only** the cache: monkeypatch `parse_pdf_isolated` to raise.
   - Compute scan-type labels from pixels per truth file and cache them in `work/accuracy-cache/scan-types.json` (file id → label + per-page numbers; no values). Labels: `digital`, `scan-clean`, `scan-tilted`, `scan-rotated`, `scan-lowdpi`, `scan-skew>5`, `+handwritten` (truth `annotation_refs`).
   - Reset the reader and scan type **per document**. The prototype had a carry-over bug between documents in one file, fixed in `work/`; keep the fix.
   - Classify each miss A-absent / A-glyph / B / C / D / E exactly as in Dx §5.1.
   - Print matrices (field group × stage, line cell × stage, by scan type), per-truth-doc rows (truth id, generic file id, stage counts) and the top 25 patterns with masked examples. With `--json OUT`, write the same as JSON.
   - Add `make accuracy-attribution` running it in the worker image (`--network none`, same mounts as `accuracy-bench`).
8. `scripts/measure_extraction.py`: replace `main()` with a message pointing to `accuracy_bench.py` and exit code 2. Its scoring ignores page splits and config (Dx M6). Deletion is decision point DP-3.
9. Freeze: copy the (b) report to `work/accuracy-reports/baseline-scanfix.json` and its attribution JSON to `work/accuracy-reports/baseline-scanfix-attribution.json`. Add a decision-log entry with the hashes and the §0.4 table.

**Tests (synthetic only)**
- `tests/unit/test_parser_hints_parity.py`: build hints from `DEFAULT_CONFIG` via `parser_hints_from_config` and via the bench path, and assert equality. Repeat with a body that has buyer entities, custom UOM aliases and gst_slabs.
- `tests/unit/test_accuracy_attribution.py`: a synthetic truth + synthetic `ParseResult` (fake page text containing chosen strings) yields the expected stage per field. Cover A-absent, A-glyph (O/0), B, C (buyer GSTIN as vendor), E (day/month swap) and D (value only on another page).
- `tests/unit/test_settings.py` (or the existing settings test): `RB_EXTRACTION_ENGINE=bogus` fails fast.

**Risks / rollback.** The hints refactor could change upload behaviour; the parity test plus an unchanged integration `test_ingestion.py` guard it. Rollback = revert the files listed above.

---

### WI-2: Test infrastructure and hard-zero regression guards (M)

**Goal.** Scan rules are testable without real data, the parser tests run in CI, and the hard-zero metrics have deterministic guards.

**Acceptance**
- `make test` runs `backend/tests/parsers`, and CI (which calls `make test`) goes green.
- At least 12 new synthetic layout tests and at least 4 synthetic image-scan tests pass locally.
- Tesseract-marked tests run (not skipped) in CI.
- `make golden` reports at least 1 verified synthetic case.

**Root cause.** Dx §8: the parsers suite is never run in CI, there are no unit tests for scan heuristics, and the golden set has 0 cases.

**Files**
- `Makefile` (`test:`).
- `.github/workflows/ci.yml` (job that runs `make test`): add `sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng`.
- New `backend/tests/support/layouts.py`: helpers that build `PageLayout`, `Word`, `Rule` and `DocumentLayout` from a compact table spec (text, x, y, w, h, conf).
- New `backend/tests/support/scan_images.py`: render a synthetic invoice image with PIL. Reuse the approach of `_scanned_invoice_pdf()` in `tests/parsers/test_phase4_pdf.py` and move it here. Parameters: `skew_deg`, `rotate`, `dpi`, `jpeg_quality`, `noise`, `tiles` (split the page image into N strips), `text_layer` (`none` / `poor` / `good`). Embed with pypdfium2 or PIL PDF save.
- `scripts/golden_run.py` and `golden/` (synthetic cases only).

**Steps**
1. Add `backend/tests/parsers` to the `test:` target. Fix any failures that surface; they were never gated.
2. Install Tesseract in the CI job (see Files). Keep the `skipif` for local machines without Tesseract.
3. Write `layouts.py`. All rule tests for WI-3 to WI-7 use it, so they need no OCR and are deterministic.
4. Write `scan_images.py` (synthetic text only, e.g. "SYN-INV-0001", made-up GSTINs with valid checksums generated in the test).
5. Hard-zero guards (`backend/tests/scenarios/test_hard_zero_guards.py`). Grep first; extend existing tests if equivalents exist.
   - A buyer GSTIN (from hints) is never `vendor_gstin`, on both the layout path (`extract_document`) and the core path (`parse_pdf`).
   - With **empty** `buyer_entities` and a single unlabelled GSTIN, `vendor_gstin.confidence < REVIEW_FLOOR` and `needs_verification`. This test fails today and is fixed by WI-3; mark it `xfail(strict=True)` until then.
   - A document with any critical field below the review floor is never `MATCHED`/`MATCHED_FLAGS` (`evaluate_components`).
   - A content-only link is `suggested`, never `auto`.
   - A vision-sourced field (`FieldSource.AI`) is never `verified` without an independent reader (consensus `decide` with a single reader).
6. Golden: add at least one synthetic PO+GRN+invoice case (`fixtures/SCN-*` style) whose expected statuses are verified, so `make golden` stops reporting `unverified_no_cases`.

**Risks.** CI time grows by about 1–2 minutes. Rollback = revert the Makefile/CI lines.

---

### WI-3: `buyer_entities` setup and party-role guard (S)

**Goal.** No role error (stage C) even when a workspace forgot to configure its own GSTINs, and a visible prompt to configure them.

**Acceptance**
- Bench `--buyer-entities none` gives `WRONG_VENDORS_CREATED = 0` and `FALSE_SAFE = 0`.
- Bench `--buyer-entities truth` gives metrics no worse than baseline.
- The WI-2 xfail test passes.
- Attribution C = 0 in both modes.

**Root cause.** Dx §5.6 and §6. The seed has `buyer_entities: []` (`seed_data.py:254`). `_gstins` (`extract.py:439-522`) can return the buyer's GSTIN as vendor when it appears once without a buyer label, and marks it `CONFIRMED` on a checksum alone (`:509-519`). The bench hides this by injecting truth (M1).

**Files / functions**
- `backend/reconbench_core/extraction/extract.py`: `_gstins`, `_role`.
- `backend/reconbench_core/parsers/pdf.py`: `_invoice_buyer_gstin` / vendor selection (same guard).
- `backend/reconbench_core/parsers/parties.py`: `guard_document`.
- `backend/reconbench_app/extraction/pipeline.py`: `layout_parse` warnings.
- Settings UI/API: `reconbench_app/ai/settings_service.py` is AI-only. Use the workspace configuration readiness surface (`reconbench_app/configuration.py` and the Settings page in `web/src`).

**Steps**
1. In `_gstins`, when `own` is empty, mark the vendor check `Status.UNVERIFIED` with note `"party role not established"` and `cap=DERIVED_CONFIDENCE` if either:
   - only one distinct GSTIN value exists and its role is `unknown`, or
   - the chosen vendor's role is `unknown` while another GSTIN has role `unknown` too.

   A checksum alone must never yield `CONFIRMED` without a seller label, a repeat, or the PAN on the page (`pan_seen` already exists).
2. Apply the same rule in the core text parser's vendor GSTIN choice.
3. When `hints.buyer_entities` is empty, add `ParseWarning(code="BUYER_ENTITIES_NOT_CONFIGURED", level=WARNING)` in `layout_parse` and `parse_pdf`, and list `vendor_gstin` in `review_fields`.
4. Show a readiness warning in Settings when `engine.buyer_entities` is empty, linking to the field. Do not block uploads.
5. Owner action (DP-2): enter the company's own GSTIN(s) in Settings.

**Tests.** Layout fixtures for:
- (a) a single unlabelled GSTIN with empty entities → vendor_gstin present, confidence < 0.70, document flagged;
- (b) the same with entities → vendor None, buyer set;
- (c) "Bill To" block first, seller block second → vendor = seller;
- (d) a repeated buyer GSTIN (bill-to + ship-to) → buyer.

**Risks.** More documents flagged when entities are empty, which is intended. Rollback = revert `_gstins`.

---

### WI-4: Document-number and PO-reference candidate selection (S)

**Goal.** Remove the 6 doc_no misses and 3 PO-ref misses where the right value is printed.

**Acceptance (bench, all docs)**
- doc_no correct ≥ 47/51 (baseline 43/51); po_refs ≥ 14/15 (baseline 12/15).
- Attribution doc_no B+E on PDF docs: 6 → 0. `trusted_but_wrong` ≤ 1. Hard metrics 0.

**Root cause.** Dx §5.5 #4:
- S02 ×4 copies: both `LLLLDDDD/DDD` and look-alike `LLLLLDDD/DDD` are in the OCR text, and the look-alike was chosen.
- S04 ×2: a 2-digit number was taken next to the label instead of `LLL/DD-DD/DDDD`.
- S05 ×2: another reference shaped `LLLL.DDDDD-DDD` was chosen over the printed PO `LL/DD-DD/DD-DDD`.
- S08 ×1: PO ref with a look-alike.

**Files / functions.** `backend/reconbench_core/extraction/extract.py`: `_doc_no` (335–381), `accept_doc_no` (234–256), `_first` (392+), `accept_po` (274+), `_PO_LABELS` (188–212), `_GENERIC_ORDER_LABELS`, `_SELLER_ORDER_WORDS`. `backend/reconbench_app/extraction/pipeline.py`: `_dual_read`.

**Steps**
1. **Diagnose first, counts only.** Write a throwaway `work/` probe that, for S02/S04/S05/S08 files, prints for each doc_no/PO candidate: label text (generic words), page, row index, masked value, confidence, and whether it was chosen. Use the local layout (`LocalHybridEngine`). Confirm the label that produced each wrong pick.
2. **Look-alike variants.** After collecting doc_no candidates, group them by `_fold(value)`. If a group has more than one spelling:
   - prefer the spelling that occurs ≥2 times on the page(s);
   - else the spelling whose letter/digit classes match its neighbours (a lone `O` inside a digit run → digit);
   - keep `has_lookalike` → never auto-verified.

   Apply the same to PO refs.
3. **Short-number guard.** Reject doc_no candidates of ≤2 characters, or purely 1–3 digits, when the same label has a longer document-number-shaped candidate (`_DOC_NO_SHAPE`) to the right or below (`aligned_below`) within 3 row heights.
4. **PO label priority.**
   - Rank PO candidates by label: explicit PO labels ("PO No", "Purchase Order", "Buyer's Order No") > generic "Order No" > "Ref".
   - Reject tokens whose label row contains seller-order words (`_SELLER_ORDER_WORDS`), and tokens matching `_MARKETPLACE_ORDER`.
   - When both a labelled PO-shaped token (contains `/` and a 2-digit year-like group) and a dot-separated token exist, prefer the labelled one.

   Write the rule generically. No supplier-specific pattern.

**Tests.** Layout fixtures for:
- (a) the doc number printed twice, once with O for 0 → the digit version is chosen and the field stays flagged;
- (b) the "Invoice No." label with "24" nearby and a long number below → the long number;
- (c) "Buyer's Order No" plus "Sales Order No" → the PO;
- (d) a marketplace order id is never a PO.

**Risks.** Over-rejecting genuine short invoice numbers. Rule 3 applies only when a longer candidate exists under the same label. Rollback = revert the functions.

---

### WI-5: Vendor-name extraction (M)

**Goal.** Read the supplier name when it is printed without a "Supplier/Seller/Bill from" label.

**Acceptance (bench)**
- vendor_name correct ≥ 45/51 (baseline 27/51).
- Attribution vendor-name B on PDF docs: 22 → ≤ 4.
- `WRONG_VENDORS_CREATED = 0`; `vendors.junk_reasons` empty; FALSE_SAFE 0.

**Root cause.** Dx §5.5 #2. `extract_document` uses only `_signatory_name` (`extract.py:689-700`; a whole row "for X" with all words at confidence ≥60) or `_supplier_block_name` (`:736-774`, labelled blocks). Names are missing on 16 scan docs and 6 digital PO/GRN docs although present in the text.

**Files / functions.** `extract.py`: `extract_document` (873), `_acceptable_name` (680), new helpers. `parsers/parties.py`: `name_problem`, `is_buyer_name`. Move `_PARTY_ADDRESS_WORDS` / `_COMPANY_WORDS` from `parsers/pdf.py` (~85–132) to `parties.py` so both paths share them.

**Steps**
1. `_gstin_block_name(ctx, vendor_candidate, buyers)`:
   - From the chosen vendor GSTIN's row, scan up to 8 rows above in the same column (word `x0` within [block_x0 − 2h, block_x0 + 12h]).
   - Return the first row that passes `_acceptable_name`, is not an address row (≥2 address words, a PIN-code shape `\b\d{6}\b`, or "GSTIN/PAN/State" labels), and is not a buyer name.
   - Prefer the row with the largest median word height (a font-size proxy) among the candidates.
2. `_title_block_name(ctx, buyers)`:
   - On page 1, rows in the top 25% of the page.
   - Exclude document titles (reuse the `pages.py` title words: TAX INVOICE, PURCHASE ORDER, GRN, etc.) and rows under a buyer label.
   - Pick the row with the largest median word height that passes `_acceptable_name` and contains a company word (`PVT`, `LTD`, `LLP`, `ENTERPRISES`, …) **or** is followed within 3 rows by an address row.
3. Order: signatory → supplier label → GSTIN block → title block.
   - If two methods give names with `SequenceMatcher` ratio ≥ 0.9, set `FieldCheck(Status.CLEAR, "two placements agree")`; otherwise `UNVERIFIED`.
   - A name never becomes verified from OCR alone; vendor creation stays GSTIN-based (AC-4).
4. Relax `_signatory_name`: allow low-confidence words if the row's mean confidence is ≥60 and the name passes `_acceptable_name`.

**Tests.** Layout fixtures:
- (a) company name above address + GSTIN in the top-left box → name;
- (b) name only in "for X" with one low-confidence word → name;
- (c) buyer block on top with "Bill To" → buyer name never chosen;
- (d) a garbage row (≤3 letters, digits) → no name;
- (e) the title row "TAX INVOICE" is never the name.

**Risks.** A wrong name on a flagged document costs a correction. Names are not in the bench `_CRITICAL` list, so this cannot create a false-safe, but **watch `WRONG_VENDORS_CREATED`**. Rollback = remove the new helpers from the order.

---

### WI-6: Header totals and tax (M)

**Goal.** Read printed totals and tax that are currently missed or mis-assigned.

**Acceptance (bench)**
- Attribution totals/tax B on PDF docs: 29 → ≤ 6.
- grand_total ≥ 45/49 (baseline 36/49), taxable ≥ 26/29 (22/29), cgst ≥ 25/27 (21/27), sgst ≥ 25/27 (23/27).
- `trusted_but_wrong` ≤ 1; FALSE_SAFE 0.

**Root cause.** Dx §5.5 #3:
- Fixed label list (`extract.py:545-573`).
- An unlabelled summary row is fed to both taxable **and** grand (`:596-608`).
- Line-level tax (marketplace format) is never summed.
- `accept_money` rejects tokens containing `%`, a common OCR reading of "₹" (`:263-266`).
- The 6 digital bundle PO/GRN docs miss their grand total.

**Files / functions.** `extract.py`: `_summary_candidates`, `accept_money`, `_rupee_glyph`, `extract_document` (916–963). `reconbench_core/extraction/verify.py` and `values.py`: `solve_document`, `DocumentNumbers` (check the arithmetic solver's candidate handling).

**Steps**
1. `_summary_candidates`: classify each `tables.summary_rows` row:
   - CGST/SGST/IGST → tax;
   - "TAXABLE|SUB TOTAL|TOTEL" → taxable;
   - "GRAND|NET PAYABLE|INVOICE VALUE" → grand;
   - a bare "TOTAL" → grand if it is the last summary row with an amount, else taxable.

   Never append the same row to both lists.
2. Line-derived header values. If no labelled taxable candidate exists, add Σ(line amounts) as a taxable candidate. If no labelled tax candidate exists and lines carry `Col.TAX` values, add Σ line tax per type (type from the heading words CGST/SGST/IGST, or from interstate). Mark them `Status.DERIVED`, capped at `DERIVED_CONFIDENCE`, and keep them only when `solve_document` closes taxable + taxes ± round-off = grand within ₹1 against a **printed** grand total.
3. `accept_money`: when `_rupee_glyph(pages)` is true, accept a token with one leading `%` followed by a money shape (`^%\s*\d[\d,]*\.\d{2}$`). Keep rejecting `\d{1,2}(\.\d+)?%` (tax percent).
4. Label tolerance: in `find_labels` (or a pre-pass), accept one-character OCR typos for label words of ≥5 letters (`TOTAL`/`TOTEL`, `TAXABLE`/`TAXABIE`). Implement with an explicit small variant map; no general fuzzy matching on numbers.
5. Grand-total sanity: when amount-in-words (`_words_totals`) exists and equals one candidate, prefer it (already partially done in `solve_document`; verify).

**Tests.** Layout fixtures:
- (a) a summary block "Total" + "CGST" + "SGST" + "Grand Total";
- (b) only per-line tax columns → header taxable/tax derived and marked derived;
- (c) a "TOTEL" typo row;
- (d) `%`-prefixed amounts on a ₹-glyph page;
- (e) a "Total" row with a quantity column → not grand.

**Risks.** Derived values could close arithmetic by coincidence. They stay capped below the review floor, so the document remains flagged. Rollback = revert `_summary_candidates`.

---

### WI-7: Line-table extraction (L)

**Goal.** Produce correct item rows on scanned tables. This is the largest error source: 166 of 194 scan line-cell misses are B.

**Acceptance (bench + attribution)**
- Scan line amount recall: 78/111 → ≥ 100/111 (90%).
- Qty ≥ 92/102, description ≥ 90/108, rate ≥ 88/106 (14 rate misses are A).
- Digital bundle PO/GRN lines: 0/12 → ≥ 10/12.
- Bench `line_spurious` (all docs): 33 → ≤ 8. FALSE_SAFE 0; `trusted_but_wrong` ≤ 1.

**Root cause.** Dx §5.5 #1, three modes:
- (M1) no heading found → 0 lines (S09-INV, S10-INV);
- (M2) heading columns misclassified (S03: no qty, two rate bands; S08: qty = `other`, duplicate `quantity`/`total_incl`; S05: two `quantity` bands) → missing or misaligned cells and spurious rows;
- (M3) heading found but 0 items (S10-PO; S05 p2 continuation).

**Files / functions.** `backend/reconbench_core/extraction/table.py`: `_classify` (146–185), `_cells` (188–225), `_tax_subcolumns`, `_resolve_amount`, `find_heading` (356–393), `_bands` (395–419), `extract_tables` (539–683), `_compatible`. `extract.py`: `_line_numbers` (636+), `solve_line`.

**Steps**
1. **Diagnose per doc (counts only).** Extend `work/diag-scan/debug_tables.py` to print, per page, the heading cell texts (generic heading words are not private), the band x-ranges, and for each body row: which band each numeric token fell in, and why the row was skipped (`no amount in band` / `description < 3 chars` / `STOP` / `summary`). Run it inside the worker image for Tesseract 5.5 parity.
2. **Classifier (M2).** In `_classify`, handle multi-word headings:
   - "UNIT PRICE" / "RATE PER" → RATE;
   - "NET AMOUNT" / "TAXABLE VALUE" → AMOUNT;
   - "TAX RATE" → TAX_RATE;
   - "TAX TYPE" → OTHER;
   - "TAX AMOUNT" → TAX;
   - "TOTAL AMOUNT" → TOTAL_INCL;
   - "QTY" split over two rows ("Qty" / "(Nos)") → QTY.

   When two cells get the same numeric kind, keep both temporarily as candidates (`Col.QTY` twice, etc.) and let step 3 decide.
3. **Column-role solver (M2).** After `find_heading`, take the first ≤10 body rows with ≥2 numbers. For each assignment of the heading's numeric bands to {QTY, RATE, AMOUNT, DISCOUNT, TAX, TOTAL_INCL} consistent with the classifier candidates, score the rows where `qty × rate − discount ≈ amount` (±₹1 or 0.5%). Pick the best assignment; tie → keep the classifier's. Implement it as a pure function `resolve_numeric_bands(heading, rows, height) -> Heading` with a bounded search (≤ 6! permutations restricted by candidates).
4. **Heading-less fallback (M1).** If a DOCUMENT page has no heading, find the body region (below the last header-block row containing a GSTIN/date/number label, above the first `_STOP`/summary row). Accept a row as an item when it has description text (≥3 letters) and either:
   - a numeric triple satisfying qty × rate ≈ amount, or
   - one money token with a single-line document (`item_single_line_explicit` case).

   Mark these lines `Status.DERIVED` (capped), and the document `needs_verification`.
5. **Row walk (M3).**
   - Extend the AMOUNT band's right edge to the next vertical rule or the page edge when amounts are right-aligned past the heading word.
   - When a page's own heading yields 0 items, retry with the master heading's bands (`_compatible` or richer).
   - Log (as a warning code, not text) `TABLE_NO_ITEMS` when a heading is found and no items are produced.
6. **Spurious-row suppression.** An item whose numbers violate arithmetic, cannot be explained by discount/tax/freight columns, and whose description is shorter than 3 letters is dropped. A dropped row adds review field `lines` and warning `TABLE_ROW_DROPPED`.
7. Re-run the bench after each sub-step (2, 3, 4, 5, 6) and keep a sub-step only if it improves scan amount recall without raising `line_spurious` or any hard metric.

**Tests.** Layout fixtures:
- (a) a two-row GST heading (CGST/SGST groups with Rate/Amount sub-columns);
- (b) a marketplace heading (Unit Price | Qty | Net Amount | Tax Rate | Tax Type | Tax Amount | Total Amount);
- (c) a heading-less single-line bill;
- (d) a 3-page table with heading only on page 1 and a continuation page;
- (e) a Tally-style ruled PO table with right-aligned amounts;
- (f) solver picks the right band when "Qty" is misread as OTHER;
- (g) a row with garbage numbers is dropped and flagged.

**Risks.** Over-fitting to the 10 truth invoices. Tests must be generic layouts, and WI-11's expanded truth set is the real check. Rollback per sub-step.

---

### WI-8: Targeted OCR and preprocessing (M)

**Goal.** Recover values that OCR loses on low-contrast or handwritten pages, and close the OCR-skip routes, **only where the evidence justifies it**.

**Acceptance**
- Attribution A (absent + glyph), scan header: 9 → ≤ 5; scan line cells: 24 → ≤ 16.
- No increase in B or E. Hard metrics 0.
- Fresh-run read time per scanned page (bench `timing.read_seconds` / scan pages) ≤ 1.5× baseline.

**Root cause.** Dx §5.4–§5.5 #5 and Dx §3 (routes 1–5). Evidence against generic changes: skew max 1.1° (0 pages beyond ±5°), no phone photos, native DPI ≥200. So wide-angle deskew, perspective correction and upscaling are **not justified** on current data.

**Files / functions.** `backend/reconbench_app/extraction/local_hybrid.py`: `ocr_page_with_image`, `is_scanned`, `_tesseract`, `LocalHybridEngine.layout`. `backend/reconbench_core/extraction/values.py`: GSTIN repair (~230). `backend/reconbench_app/pdf_sandbox.py`: `_prefer_layout`, `PDF_LAYOUT_*`.

**Steps**
1. **Low-confidence re-OCR (experiment-gated).** On pages where >20% of words have conf <60, re-OCR the header band (top 35%) and the table body band (from WI-7) with: 2× Lanczos upscale, local adaptive threshold (Sauvola, a numpy implementation; no new dependency), `--psm 6`. Replace a word only where the re-read word overlaps an original word with conf <60 and has higher conf. Keep the change only if (Acceptance) holds.
2. **GSTIN checksum-guided repair.** Extend the repair beyond 2 swaps only on characters with conf <60, from the look-alike table, accepting a unique checksum-valid result. The result stays `UNVERIFIED` ("GSTIN repaired from OCR"). Target: the 2 A-glyph GSTIN misses (S07 copies).
3. **Force OCR on poor or tiled text layers.** `is_scanned` returns true when the **sum** of image areas ≥ 50%, or when an image covers ≥30% and the text layer has <20 alnum chars. In `_prefer_layout`, treat a page with a ≥50% image as scanned even if the core parser saw a text layer, so the layout result wins ties. Synthetic tests only (0 such pages in truth).
4. **More than 20 pages.** Raise the layout cap to config `max_pages` with a CPU budget of 8 s/page (cap 600 s), or process in 20-page chunks. At minimum, emit `ParseWarning("LAYOUT_PAGE_LIMIT")` instead of a silent skip (decision DP-6).
5. **Portability.** `_tesseract`: `subprocess.run(..., text=True, encoding="utf-8", errors="replace")`.
6. **Deferred (conditional on WI-11 data):** skew >5° (widen `_MAX_SKEW_DEGREES` to 15 with a coarse 1° sweep), perspective/dewarp for phone photos, upscale when native DPI <200, denoise. Implement each only when the expanded truth set has ≥5 such docs and attribution shows A misses there.

**Tests.** Synthetic image scans (WI-2): skew 3° (must OCR fine), rotation 180°, a tiled-image page (must OCR), a poor text layer (must OCR), a GSTIN with 3 look-alike low-conf characters (repaired, unverified), a 25-page synthetic PDF (warning or chunked parse).

**Risks.** Runtime; the sandbox CPU limits may trip. Measure with `--refresh` on a few files first. Rollback = feature flag constant per step.

---

### WI-9: Parser-version guard and re-parse plan (S)

**Goal.** Files parsed with old rules are always identifiable, and re-parsing is safe and bulk-capable.

**Acceptance**
- A CI check fails when extraction code changes without a `PARSER_VERSION` bump.
- The dry-run CLI lists outdated files.
- An integration test proves a re-parse keeps manual corrections and verified references.

**Root cause.** Dx §3: `PARSER_VERSION = "phase4-parser-6"` (`ingestion.py:87`) is bumped by hand, independent of `ENGINE_VERSION` and the code hash. Re-parse is per-file manual only (`ingestion.py:753-786`).

**Files.** New `backend/reconbench_app/PARSER_HASH`, new `scripts/check_parser_version.py` (same file set as bench `_CODE_PATHS`, excluding `vision*`/`consensus*`), `Makefile` `check-static`. New `backend/reconbench_app/reparse_outdated.py` (`python -m reconbench_app.reparse_outdated --workspace <uuid> [--apply] [--batch 20]`).

**Steps**
1. Hash script: the hash of the listed code. Fail if the hash ≠ `PARSER_HASH` while `PARSER_VERSION` is unchanged versus `PARSER_HASH`'s recorded version. `--update` writes both.
2. Bump `PARSER_VERSION` to `phase4-parser-7` when WI-3 to WI-8 are merged (one bump per release, not per WI).
3. CLI: select `source_file` rows with `status='parsed'` and an outdated `parser_version`. Dry run prints the count by zone. `--apply` uses the existing `retry_file` service method per file, in batches, with audit events.
4. Verify: `_upsert_document` must append a new `document_version` and carry over manual corrections and verified refs (D48–D50 behaviour). Read `ingestion.py` around `_upsert_document` and the correction-refresh code before writing the test.

**Tests.** `tests/integration/test_reparse_outdated.py` (disposable PG per `docs`/memory notes): seed a parsed file with a manual correction, bump the version, run the CLI `--apply`, and assert a new version exists, the correction value is preserved, and audit rows are written.

**Risks.** A bulk re-parse triggers vendor/link/recon recompute. Run per workspace, owner-triggered (DP-8).

---

### WI-10: Link-recall regression from AC-9a (M)

**Goal.** Recover the 4 links lost when matching v2 landed, without loosening D-6.

**Acceptance (bench).** Links ≥ 7/10 on the current truth with `FALSE_AUTO_LINKS = 0` and `false_suggestions = 0`; ≥ 95% on the expanded truth set (WI-11, together with WI-3 to WI-7).

**Root cause.** Dx §5.7 (verified): identical extraction, links 7/10 → 3/10. Lost: S09-GRN→PO, S14/S15/S16-GRN→PO. The cause in `reconbench_core/mapping/links.py` is UNKNOWN. Hypotheses: (a) the XML PO plus a bundle PO copy (identity extra) make the exact-ref candidate ambiguous; (b) the new same-vendor gate when one copy's vendor is pending; (c) the verified-anchor rule rejects GRN→PO refs from OCR.

**Files.** `backend/reconbench_core/mapping/links.py` (`resolve_links`, `_via_grn_resolution`, candidate retrieval), `scripts/accuracy_bench.py` (`score_links`).

**Steps**
1. Add a bench `--link-trace` that, for each expected pair, prints labels only: candidate generated (y/n), vendor gate (pass/fail/pending), anchor type, verified (y/n), ambiguity count, final state.
2. Run it on the baseline cache and identify the failing stage per pair.
3. Fix at that stage. Examples: dedupe candidate POs that share a verified natural key (XML wins over the PDF copy, per AC-3); allow a GRN→PO **suggestion** when the vendor is pending but the ref matches exactly.
4. Keep auto-link only for verified anchors (D-6/D57).

**Tests.** Synthetic mapping tests mirroring each failing stage (no real values).

**Risks.** False suggestions. The bench `false_suggestions` must stay 0.

---

### WI-11: Truth-set expansion (L, owner-led)

**Goal.** A truth set that can prove 98/95/95% targets: 15–20+ complete PO+GRN+invoice sets, tagged by scan type.

**Acceptance.**
- `load_truth` accepts format v3.
- At least 15 complete sets and at least 60 invoice documents.
- Every scan type that occurs in production has ≥5 documents.
- At least 40 expected link pairs; UOM truth on all lines.

**Steps**
1. Truth format `reconbench-accuracy-truth/v3`: v2 plus a per-source `scan_type` (`digital|searchable_scan|scan_clean|scan_tilted|scan_rotated|phone_photo|low_dpi`), `handwritten: bool`, and per-line `uom`. The bench accepts v2 and v3; attribution prefers truth `scan_type` over pixel labels.
2. Owner collects sets covering: no-ref invoices, handwritten refs, bundles, re-scans, partial receipts, multi-page invoices, marketplace invoices, suppliers without GSTIN.
3. Draft truth with `accuracy_bench.py --prefill work/accuracy-truth/draft.json`. The owner corrects it; nothing leaves the machine.
4. Re-freeze the baseline on v3 (WI-1 step 9).

**Risks.** Owner time (DP-5).

---

### WI-12: GATED experiment, Tesseract+rules vs Gemini through H6 (M code + owner run)

> **Gate.** No external call and no real invoice leaves this machine until the owner approves in writing (decision-log entry). Use a **paid** Gemini API key only. Free-tier data may be used by Google to improve its products, so the D39 free-tier testing allowance does not apply to this experiment. Masking stays on, `store=False`.

**Goal.** Measure whether Gemini `gemini-3.5-flash` (thinking LOW and MEDIUM; HIGH optional) and Flash-Lite (comparison) read scanned pages better than Tesseract+rules, safely and affordably.

**Acceptance (of the experiment itself).** All variants complete within budget, and the report holds the metrics below for each variant. The decision rule (below) is applied as written.

**Code preparation (local, no calls)**
1. `backend/reconbench_app/ai/provider.py`: widen `ProviderRequest.thinking_level` to `Literal["minimal","low","medium","high"]`; the default stays `"low"`. The app hook keeps `"low"` until WI-13.
2. Bench flags in `scripts/accuracy_bench.py` / `scripts/accuracy_vision.py`:
   - `--vision-thinking {low,medium,high}` and `--vision-media-resolution {high,ultra_high}` (default `high`). Both go into the vision cache key and `report["vision"]["meta"]`.
   - Model via the existing `--vision-model` / `RB_BENCH_VISION_MODEL`. Note that the current default is `gemini-3.8-flash`, so always pass the model explicitly. Whether the model ids exist for the owner's key is UNKNOWN. After approval, verify with a 1-call synthetic-page probe before the run: `MSYS_NO_PATHCONV=1 PROBE_KEY=<paid key> PROBE_MODEL=gemini-3.5-flash make vision-probe PROBE_ARGS="--send"`. This is an external call and falls under the same gate.
3. Do not set temperature, top_p or top_k (keep provider defaults, as Google recommends for Gemini 3.x; the adapter already sends none: `ai/worker/gemini.py:73-90`).
4. Latency: record wall seconds per call in the vision cache entry; summarise p50/p95 per variant.
5. Cost: if `RB_AI_PRICE_FILE` (owner-supplied prices) is set, compute cost per page and per document from usage tokens (input, output, thought). Otherwise report tokens only. **Never hard-code prices.**
6. Unreadable signal: `vision.schema.v4` = v3 plus a top-level `unreadable: [{name, page, box_2d}]`; `vision.prompt.v4` adds "If a value is printed but you cannot read it with certainty, list its field name in `unreadable` and do not output a value." Core `check_reading` ignores `unreadable` for values but counts it.
   Metrics:
   - **unreadable-to-null rate** = truth-present fields the model left empty or listed as unreadable, split into listed vs silently omitted;
   - **wrong-when-answered** = answered fields that are wrong.
7. Standalone validators in `reconbench_core/extraction/vision.py::check_reading`, needed for both the experiment and WI-13:
   - totals arithmetic without needing OCR (taxable + taxes ± round-off = grand, ±₹1);
   - Σ line amounts = taxable (±₹1 or `total_vs_expected` tolerance);
   - line qty × rate ≈ amount;
   - date window (reuse the D42 window from `extraction/consensus.py`).

   Violations drop the value with a new `dropped` reason. Unit-test each.
8. Vision-primary **simulation** in the bench (`--vision-primary-sim`): build per document the candidate per WI-13 rules (pure function, step 2 of WI-13) and score it with `score_document`. Report header/line accuracy and **false-safe under the WI-13 gating**.
9. Disagreement with Tesseract: the existing OCR×vision block (agree-correct, agree-wrong, disagree-*).

**Owner run (after approval; Git Bash; each variant writes its own report)**
```
export MSYS_NO_PATHCONV=1 BENCH_GEMINI_KEY=<paid key>
RB_BENCH_VISION_MODEL=gemini-3.5-flash      make accuracy-bench-vision BENCH_ARGS="--vision-thinking low    --vision-primary-sim --max-provider-pages 80 --workers 1 --out work/accuracy-reports/exp-flash-low.json"
RB_BENCH_VISION_MODEL=gemini-3.5-flash      make accuracy-bench-vision BENCH_ARGS="--vision-thinking medium --vision-primary-sim --max-provider-pages 80 --workers 1 --out work/accuracy-reports/exp-flash-medium.json"
RB_BENCH_VISION_MODEL=gemini-3.5-flash-lite make accuracy-bench-vision BENCH_ARGS="--vision-thinking low    --vision-primary-sim --max-provider-pages 80 --workers 1 --out work/accuracy-reports/exp-lite-low.json"
# optional: --vision-thinking high
```
Scope: 39 PDF documents / 67 pages per variant (current truth). Token reference from cached D41 flash-lite LOW: about 2.6k input + 2.1k output per document. MEDIUM/HIGH thought tokens are UNKNOWN; cap with `--max-provider-calls`.

**Metrics per variant (scan-page docs and all PDF docs)**
- Header accuracy and line accuracy (amount recall; qty/rate/description cells), each vision-alone and vision-primary-sim.
- False-safe (sim), agree-wrong with Tesseract, disagreement counts.
- Unreadable-to-null rate (listed/omitted), wrong-when-answered.
- Handwritten-ref recall.
- Tokens (in/out/thought) and cost per page; latency p50/p95; failures by code.

**Pre-registered decision rule.** Vision-primary "wins" for scanned pages only if **all** hold on scan-page docs:
- header (sim) ≥ OCR baseline + 5 points and lines (sim) ≥ OCR + 10 points;
- false-safe (sim) = 0 and agree-wrong ≤ 1;
- wrong-when-answered ≤ 2% of answered critical fields;
- p95 latency ≤ 60 s per document;
- cost per document within the owner's D-8 budget.

Pick the cheapest variant that wins.

**Risks.** Data leaving the machine (gated, masked, paid, `store=False`); quota failures (bench stops early, as in D40); model availability.

---

### WI-13: Vision as the primary reader on scanned pages (L, only if WI-12 wins and the owner approves)

**Goal.** On scanned pages, H6 provides the candidate values. Deterministic validators and the existing D42 consensus decide what is verified. Every invariant holds.

**Acceptance (bench on the v3 truth set, fresh run)**
- Scan-page headers ≥ 98% and lines ≥ 95%.
- Hard metrics 0; `trusted_but_wrong` = 0.
- No vision-only value verified without an independent reader.
- Workspace setting off → behaviour identical to the pre-WI-13 baseline (bench hash equal).

**Design**
1. **Switch.** New workspace setting `ai_setting.vision_primary_scans` (migration `0025`; default false; requires `vision_enabled`, current consent and the deployment gate). Deployment env `RB_VISION_READER` must not be `off`.
2. **Pure merge in core.** `reconbench_core/extraction/vision.py::merge_primary(ocr: Document, check: VisionCheck, *, buyer_entities, gst_rates, policy) -> Document`:
   - Per field:
     - vision value present and passes validators, and OCR agrees (normalised) → keep, `supporting=(ocr, vision)`;
     - vision passes and OCR is missing or fails a validator → vision value, `source=AI`, confidence capped at **0.69** (< `extraction_gate` 0.70);
     - both pass but disagree → keep OCR as the value, vision as an alternative, flag the field;
     - vision unreadable/omitted → OCR value (or none) + flag.
   - Lines: align vision rows to OCR rows (existing `align_lines` in core vision); same rules per cell; a vision-only row is added as an unverified line.
   - Handwritten annotations never enter header values; refs from annotations stay suggestions.
3. **Validators before merge.** WI-12 step 7 plus the existing checks (GSTIN checksum, buyer role, tax type vs state, ref shape, masked box).
4. **Where it runs (keeps the SDK boundary).** The AI worker stores the vision answer (as today). A new Procrastinate task in the **general** worker (`jobs.py`, `merge_vision_primary`) loads the cached answer (no SDK import), runs `merge_primary`, and appends a new `document_version` (`derived_from_version_id` = OCR version, `extraction_method = "pdf_vision"`, field provenance per value). Vendor/links/recon then recompute as for any new version.
5. **Verification.** Only the existing consensus job (AC-6, `ai/consensus.py`) may raise verification, under D42: two independent readers agree + all validators pass + local Tesseract crop re-read agrees, per opted-in field type; never handwritten, a new vendor name, a corrected value or an unconfirmed split. Without that, the document stays `needs_verification`. The recon extraction gate (`engine.py:1183`) and content-link suggestion-only rule keep it out of `MATCHED` and auto-links.
6. **Fallback.** Vision off, failed or over budget → the OCR version stays current (no change).
7. **UI.** The verify screen shows the source of each value (OCR / vision / both agree) and alternatives; the existing correction save applies a choice (versioned, audited).
8. **Limits.** D-8 monthly caps must be set before enabling; per-day caps already exist.

**Tests**
- Core unit tests for every `merge_primary` branch: agree, vision-only valid, disagree, unreadable, arithmetic-fail, buyer-GSTIN, handwritten.
- A property test: no field with `source=AI` and a single reader has confidence ≥ 0.70 or `verified`.
- An integration test: vision answer cached → merge task → new version flagged; consensus with a crop agreement → only allowed field types verified.
- Bench `--vision-primary-sim` equals the app merge output on synthetic docs (shared pure function).
- import-linter: the merge task module does not import the Google SDK.

**Risks / rollback.** Turn the setting off; already-appended versions remain in history. A "restore OCR version" action appends a copy of the OCR version (append-only).

---

## 2. Regression guards for the hard-zero metrics (cross-cutting)

- The bench exit code is non-zero if any hard metric is non-zero (exists). Run the bench after every WI; a non-zero result blocks the WI.
- WI-2 scenario tests (buyer GSTIN never vendor; empty-entities role guard; low confidence never MATCHED; content link never auto; AI single reader never verified) run in CI.
- WI-12/WI-13: a property test on `merge_primary`, plus bench false-safe under simulation.
- Attribution must show C = 0 and D = 0 after every WI. A new C or D miss blocks the WI.
- `trusted_but_wrong` (bench) must not exceed baseline (1), and must be 0 for DoD.

## 3. Decision points for the owner

| ID | Decision | Recommendation |
|---|---|---|
| DP-1 | Approve WI-12 (external calls with real masked pages), which variants, budget, and a **paid** key | Approve after WI-1 to WI-7; run flash LOW + MEDIUM + lite LOW first |
| DP-2 | Enter `buyer_entities` in Settings now; show a readiness warning when empty | Yes, both |
| DP-3 | Delete `scripts/measure_extraction.py` or keep it as a deprecated stub | Delete after WI-1 |
| DP-4 | DoD meaning for values OCR cannot see (handwriting, faint print): must they be extracted (needs vision), or is "correctly flagged for a person" acceptable? | Decide before WI-13; affects whether WI-13 is required |
| DP-5 | Supply 15–20+ complete sets for truth v3 | Needed for any DoD claim |
| DP-6 | Files over 20 pages: support (time budget/chunking) or explicit rejection | Explicit warning now, chunking later |
| DP-7 | GST slab config: update the seed to the current slab set with effective dates (40% is missing; 12/28 kept for older documents) | Owner's tax policy |
| DP-8 | After the parser bump: bulk re-parse of existing files (CLI) or per-file only | Bulk per workspace, owner-triggered |
| DP-9 | D-8 monthly cost/call caps (required before WI-13) | Set from WI-12 tokens and price |

## 3a. Owner answers (9 Oct 2026)

- **Docker** is running; `buyer_entities` is filled in the workspace Settings. WI-1 step (d) and WI-3 still measure the empty-entities case, because new workspaces start empty.
- **DP-1 / WI-12 key:** the owner has only a Gemini **free-tier** key and accepts using it for these test documents (same basis as D39). This replaces the "paid key only" condition for the WI-12 test run only. Risk accepted by the owner: free-tier inputs may be used by Google to improve its products. Masking stays on. Production use still needs a paid key (D-1). Free-tier quotas are low, so run one variant at a time with `--max-provider-calls` and `--vision-pause`.
- **DP-5 / WI-11:** target about **5 complete sets** (not 15–20). With 5 sets, report DoD numbers as indicative only; one document still moves rates by several points.
- **DP-4:** the deterministic path flags what it cannot read for review. The owner expects the LLM path (WI-12 → WI-13) to close the accuracy gap. So WI-13 is the intended route to ≥98% headers, still under the WI-12 decision rule and the D42 verification rule (vision alone never verifies a value).

## 4. Out of scope

- Duplicate/identity work (AC-8b: 8 extra docs, duplicates 0/2), except where WI-4/WI-8 make re-scan keys more stable.
- Reconciliation rules and tolerances; matching thresholds beyond the WI-10 regression.
- XML/CSV parsers; GSTR-2B; UI redesign beyond the WI-3 warning and WI-13 provenance display.
- Production rollout of any external reader without owner approval; any external call outside WI-12.
- Training custom OCR models or adding new heavy dependencies (OpenCV etc.) unless WI-11 data justifies WI-8 step 6.

## 5. Definition of done

All measured by a **fresh** worker-image bench on the **v3 truth set** (≥15 complete sets), plus attribution:

1. Header accuracy ≥ 98% (scan-page docs **and** all docs).
2. Line accuracy ≥ 95% (amount recall, and qty/rate cells).
3. Link recall ≥ 95%, with `false_suggestions` = 0.
4. `FALSE_SAFE` = `FALSE_AUTO_LINKS` = `FALSE_MATCHED` = `WRONG_VENDORS_CREATED` = 0, and `trusted_but_wrong` = 0.
5. Attribution: C = 0, D = 0; every remaining miss is flagged for review (no unflagged wrong critical field).
6. CI: `make check-static`, `make test` (now including `tests/parsers`, with Tesseract), `make golden` (≥1 verified case) and the parser-version check pass. Integration groups touched (ingestion, re-parse) pass on a disposable PostgreSQL.
7. `PARSER_VERSION` bumped; the owner has re-parsed or consciously deferred existing files (DP-8).
8. Decision-log entries exist for each WI with before/after aggregate numbers. If WI-12/WI-13 ran, the owner's approval entry precedes the first external call.

If the OCR-only ceiling (§0.4) prevents (1) and the owner chooses "flagged is acceptable" in DP-4, then (1) is restated as "≥98% correct-or-flagged, with all wrong values flagged". Record that in the decision log.
