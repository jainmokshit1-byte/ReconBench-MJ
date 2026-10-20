# Plan: LLM extraction call for scanned invoices (Gemini + local vLLM), and the remaining accuracy work

Date: 16 Oct 2026. Status: plan only; nothing here is implemented. Evidence: `docs/progress-accuracy-plan.md` (cited **PG §n**) and `docs/audit-llm-extraction.md` (cited **AU §n**). Earlier plans it builds on: `docs/accuracy-plan.md` (main plan, **AP**), `docs/accuracy-fix-plan-scan-extraction.md` (**FP**), `docs/diagnosis-scan-extraction.md` (**Dx**).

This file is written so a coding model can implement it without the conversation that produced it. Read §0 first.

---

## 0. Read first

### 0.1 Invariants (must hold after every work item)

1. AI never decides reconciliation status, payable amount, links or PO capacity.
2. LLM output is **untrusted evidence** until deterministic validators pass. An LLM-only value is never `verified`; verification happens only through the D42 rule in the consensus job (independent non-LLM reader + all validators + local crop reread), per opted-in field type.
3. `backend/reconbench_core` stays pure (`scripts/check_core_purity.py`, import-linter).
4. Provider SDKs and HTTP clients live only in `reconbench_app/ai/worker/` (import-linter; WI-G2 tightens the contract to cover `httpx` and `openai`).
5. The PDF sandbox has no network.
6. Money is `Decimal`.
7. Content-based links stay suggestion-only (D-6).
8. Corrections are versioned and audited; `document_version` is append-only; a re-parse or merge never silently replaces a manual correction.
9. Every AI result stores provider, model, prompt version, schema version and reader/image parameters (WI-BE5).
10. Settings: API keys are write-only (never returned to the browser). A local endpoint URL is validated **server-side** against a deployment-level allowlist (SSRF). Only users with `ai.manage` (role `manager`) edit AI settings. Every settings change writes an audit event.
11. Hard-zero metrics stay zero on every bench run: `FALSE_SAFE`, `FALSE_AUTO_LINKS`, `FALSE_MATCHED`, `WRONG_VENDORS_CREATED`.

### 0.2 Privacy and approvals

- Real samples and truth live only in `work/` (git-ignored). Never copy them into the repo, tests, fixtures or docs. Scripts print counts, booleans and masked patterns (`L` letter, `D` digit).
- No external call (Gemini) and no call to a local model server unless the owner wrote YES in the approval block of the request that started the work item, with the dataset and the page/call budget. Record the approval in `docs/decision-log.md` before the first call.
- Do not commit, push or open PRs. Treat `docs/ReconBench_*.md` as read-only.

### 0.3 Commands (Windows host, from the repo root unless stated)

| Purpose | Command |
|---|---|
| Offline Python suites | `cd backend; .venv\Scripts\python.exe -m pytest tests/unit tests/parsers tests/property tests/scenarios -q` (PowerShell). Baseline per D61: 826 passed, 1 xfailed. Never collect `tests/unit` and `tests/integration` in one run. |
| Static | `make check-python`; `uv run --project backend python scripts/check_core_purity.py`; `… scripts/check_rules_hash.py`; `… scripts/check_engine_version.py` |
| OpenAPI + generated types drift | `make check-static` (runs `export_openapi.py`, `npm run api:generate`, `git diff --exit-code`) |
| Web unit tests | from `web/`: `C:/Users/jainm/AppData/Local/nvm/v24.19.0/node.exe node_modules/vitest/vitest.mjs run` (Node 24 needed; Node 20 fails on undici) |
| Web typecheck/lint | `npm run typecheck --prefix web`; `npm run lint --prefix web` |
| Playwright | `npm run test:e2e --prefix web` (mocked APIs) |
| Integration (disposable PG) | `docker run --rm --name rb-ephemeral-pg -e POSTGRES_PASSWORD=phase2-ephemeral-local-only -p 127.0.0.1:15499:5432 postgres:16.15`, then alembic upgrade + `make queue-schema`, then e.g. `cd backend; .venv\Scripts\python.exe -m pytest tests/integration/test_ai_vision.py -q` |
| Bench (fresh, worker image, no network) | Git Bash: `MSYS_NO_PATHCONV=1 make accuracy-bench BENCH_ARGS="--workers 1 --out work/accuracy-reports/<name>.json"` |
| Bench, cached flash-lite vision scored | add `--vision-model gemini-3.5-flash-lite` (the default `gemini-3.8-flash` has no stored answers: PG §4 C6) |
| Attribution | `make accuracy-attribution REPORT=work/accuracy-reports/<name>.json` |

### 0.4 Baseline to beat (verified, PG §1, AU §4)

| Metric | Value |
|---|---|
| Headers, all 51 docs | 302/373 (81.0%) |
| Headers, scan-page docs (33) | 224/283 (79.2%): misses A 9, B 45, C 0, D 0, E 5 |
| Headers, 39 PDF docs: OCR / cached flash-lite vision / best-of-both bound | 242/313 / 271/313 / 302/313 |
| Lines: recall all docs / scan-page amount | 96/141 / 78/111 |
| Links | 3/10 (0 auto, 3 suggested) |
| Identity extra / duplicates caught | 8 / 0 of 2 |
| Hard metrics | 0/0/0/0 |
| Reference reports | `scanfix-truth-2.json` (`470682236b6d`), `after-d41-lite.json` (vision), `baseline-scanfix-attribution.json` |

### 0.5 Target vLLM server (owner input + one check, 16 Oct 2026)

| Fact | Value | Tag |
|---|---|---|
| Base URL (includes a path prefix behind a reverse proxy) | `http://43.242.226.63/vllm/v1` | owner |
| Access | over the company VPN from the owner's machine; plain HTTP; **no API key** | owner |
| Served model id (use exactly this string in requests) | `qwen3.5:9b` (`root`: `Qwen/Qwen3.5-9B`, `owned_by: vllm`) | verified: `GET …/vllm/v1/models` → HTTP 200 in 0.20 s |
| Context window | `max_model_len` **8192** tokens (prompt + images + output) | verified, same response |
| vLLM version | **0.23.0** (`GET http://43.242.226.63/vllm/version`; `system_fingerprint` `vllm-0.23.0-d7a4030c`) | verified 16 Oct |
| Thinking default | **ON**, and the server has **no reasoning parser**: without `enable_thinking: false` the model writes a "Thinking Process" text into `content`, `reasoning` is `null`, and a 200-token budget ran out (`finish_reason: length`, 4.6 s) | verified 16 Oct (synthetic one-line prompt) |
| Thinking off per request + JSON schema | works: `chat_template_kwargs {"enable_thinking": false}` + `response_format json_schema` returned `{"ok": true}` in 10 tokens, `finish_reason: stop`, 0.62 s | verified 16 Oct |
| Shared with other projects; GPU, image limits, request logging | UNKNOWN | owner |
| Reachable from the Docker `ai-worker` container (through the host VPN) | UNKNOWN | to test in WI-OPS1 |

Consequences, applied in the work items below:
- **The owner does not run or size hardware.** WI-OPS1 becomes a server checklist + smoke test against this URL (no compose service, no model download).
- **8192 tokens is tight.** A full page image costs about 3–4k tokens (inferred; measured in the smoke test), the v3 prompt + schema about 1.5–2k (inferred from the 5,875-byte payload, D40), and D41 answers averaged about 2,080 output tokens per document with a maximum of 4,399. So: **one page per call**, `max_tokens` computed per call as `max_model_len − estimated_prompt_tokens − 256`; if that is below 1,024 the call is skipped with `CONTEXT_TOO_SMALL` instead of sending a request the server will reject, image long side reduced (start at 1,600 px, tune in the smoke test), and the crop strategy (WI-BE8) becomes the likely default for dense table pages. Multi-page documents are read page by page and merged in the core (`merge_page_readings`, WI-BE2).
- **Shared server:** every request carries its own settings (thinking off, sampling, schema); concurrency 1 from ReconBench; latency varies with other users' load; `429/503` → backoff.
- **No key:** the key field in the UI is optional and hidden behind "Server needs a key".
- **Data path:** masked page images leave this machine to a company server over the VPN, in plain HTTP inside the company network. This is not a third-party provider, but it is still a data flow, so workspace consent is required. **Owner approval (16 Oct 2026): real invoices may be sent to this server.** Record it in `docs/decision-log.md` before the first real-page call.
- **Thinking must be turned off in every request.** The server default is on and there is no reasoning parser, so a missing `enable_thinking: false` puts free text into `content` and burns the token budget. The provider (WI-BE2) always sends it. If `content` does not parse, the result is `invalid_output`, and a `thinking_leak` counter is incremented when it starts with "Thinking Process" or `<think>`. Never raise `max_tokens` to work around it.
- **Speed (inferred from one call):** about 44 output tokens/s on the shared GPU, so a page answer of about 2,000 tokens takes about 45 s. Use a 300 s timeout and process pages in the background queue.

### 0.6 Owner decision, 16 Oct 2026: providers are separate

Gemini and the vLLM server are **independent alternatives**. Every AI request goes to exactly one provider. A workspace picks one vision provider at a time (`single`), or runs one provider in `shadow`. There is **no** `dual_agree` strategy, no request that calls both, and no merge of a Gemini answer with a vLLM answer. Where this file still mentions `dual_agree`, `secondary_reader`, LLM×LLM agreement, policies P3/P4, rule 3 of WI-BE7 or DP-L2, treat it as **out of scope**. The offline bench may still report Gemini and Qwen arms side by side for comparison, but the two answers are never combined.

---

## 1. Proposed amendments to `docs/accuracy-plan.md` (do not edit that file; the owner decides)

1. **New phase AC-L "LLM reader layer"**, split into four sessions. It supersedes FP WI-12 and WI-13 (which assumed Gemini only) and extends AP §3 reader C to "one or two vision LLM readers behind one interface".

| Phase | Scope | Exit gate |
|---|---|---|
| **AC-L0** provider layer | WI-G1, WI-G2, WI-BE1, WI-BE5, WI-EV0 | Gemini request bytes, cache keys (until BE5) and bench vision block unchanged; D59 strict xfail removed and passing; offline suite green |
| **AC-L1** local provider + settings + shadow | WI-BE2, WI-BE3, WI-BE4, WI-BE6, WI-FE1, WI-FE3 (part), WI-OPS1, WI-OPS2 | Both providers configurable and testable from the UI; shadow mode writes 0 document rows (test); hard metrics 0 |
| **AC-L2** evaluation and decision | WI-EV1, WI-EV2, WI-BE8 (as experiment arm) | Experiment reports for every approved arm with N=3 repeats; decision matrix filled; owner approves thresholds (§5 DP-L1) |
| **AC-L3** LLM values on scans | WI-BE7, WI-FE2, WI-FE3 (rest), WI-G3 | DoD §7 rows 1–2 on the expanded truth set, hard metrics 0, `trusted_but_wrong` 0, flag-off bench hash identical to pre-AC-L3 |

2. **Order.** AC-L0 and AC-L1 can run in parallel with FP WI-4…WI-8 (deterministic), because shadow mode writes nothing. AC-L3 comes after FP WI-4…WI-7 (so the OCR side of the merge is as good as it can be) and after WI-G1. AC-11 needs AC-L3 or an explicit owner decision (FP DP-4) that "flagged for a person" is acceptable.
3. **Plan text to correct** (PG §4): §1 "today" numbers (C1), §7 "paid tier only" vs D39/free-tier testing (C2), D-4 wording (C3), D-7 target (C4), and add AC-L to §9.
4. **D42 clarification:** two different vision LLMs count as **one** reader kind (`vision`) for D42 rule 1. Their agreement can make a value an *auto-accept candidate* (prefilled, still unverified) but never verifies it. Rationale: correlated errors (both read the same masked pixels; D41 already shows an OCR×vision agree-wrong).

---

## 2. Work items: deterministic (cheap, not done per PG §2.1)

These are open FP items. FP holds their full step lists; the summaries below give enough to start, and the acceptance is binding.

### WI-D1 Scan rules: FP WI-4 (doc-no/PO-ref), WI-5 (vendor name), WI-6 (totals/tax), WI-7 (line tables)

- **Goal / acceptance (copied from FP):**
  - WI-4: doc_no ≥ 47/51 (baseline 43/51), po_refs ≥ 14/15 (12/15); attribution doc_no B+E on PDF docs 6 → 0.
  - WI-5: vendor_name ≥ 45/51 (27/51); vendor-name B on PDF docs 22 → ≤ 4; `WRONG_VENDORS_CREATED` 0, `junk_reasons` empty. Also close the stage-C leftover seen with empty buyer entities (PG §2, AC-1 row).
  - WI-6: totals/tax B on PDF docs 29 → ≤ 6; grand_total ≥ 45/49, taxable ≥ 26/29, cgst ≥ 25/27, sgst ≥ 25/27.
  - WI-7: scan line amount 78/111 → ≥ 100/111; qty ≥ 92/102, description ≥ 90/108, rate ≥ 88/106; digital bundle PO/GRN lines 0/12 → ≥ 10/12; `line_spurious` 33 → ≤ 8.
  - All: hard metrics 0; `trusted_but_wrong` ≤ 1; attribution C = 0, D = 0.
- **Basis:** 45 of 59 scan header misses and 166 of 194 line-cell misses are stage B (AU §4, Dx §5).
- **Why before AC-L3:** the merge keeps OCR values when both pass validators; a better OCR side lowers disagreement and review load.
- **Effort:** S + M + M + L. **Depends on:** nothing open.

### WI-D2 Force OCR on poor or tiled text layers; >20-page warning; UTF-8 decode (FP WI-8 steps 3, 4 minimum, 5)

- **Goal:** no page with a scan image is read only from a poor text layer; no silent skip above 20 pages; no Windows decode loss.
- **Acceptance:** synthetic tests `tests/parsers/test_scan_images.py::test_tiled_page_is_ocrd`, `::test_poor_text_layer_is_ocrd`, `::test_25_pages_warns` pass; bench headers/lines not lower than §0.4; hard 0.
- **Files:** `backend/reconbench_app/extraction/local_hybrid.py` (`is_scanned` sum of image areas ≥ 50%, or one image ≥ 30% with < 20 alnum chars; `_tesseract` → `subprocess.run(..., text=True, encoding="utf-8", errors="replace")` at `:179`); `backend/reconbench_app/pdf_sandbox.py:565 _prefer_layout` (layout wins ties on pages with a ≥ 50% image); emit `ParseWarning("LAYOUT_PAGE_LIMIT")` where `:582` skips layout.
- **Tests:** use `tests/support/scan_images.py` (`render_invoice_image`, `scanned_invoice_pdf` with `tiles`, `text_layer="poor"`).
- **Risk/rollback:** runtime; constant flags per step. **Effort:** M. **Depends:** none.

### WI-D3 Parser-version guard and bulk re-parse (FP WI-9)

- **Acceptance:** `scripts/check_parser_version.py` fails CI when the hashed extraction code changes without a `PARSER_VERSION` bump (`ingestion.py:88`, now `phase4-parser-6`); `python -m reconbench_app.reparse_outdated --workspace <uuid>` dry-run prints counts by zone; integration test `tests/integration/test_reparse_outdated.py` proves a re-parse keeps manual corrections and verified refs.
- **Files:** new `backend/reconbench_app/PARSER_HASH`, `scripts/check_parser_version.py` (file set = bench `_CODE_PATHS` minus `vision*`/`consensus*`), `Makefile` `check-static`, new `backend/reconbench_app/reparse_outdated.py` (uses the existing per-file retry service; audit per file).
- **Effort:** S. **Depends:** bump once after WI-D1/WI-D2 land (FP DP-8: bulk per workspace, owner-triggered).

### WI-D4 Link-recall regression (FP WI-10)

- **Acceptance:** links ≥ 7/10 on the current truth with `FALSE_AUTO_LINKS` 0 and `false_suggestions` 0.
- **Steps:** add `--link-trace` to `scripts/accuracy_bench.py` (labels only per expected pair: candidate generated, vendor gate pass/fail/pending, anchor type, verified, ambiguity count, final state); find the failing stage of the 4 lost GRN→PO links (PG §7); fix in `reconbench_core/mapping/links.py` (`resolve_links` `:2593`, `_via_grn_resolution` `:804`, candidate retrieval). Keep D-6.
- **Effort:** M. **Depends:** none. This is the only lever for 4 of the 7 link misses; no LLM work moves them.

### WI-D5 Truth set v3 (FP WI-11, owner-led)

- **Acceptance:** `load_truth` accepts `reconbench-accuracy-truth/v3` (v2 + per-source `scan_type` ∈ `digital|searchable_scan|scan_clean|scan_tilted|scan_rotated|phone_photo|low_dpi`, `handwritten: bool`, per-line `uom`); at least the owner's revised target of 5 complete sets (FP §3a), ideally 15+; every production scan type has ≥ 5 documents; UOM truth on all lines.
- **Effort:** L (owner time). **Depends:** none; needed for every DoD claim.

### WI-D6 AC-3 page-split integration defect

- **Goal:** fix `MultipleResultsFound` in `_current()` with duplicate PO rows (D57: 2 failures in `tests/integration/test_page_split.py`).
- **Acceptance:** `pytest tests/integration/test_page_split.py -q` all pass on disposable PG; no other integration group regresses.
- **Effort:** S–M.

---

## 3. Work items: LLM feature

### WI-G1 Fix the consensus self-confirmation gap (prerequisite for any LLM value)

- **Goal:** an AI-sourced field value never counts as an OCR/rules reading in consensus.
- **Basis:** `tests/scenarios/test_hard_zero_guards.py:255` is `xfail(strict=True)`; `reconbench_core/extraction/consensus.py:434 rules_readings` treats `FieldSource.AI` as OCR (D59).
- **Steps:** in `rules_readings`, skip values whose `source` is `ai` (or map them to `ReaderKind.VISION` with the originating provider). Add `ReaderKind.VISION` identity so two LLM readings collapse to one reader kind (amendment §1.4). Remove the xfail mark.
- **Tests:** the existing scenario test passes without xfail; new unit test: two vision readings from different providers + no OCR → never L1.
- **Acceptance:** offline suite 827 passed, 0 xfailed (or baseline + new tests); bench hard 0; consensus block of `after-d41-lite`-style replay unchanged or with fewer L1 (never more).
- **Risk:** fewer auto-verified fields (correct direction). **Effort:** S. **Depends:** none.

### WI-G2 Import and dependency guard for HTTP clients

- **Goal:** only the AI worker may import `httpx`, `openai` or any provider SDK.
- **Steps:** in `backend/pyproject.toml` contract "Only the AI worker may load the Gemini SDK or AI worker code" (`:120-161`) add `"httpx"`, `"openai"`, `"requests"` to `forbidden_modules`. Run `cd backend && uv run lint-imports`; if a listed module imports `httpx` legitimately (none found in audit, AU §2), move that code into the worker.
- **Acceptance:** `lint-imports` 3 contracts kept, 0 broken. No new dependency: use plain `httpx` (already present) for vLLM; do **not** add the `openai` SDK.
- **Effort:** S.

### WI-BE1 Provider interface; Gemini behind it with no behavior change

- **Goal:** the runner can hold several providers; the vision request is built by a `VisionReader`; Gemini traffic is byte-identical.
- **Basis:** AU §2 (worker hard-wired at `ai/worker/main.py:80`; `ProviderRequest.thinking_level: Literal["low"]` at `ai/provider.py:40`).
- **Files / functions:**
  - `backend/reconbench_app/ai/provider.py`: widen `thinking_level` to `Literal["minimal","low","medium","high"] | None` (default `"low"`); add `options: Mapping[str, Any] = field(default_factory=dict)` (provider-specific: sampling, template kwargs); add to `ProviderResult` `latency_ms: int | None = None`, `finish_reason: str | None = None`, `model_reported: str | None = None`, `reasoning_present: bool = False`.
  - `backend/reconbench_app/extraction/vision.py`: extend `VisionReaderInfo` with `prompt_variant: str`, `schema_dialect: Literal["gemini","openai_json_schema"]`, `box_order: Literal["yx","xy"]`, `billing: bool`. Keep `vision_gemini` values equal to today.
  - New `backend/reconbench_app/ai/worker/providers.py`: `build_providers(settings: AiWorkerSettings) -> dict[str, AiProvider]` (today: `{"gemini": GeminiProvider()}`).
  - `backend/reconbench_app/ai/worker/runner.py`: constructor takes `providers: Mapping[str, AiProvider]`; in `run`, `provider = self.providers.get(str(request["provider"]))`; if missing → `ledger.skip(..., "PROVIDER_NOT_CONFIGURED")`; pass `provider_name=request["provider"]` to `request_blocker`.
  - `backend/reconbench_app/ai/worker/main.py:74-85`: `providers=build_providers(settings)`.
  - `backend/reconbench_app/ai/runtime_checks.py:149 request_blocker`: unchanged signature; it already compares `settings.provider` with the request.
- **Steps:** (1) Before touching code, add a golden test that records the exact JSON body the Gemini adapter sends for a synthetic vision payload via `httpx.MockTransport` (pattern: `tests/unit/test_ai_gemini_adapter.py:63`) and the `cache_key` for a fixed synthetic payload; commit the expected values inside the test. (2) Refactor. (3) Golden test must still pass unchanged.
- **Tests:** golden body + cache key; runner dispatch by provider (two fake providers); missing provider → skip code.
- **Acceptance:** offline suite green with 0 new failures; `tests/integration/test_ai_vision.py` and `test_ai_settings_api.py` pass on disposable PG; `lint-imports` kept. With approval to run benches: cached replay `MSYS_NO_PATHCONV=1 make accuracy-bench BENCH_ARGS="--workers 1 --vision-model gemini-3.5-flash-lite --out work/accuracy-reports/acl0-be1.json"` has the same `vision_hash` and main `report_hash` as the same command before the change.
- **Risk/rollback:** revert the listed files. **Effort:** M. **Depends:** WI-G2 recommended first.

### WI-BE5 Provenance and cache key v2

- **Goal:** every AI result records provider, model, prompt, schema and reader/image parameters; the cache key includes all of them.
- **Basis:** `ai/cache.py:30-51` lacks provider and reader params; `ai_request` has no reader-params column (AU §2).
- **Schema (migration `0025_ai_reader_params`; follow the migration checklist in `docs/accuracy-implementation-plan.md` §0.7):** `ai_request.reader_params jsonb NOT NULL DEFAULT '{}'`; `ai_cache.provider ai_provider NULL`, `ai_cache.schema_version text NULL`, `ai_cache.reader_params jsonb NULL`. Update `db/metadata.py`, `db/schema_contract.json`, `docs/schema-errata.md`.
- **`reader_params` (canonical JSON, sorted keys):** `{"reader": "vision_gemini", "render_version": "vision.render.v1", "max_side": 2048, "jpeg_quality": 90, "mask_version": "image_mask.v1", "media_resolution": "high" | null, "max_pixels": null | int, "thinking": "low" | "off", "sampling_profile": "provider_default" | "qwen_card_nonthinking", "prompt_variant": "...", "pass": "full" | "crop:header" | "crop:table" | "crop:totals"}`.
- **Cache key:** new function `cache_key_v2(provider, hook, model, prompt_version, schema_version, redaction_version, reader_params_digest, payload_digest)`; keep `cache_key` (v1) for text hooks H1–H4 so their caches stay valid.
- **Bench:** `scripts/accuracy_vision.py:209 request_key` uses the same v2 fields. Note: this changes every vision cache key, so stored flash-lite answers will no longer match. Before landing, copy the cache (`work/accuracy-cache/vision` → `work/accuracy-cache/vision-v1`) and give the bench a `--vision-cache-compat v1` read-only mode that maps v1 entries when provider=`gemini`, model, prompt/schema versions match and reader params equal the v1 defaults. This keeps the D41 evidence usable without re-sending.
- **Tests:** unit: different provider / thinking / sampling / pass → different key; same → same. Migration test (upgrade from 0024, downgrade-free policy as in repo). Compat test for v1 mapping.
- **Acceptance:** `make check-static` clean (schema contract regenerated); migration safety checks pass on disposable PG (`make phase2-migration-safety`, drift, catalog, indexes, table count); bench replay with `--vision-cache-compat v1` reproduces `after-d41-lite.json` vision numbers (header 271/313, lines 80/123).
- **Effort:** M. **Depends:** WI-BE1.

### WI-BE4 Prompt + schema v4 (shared), provider prompt variants, standalone validators

- **Goal:** one JSON contract for both providers with explicit unreadable values, and validators that check LLM values without needing OCR to be consistent.
- **Basis:** AU §3 P6 (omission ≠ unreadable), AU §7 (vision arithmetic only checked when OCR totals are consistent: `reconbench_core/extraction/vision.py:626`, `:666-667`).
- **Schema `vision.schema.v4`** (`backend/reconbench_app/ai/contracts.py`): v3 plus top-level `unreadable: list[{name, page, box_2d}]` (a printed value the model cannot read with certainty; no value given). Keep: values as printed, party roles in field names (`supplier_*`, `buyer_*`; `reconbench_core/extraction/vision.py:73-79`), handwritten/stamped marks only in `annotations`, per-field `page` + `box_2d` + `label`. Generate two provider dialects from one Pydantic model: `provider_schema(dialect="gemini")` (no `maxItems`, as today) and `provider_schema(dialect="openai_json_schema")` (`additionalProperties: false`, every property in `required`, nullable via `anyOf` only where needed). The strict local parse (`parse_output`) is the authority for both.
- **Prompts** (`backend/reconbench_app/ai/prompts.py`): `vision.prompt.v4.gemini` (v3 text + unreadable rule) and `vision.prompt.v4.qwen` (shorter rules, one synthetic JSON example built from `SYN-INV-0001`-style made-up values, an explicit coordinate rule "box_2d = [ymin, xmin, ymax, xmax], integers 0–1000 relative to this image"). `PROMPT_VERSIONS["h6_vision"]` becomes a mapping per reader; the consent fingerprint includes the variant (re-consent required, as after D38/D40).
- **Standalone validators** in `reconbench_core/extraction/vision.py::check_reading` (pure): taxable + taxes ± round-off = grand (±₹1); Σ line amounts = taxable (±₹1 or the `total_vs_expected` tolerance); line qty × rate ≈ amount (existing `_line_consistent`); date within the D42 window (reuse from `reconbench_core/extraction/consensus.py`). A failure does **not** delete the value; it marks the item `check_failed:<reason>` so it stays a visible suggestion but is excluded from merge (WI-BE7). Count unreadable entries.
- **Tests:** synthetic unit tests for each validator and for v4 parsing in both dialects; golden JSON fixtures `backend/tests/fixtures/vision_v4/{gemini_ok.json, qwen_ok.json, qwen_xy_boxes.json, malformed.json, truncated.json}` (made-up values only).
- **Acceptance:** offline suite green; rules hash updated (`check_rules_hash.py --update` per repo practice) and engine version unchanged unless a rule path changes; consent fingerprint test: with vision off the legacy fingerprint is byte-identical.
- **Effort:** M. **Depends:** WI-BE1.

### WI-BE2 `vision_openai_compat` provider for vLLM (Qwen3.5-9B)

- **Goal:** the AI worker can send a vision request to a local OpenAI-compatible server, with the same ledger, gate, consent and checks as Gemini.
- **New file** `backend/reconbench_app/ai/worker/openai_compat.py`, class `OpenAICompatProvider` (`name = "vllm"`), constructor `(base_url: str, allowed_prefixes: frozenset[str], *, inner_transport: httpx.BaseTransport | None = None)`. Client: `httpx.Client(transport=SingleAttemptTransport(inner or HTTPTransport(retries=0)), follow_redirects=False, trust_env=False, timeout=httpx.Timeout(connect=5, read=request.timeout_ms/1000, write=30, pool=5))`.
- **Request** `POST {base_url}/chat/completions` (`base_url` already ends in `/v1`, e.g. `http://43.242.226.63/vllm/v1`; never append a second `/v1`):

```json
{
  "model": "<model name from settings, e.g. qwen3.5:9b>",
  "messages": [
    {"role": "system", "content": "<system_instruction>"},
    {"role": "user", "content": [
      {"type": "text", "text": "<input_text>"},
      {"type": "text", "text": "Page 1"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<...>"}}
    ]}
  ],
  "max_tokens": 3072,
  "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
  "presence_penalty": 1.5, "repetition_penalty": 1.0,
  "seed": 20261016,
  "chat_template_kwargs": {"enable_thinking": false},
  "response_format": {"type": "json_schema",
                      "json_schema": {"name": "vision_reading", "schema": {"...": "v4 openai dialect"}, "strict": true}},
  "stream": false
}
```

  Sampling is the Qwen3.5 model-card **non-thinking general** profile (`qwen_card_nonthinking`; AU §10). `top_k`, `min_p`, `repetition_penalty` are vLLM extra parameters: verify on the pinned vLLM version. `presence_penalty 1.5` may distort repeated digits in JSON; WI-EV2 tests a `qwen_card_nonthinking_pp0` arm (presence 0.0). Do not hard-code a temperature of 0.
- **Response:** `choices[0].message.content` (string; parse with the strict v4 model); `finish_reason == "length"` → `invalid_output` with `at_output_cap=true`; usage from `usage.prompt_tokens` / `usage.completion_tokens`; if `message.reasoning_content` (or `reasoning`) is non-empty, set `reasoning_present=True` and log a counter (thinking should be off). Boxes: if the reader's `box_order == "xy"`, convert `[x1,y1,x2,y2]` → `[ymin,xmin,ymax,xmax]` before the core check (decided by the WI-OPS1 image smoke test).
- **Error mapping** (`map_openai_compat_error`, new, in the same file):

| Condition | Code | Delivery | Note |
|---|---|---|---|
| DNS/connect refused/connect timeout | `PROVIDER_UNAVAILABLE` | `not_sent` | request released; one deferred retry after 60 s |
| read timeout | `timeout` | `responded` for `billing=False` readers | no "uncertain" state for local: nothing is billed; the runner uses `VisionReaderInfo.billing` |
| 400 (e.g. context too long, bad schema) | `INVALID_REQUEST` | `responded` | keep a 300-char sanitized message for diagnostics (digits ≥ 6 masked) |
| 401/403 | `bad_key` | `responded` | |
| 404 (unknown model) | `MODEL_UNAVAILABLE` | `responded` | |
| 429 / 503 | `rate_limited` / `PROVIDER_UNAVAILABLE` | `responded` | cooldown 30 s |
| 5xx other | `error` | `responded` | |
| body not JSON / no choices | `invalid_output` | `responded` | |

- **Health:** `health() -> HealthResult(ok, latency_ms, models: tuple[str, ...])` via `GET {base_url}/models` with a 5 s timeout; it also returns `max_model_len` per model (verified on the target server), which the provider uses for the `max_tokens` budget. Used by the provider test (WI-BE3) and a 60 s heartbeat that writes `ai_provider_config.last_health_*` (WI-BE3 columns).
- **Endpoint policy (SSRF), pure module** `backend/reconbench_app/ai/endpoint_policy.py` (no `httpx` import): `normalize_base_url(url) -> str` (scheme `http|https`; no userinfo, query or fragment; path normalised without a trailing slash, no `..` segments; port explicit or scheme default) and `check_allowed(url, allowlist) -> str` (exact match of the normalised base URL, i.e. origin **and** path prefix, case-insensitive host). Called by the API on save **and** by the worker before every request.
- **Limits per provider** (new `CallKindLimits` overrides inside `AiLimits`, e.g. `h6_vision_local`): `max_pages` **1 per call** (the target server has `max_model_len` 8192; multi-page documents are read page by page and combined by a new pure core function `merge_page_readings` in `reconbench_core/extraction/vision.py`), `max_output_tokens` computed per call (see §0.5), image long side 1,600 px to start, `call_timeout_seconds` 300 (raise `transport_guard._MAX_TIMEOUT_MS` to 300 000 only for `billing=False` readers), `max_concurrency` 1. Ledger `reserve` counts in-flight per provider.
- **Deployment gate:** `ai/gate.py` gets `vllm_mode: Literal["disabled","synthetic_only","enabled"]` from `RB_AI_VLLM_CALLS` (default `disabled`; `synthetic_only` mirrors the Gemini gate so smoke tests can run before real pages are approved) and `vllm_endpoints` from `RB_AI_VLLM_ENDPOINTS` (comma list of base URLs, e.g. `http://43.242.226.63/vllm/v1`). `refusal(..., external: bool)`: external calls use the existing rules; vLLM calls need `vllm_mode` to allow the subject and an allowlisted base URL. No approval ref is needed (company server), but workspace consent is, and real pages need the owner's written YES.
- **Registry:** `extraction/vision.py` adds `vision_openai_compat` (`provider="vllm"`, `external=False`, `billing=False`, `schema_dialect="openai_json_schema"`, `prompt_variant="vision.prompt.v4.qwen"`). `RB_VISION_READER` stays a deployment switch: allowed values `off`, `vision_gemini`, `vision_openai_compat`, `all` (new default `all` keeps both available; `vision_gemini` keeps today's behaviour).
- **Ledger/cost:** tokens and latency recorded as for Gemini; cost `NULL` unless the price file lists the local model (owner may enter 0).
- **Tests:** unit with `httpx.MockTransport` (request body shape, thinking off, sampling, schema; every error row above; redirect refused; non-allowlisted origin refused before any I/O; `trust_env` off); integration with a stdlib mock server `backend/tests/support/mock_openai_server.py` (`http.server` on 127.0.0.1, canned golden JSON, configurable delay/500/malformed) for prepare → request → answer → cache → consensus enqueue.
- **Acceptance:** all new tests pass; `lint-imports` kept; no change in Gemini golden tests.
- **Risk:** vLLM extra-parameter names differ by version → pin version (DP-L6). **Effort:** L. **Depends:** WI-BE1, WI-BE4, WI-BE5, WI-G2.

### WI-BE3 Settings model, API, migration, consent, audit

- **Goal:** a manager configures Gemini and the local server separately, chooses a reader strategy, and tests each provider, without changing behaviour for existing Gemini-only workspaces.
- **Migration `0026_ai_providers`** (separate from 0025; `ALTER TYPE … ADD VALUE` must run in an autocommit block: `with op.get_context().autocommit_block():`):
  - `ALTER TYPE ai_provider ADD VALUE IF NOT EXISTS 'vllm'`.
  - `CREATE TYPE ai_reader_strategy AS ENUM ('off','shadow','single')`; `CREATE TYPE ai_apply_mode AS ENUM ('suggest','merge')`.
  - Table `ai_provider_config`: `id uuid pk`, `workspace_id uuid not null fk`, `provider ai_provider not null`, `enabled bool not null default false`, `model text`, `base_url text` (CHECK `provider = 'vllm' OR base_url IS NULL`), `key_secret_id uuid fk secret`, `limits jsonb not null default '{}'`, `last_test_at timestamptz`, `last_test_ok bool`, `last_health_at timestamptz`, `last_health_ok bool`, `version int not null default 1`, `updated_by uuid`, `updated_at timestamptz not null default now()`; `UNIQUE (workspace_id, provider)`; grants/RLS exactly like `ai_setting` (see `docs/database-roles-and-seeding.md`).
  - `ai_setting` add `reader_strategy ai_reader_strategy not null default 'off'`, `primary_reader ai_provider`, `apply_mode ai_apply_mode not null default 'suggest'`; CHECK: `single|shadow` ⇒ `primary_reader` not null; `off` ⇒ `primary_reader` null.
  - **Backfill:** for every `ai_setting` with `provider = 'gemini'`: insert `ai_provider_config(gemini, enabled = (mode <> 'rules_only'), model, key_secret_id)`; set `reader_strategy = 'single', primary_reader = 'gemini'` when `vision_enabled`, else `'off'`. `vision_enabled` stays and is kept equal to `reader_strategy <> 'off'` for one release (read by old code paths), then dropped in a later migration.
- **Mapping of existing values (documented in the UI help text and decision log):**

| `mode` (unchanged meaning: which documents) | `reader_strategy` (new: which readers, how used) | Behaviour |
|---|---|---|
| `rules_only` | any | No LLM call. |
| `ai_assisted` | `off` | Today's H1 text hook on documents needing verification. |
| `ai_first` ("AI first" in the UI) | `off` | Today's H1 on every PDF ≤ 8 pages; suggestions only. |
| `ai_assisted` / `ai_first` | `single` + `gemini` | **Identical to today's "Read page images" ticked** (H6 replaces H1). |
| `ai_assisted` / `ai_first` | `single` + `vllm` | H6 through the local server. |
| `ai_assisted` / `ai_first` | `shadow` + primary (+ optional secondary) | Readers run; answers stored and compared; **no suggestions shown, nothing written** to documents. |
| any | `apply_mode = merge` | Only with `single` or `dual_agree`, after AC-L2 approval (WI-BE7). Default `suggest`. |

  Text hooks H1–H4 keep using `ai_setting.provider/model/key` (Gemini). No silent change: a migrated workspace produces the same consent fingerprint and the same requests (test).
- **API** (`backend/reconbench_app/api/ai.py`, `ai/settings_service.py`):
  - `GET /ai/settings` adds `providers: [{provider, enabled, model, base_url, key: {set, hint, storage}, limits, external, data_destination, last_test: {at, ok}, health: {at, ok}}]`, `reader_strategy`, `primary_reader`, `secondary_reader`, `apply_mode`, `vllm_calls: "disabled"|"synthetic_only"|"enabled"`, `vllm_endpoints_allowed: [base URLs]`.
  - `PUT /ai/settings` accepts `providers: [{provider, enabled?, model?, base_url?, api_key?, key_storage?, limits?}]` plus the strategy fields. Validation: Gemini model regex unchanged (`settings_service.py:73`); local model `^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`; `base_url` via `endpoint_policy.check_allowed` (422 `endpoint_not_allowed`); keys write-only `SecretStr`; `dual_agree` needs two enabled providers; `merge` refused (422 `merge_not_enabled`) until a deployment flag `RB_AI_LLM_MERGE=enabled` is set (WI-BE7).
  - `POST /ai/test/provider` gains a body `{provider: "gemini"|"vllm", kind: "text"|"image"}` (default `{gemini, text}` = today). The API only queues; the AI worker runs: for local → health (`/v1/models`), a text JSON smoke, and (`kind=image`) a tiny **synthetic** page (bytes embedded in `ai/fixtures.py`, generated once from `tests/support/scan_images.py::render_invoice_image` with made-up values; digest added to `SYNTHETIC_FIXTURE_DIGESTS`). `GET /ai/requests/{id}` returns state, latency, model list (names only) and a pass/fail per step.
  - `GET /ai/consent-preview` adds `providers` and per-provider `data_destination`: Gemini = "Google Gemini API: masked page images leave this server"; vllm = "Company vLLM server at <base URL> (over the company VPN): masked page images leave this machine but not the company network". The consent fingerprint adds `reader_strategy`, providers (+ model, + base URL for vllm) and `apply_mode`, **only when they differ from the legacy mapping**, so legacy fingerprints stay byte-identical (test).
  - `GET /ai/usage?provider=` per-provider totals (calls, tokens, latency p50/p95 today).
  - Audit events: `ai.provider_config_save` (before/after without key; includes base_url origin), `ai.reader_strategy_change`, `ai.apply_mode_change`, existing `ai.test_requested` with provider.
  - Roles: every write needs `ai.manage` (only `manager`, `authorization.py:41-54`).
- **OpenAPI:** `make openapi`, then replace the hand-written interfaces in `web/src/api/ai.ts` with types derived from `web/src/api/generated.ts` (see WI-FE3 drift check).
- **Tests:** integration `tests/integration/test_ai_settings_api.py` extended: migration backfill equivalence; key never in any response (search the JSON for the posted key); SSRF cases (`http://169.254.169.254`, `http://user@vllm:8000`, `http://vllm:8000/x?y`, redirecting server, IPv6 literal, uppercase host); non-manager 403; audit rows; consent fingerprint unchanged for migrated workspaces; `dual_agree` validation.
- **Acceptance:** above tests pass on disposable PG; migration safety/drift/catalog/index/table-count checks pass; `make check-static` clean.
- **Effort:** L. **Depends:** WI-BE1, WI-BE2 (endpoint policy), WI-BE5.

### WI-BE6 Shadow mode and comparison store

- **Goal:** run one or two LLM readers in production-like conditions with **no** effect on documents, and measure agreement.
- **Schema (migration `0027_reader_comparison`):** append-only `reader_comparison`: `id`, `workspace_id`, `document_version_id`, `ai_request_id`, `provider`, `model`, `prompt_version`, `schema_version`, `reader_params_digest`, `created_at`, `counts jsonb` (`fields_total, agree_ocr, disagree_ocr, llm_only, ocr_only, unreadable, check_failed_by_reason, dropped_by_reason, rows`), `labels jsonb` (`[{field_key, label}]`, labels only, never values). Report role cannot read it (same as `consensus_decision`).
- **Flow:** `reader_strategy = shadow` → enqueue for primary (and secondary) → worker validates → `apply` writes the cache entry and one `reader_comparison` row; **no** consensus run, **no** suggestion view (`ai-suggestions` returns state `not_applicable`, code `SHADOW`), page bytes purged.
- **Report:** `python -m reconbench_app.ai.shadow_report --workspace <uuid> [--since YYYY-MM-DD]` and `GET /ai/shadow-report` (`ai.manage`): aggregates only (per provider × field: agree/disagree/llm-only/unreadable; per-provider latency p50/p95, failures by code). For dual readers: LLM×LLM agreement and LLM×LLM×OCR.
- **Tests:** integration: shadow run writes 0 rows to `document_version`, `doc_line`, `consensus_run`, `vendor`, `document_link` (row counts before/after equal); report contains no value strings (assert against the synthetic values).
- **Acceptance:** tests pass; hard metrics unaffected (bench). **Effort:** M. **Depends:** WI-BE3.

### WI-BE7 LLM values become document values (merge), fallback, disagreement routing

- **Goal:** on scanned pages, validated LLM values fill or challenge OCR values in a new document version with full provenance; verification stays with consensus (D42).
- **Gate to start:** AC-L2 decision (DP-L1 thresholds met by the chosen arm) and WI-G1 done.
- **Pure core** `backend/reconbench_core/extraction/llm_merge.py`:
  `merge_readings(ocr: Document, readings: Sequence[CheckedReading], policy: MergePolicy) -> MergeResult` where `CheckedReading = (reader_id: str, check: VisionCheck)`, `reader_id = "llm:<provider>:<model>:<prompt_version>"`. Per header field and per line cell:
  1. LLM value passes validators and equals the OCR value (normalised) → keep, `supporting=(ocr, reader_id…)`.
  2. LLM passes; OCR missing or failing a validator → LLM value, `source=AI`, confidence capped at **0.69** (below the 0.70 review floor), `evidence.origin=reader_id`.
  3. In `dual_agree`: both LLMs give the same normalised value and pass validators, OCR missing/failing → as rule 2 plus `auto_accept_candidate=true` (still unverified).
  4. Both pass and disagree with OCR → keep OCR value, add the LLM value as an alternative, add the field to `review_fields`, warning `LLM_DISAGREEMENT`.
  5. LLM `unreadable`, omitted, or `check_failed:*` → OCR value (or none) + flag.
  6. Annotations (handwriting/stamps) never enter fields; references from annotations stay suggestions.
  Lines: align with existing `align_lines` (`reconbench_core/extraction/vision.py:900`); per cell as above; an LLM-only row is added unverified.
- **Where it runs:** a new Procrastinate task `merge_llm_reading` in the **general** worker (`backend/reconbench_app/jobs.py`), queued by the AI worker's `apply` when `apply_mode = merge` (no SDK import; import-linter test). For `dual_agree` it waits until both requests are terminal or 10 minutes pass, then merges what exists.
- **Persistence:** append a `document_version` with `extraction_method = 'ai_fallback'` (enum value already exists: `db/metadata.py:1665`, `reconbench_core/schema/__init__.py:118`; no enum migration), `derived_from_version_id` = OCR version, per-field evidence `{origin, schema_version, reader_params_digest, ai_request_id, page, box}`. The new version becomes current only if the OCR version has no manual correction and no verified field that the merge would change; otherwise it is stored as an alternative (D19 guard). Vendor/link/recon recompute as for any new version.
- **Verification:** only the existing consensus job may raise verification, under D42 with the WI-G1 reader-kind rule.
- **Fallback:** timeout, malformed JSON, server down, budget exceeded, stale source → no merge; the OCR version stays current; nothing else changes (the LLM path is asynchronous).
- **Flag/rollback:** deployment `RB_AI_LLM_MERGE` (default `disabled`) and workspace `apply_mode` (default `suggest`). Turning either off stops new merges; existing versions remain in history; a "restore OCR version" action appends a copy of the OCR version.
- **Tests:** core unit test per rule 1–6; property test: no field with `source=AI` and only LLM readers has confidence ≥ 0.70 or `verified=true`; integration: cached answer → merge task → new version flagged; manual correction present → alternative only; consensus with crop agreement → only opted-in field types verified; flag off → bench hash identical.
- **Acceptance:** DoD §7 on the v3 truth set with the chosen arm; hard 0; `trusted_but_wrong` 0.
- **Effort:** L. **Depends:** WI-G1, WI-BE2…BE6, WI-EV2 decision.

### WI-BE8 Cropped two-pass strategy for the local model

- **Goal:** give a 9B model higher-resolution views of the dense regions (header, table, totals) without sending more pixels per call than it handles well.
- **Design (variant A, default; no extra LLM call):** during `prepare_vision_pages` (general worker, sandbox), cut three bands per page from the **masked** page image using the deterministic layout: header band (top 35% or above the table heading), table band (from the table heading to the summary rows found by the layout engine), totals band (summary rows to page end). Store them as extra rows in `document_page_image` with `render.crop = {page, x0, y0, x1, y1, band}`; long side up to 1600 px per crop. One request per document carries the crop images in band order (`Page 1 header`, `Page 1 table`, …). The model returns boxes relative to each crop; core `map_crop_box(box, crop) -> page box` (pure, `reconbench_core/extraction/vision.py`) converts them before `check_reading`. Variant B (LLM finds regions in pass 1) is an experiment arm only.
- **Tests:** core unit tests for `map_crop_box`; sandbox test that crops come from masked bytes (mask boxes inside a crop stay black); synthetic layout without a table heading falls back to full page.
- **Note (16 Oct):** with the server's 8,192-token window, crops are the likely default for dense table pages (more pixels per region within the budget).
- **Acceptance:** used only if WI-EV2 shows the crops arm beats full-page on scan-page lines by ≥ 5 points with no rise in wrong-when-answered. **Effort:** M. **Depends:** WI-BE2, WI-BE4.

### WI-EV0 Offline re-score of stored vision answers (smallest approved run)

- **Needs:** approval line "Run existing local tests/benchmarks: YES". No network, no provider.
- **Goal:** fill AU §4 UNKNOWNs: vision per scan type, per line cell (qty/rate/HSN), miss classes M1–M6 where derivable, drop-reason correctness (was a dropped value correct?), not-in-OCR-text rate.
- **Steps:** extend `scripts/accuracy_attribution.py` with `--vision-cache <dir> --vision-model gemini-3.5-flash-lite` (read-only; monkeypatch provider sends to raise, as the attribution script already does for parsing); for each truth field: vision value, OCR value, presence of the truth value in OCR page text, drop reason; classify M1 (value present in OCR text, vision wrong), M3 (box in mask / low-DPI page), M4 (correct value dropped by a core check or the whole answer rejected), M5 (truth absent from OCR text and vision ≠ truth but OCR = vision → flag for owner review), M6 (party field holds the other party's value). M2 cannot be separated without prompt variants (UNKNOWN until WI-EV2).
- **Output:** `work/accuracy-reports/ev0-vision-attribution.json` with counts only.
- **Caveat:** stored answers are found by the request payload digest, which includes the masked page-image hashes; the page cache is keyed by `scripts/accuracy_vision.py:70-75 _VISION_CODE` (`vision_pages.py`, `extraction/vision.py`, `local_hybrid.py`, `ai/redaction.py`). The 9 Oct cache replay found the answers (Dx §4); whether they still map after the D59–D61 edits is UNKNOWN. If 0 answers map, stop and report; recovering them needs the WI-BE5 compat reader or a re-send (approval).
- **Acceptance:** command `make accuracy-attribution REPORT=work/accuracy-reports/scanfix-truth-2.json ATTR_ARGS="--vision-cache /app/work/accuracy-cache/vision --vision-model gemini-3.5-flash-lite --json /app/work/accuracy-reports/ev0-vision-attribution.json"` (Git Bash, `MSYS_NO_PATHCONV=1`; `ATTR_ARGS` is already passed through by `Makefile:59-60`; paths are inside the container) completes with exit 0 and the totals equal `after-d41-lite.json` (header 271/313).
- **Effort:** S–M. **Depends:** none.

### WI-EV1 Evaluation harness for two providers

- **Goal:** one bench that compares Tesseract+rules, Gemini variants and Qwen variants on the same truth set, with repeats and agreement policies.
- **Flags** (`scripts/accuracy_bench.py`, `scripts/accuracy_vision.py`): `--vision-provider {gemini,vllm}`, `--vision-base-url URL` (local only; must be in `RB_AI_VLLM_ENDPOINTS`), `--vision-model`, `--vision-thinking {off,minimal,low,medium,high}`, `--vision-sampling {provider_default,qwen_card_nonthinking,qwen_card_nonthinking_pp0}`, `--vision-strategy {full,crops}`, `--repeat N` (repeat index in the cache key), `--vision-primary-sim` (runs `merge_readings` from WI-BE7 core; available once that pure function exists, otherwise the WI-13 rules), `--agreement-policy {single,llm_x_ocr,llm_x_llm,llm_x_llm_x_ocr}` with `--second-vision-report PATH`.
- **Network:** new Make target `accuracy-bench-vllm` that runs the worker image with network access (the vLLM server is reached through the host's company VPN; `--network none` cannot be used). The bench refuses any `--vision-base-url` not in `RB_AI_VLLM_ENDPOINTS` and makes no other outbound call (the Gemini client is not constructed in this mode). Gemini arms keep `accuracy-bench-vision`.
- **Metrics (report block `vision_eval`, counts only):** header and line-cell accuracy (amount, qty, rate, description, HSN, UOM when truth has it), scan-page and all-PDF, per scan type; auto-accept subset precision, coverage and Wilson 95% lower bound per field type; false-safe under simulation; unreadable→null rate (listed vs silently omitted); wrong-when-answered; not-in-OCR-text rate (hallucination upper bound: OCR can miss real values); agree-wrong LLM×OCR and LLM×LLM (correlated-error measure); tokens in/out/thought, cost (only from `RB_AI_PRICE_FILE`), latency p50/p95, failures by code; repeat variance = share of fields whose normalised value changes across the N runs.
- **Tests:** unit tests with synthetic truth + recorded fake answers for every metric; the default bench output (no new flags) keeps its report hash.
- **Effort:** M. **Depends:** WI-BE2, WI-BE4, WI-BE5.

### WI-EV2 Experiments (owner-run; approvals required)

- **Arms** (same truth set, N=3 repeats each unless noted; masked pages; page/call caps from the approval):

| Arm | Reader | Settings | Approval needed |
|---|---|---|---|
| E0 | Tesseract + rules | current | none (cached) |
| E1 | Gemini Flash-Lite (`gemini-3.5-flash-lite`) | thinking low; media high; temperature/top_p/top_k not sent | external calls YES |
| E2 | Gemini Flash-Lite | thinking medium | same |
| E3 | Gemini Flash (`gemini-3.8-flash`) | thinking low | same (model id availability: probe first) |
| E4 | Gemini Flash | thinking medium | same |
| E5 | Qwen3.5-9B as served (`qwen3.5:9b`, precision UNKNOWN) | thinking off (template kwargs), `qwen_card_nonthinking`, one page per call | vLLM calls YES (real pages) |
| E6 | (dropped: the server's weights are not ours to change) | — | — |
| E7 | Qwen3.5-9B as served | `qwen_card_nonthinking_pp0` | same |
| E8 | Qwen3.5-9B (best) | `--vision-strategy crops` (WI-BE8) | same |
| E9 (optional) | Qwen3-VL-8B-Instruct | as E5 | same |
| P1–P4 | agreement policies over the best Gemini and best Qwen arm | `single`, `llm_x_ocr`, `llm_x_llm`, `llm_x_llm_x_ocr` | none (offline from stored answers) |

- **Order:** EV0 → E5/E6 smoke on 3 documents → E5–E8 full → E1–E4 (quota-limited; one arm per day on free tier with `--max-provider-calls` and `--vision-pause`) → P1–P4 offline.
- **Pre-registered decision rule** (adapted from FP WI-12): an arm can become the primary reader on scans only if, on scan-page docs, (a) header (sim) ≥ OCR + 5 points and lines (sim) ≥ OCR + 10 points; (b) false-safe (sim) 0 and LLM×OCR agree-wrong ≤ 1; (c) wrong-when-answered ≤ 2% of answered critical fields; (d) p95 latency ≤ 60 s/document (Gemini) or ≤ 120 s/document (local); (e) cost within D-8 (Gemini) or GPU time within the owner's budget (local); (f) repeat variance ≤ 2% of fields. An agreement policy can enable auto-accept candidates only if it meets DP-L1.
- **Correlated-error statement:** two LLM readers share the same masked image and similar training data; their agreement is weaker evidence than LLM×OCR or LLM×QR. Report LLM×LLM agree-wrong separately; never treat it as D42 independence.
- **Decision matrix (fill after the runs):**

| Arm | Header scan | Lines scan | Agree-wrong | Wrong-when-answered | Unreadable→null | p95 latency | Cost or GPU-s/doc | Variance | Verdict (primary / fallback / no) |
|---|---|---|---|---|---|---|---|---|---|
| E0 … E9, P1 … P4 | | | | | | | | | |

- **Effort:** M (code in EV1) + owner run time.

### WI-FE1 AI settings UI with two provider cards

- **Goal:** a manager configures both providers from the browser; nobody else can change them; keys are never shown.
- **Where:** move the dialog content into a Settings page section `web/src/settings/AiSettingsSection.tsx` (Settings already gates on `hasCapability("ai.manage")`, `pages/SettingsPage.tsx:77`); keep the Home card (`components/AiAssistCard.tsx`) as a status summary with a link.
- **Build with** react-hook-form + Zod (already dependencies) and TanStack Query (`useQuery(['ai-settings'])`, `useMutation` with `If-Match` ETag; on 412 reload and show the existing message). Types from `generated.ts`.
- **Components:**
  - `ProviderCard` ×2 (`gemini`, `vllm`): enable toggle; model input (Gemini: existing pattern; local: free text with the server regex); Gemini key: password input, placeholder `Saved key ••••<hint>; leave blank to keep`, Replace / Remove buttons, never pre-filled; vLLM: **Server URL** text field (pre-filled with the allowlisted URL when there is exactly one; the server rejects any URL not in `RB_AI_VLLM_ENDPOINTS`), **Model** dropdown filled from the Test connection model list (free text allowed as fallback; must equal the served id, e.g. `qwen3.5:9b`), key field hidden behind "Server needs a key"; limits (max pages, timeout, max output tokens, concurrency) with Zod ranges equal to the server's; "Test connection" with kind text/image → shows status, latency, model names list, per-step pass/fail; data-flow badge: Gemini "Page images leave this machine (Google)", vLLM "Company server over VPN (<base URL>)"; per-provider usage panel (calls/tokens today, p50/p95 latency).
  - `ReaderStrategySelect`: off / shadow (pick one provider) / single (pick one provider); disabled options explain why (provider disabled, `vllm_calls` disabled on the deployment).
  - `ApplyModeNotice`: shows `suggest` (fixed) until the deployment enables merge.
  - Consent block per provider from `consent-preview` with the data-destination text; save blocked until consent is checked when the fingerprint changes (existing pattern `AiAssistCard.tsx:173-184`).
- **States:** loading skeleton, error banner with retry, read-only view for non-managers ("Only managers can change AI settings"), disabled controls while saving.
- **Phone width:** cards stack in one column below 640 px; no horizontal scroll; tap targets ≥ 44 px.
- **Acceptance:** WI-FE3 tests pass; manual check at 375 px width in Playwright screenshot test.
- **Effort:** L. **Depends:** WI-BE3.

### WI-FE2 Review UI: provenance and reader disagreement

- **Goal:** a reviewer sees where each value came from and resolves disagreements through the existing versioned correction save.
- **API (backend part):** `GET /documents/{id}/ai-suggestions` adds per suggestion `origin = {provider, model, prompt_version, schema_version}` and, for two readers, `readings: [{reader_id, value, page, box}]` plus `agreement: "agree"|"disagree"|"single"`; the document view adds per-field `evidence.origin` for merged values (WI-BE7).
- **UI:** `components/AiSuggestionsPanel.tsx` shows a provenance chip (`Gemini · gemini-3.5-flash-lite · prompt v4` / `Local · qwen3.5:9b · prompt v4.qwen`); `IngestionPreview`/verify screen shows both readers' values side by side with the OCR value when they disagree, each with a "Use this value" action that only changes the draft; save goes through `PUT /documents/{id}/correction` (unchanged, audited); a filter "LLM disagreement" in the pending list (backed by the `LLM_DISAGREEMENT` warning). No new route can set `verified` or bypass validators.
- **Tests:** Vitest for chips, side-by-side values, filter; Playwright `verify-screen.spec.mjs` extended with a disagreement case.
- **Effort:** M. **Depends:** WI-BE6 (shadow data for dev), WI-BE7.

### WI-FE3 Frontend tests and type-drift guard

- **Vitest:** Zod schema rules (model patterns, URL must be one of the allowlist options, limits ranges, `dual_agree` needs two enabled providers); **key never rendered** (render with a view containing `hint` only; assert the DOM never contains the typed key after save; the password field is empty after submit); strategy options disabled with reasons; non-manager read-only.
- **Playwright** (mocked API with `page.route`, pattern from `web/e2e/ai-assist-card.spec.mjs:74`): configure both providers; run "Test connection" for each (mock returns latency + models); change strategy to `dual_agree`; review a disagreement and save a choice; 375 px viewport run.
- **Type drift:** replace hand-written `web/src/api/ai.ts` interfaces with aliases of `components["schemas"][…]` from `generated.ts`, so `make check-static` covers them; add a Vitest type test (`expectTypeOf`) that fails if the alias diverges.
- **Effort:** M. **Depends:** WI-FE1/FE2.

### WI-OPS1 Company vLLM server: checklist and smoke test (no hosting work)

- **Context:** the owner uses an existing shared company server (§0.5). ReconBench does not host, size or start a model. The earlier compose/GPU recipe is dropped.
- **Deliverables:** `docs/ops/vllm-server.md` (facts table from §0.5, kept current) and `scripts/local_llm_smoke.py` (synthetic pages only).
- **Deployment config (owner's `.env`, not committed):** `RB_AI_VLLM_CALLS=synthetic_only` (later `enabled` after the real-page YES), `RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1`, `RB_VISION_READER=all`. Pass both `RB_AI_VLLM_*` variables to the `api` service (URL validation) and the `ai-worker` service (enforcement) in `compose.yaml`.
- **Checklist to fill (ask the server admin; record answers in the doc):** vLLM version; whether `--reasoning-parser qwen3` is set; default thinking on/off; JSON-schema `response_format` support; max images per prompt; any image pixel limit; request logging and retention; usage rules for a shared GPU (rate limits, peak hours).
- **Smoke tests (each needs the owner's YES for "calls to the vLLM server"; synthetic data only):**
  1. Models (done 16 Oct, read-only; version 0.23.0 at `/vllm/version`): `curl -s http://43.242.226.63/vllm/v1/models` → `qwen3.5:9b`, `max_model_len` 8192.
  2. Text JSON + thinking off (done 16 Oct: passed; see §0.5): `curl -s http://43.242.226.63/vllm/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"qwen3.5:9b","messages":[{"role":"user","content":"Return {\"ok\": true}"}],"max_tokens":32,"chat_template_kwargs":{"enable_thinking":false},"response_format":{"type":"json_schema","json_schema":{"name":"ok","schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}}}}'` → expect content `{"ok": true}` and no reasoning text. If `response_format` is rejected, the provider falls back to prompt-only JSON plus the strict local parse.
  3. Synthetic image: `backend\.venv\Scripts\python.exe scripts\local_llm_smoke.py --base-url http://43.242.226.63/vllm/v1 --model qwen3.5:9b --long-side 1600,1280,1024` renders the made-up `SYN-INV-0001` page (`tests/support/scan_images.py::render_invoice_image`), sends the v4 Qwen prompt + schema, and prints counts only: parse ok, `prompt_tokens` per image size, `completion_tokens`, latency, `finish_reason`, and whether the invoice-number box lands on the known synthetic position (decides `box_order` yx/xy). Pick the largest long side that leaves ≥ 3,000 output tokens inside 8,192.
  4. Container reachability: `docker compose exec ai-worker /app/backend/.venv/bin/python -c "import urllib.request;print(urllib.request.urlopen('http://43.242.226.63/vllm/v1/models',timeout=5).status)"` → `200`. If it fails, the Docker VM does not route through the host VPN; fix the network before WI-BE6.
- **Acceptance:** the doc lists every checklist answer (or UNKNOWN), smoke 2–4 pass, and the chosen image size and `box_order` are written into the `vision_openai_compat` registry entry.
- **Effort:** S + owner time. **Depends:** WI-BE4 for smoke 3.

### WI-OPS2 Health, concurrency and fallback in operation

- **Goal:** the app stays correct when the local server is slow or down.
- **Steps:** AI worker heartbeat calls `health()` per enabled local provider every 60 s and stores `last_health_*`; settings status shows "Local model server unreachable" (new `StatusCode` `LOCAL_PROVIDER_DOWN`); enqueue skips local requests with `PROVIDER_UNAVAILABLE` while health is failing for > 5 minutes (no queue pile-up); ledger in-flight per provider (default 1 for local); timeouts per WI-BE2.
- **Tests:** unit with fake clock; integration with the mock server returning 503 then 200.
- **Effort:** S. **Depends:** WI-BE2, WI-BE3.

### WI-G3 Regression guards and rollback switches

- Bench exit code non-zero on any hard metric (exists); every WI above re-runs the offline bench (cached replay where no reader changed).
- Scenario tests added to `tests/scenarios/test_hard_zero_guards.py`: an LLM-only value is never verified (WI-G1); two LLMs agreeing never verify (WI-G1); a merged version never replaces a manual correction (WI-BE7); a local endpoint outside the allowlist never receives a request (WI-BE2); shadow writes no document rows (WI-BE6).
- Property test on `merge_readings` (WI-BE7).
- Golden JSON contract tests per provider (WI-BE4).
- Switches (all default off): `RB_AI_VLLM_CALLS`, `RB_AI_LLM_MERGE`, workspace `reader_strategy`, `apply_mode`. `RB_VISION_READER=vision_gemini` restores today's reader set.

---

## 4. Sequence and dependencies

```
WI-G2 ─► WI-BE1 ─► WI-BE5 ─► WI-BE4 ─► WI-BE2 ─► WI-BE3 ─► WI-BE6 ─► WI-EV1 ─► WI-EV2 ─► (decision) ─► WI-BE7 ─► WI-FE2
WI-G1 ───────────────────────────────────────────────────────────────────────────────────────────────► WI-BE7
WI-EV0 (offline, any time after approval)                         WI-BE3 ─► WI-FE1 ─► WI-FE3
WI-OPS1 (doc any time; smoke after WI-BE4) ─► WI-OPS2 (after WI-BE2/BE3)        WI-BE8 (arm in WI-EV2)
Deterministic in parallel: WI-D1, WI-D2, WI-D4, WI-D6 now; WI-D3 bump after WI-D1/D2; WI-D5 owner-led
```

Main-plan items touched: AC-5 (reader C becomes two readers), AC-6 (WI-G1 fixes the open gap; D42 reader-kind rule), AC-7 (WI-FE2 extends the verify screen), AC-8b (more verified fields may reduce extras; measure), AC-9a (WI-D4), AC-11 (needs AC-L3 or FP DP-4), D-8 (Gemini caps before any production merge).

## 5. Decision points for the owner

| ID | Decision | Recommendation |
|---|---|---|
| DP-L1 | Auto-accept candidate thresholds per field type | Precision ≥ 99.5% **and** Wilson 95% lower bound ≥ 98% on ≥ 100 scored values per field type, coverage ≥ 50%, false-safe 0, LLM×OCR agree-wrong 0 for that type. With the current truth set (≤ 37 values per field) no field type can pass, so auto-accept stays off until WI-D5 grows the set. You approve the numbers. |
| DP-L2 | Do two different LLMs agreeing count as independent readers for D42? | **No** (amendment §1.4). |
| DP-L3 | ~~GPU host~~ **Resolved 16 Oct:** the company vLLM server (§0.5) is used, and the owner approved real invoices to it | Optional: ask the admin whether requests are logged. |
| DP-L4 | Approvals: local calls (URL, model), Gemini calls (dataset, max pages), running local benches | Approve WI-EV0 first (offline), then local E5–E8, then Gemini E1–E4. |
| DP-L5 | Gemini tier for production (D-1 paid vs D39 free-tier testing) | Paid key for any production use of real pages. |
| DP-L6 | Pin vLLM image tag and Qwen revision | Pin both after the smoke test passes; record in `docs/ops/local-vllm-serving.md`. |
| DP-L7 | Default reader strategy for new workspaces | `off`; `shadow` for a 2-week trial on real traffic before `single`/`dual_agree`. |
| DP-L8 | Local model for PO/GRN PDFs too (D-3 analogue) | Yes for shadow; same gates as invoices. |
| DP-L9 | D-8 monthly caps (Gemini) and a GPU-time budget (local) | Set before `apply_mode = merge`. |
| DP-L10 | FP DP-4: is "correctly flagged for a person" acceptable where no reader is sure? | Yes for handwriting (never auto); decide for printed fields after WI-EV2. |

## 6. Out of scope

- Training or fine-tuning models; adding OpenCV or other heavy dependencies.
- LLM text hooks H1–H4 on the local model (design note: they can reuse `providers.py` and the endpoint policy later; prompts and schemas would need their own variants and golden tests).
- Any change to reconciliation rules, tolerances or link auto-accept policy.
- GSTR-2B (AC-12), vendor profiles (AC-10), AC-9b UI.
- Production rollout of any reader without the owner's written approval.

## 7. Definition of done

Measured by a fresh worker-image bench on the expanded truth set (WI-D5; report the set size next to every rate) plus attribution:

1. Header fields correct ≥ 98% (scan-page docs and all docs).
2. Line items correct ≥ 95% (amount recall and qty/rate cells).
3. Link recall ≥ 95% with `false_suggestions` 0.
4. `FALSE_SAFE` = `FALSE_AUTO_LINKS` = `FALSE_MATCHED` = `WRONG_VENDORS_CREATED` = 0, and `trusted_but_wrong` = 0.
5. With `RB_AI_LLM_MERGE=disabled` and `reader_strategy=off`, the bench report hash equals the pre-AC-L hash (no hidden behaviour change).
6. Every LLM-sourced value in the app carries provider, model, prompt version, schema version and reader params; no API response contains a key; the SSRF tests pass.
7. CI: `make check-static`, `make test`, `make golden`, `lint-imports` (with the WI-G2 contract), the parser-version check, and the touched integration groups pass.
8. Decision-log entries exist for each work item with before/after aggregate numbers, and the owner's approval entries precede the first external or local model call.
