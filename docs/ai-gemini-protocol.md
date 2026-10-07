# Gemini Interactions protocol gate

**Phase 1 Gemini protocol retry criterion: pass with a single-shot HTTPX transport guard (4 Oct 2026).** SDK 2.28.0's public retry setting alone still permits repeated Interactions POSTs. The app now creates a fresh SDK/HTTPX client for each provider call, disables HTTP transport retries, and wraps its transport with a one-request guard. The SDK retry setting remains `attempts=1` as defense in depth. The SDK's async client path (`client.aio`), which the guard does not cover, is blocked before any I/O; provider calls must use the guarded sync client. Both Google error-body formats map to sanitized codes, and every client requires an explicit, bounded timeout. Focused tests exercise the locked SDK over synthetic HTTPX transports; no live provider call or real business data was used. This resolves the retry criterion only; it does not approve real-document provider calls or by itself close any unrelated project gate.

All runtime checks used synthetic request/response data through `httpx.MockTransport`. No provider endpoint was contacted, no real provider key was used, and no invoice, PO, or GRN data was opened. Procrastinate registration was checked with in-memory `App` objects and a dummy connector, without a database.

## Verified protocol facts

| Required fact | Result | Evidence |
|---|---|---|
| Inject a transport | Pass. Construct `httpx.Client(transport=httpx.MockTransport(handler))` and pass it as `types.HttpOptions(httpx_client=...)`. The test handler observed the complete request. | Mock protocol check below; pinned SDK source uses the configured HTTP client. |
| Interactions on stable API v1 and chosen model | Pass for request construction. SDK emitted `POST /v1/interactions` for `gemini-3.8-flash`. Google's current model page lists structured outputs as supported for this model. | [Gemini 3.8 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash); [Interactions API overview](https://ai.google.dev/gemini-api/docs/interactions-overview). This proves protocol construction, not a live provider-side response. |
| Exact request shape | Pass. The SDK serialized `model`, `system_instruction`, `input`, object `response_format`, `generation_config.max_output_tokens`, `generation_config.thinking_level`, and `store:false`. `tools`, `stream`, and `previous_interaction_id` were absent. | Mock protocol check below; current [structured-output API guide](https://ai.google.dev/gemini-api/docs/structured-output) and [Interactions API v1 reference](https://ai.google.dev/api/interactions-api-v1). |
| Status, text, and usage fields | Pass, with a correction (Phase 6). SDK parses `status` and `usage.total_input_tokens`, `usage.total_output_tokens` and `usage.total_thought_tokens`. It derives `output_text` only from `steps[].content[]` items with `type: "text"` (`google/genai/_gaos/google_genai.py`, `_output_properties_from_steps`). The earlier Phase 1 mock put text directly on the step (`steps[].text`); with that shape `output_text` is empty, so the original "derived `output_text`" claim was not actually exercised. `backend/tests/unit/test_ai_gemini_adapter.py` now uses the real shape and verifies the text. | Mock response; [Interactions API v1 response example and usage fields](https://ai.google.dev/api/interactions-api-v1). |
| Thought bound and minimum thinking | Pass with limits. Google documents `max_output_tokens` as a hard ceiling that includes thought and output tokens. Hitting it while reasoning can yield `incomplete` and truncated/empty output. The selected model does not support `minimal`; its lowest supported level is `low`, which the request uses. | [Thinking guide](https://ai.google.dev/gemini-api/docs/thinking); [Gemini 3.8 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash). |
| Timeout units | Pass. `HttpOptions.timeout=8000` produced 8.0-second HTTPX timeouts. Per-call `interactions.create(..., timeout=2.5)` produced 2.5-second timeouts. The pinned SDK documents the client value in milliseconds and per-call value in seconds. The guarded factory now requires `timeout_ms` (no default) within 1,000–120,000 ms; `timeout_ms=60000` reached the transport as 60-second connect/read/write/pool timeouts. | `google/genai/_api_client.py` converts client milliseconds to seconds; `google/genai/_gaos/interactions.py` converts per-call seconds to milliseconds before dispatch; captured mock request extensions showed the resulting values. `test_timeout_must_be_explicit_and_bounded` and `test_timeout_reaches_the_transport_in_seconds`. |
| Disable automatic retries | Pass with guard. SDK 2.28.0 still treats `attempts=1` as one retry on Interactions, but `SingleAttemptTransport` blocks the SDK retryable responses 408, 409, 429 and all 5xx as non-HTTPX permanent exceptions before SDK retry/sleep handling. Connection failures, and any other exception from the inner transport, are converted to non-HTTPX exceptions with conservative delivery classification. `HTTPTransport(retries=0)` disables its own transport retry path. Each provider call gets a new client and transport. The SDK would otherwise build its own unguarded async HTTPX client with the same one-retry mapping, so the factory passes an async client whose transport raises `AsyncProviderPathBlocked` before any I/O (this also prevents the SDK's aiohttp path). | `backend/tests/unit/test_ai_worker_transport_guard.py` runs against the exact pinned SDK and `httpx.MockTransport`; 408, 409, 429 and 503 with `Retry-After: 3600`, 500, 504, ConnectError, ConnectTimeout, ReadTimeout, WriteError, RemoteProtocolError, and a non-HTTPX `OSError` each yielded exactly one inner transport dispatch and no `time.sleep`/`asyncio.sleep`. A second request is blocked. `client.aio.interactions.create` and `client.aio.models.get` raise `AsyncProviderPathBlocked` with zero dispatches. |
| Retry-After and retry classification | Pass. The guard consumes retryable status responses before the SDK sees them, so an uncapped `Retry-After` cannot cause a worker sleep. ConnectError/ConnectTimeout map to `not_sent`; read/write/protocol and other HTTPX failures map to `may_have_been_sent`. Retryable HTTP statuses are conservatively `may_have_been_sent`. Provider messages are discarded; only allowlisted status/reason and request ID metadata may be kept. | Focused tests in `backend/tests/unit/test_ai_worker_transport_guard.py`; 429 includes `Retry-After: 3600` and verifies no sleep. Exception mapping tests verify one-shot handling and sanitized errors. |
| Error classes and status mapping | Pass for HTTP responses in both documented body formats: google.rpc-style (`error.status` plus `details[].reason`) and the current string `error.code` values (`invalid_request`, `failed_precondition`, `authentication`, `payment_required`, `permission_denied`, `not_found`, `model_not_found`, `rate_limit_exceeded`, `quota_exceeded`, `api_error`, `service_unavailable`, `deadline_exceeded`). 400→`INVALID_REQUEST`, or `BILLING_OR_PREREQUISITE` for `FAILED_PRECONDITION`/`failed_precondition`; 401→`bad_key`; 402→`PREPAY_DEPLETED`; 403→`PERMISSION_DENIED`; 404→`MODEL_UNAVAILABLE`, or `NOT_FOUND` for string code `not_found`; 408/504 and timeout errors→`timeout`; 429→`rate_limited`, or `QUOTA_EXHAUSTED` for quota reasons/statuses/codes, with a coarse 60-second cooldown; 500/503→`PROVIDER_UNAVAILABLE`. Other statuses map to generic `error`. Integer and unknown string codes are ignored; provider text is never included. SDK exception text can contain the provider message, so callers must log only `safe_log_fields(error)`. | Mock SDK calls for every listed status in each available format (402 and string `not_found` exist only in the current format; 409 only in the legacy format); pinned SDK `google/genai/_gaos/lib/compat_errors.py`; `backend/reconbench_app/ai/worker/transport_guard.py`. The current string-code format comes from Google's error reference and is verified only against mocks; the live format is observed at the synthetic smoke test. |
| API key location | Pass. Synthetic key was present as `x-goog-api-key`; absent from URL and serialized JSON body. | The pinned guard tests now assert this, plus `store:false`, top-level `response_format`, and the absence of `contents`, `generationConfig`, `responseMimeType`, `responseSchema`, `responseJsonSchema`, `tools`, `stream`, and `previous_interaction_id`, on every mocked SDK request. |
| Local tokenizer | No ready-to-use tokenizer in the locked environment. SDK contains `google.genai.local_tokenizer`, but import fails because optional `sentencepiece` is not installed. `models.count_tokens` is not a local-tokenizer substitute. | Local import attempted; `ModuleNotFoundError: sentencepiece`. No provider count-token call was made. |
| Procrastinate task names | Pass. The same explicit task name registered in two distinct `App` registries; registering that name twice in one app raised `TaskAlreadyRegistered`. | Procrastinate 3.10.0 local source and in-memory test; no DB connection. |

## Synthetic request captured

The following is the actual serialized body shape, with fixture values:

```json
{
  "model": "gemini-3.8-flash",
  "system_instruction": "Return only JSON matching the supplied schema.",
  "input": "Synthetic fixture; no business data.",
  "response_format": {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
      "type": "object",
      "properties": {"fixture": {"type": "string"}},
      "required": ["fixture"],
      "additionalProperties": false
    }
  },
  "generation_config": {
    "max_output_tokens": 128,
    "thinking_level": "low"
  },
  "store": false
}
```

The mock response had `status="completed"`, a text block in `steps`, and usage totals 11 input, 4 output, and 2 thought tokens. The SDK exposed the text block as `output_text`.

## Single-shot transport guard and exact local checks

The guard is implemented in `backend/reconbench_app/ai/worker/transport_guard.py`, inside the AI-worker-only package. It uses only the public `HttpOptions.httpx_client` and `HttpOptions.httpx_async_client` injection points, constructs `httpx.HTTPTransport(retries=0)` in production, and keeps `HttpRetryOptions(attempts=1)`. `new_gemini_client(api_key, *, timeout_ms, ...)` requires the timeout. Retryable statuses are converted to `ProviderAttemptFinished` with only safe metadata. HTTPX network errors and any other inner-transport exception become `ProviderTransportFailure` with delivery certainty and no underlying exception message. Unexpected second dispatches raise `DuplicateAttemptBlocked`; SDK async calls raise `AsyncProviderPathBlocked` before any I/O. The exception mapping helpers follow AI Assist Plan §5.4 without returning the provider's message or body, and `safe_log_fields(error)` is the only provider-error data that may be logged.

The import boundary is enforced now rather than in Phase 6. An import-linter contract forbids `reconbench_app.api`, `reconbench_app.jobs`, `reconbench_app.worker`, `reconbench_app.ingestion`, `reconbench_app.mapping_service`, `reconbench_app.reconciliation_service`, `reconbench_app.vendor_resolution` and `reconbench_app.ai_assist` from importing `reconbench_app.ai.worker` or `google`. `backend/tests/unit/test_ai_worker_boundary.py` scans every application module outside `reconbench_app/ai/worker/` (including function-level imports and literal `importlib` calls) for `google.genai` or `reconbench_app.ai.worker`, tests that scanner against synthetic violations, and checks in a subprocess that importing the API application, job registry and general worker loads neither.

The SDK itself retries on eligible response statuses when configured with `attempts=1`, and its retry utility honours `Retry-After` without a cap. Its Interactions call has no per-call retry override. The guard prevents those behaviors at the transport boundary without private SDK state. The locked-version assertion deliberately fails if the SDK version changes, requiring revalidation before an upgrade.

Exact focused checks run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_ai_worker_transport_guard.py tests/unit/test_ai_worker_boundary.py -q
.\.venv\Scripts\python.exe -m ruff check reconbench_app/ai tests/unit/test_ai_worker_transport_guard.py tests/unit/test_ai_worker_boundary.py
.\.venv\Scripts\python.exe -m ruff format --check reconbench_app/ai tests/unit/test_ai_worker_transport_guard.py tests/unit/test_ai_worker_boundary.py
.\.venv\Scripts\python.exe -m mypy reconbench_app/ai
.\.venv\Scripts\lint-imports
```

Results on 4 Oct 2026, after the review fixes: **60 passed** (45 guard, 15 boundary); Ruff check and format check pass; mypy reports no issues in 3 source files; import-linter keeps 3 contracts. The earlier 21-test run against `reconbench_app/ai_transport_guard.py` is superseded. All requests use synthetic API keys, payloads, and responses through mock transports.

## Exact earlier local checks

The throwaway script was kept outside the repository, as required by the plan:

```powershell
& .\backend\.venv\Scripts\python.exe "$env:TEMP\reconbench-ai-protocol-20261004\verify_protocol.py"
```

Result: request shape, v1 path, structured response parsing, `store:false`, header-only key, both timeout levels, status error mapping, optional tokenizer presence, and Procrastinate registration checks passed. At that time, the script observed two attempts for 429; the new guard test suite supersedes that earlier failing retry observation.

Dependency resolution and audit:

```powershell
uv lock --project backend
uv sync --project backend
& .\backend\.venv\Scripts\python.exe -m pip_audit --skip-editable
```

The lock/sync completed with `google-genai==2.28.0` and `cryptography==50.0.2`. `pip-audit` reported no known vulnerabilities and skipped the editable `reconbench-app` distribution. The Google SDK release is recorded at [v2.28.0](https://github.com/googleapis/python-genai/releases/tag/v2.28.0).

## Remaining limits and gate decision

The mocked retry criterion now passes for the sync call path, and the async path is blocked. The SDK retry configuration by itself is still unsuitable, so future adapter code must use the guarded client factory, call the SDK synchronously (from a worker thread if the task is async), and must not construct an unguarded Gemini client. The import boundary above is already enforced; Phase 6 should add a check that the adapter obtains clients only from `new_gemini_client`. Phase 5 must persist `not_sent` as `released` and `may_have_been_sent` as `uncertain`, without automatic retries; do not claim exactly-once execution.

The maximum output-token setting is a hard output bound and includes thought tokens. Reaching it at the model's minimum thinking level can produce `incomplete`; callers must count actual usage and treat incomplete output as `invalid_output`. The H1 4,096-token limit still needs a later smoke check. Input-token caps remain estimates because the optional local tokenizer requires `sentencepiece` and the server-side template overhead is not independently known. Do not claim guaranteed-token or USD hard-cap enforcement until those inputs are proven. Real-document/provider calls remain unapproved, and the six `docs/ReconBench_*.md` source copies were not changed.

## Phase 6 adapter checks (4 Oct 2026)

`backend/reconbench_app/ai/worker/gemini.py` obtains every client from `new_gemini_client`, sends exactly the shape above (`store:false`, top-level `response_format`, `generation_config` with `max_output_tokens` and `thinking_level: low`, no tools or streaming), and accepts output only when `status == "completed"` and `output_text` is non-empty. Incomplete or failed interactions become `invalid_output` with any reported usage kept for billing. Every HTTP status and transport failure maps to a sanitized code and a delivery certainty (`responded`, `not_sent`, `may_have_been_sent`) after exactly one transport attempt, with no sleep and no provider message or key in the exception. `backend/tests/unit/test_ai_gemini_adapter.py`: 20 passed against the locked SDK over `httpx.MockTransport`. No live provider call was made.

## Page-image input for the vision reader (AC-5, 7 Oct 2026)

`h6_vision` sends masked page images. `ProviderRequest.images` carries one `ProviderImage` per page (caption, MIME type, bytes, SHA-256, `resolution`). With images, `gemini.py` passes `input` as a content list (an intro text, then a `Page N` text and an image part per page); text-only hooks keep the plain-string `input`, so their request shape and payload digests are unchanged. The pinned SDK 2.28.0 serialises that list as **one `user_input` step**:

```json
"input": [{"type": "user_input", "content": [
  {"type": "text", "text": "<intro: page numbers only>"},
  {"type": "text", "text": "Page 1"},
  {"type": "image", "data": "<base64 JPEG>", "mime_type": "image/jpeg", "resolution": "high"}
]}]
```

`backend/tests/unit/test_ai_vision.py` checks this shape, the key in the header only and `store:false` over `httpx.MockTransport`. The payload digest adds `images` (caption, MIME type, image hash, resolution) only when images are sent. The response schema (`vision.schema.v2` since D38; v2 makes `description` required in each line row) uses only the constructs H1 already uses on Gemini (object, array, string enums, integer; no `maxItems`, `minItems`, `$ref` or `anyOf`). Whether the live API accepts this body, how `box_2d` is ordered, and the input tokens per page at each `resolution` are **not proven by mocks**: `scripts/vision_probe.py` (owner-run, dry run by default) measures them, and its numbers feed the cost cap (D-8). The owner probe of 7 Oct 2026 accepted the body and measured about 1,100 input tokens per page at `high` (D38). The vision call timeout is 120 s, the guard's maximum.

A finished interaction that is not `completed` (for example `incomplete` when the answer reaches `max_output_tokens`) is `invalid_output` in the app, with its token usage recorded. The app never logs provider messages; only the owner's bench and probe read them, through `scripts/vision_diagnostics.py`, which sits under the single-attempt guard and keeps Google's error text (HTTP errors) or the answer's status, tokens and row counts (never the text).
