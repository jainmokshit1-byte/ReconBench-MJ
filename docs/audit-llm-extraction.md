# Audit: LLM extraction for scanned invoices (Gemini H6 today, local vLLM next)

Date: 16 Oct 2026. Mode: read-only. No source/config/migration/test file was edited; nothing was committed; no test, benchmark, container, provider or model was run; `.env` and secrets were not opened. External documentation was read on the web (no data sent). Throwaway scripts: `work/progress_metrics.py` (counts only).

**Tags.** **verified** = code `path:line` or command output in this session; **inferred** = follows from verified facts, not executed; **UNKNOWN** = cannot be checked here (reason given).

---

## 1. Repository state (verified)

- `git rev-parse HEAD` → `0a0521071d301dee6e805d1be9d050e8f546c130`, branch `phase2-hosted-ci`.
- `git status --short` → 107 entries: modified tracked files across backend AI/extraction/ingestion/mapping/recon, tests, web, docs, scripts, plus untracked migrations `0021`–`0024`, `reconbench_app/ai/consensus.py`, `reconbench_app/document_identity.py`, `reconbench_core/extraction/consensus.py`, new tests, `CODEBASE_CONTEXT.md`, and two docs. No file changed after 9 Oct 2026 14:36. This audit describes the working tree.

## 2. Provider coupling map of Gemini H6

Paths are under `backend/reconbench_app/` unless stated. N = provider-neutral, G = Gemini-specific, M = mixed.

| Concern | Where (verified) | N/G | Notes |
|---|---|---|---|
| SDK import | `ai/worker/gemini.py:8-10` (`httpx`, `google.genai._gaos.lib.compat_errors`); `ai/worker/transport_guard.py:14` (`httpx`), `:322 new_gemini_client` | G | Only the AI worker imports them. |
| Import rule | `backend/pyproject.toml:120-161`: non-worker app modules may not import `reconbench_app.ai.worker` or `google`. Core may not import `httpx`, `requests`, `openai`, `google` (`:105-118`) | M | **Gap:** the app-level contract does **not** forbid `httpx` or `openai` in non-worker modules. An OpenAI-compatible client in the API process would not be caught. |
| Provider port | `ai/provider.py:33-72` (`ProviderRequest`, `ProviderResult`, `ProviderFailure`, `AiProvider` protocol) | M | `thinking_level: Literal["low"]` (`:40`) and `ProviderImage.resolution` (`low|medium|high|ultra_high`, `:29`) are Gemini vocabulary. |
| Provider instance | `ai/worker/main.py:80` `provider=GeminiProvider()` | G | **One provider per worker process.** The runner holds `self.provider` (`ai/worker/runner.py:97`). |
| Request shape | `ai/worker/gemini.py:129-145 _input`, `:159-173` `interactions.create(…, response_format={type:text, mime_type:application/json, schema}, generation_config={max_output_tokens, thinking_level}, store=False)` | G | No temperature/top_p/top_k sent (matches Google guidance). |
| Error mapping | `transport_guard.py:223 map_provider_error`, `:267`, `:277`; label table `ai/settings_service.py:75-90` | G | Codes such as `PREPAY_DEPLETED`, `BILLING_OR_PREREQUISITE` are Google-shaped. |
| Transport guard | `transport_guard.py:102 SingleAttemptTransport`, `HTTPTransport(retries=0)` (`:338`), max timeout 120 s (`:23`) | N | Reusable for any httpx client. 120 s cap is too tight for a multi-page local call on a small GPU (inferred). |
| Workspace settings | table `ai_setting` (`db/metadata.py:863-945`): one `mode`, one `provider` (enum `claude|openai|gemini`, `:876`), one `model`, one `key_secret_id`, `vision_enabled`, `auto_verify_field_types` | M | **Single provider/model/key per workspace.** No enum value for a local server. |
| Settings API model | `ai/settings_service.py:149-192 AiSettingsUpdate`: `provider: Literal["gemini"]` (`:155`), model regex `gemini-…` (`:73`), key regex (`:74`) | G | |
| Consent | `ai/consent.py:106-111` text says "sends … to {provider} … with storage disabled on the provider side"; `:151-183` fingerprint covers provider, model, mode, hooks, prompt/schema/redaction versions, image mask and render versions | M | Consent text is rendered with `provider="Google Gemini"` hard-coded (`settings_service.py:287`, `:521`); preview hard-codes `"provider": "gemini"` (`:517`, `:524`). No wording for "stays on this network". |
| Gate | `ai/gate.py:266-305 DeploymentGate.refusal`: `disabled` / `synthetic_only` / `enabled` + approval ref | N | Has no notion of an internal (non-external) endpoint. `VisionReaderInfo.external` exists (`extraction/vision.py:32`) but nothing reads it (grep, verified). |
| Reader switch | `extraction/vision.py:19-76`: `VISION_READERS = ("vision_gemini",)`, `RB_VISION_READER` validated at startup (`ai/worker/config.py:52`) | M | The registry is the natural place for `vision_openai_compat`. |
| Enqueue coupling | `ai/enqueue.py:76-77`: vision hook only when `reader.provider == settings.provider` | M | Ties the vision reader to the single workspace provider. |
| Eligibility | `ai/vision.py:105-122 vision_eligibility`: PDF invoice/PO/GRN; `ai_assisted` → only `needs_verification`; `ai_first` → every PDF; ≤ `max_pages` (8) | N | |
| Prompt | `ai/prompts.py:157-213` `VISION_SYSTEM`, `vision_prompt`; version `vision.prompt.v3` (`:35`) | M | Written for Gemini; "Leave out anything you cannot read clearly" (`:163`), so omission ≠ unreadable. |
| Output schema | `ai/contracts.py:38` `vision.schema.v3`; `VisionLineItem` `:223`, `VisionOutput` `:258` | M | Provider schema avoids `maxItems` because Gemini rejects it (D31/D40). Strict local parse is provider-neutral. |
| Validators | core `reconbench_core/extraction/vision.py:534 check_reading`; app `ai/vision.py:452 validate_vision` | N | See §4/§7 for gaps. |
| Cache key | `ai/cache.py:30-51`: hook, model, prompt_version, schema_version, redaction_version, payload_digest (images: caption, mime, sha256, resolution; `ai/gate.py:235-262`) | M | **Provider is not in the key.** Thinking level, max_output_tokens and sampling are not in the key. Render params enter only through the image hash. |
| Usage ledger | `ai/ledger.py:114 create_request`, `:198 reserve`, `:372 finish`; `ai_call_log` row per call (`runner.py:451-497`) with provider, model, prompt_version, tokens, latency, cost | N | Cost uses an owner price file (`ai/limits.py:115`); no hard-coded prices. |
| Limits | `ai/limits.py:44-71`: `h6_vision` 16,384 output tokens, 8 pages, 12 MB images, 1,200 tokens/image estimate, 120 s; 200 calls/day, 10/min, 2 in flight; month caps `None` | M | Token/USD budgets are cloud-shaped; a local server needs GPU-time/concurrency limits instead. |
| Worker/queue | Procrastinate `ai` queue consumed only by the AI worker (`ai/worker/main.py:27`); no explicit worker concurrency (`worker_control.py:121`), so the library default applies (inferred: 1); ledger caps 2 in flight (`ai/ledger.py:244-253`) | N | |
| Key | sealed with a public key in the API, opened only in the AI worker (`ai/key_seal.py`, `ai/worker/key_open.py`); worker refuses `GEMINI_API_KEY`/`GOOGLE_API_KEY` env (`ai/worker/config.py:17`) | N | Works for an optional local API key too. |
| Tables | `ai_setting`, `secret`, `ai_request`, `ai_cache`, `ai_call_log`, `document_page_image`, `consensus_run`, `consensus_decision` (`db/metadata.py`) | M | `ai_request` stores provider, model, prompt_version, schema_version, redaction_version, cache_key (verified). **No column for image/reader params** (render version, media resolution, thinking, sampling). |

**Where to cut the interface (proposal).** Keep `AiProvider.complete(request, api_key)` as the transport port. Add a `VisionReader` layer above it in the AI worker: `VisionReader.build_request(prepared, reader_params) -> ProviderRequest` and `VisionReader.parse(result) -> VisionOutput`. It owns the prompt variant, schema dialect and image parts. Registry entries: `vision_gemini` (provider `gemini`, external) and `vision_openai_compat` (provider `local_vllm`, internal). The runner picks the provider by `request["provider"]` from a `Mapping[str, AiProvider]`, not one instance. Details: plan WI-BE1/BE2.

## 3. Output path: how a returned value is used today

1. The AI worker validates the answer (`ai/worker/hooks/vision.py:88-91` → `ai/vision.py:452 validate_vision` → core `check_reading`). If readings existed and none survived, the whole answer is `ungrounded` (`ai/vision.py:459-460`).
2. `apply` writes **nothing** to the document (`hooks/vision.py:96-143`). The normalised answer is stored only in `ai_cache` (`runner.py:339-349`, TTL 30 days, `ai/limits.py:71`). Page bytes are purged.
3. If accepted, a **dark consensus run** is queued (`hooks/vision.py:127-137` → `ai/consensus.py:117 enqueue_consensus_run`). Consensus reads the stored answer, rereads crops locally, writes `consensus_decision` provenance and, only for opted-in field types, appends a **same-value** version that changes verification state (`ai/consensus.py:802 _append_verified_version`). It never replaces an OCR value with a vision value.
4. The review view (`GET /documents/{id}/ai-suggestions`, `api/ingestion.py:909` → `ingestion.py:4833`) re-checks and shows suggestions. A person applies a value through the versioned correction save. The view exposes `model`, `mode`, `source` (vision/text) and `ai_call_id` (`web/src/api/ai.ts:72-87`); **not** provider, prompt version or schema version.
5. In the core, `vision_document` gives every vision value confidence 0.65 (`reconbench_core/extraction/vision.py:45`, `:783`), below the 0.70 review floor.

**Where a correct LLM value can be dropped or ignored (verified unless tagged):**

| # | Drop point | Evidence |
|---|---|---|
| P1 | Never enters the document: suggestion only, until a person saves | `hooks/vision.py:1-7` |
| P2 | Not requested: `ai_assisted` skips documents that do not need verification; > 8 pages skipped | `ai/vision.py:118-121` |
| P3 | Strict parse failure throws away the **whole** answer (`invalid_output`) | `runner.py:270-307` |
| P4 | Core drops per item: in D41's 39 answers `bad_name` 29, `bad_value` 15, `duplicate` 12, `bad_box` 8, `bad_cell` 8, `conflict` 6, `in_mask` 2, `gst_type` 1 (`after-d41-lite.json` `vision.dropped`, verified). How many of these were correct values: UNKNOWN (not scored) |
| P5 | All-or-nothing `ungrounded` when every item is dropped | `ai/vision.py:459-460` |
| P6 | Model omits values it "cannot read clearly"; omission and absence look the same | `ai/prompts.py:163` |
| P7 | Consensus keeps the OCR value when readers disagree (L3) | `consensus.py` (`decide`, `:721`); D44 table |
| P8 | Cache expiry after 30 days hides the suggestion | `ai/limits.py:71` |
| P9 | A stale source (new version, consent or settings change) discards the answer | `runner.py:326-329`; `runtime_checks.py:149-172` |
| P10 | Handwritten marks never become fields | prompt rule + D42 rule 4 |

**What must change so LLM values can become document values safely (inferred design; plan WI-BE5/BE7):** a pure core merge (OCR document + checked LLM reading → candidate document) run by the **general** worker; LLM-only values capped below the review floor; standalone validators that do not depend on OCR consistency; per-field provenance `origin = llm:<provider>:<model>:<prompt_version>` with schema/render/thinking params; verification only through D42 (independent non-LLM reader + validators + crop); fallback = the OCR version stays current; workspace flag off by default.

## 4. Baseline: what is stored locally

Population: 39 PDF documents (67 pages) of the current truth set. All 39 were read by the layout engine (33 on scan pages via Tesseract, 6 on digital bundle pages) (`baseline-scanfix-attribution.json` `documents_by_reader`, verified). Scan types from pixels: scan-clean, scan-tilted (0.5–1.1°), scan-rotated 90°, mixed bundle; **no phone photos** (diagnosis §5.1, verified there). Stored H6 answers: `gemini-3.5-flash-lite`, schema/prompt v3, free tier, recorded 8 Oct (D41). Cache files: `work/accuracy-cache/vision/` holds 72 entries (count verified).

### 4.1 Header fields per field (same 39 docs; OCR = Tesseract+rules; vision = cached flash-lite; from `after-d41-lite.json` `vision.agreement`, verified)

| Field | Scored rows | OCR correct | Vision correct | Either correct | Agree on a wrong value |
|---|---:|---:|---:|---:|---:|
| doc_no | 39 | 31 | 21 | 36 | 0 |
| doc_date | 39 | 38 | 32 | 39 | 0 |
| vendor_name | 39 | 15 | 38 | 38 | **1** |
| vendor_gstin | 39 | 32 | 36 | 36 | 0 |
| buyer_gstin | 27 | 23 | 23 | 23 | 0 |
| po_refs (printed) | 31 | 12 | 11 | 15 | 0 |
| taxable_total | 29 | 22 | 19 | 22 | 0 |
| cgst / sgst / igst | 29 / 29 / 29 | 21 / 23 / 1 | 27 / 27 / 2 | 27 / 27 / 2 | 0 |
| grand_total | 37 | 24 | 35 | 37 | 0 |
| **All** (truth-present fields: 313) | — | **242/313 (77.3%)** | **271/313 (86.6%)** | **302/313 (96.5%)** | **1** |

"Scored rows" include fields known to be absent (`both_missing` 60 in total), so the per-field row sums exceed 313. "Either correct" is an upper bound for a perfect chooser (inferred). Vision is better on vendor name, totals and taxes; OCR is better on doc number, date and taxable value. Even the best-of-both bound is below 98%.

### 4.2 Lines

- OCR, all 51 docs: line recall 96/141 (68.1%), spurious 33. Scan-page cells (attribution, verified): amount 78/111, qty 65/102, rate 63/106, description 53/108, HSN 49/75.
- Vision, 39 PDF docs: line recall 80/123 (65.0%), description ok 40/77, spurious 34; 116 rows returned, all with description and amount, 106 with quantity, 103 with rate (verified). Per-cell vision correctness (qty, rate, HSN, UOM) is **not stored**: UNKNOWN.
- UOM: the truth set has no UOM (diagnosis §5.3): UNKNOWN for both readers.

### 4.3 By scan type

- OCR (verified, attribution `by_scan_type`, header ok/total): scan-clean 94/112, scan-tilted 47/61, scan-clean+handwritten 52/72, scan-rotated+handwritten 26/27, scan-tilted+handwritten 5/11, digital 18/30.
- Vision by scan type: **not stored** (UNKNOWN). The bench never split vision by scan type.

### 4.4 Miss classes M1–M6 for vision

Not stored per miss: UNKNOWN. What the stored aggregates allow:

| Class | Stored evidence |
|---|---|
| M1 model misread | header_wrong 8 (vision alone, verified); split by cause UNKNOWN |
| M2 prompt/schema ambiguity | historical: D40 showed `uom` filled 96/109 and `amount` 10/109 under v2 (contract problem), fixed in v3 |
| M3 image quality/resolution/masking | `in_mask` 2 drops; render cap 2,048 px long side (`extraction/vision.py:47-53`) ≈ 175 DPI on A4 (inferred) |
| M4 pipeline dropped a correct value | candidates: the P4 drop counts above (correctness UNKNOWN) |
| M5 truth-label error | UNKNOWN |
| M6 party-role confusion | 1 agree-wrong on `vendor_name` (verified); earlier H1 (text) put the buyer as vendor (memory of the 6 Oct test) |
| Missing | header_missing 34 (vision alone, verified) |

**Smallest run that fills these gaps (needs your approval "Run existing local benchmarks: YES"; no network, no provider):** replay the bench from cache with `--vision-model gemini-3.5-flash-lite` (as `work/diag-scan/replay_bench.py` did on 9 Oct) and extend `scripts/accuracy_attribution.py` to score cached vision answers per field, per scan type and per miss class (plan WI-EV0). Output: aggregates only.

## 5. Wiring: upload path vs scripts

- **Upload (verified):** `POST /api/v1/files` → Procrastinate `ingest_file` → `ingestion.py:1755 parse_pdf_isolated(…, parser_hints_from_config(…))` → `pdf_sandbox.py:715 _extract_isolated` (core text parser, then `:565 _prefer_layout` with the layout engine for scanned/unverified files ≤ 20 pages, OCRmyPDF fallback) → QR child → page dHash child → `_persist_parse`. If the workspace has vision on and the version is eligible: `jobs.py:133 enqueue_vision_pages` → `prepare_vision_pages` (render + mask in the sandbox, `extraction/vision_pages.py:351`) → `ai/enqueue.py:214 vision_read` → AI worker → cache → consensus job.
- **`scripts/accuracy_bench.py`:** same `parse_pdf_isolated` entry and the same hint builder (WI-1, verified by `tests/unit/test_parser_hints_parity.py`). Vision via `scripts/accuracy_vision.py:239 send` using the app's `GeminiProvider` (`:264-271`) and the same render/mask code.
- **`scripts/measure_extraction.py`:** deprecated by WI-1 (exit 2, D59).
- **Mismatches:** (a) bench runs vision on every PDF doc; the app only in `ai_first` or on `needs_verification` docs (diagnosis M7); (b) bench default model `gemini-3.8-flash` (`accuracy_bench.py:1894`) ≠ stored answers (flash-lite), so recent reports have an empty vision block; (c) the bench never exercises the app's consensus job end to end, only the shadow upper bound; (d) the app's live model id is in the DB (`ai_setting.model`): UNKNOWN which (Flash or Flash-Lite), no DB access.

## 6. Local serving readiness

| Topic | Today (verified) | Gap / proposal |
|---|---|---|
| Gate semantics | `RB_AI_EXTERNAL_CALLS` covers every provider call (`ai/gate.py:287-297`); `enabled` needs an approval ref | A localhost/LAN server is not "external" but still a data flow. Add `RB_AI_LOCAL_CALLS` (`disabled` default / `enabled`) plus `RB_AI_LOCAL_ENDPOINTS` (exact-origin allowlist). The gate checks `external` from the reader registry. Consent stays required. |
| What `RB_AI_EXTERNAL_CALLS` covers | All `AiProvider.complete` calls in the AI worker; `POST /ai/test/provider` refuses when `disabled` (`settings_service.py:907-913`) | Local test must be governed by the local gate instead. |
| Concurrency/timeouts | 2 in flight, 10/min, 120 s cap (`ai/limits.py`, `transport_guard.py:23`) | Local: per-provider `max_concurrency` (default 1), timeout up to 300 s for local only, queue backpressure. |
| GPU contention | No GPU code in the repo (verified: no `cuda`/`torch` deps) | The model runs in a separate vLLM container; Tesseract runs on CPU, so contention is inferred low. `--gpu-memory-utilization` must leave room if the GPU is shared. |
| Health / fallback | none for providers | `GET {base}/v1/models` (OpenAI standard; vLLM support assumed, verify on the pinned version) and a heartbeat probe; when down, release the request with `PROVIDER_UNAVAILABLE` and keep the OCR version (nothing changes, because the LLM path is async). |
| Deployment | `compose.yaml:156-176` ai-worker: no networks defined, `RB_VISION_READER` default `vision_gemini`; no `RB_AI_LIMITS_FILE` (defaults apply) | Add a `vllm` service on an `internal: true` network shared only with `ai-worker`; no published port, or bind to `127.0.0.1`. |
| Network rules | PDF sandbox has no network (invariant); ai-worker has default bridge network (inferred: full egress) | Proposal: ai-worker egress only to Google endpoints and the vLLM origin (firewall/proxy; outside compose's abilities: UNKNOWN per host). |
| Dependency policy | `httpx` is already a transitive dependency of `google-genai` and used directly in the worker (`gemini.py:8`) | Use plain `httpx` (no `openai` SDK). Extend the import-linter contract to forbid `httpx` and `openai` outside `reconbench_app.ai.worker`. |
| This machine as the server | `nvidia-smi`: **NVIDIA GeForce RTX 2050, 4096 MiB**, driver 536.52; RAM 15.7 GB; Windows 11 (verified) | Qwen3.5-9B does **not** fit 4 GB VRAM even at 4-bit with the vision encoder and KV cache (inferred: 9B params × 0.5 byte ≈ 4.5 GB weights alone). vLLM targets Linux; on Windows it needs WSL2 (inferred). A separate GPU host is needed (plan §Serving). |

## 7. Cross-check inventory

| Check | Exists? | Where | Gap |
|---|---|---|---|
| Line qty × rate = amount, Σ lines, taxable + tax = grand, round-off (OCR path) | yes | `reconbench_core/extraction/verify.py:175 solve_line` and the D18 tree | — |
| Same arithmetic on **vision** values on their own | **no** | vision totals checked only when OCR totals are self-consistent (`core vision.py:626`); line check only against an OCR line with the same amount (`:666-667`) | On exactly the scans where OCR fails, LLM arithmetic is not checked. Σ lines vs taxable missing. |
| GSTIN checksum + look-alike repair | yes | `reconbench_core/extraction/values.py:230`; vision checks use it | — |
| Buyer-entity / party role | yes | `parsers/parties.py:396`, vision `check_reading` | Role still depends on configured `buyer_entities`. |
| GST type vs state | yes | vision drop `gst_type` | — |
| Reference shape (no address/marketplace/sales order as PO) | yes | vision checks + `accept_po` | — |
| "Value present in OCR text" | H1 only | `ai/validators.py:125 validate_h1`, `:47 find_quote` | Deliberately absent for vision (independence). Needed as a **metric** (not a gate): "not-in-OCR-text rate" as a hallucination upper bound. Attribution already has a presence check. |
| Null-on-unreadable | **no** | prompt says "leave out" (`prompts.py:163`) | Add an explicit `unreadable[]` list (WI-12 step 6, not done). |
| Date window | consensus only | D42 window in `extraction/consensus.py` | Not inside vision checks. |
| Crop reread (local OCR of the LLM box) | yes | `ai/consensus.py:292 _crop_checks` | Local only (D45). |
| Two independent readers | yes | `consensus.decide` | **Bug:** AI-sourced value counted as an OCR reading (`tests/scenarios/test_hard_zero_guards.py:255`, xfail strict). Two LLMs are not modelled as one reader kind. |

## 8. Frontend / settings audit

| Item | Finding (verified unless tagged) |
|---|---|
| Location | Not on the Settings page. The AI settings are a dialog opened from the Home card: `web/src/components/AiAssistCard.tsx` (`AiAssistCard` `:67`, `AiAssistDialog` `:118`), mounted in `pages/HomePage.tsx:151`. The Settings page has only the offline guardrail test and a link to Home (`pages/SettingsPage.tsx:250`). |
| State/validation | Plain `useState` + `fetch` wrappers in `web/src/api/ai.ts` (`getAiSettings` `:370`, `saveAiSettings` `:374`, `getAiConsentPreview` `:386`, `startAiProviderTest` `:411`, `getAiUsage` `:428`). No react-hook-form, Zod or TanStack Query in this flow (all three are dependencies in `web/package.json`; Zod/RHF used in `settings/configModel.ts`). Model check is a regex `AI_MODEL_PATTERN = /^gemini-…/` (`ai.ts:287`). |
| Types | `ai.ts` uses **hand-written** interfaces (`AiSettingsView` `:206`, `AiSettingsUpdate` `:263`, …), not `generated.ts`. The CI drift check (`Makefile:21-23`: export OpenAPI → `npm run api:generate` → `git diff --exit-code`) protects `generated.ts` only, so `ai.ts` can drift silently. |
| Fields exposed | mode (3 radio options `:23-27`), auto-verify field types, "Read page images with Gemini vision" checkbox (`:165`), model id, Gemini key (password input, `:192-208`), key storage session/saved, consent text + per-hook data list + checkbox, withdraw consent, remove key, provider test, usage (calls/tokens today, calls this month, cost today), uncertain requests + acknowledge. |
| "AI first" | UI label "AI-first" = backend `ai_mode` value `ai_first` (`AiAssistCard.tsx:26`; enum `0001_setup.py:63`). Hooks: `h1_extract:all_pdfs` (or `h6_vision:all_pdfs` when page images are on), plus `h2_item`, `h3_rerank`, `h4_vendor` (`ai/consent.py:39-44`, `:120-127`). Gate behaviour: eligibility runs H1/H6 on **every** PDF (≤ 8 pages) instead of only documents that need verification (`ai/vision.py:120`). Output is still suggestion-only: "rules output stays authoritative" (`ai/vision.py:70`). It does **not** make AI the primary extractor. |
| Who may edit | `PUT /ai/settings`, consent preview, key delete, provider test, usage, request state need `ai.manage` (`api/ai.py:123-213`); only role `manager` has it (`authorization.py:41-54`). `GET /ai/settings` needs `data.view`. UI hides controls without `ai.manage` (`AiAssistCard.tsx:69`, `:102-104`). |
| Audit | `ai.settings_save` with before/after public state (no key) (`settings_service.py:784-792`), `ai.consent_change` (`:794-807`), `ai.key_remove` (`:847`), `ai.test_requested` (`:943`). |
| Key handling | `SecretStr`, write-only, sealed on receipt; view returns `{set, hint (4 chars), storage, expires_at}` only (`settings_service.py:328`); UI clears the draft on submit (`AiAssistCard.tsx` save path). |
| `POST /ai/test/provider` | API inserts an `ai_request` (`call_kind="provider_test"`, synthetic fixture) and enqueues it (`settings_service.py:859-952`); the **AI worker** sends it (`runner.py`). The API never calls a model (respects the invariant). It is a text-only fixture: no image smoke test, no latency/model list in the result. |
| Usage endpoint | `GET /ai/usage` totals only, not per provider (`settings_service.py:983`). |
| Provenance in review UI | `AiSuggestionsPanel.tsx:77` shows `model` only; no provider, prompt version or schema version; no per-field agreement between two readers; `PdfPreview` draws boxes for evidence (D50). |
| Phone width | UNKNOWN (no visual check run). |

## 9. Tests

| Area | Exists (verified present) | Gap |
|---|---|---|
| Gemini adapter | `tests/unit/test_ai_gemini_adapter.py` (httpx `MockTransport`, `:63`) | — |
| Worker boundary / transport guard / keys+gate | `test_ai_worker_boundary.py`, `test_ai_worker_transport_guard.py`, `test_ai_keys_and_gate.py` | No local-gate tests |
| Vision core/app | `test_vision_core.py`, `test_ai_vision.py`, `test_accuracy_vision.py`, integration `test_ai_vision.py` | No golden JSON contract tests per provider |
| Consensus | `test_consensus_core.py`, `test_consensus_crop.py`, `test_accuracy_consensus.py`, `scenarios/test_hard_zero_guards.py` (1 strict xfail) | D59 gap |
| Settings API | integration `test_ai_settings_api.py` | Gemini-only |
| Web | `AiReviewSurfaces.test.tsx`, `VerifyDocument.test.tsx`, `SettingsPage.test.tsx`; Playwright `ai-assist-card.spec.mjs` (mocked routes, e.g. `/ai/test/provider` `:74`), `verify-screen.spec.mjs` | No two-provider form, no "key never rendered" test for a second key, no disagreement review |
| Mock OpenAI-compatible server | **none** | needed (httpx `MockTransport` in unit tests; a tiny stdlib HTTP server for integration/Playwright) |
| Failure modes | Gemini mapping covered | timeout / malformed JSON / server down for a local provider: none |

## 10. External facts checked (documentation, not memory)

| Hypothesis | Result | Source |
|---|---|---|
| Qwen3.5-9B is multimodal, Apache-2.0 | **verified**: "Image-Text-to-Text", "Causal Language Model with Vision Encoder", license apache-2.0 | [Qwen/Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B) |
| Thinking on by default; disable per request with `chat_template_kwargs {"enable_thinking": false}` | **verified** (model card). `/think` `/nothink` soft switches are not supported | model card |
| Server-side `--default-chat-template-kwargs` | **verified** in vLLM reasoning docs (example with `{"enable_thinking": false}`); per-request value wins | [vLLM reasoning outputs](https://docs.vllm.ai/en/stable/features/reasoning_outputs/), [Qwen vLLM guide](https://qwen.readthedocs.io/en/stable/deployment/vllm.html) |
| `--reasoning-parser qwen3` | **verified** (model card serve command) | model card |
| Sampling from the card | **verified**: non-thinking general `temperature 0.7, top_p 0.8, top_k 20, min_p 0.0, presence_penalty 1.5, repetition_penalty 1.0`; thinking general `1.0 / 0.95 / 20 / 0.0 / 1.5 / 1.0`. No vision-specific set; the card is internally inconsistent for non-thinking reasoning | model card |
| Context length | 262,144 native | model card |
| Quantized variants | **verified to exist, not from Qwen**: `RedHatAI/Qwen3.5-9B-FP8-dynamic`, `…-quantized.w4a16`, `…-quantized.w8a8` (listed in vLLM LLM-Compressor docs); community AWQ. An official Qwen quant: not found | [LLM Compressor Qwen3.5](https://docs.vllm.ai/projects/llm-compressor/en/stable/key-models/qwen3.5/), [QuantTrio AWQ](https://huggingface.co/QuantTrio/Qwen3.5-9B-AWQ) |
| vLLM version | card says vLLM "from the main branch" (nightly); no version number: **UNKNOWN which release** supports it | model card |
| Structured output in vLLM chat | **verified**: `response_format {"type":"json_schema", "json_schema":{name, schema}}` and `structured_outputs {"json": …}`; works with reasoning; a Qwen3-Coder caveat needs `--structured-outputs-config.enable_in_reasoning=True` when thinking is on | [vLLM structured outputs](https://docs.vllm.ai/en/stable/features/structured_outputs/), [OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/) |
| `--api-key` | **verified**: protects `/v1`, `/v2`, `/inference` only; `/invocations` is not authenticated | same page |
| `--limit-mm-per-prompt`, image pixel limits, `/health`, `/v1/models` | **UNKNOWN** (not on the pages read); check `vllm serve --help` on the pinned version | — |
| Third-party claims (strong KIE, weaker raw OCR/tables, prompt sensitivity) | not checked; treat as hypotheses for the experiment | — |
| Gemini 3: keep temperature at default | **verified**: "strongly recommend keeping the temperature parameter at its default value of 1.0"; lowering it may cause looping. top_p/top_k not mentioned | [Gemini 3 guide](https://ai.google.dev/gemini-api/docs/gemini-3) |
| Thinking level | **verified for Gemini 3 / 3.1** (`minimal|low|medium|high`, default `high`; Flash-Lite 3.1 default `minimal`). For 3.8 Flash: Google pages show examples with `medium`; third-party reports say levels are `low|medium|high`, default `medium`, `minimal` rejected: **partly verified** | Gemini 3 guide; [What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/generate-content/latest-model?hl=en); [Apidog](https://apidog.com/blog/gemini-3-8-flash-thinking-levels/) |
| Media resolution | **verified**: low 280 / medium 560 / high 1120 / ultra_high 2240 (per item) max tokens per image; `high` recommended for images; documents saturate at `medium` (for PDF input) | [Media resolution](https://ai.google.dev/gemini-api/docs/media-resolution) |
| Structured JSON output | **verified** supported on Gemini 3 | Gemini 3 guide |
| Paid key only | Not a documentation fact; repo D-1 says paid, D39/§3a allow free tier for tests. The repo cannot tell the tier | — |
| Model ids `gemini-3.8-flash`, `gemini-3.5-flash-lite` | Names appear on Google's model pages ("Gemini 3.8 Flash", "Gemini 3.6 Flash and 3.5 Flash-Lite"); exact availability for the owner's key: UNKNOWN | latest-model page |

## 11. Top findings

1. The reader port exists, but the worker is **hard-wired to one provider** (`main.py:80`) and the workspace has **one provider/model/key** (`ai_setting`), so two providers need a runner change and a settings table, not just a new adapter.
2. "AI first" (`ai_first`) only widens **which** PDFs get suggestions; LLM output never becomes a document value today (10 drop points, §3). Consensus also has a known false-safe gap (strict xfail) that must be fixed before any LLM value is used.
3. The cache key omits the provider and reader parameters; the review UI shows only the model; LLM arithmetic is not checked on its own; there is no unreadable/null signal. The dev machine (RTX 2050, 4 GB) cannot serve Qwen3.5-9B.
