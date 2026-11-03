# Plan: KI-006 (dense-page AI output cap) and two extraction gaps

Written 3 Nov 2026. **Plan only: nothing here is implemented.** No file in `work/` and no real invoice was opened.
Labels: **verified** = read in code this session (path + function); **hypothesis** = consistent with the evidence but not shown; **unknown** = needs data.

## Item 3. KI-006: the AI reading runs out of output room on a dense page

### What is verified
- The self-hosted request is built in `ai/worker/openai_compat.py:request_body`: `max_tokens = request.max_output_tokens`, `response_format = json_schema` with `strict: True`, sampling profile `qwen_card_nonthinking` (`presence_penalty 1.5`).
- The cap is the context window minus the prompt and image estimate (`ai/limits.py:local_output_budget`, `local_image_tokens`). With an 8192-token window it is about 4096 for an A4 page (b11). A bigger cap is not possible; more retries do not help (KNOWN_ISSUES KI-006).
- b12 counts for doc-4 page 1 (from KNOWN_ISSUES): 8936 characters, about 52 `fields` entries each with a `box_2d` (boxes 53, distinct 51), 1 line row started, 51% of characters are formatting whitespace (601 line breaks).
- **The vLLM dialect forces every key.** `ai/contracts.py:_openai_dialect` lists every property as `required`; a key that is optional in the Gemini schema becomes `anyOf [type, null]`. So:
  - every `fields` entry must write `name, text, label, page, box_2d` (`label` is almost always `null`);
  - every `lines` row must write all ~20 `VisionLineItem` keys (`description … po_ref`, including `received_qty`, `accepted_qty`, `rejected_qty`, `serials`), most of them `null`.
- The schema puts `fields` before `lines`, so a header-heavy page is cut at the first item row.

### Root cause (verified mechanism, share of each part is a hypothesis)
The answer is long because of three multiplying costs, not because of a loop:
1. **Many header fields** (~52, against 7–15 on other pages): the exact field names are unknown.
2. **Fixed per-entry overhead**: `label: null` on every field, about 20 keys on every line row (hypothesis: roughly 100–150 tokens per empty row, more with values), `box_2d` as four integers per entry.
3. **Pretty-printed output** (about 600 line-break tokens, hypothesis ~10–15% of the cap). The server decides the whitespace; the schema does not forbid it.

So even if page 1 lost its extra header fields, a dense invoice with about 15 or more rows would still hit the cap. Fixing only the header is therefore not enough.

### Unknown (needs one measurement before choosing)
- Which names repeat among the ~52 fields (the schema has 40 names). The b12 log prints counts only.
- The company server's vLLM version and whether it accepts a "no extra whitespace" option for structured output.
- Whether `presence_penalty 1.5` makes the model avoid repeating key names/`null` and so change how it writes rows. It is the Qwen card profile; the `_pp0` arm exists in `SAMPLING_PROFILES` but I found no recorded result.

### Proposed fix, in order (each is a separate change and a separate go)

**Status 3 Nov 2026:** step A is done (below). Step B was measured, not built (see the note under the table).
| Step | Change | Files | Needs |
|---|---|---|---|
| A | Log a count per field name for each vision answer (names are the fixed enum, so safe to log) and make `tail_period` ignore short repeats | `ai/answer_shape.py`, `ai/worker/runner.py` | no provider call by itself; the next owner-approved real read shows the names |
| B | Trim the vLLM dialect only: drop `label` from `fields`; for an invoice drop `received_qty`, `accepted_qty`, `rejected_qty`, `serials` from line rows (they are GRN keys). Gemini schema and cache keys unchanged | `ai/contracts.py:_openai_dialect` (a per-doc-kind variant), `ai/worker/hooks/vision.py` | schema version bump; cache keys of vLLM answers change; an accuracy run (D92 style) before adoption |
| C | Ask vLLM for compact JSON | `ai/worker/openai_compat.py:request_body` | owner's go for a `/version` request to the company server; the exact option depends on that version |
| D | If still short: split header fields and line rows into separate calls for dense pages | `ai/worker/hooks/vision.py`, `ai/vision.py` | biggest change; only if A–C do not fit |
| not a fix | a bigger cap, more retries, `presence_penalty` changes without a measured arm | — | — |

**Step A done:** `ai/answer_shape.py` now adds `names=<name>:<count>,…` to the `vision_answer_shape` log line (top 12 names, only names of the reader's fixed list; test in `tests/unit/test_ai_answer_shape.py`). The `tail_period` change was **not** made: an existing test relies on runaway whitespace showing as period 1, and `longest_ws_run` already tells the two cases apart.

**Step B measured, not built.** One empty line row in the vLLM form is 307 characters compact (411 pretty), 20 keys; the four GRN-only keys are 75 of those characters (~24%). `label` is 13 of 69 characters (~19%) of each field entry. At the observed ~2.2 characters per token that is a saving of very roughly a fifth of rows and fields. It is **not** enough on its own to fit ~52 fields plus rows in 4096 tokens, and it changes every vLLM request (cache keys) and the readings, so it needs an accuracy run first. Needs your decision before any code.

Recommended order: **A first** (cheap, removes the unknowns), then **B** (largest token saving with no content change), then **C**. Estimate of B's saving is a hypothesis until counted: write a unit test that counts schema tokens for an empty row before and after.

### Risks
- B and C change request bytes, so every cached vLLM answer misses once (budget: provider calls are budgeted per run).
- B drops keys the model may use for a GRN scanned as an invoice; scope it by document kind and keep GRN/PO schemas as they are.
- Changing the prompt or `presence_penalty` changes readings: needs an accuracy run first (D92). Do not do it as part of KI-006.
- Hard zeros: no part of this changes how a value becomes verified; AI values stay unverified.

### Tests
- Unit: `_openai_dialect` for invoice has no `label` on `fields` and no GRN-only line keys; for GRN/PO unchanged; Gemini schema byte-identical (existing schema tests in `tests/unit` for contracts must still pass).
- Unit: schema parity test (`VisionOutput` fields vs provider schema) updated, not weakened.
- Unit: `answer_shape` per-name counts on a synthetic answer; `tail_period` ignores a repeat under 64 characters.
- Unit: `request_body` includes the compact-JSON option only when configured.
- Integration (disposable DB, owner's go): one budgeted real read of the failing page, then compare `finish` and `vision_answer_shape`.
- Offline replay: existing accuracy-bench replay of stored answers must give identical readings.

## Item 4a. Unlabeled totals row (inv07, inv08: header totals not read)

### What is verified in code
- Header totals come from `extract.py:_summary_candidates`: (1) words after a known label (`find_labels` with `label_sets`, e.g. `GRAND TOTAL`, `TAXABLE VALUE`), (2) the amount aligned below a label, (3) `tables.summary_rows`. Every path needs a label word.
- `table.py:extract_tables` records a `SummaryRow` only when the row text matches `_STOP` / `_TOTAL_WORD` / `_SUMMARY`. A numeric-only footer row has none of those words.
- Such a row is neither an item (`has_text` needs ≥ 3 letters/digits in the description column) nor a summary row. With an open item above it, it falls to the block "for col in (TAX, TAX_RATE, TOTAL_INCL)": **numbers in those columns are appended to the previous item's values** (`table.py` ~line 664). So the totals row can also leak into the last line item.
- `verify.py:solve_document` accepts only combinations where taxable + taxes = grand total (within ₹1 of a whole rupee), scored by independent facts (lines sum, amount in words, CGST = SGST, standard rate). It already adds the sum of the lines as a taxable candidate, but needs a grand total candidate to combine with.

### Root cause (hypothesis, strongly suggested by the code; not seen on the invoices)
The totals row of inv07/inv08 has no usual label, so no candidate is created. Whether it leaked into the last line is **unknown**.

### Proposed fix
- In `table.py:extract_tables`: a row after the last item, with an amount but no description text, no quantity and no rate, and whose amount is ≥ the running sum of item amounts, is recorded as a `SummaryRow` with `label=None` and is **not** added to the previous item. Keep the existing break/continue behaviour.
- In `extract.py:_summary_candidates`: an unlabeled `SummaryRow` adds its amount only as a candidate for `taxable` and `grand` (as the existing `else` branch does); the solver, not the extractor, decides.
- Never set a header value from an unlabeled row alone: the value must be confirmed by `solve_document`'s arithmetic (status CONFIRMED/DERIVED) or stay unverified with a lowered confidence.

### Risks
- A row that is really a continuation line (no description) would be taken as a totals row. Mitigation: the "≥ running sum" test, "after the last item", and the solver's exact arithmetic.
- Changes under `reconbench_core/extraction` change the rules hash (not the engine rule paths, so no `ENGINE_VERSION` bump is required, but `RULES_HASH` must be updated). The accuracy bench must be re-run on the owner's truth set: previously correct invoices must not change.

### Tests
- Synthetic layout fixtures in `tests/parsers` (words with coordinates, no real data): (1) unlabeled totals row → taxable/grand read and confirmed; (2) same row, arithmetic inconsistent → value left unverified, nothing silently accepted; (3) the row's tax-column numbers do **not** change the last item; (4) a multi-line description continuation is not taken as totals; (5) labelled totals unchanged.
- Property test (existing `tests/property`): no accepted header total that fails taxable + tax = grand.
- Owner step (needs a go): run `make accuracy-bench`/`measure_extraction` on the local truth set and compare field counts before and after.

## Item 4b. Split CGST/SGST rate stored as the line rate (inv05) — DONE 3 Nov 2026 (uncommitted)

**Correction to the first version of this plan.** It blamed `extract.py:_line_numbers`. That was wrong for the layout reader: `_line_numbers` uses the rate only inside `verify.py`'s arithmetic, and the layout reader never writes a line `tax_rate` into the document (`extract.py` builds `raw` from description, item code, HSN, unit, quantity, rate, amount, discount only). A line `tax_rate` reaches the engine from the **AI reading** (`VisionLine` cell `tax_rate`) or from XML/CSV.

### Cause (verified mechanism, not seen on the invoice)
The model can read the Rate under the CGST heading (9%) as the line's `tax_rate`. The engine then uses 9 in `s6.tax_rate_diff` (against the PO and the slab list) and `s6.computed_tax` (`engine.py` ~965, ~1138): half the real 18%.

### Fix made
`reconbench_core/extraction/vision.py:_split_tax_rate`, used in `check_reading`'s line loop: when a line has `tax_rate`, `cgst`, `sgst` and `amount`, the two taxes are equal, and `amount × tax_rate / 100` equals one of them (not their sum), the `tax_rate` cell is **left out** (counted as `dropped["split_tax_rate"]`). Nothing is derived or invented: without the rate the engine falls back to the PO's rate for the line.
- Tests: `tests/unit/test_vision_core.py` (half rate dropped; whole rate, missing SGST, unequal taxes and IGST kept).
- Not done on purpose: summing the two rates into 18. That would create a value the model did not read.
- Risk: a line whose real combined rate happens to equal its per-tax rate is impossible when CGST = SGST > 0, so no correct value is dropped.
- Open: whether inv05's wrong rate really had this shape is **unknown** (its record is in `work/`); one real AI reading of that invoice would show it.

## Order and approvals
1. KI-006 step A, then B/C (B and C need your go for any real call and an accuracy run).
2. 4b (smaller, self-contained) before 4a (touches row classification, so riskier).
3. Both extraction fixes need the real invoices to reproduce the failure first: ask before opening anything under `work/` (suggested: copy the two or three relevant files to the scratchpad as on 1–2 Nov, mask values in anything written to the repo).
4. Each fix: tests first, then `ruff`, `mypy --strict` on core, `lint-imports`, `check_core_purity`, `check_rules_hash`, the offline suite, then your review. No commit, deploy or live change.
