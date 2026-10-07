"""Execute one AI ledger row: gate → reserve → open key → re-check → send → apply.

No database lock is held during the provider call. ``sending`` is committed
before the call so a crash is swept to ``uncertain``; nothing is retried
automatically after that point. Results are applied in one transaction only if
settings, consent, key and source are still current; otherwise they are
discarded and the reason recorded.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import ledger
from reconbench_app.ai.contracts import OUTPUT_MODELS, InvalidOutput, parse_output
from reconbench_app.ai.gate import DeploymentGate, payload_digest
from reconbench_app.ai.limits import (
    LimitsState,
    PriceFile,
    cost_usd,
    estimate_reservation,
)
from reconbench_app.ai.prompts import payload_bytes
from reconbench_app.ai.provider import AiProvider, ProviderFailure, ProviderRequest
from reconbench_app.ai.runtime_checks import (
    BLOCK_KEY_UNREADABLE,
    load_key,
    load_settings,
    request_blocker,
)
from reconbench_app.ai.worker.handlers import Handler, Prepared, Skip, record_test_failure
from reconbench_app.ai.worker.key_open import Keyring, KeyUnreadable
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

SKIP_NO_HANDLER = "NO_HANDLER"
SKIP_LIMITS_NOT_CONFIGURED = "LIMITS_NOT_CONFIGURED"
SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"

_OUTCOMES = {
    "timeout": "timeout",
    "rate_limited": "rate_limited",
    "QUOTA_EXHAUSTED": "rate_limited",
    "bad_key": "bad_key",
    "invalid_output": "invalid_output",
    "ungrounded": "ungrounded",
}


class AiRequestDeferred(RuntimeError):
    """Budget windows are full; the job is retried later without calling the provider."""

    def __init__(self, code: str, retry_after_seconds: int) -> None:
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"AI request deferred ({code})")


@dataclass(frozen=True, slots=True)
class RunResult:
    state: str
    code: str | None = None
    apply_outcome: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiRequestRunner:
    def __init__(
        self,
        *,
        engine: Engine,
        keyring: Keyring,
        gate: DeploymentGate,
        limits: Callable[[], LimitsState],
        prices: Callable[[], PriceFile | None],
        provider: AiProvider,
        handlers: Mapping[str, Handler],
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.engine = engine
        self.keyring = keyring
        self.gate = gate
        self.limits = limits
        self.prices = prices
        self.provider = provider
        self.handlers = handlers
        self.clock = clock

    def _row(self, connection: Connection, request_id: UUID) -> Any:
        return (
            connection.execute(sa.select(ledger.REQUESTS).where(ledger.REQUESTS.c.id == request_id))
            .mappings()
            .first()
        )

    def run(self, request_id: UUID, *, job_id: int | None = None) -> RunResult:
        now = self.clock()
        limits_state = self.limits()
        with self.engine.connect() as connection:
            request = self._row(connection, request_id)
            if request is None or request["request_state"] != "queued":
                return RunResult("not_queued")
            handler = self.handlers.get(request["hook"] or request["call_kind"])
            if handler is None:
                ledger.skip(self.engine, request_id, SKIP_NO_HANDLER, now)
                return RunResult("skipped", SKIP_NO_HANDLER)
            if limits_state.limits is None:
                ledger.skip(self.engine, request_id, SKIP_LIMITS_NOT_CONFIGURED, now)
                return RunResult("skipped", SKIP_LIMITS_NOT_CONFIGURED)
            limits = limits_state.limits
            blocker = request_blocker(connection, request, now, provider_name=self.provider.name)
            if blocker is not None:
                ledger.skip(self.engine, request_id, blocker, now)
                return RunResult("skipped", blocker)
            prepared = handler.prepare(connection, request, limits)
            connection.rollback()
        if isinstance(prepared, Skip):
            ledger.skip(self.engine, request_id, prepared.code, now)
            return RunResult("skipped", prepared.code)

        kind = request["hook"] or request["call_kind"]
        kind_limits = limits.for_kind(kind)
        timeout_seconds = limits.timeout_for(kind)
        size = payload_bytes(prepared.payload)
        images = prepared.payload.images
        if size > kind_limits.max_input_bytes or (
            images
            and (
                len(images) > (kind_limits.max_pages or 0)
                or sum(len(image.data) for image in images) > (kind_limits.max_image_bytes or 0)
            )
        ):
            ledger.skip(self.engine, request_id, ledger.SKIP_INPUT_TOO_LARGE, now)
            return RunResult("skipped", ledger.SKIP_INPUT_TOO_LARGE)
        digest = payload_digest(
            system_instruction=prepared.payload.system_instruction,
            input_text=prepared.payload.input_text,
            response_schema=prepared.payload.response_schema,
            images=prepared.payload.image_digest_parts(),
        )
        refusal = self.gate.refusal(
            subject_type=str(request["subject_type"]),
            fixture_id=prepared.fixture_id,
            digest=digest,
        )
        if refusal is not None:
            ledger.skip(self.engine, request_id, refusal, now)
            return RunResult("skipped", refusal)

        cache_key = None
        if prepared.cacheable and request["hook"] is not None:
            cache_key = ai_cache.cache_key(
                hook=request["hook"],
                model=request["model"],
                prompt_version=request["prompt_version"],
                schema_version=request["schema_version"],
                redaction_version=request["redaction_version"],
                payload_digest=digest,
            )
            with self.engine.begin() as connection:
                connection.execute(
                    sa.update(ledger.REQUESTS)
                    .where(ledger.REQUESTS.c.id == request_id)
                    .values(cache_key=cache_key, job_id=job_id)
                )
                hit = ai_cache.lookup(
                    connection,
                    workspace_id=request["workspace_id"],
                    key=cache_key,
                    now=now,
                    ttl_days=limits.cache_ttl_days,
                )
                if hit is not None:
                    return self._apply_cache_hit(
                        connection, request, handler, prepared, hit, digest, now
                    )

        max_output_tokens = min(prepared.max_output_tokens, kind_limits.max_output_tokens)
        prices = self.prices()
        reservation = estimate_reservation(
            payload_bytes=size,
            max_output_tokens=max_output_tokens,
            limits=limits,
            model=str(request["model"]),
            prices=prices,
            image_tokens=len(images) * (kind_limits.image_tokens_estimate or 0),
        )
        reserved = ledger.reserve(
            self.engine,
            request_id,
            reservation=reservation,
            limits=limits,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        if reserved.status == "deferred":
            raise AiRequestDeferred(reserved.code or "DEFERRED", reserved.retry_after_seconds or 10)
        if reserved.status != "reserved":
            return RunResult(reserved.status, reserved.code)

        with self.engine.connect() as connection:
            settings = load_settings(connection, request["workspace_id"])
            key = load_key(connection, settings, now) if settings is not None else "KEY_NOT_SET"
            blocker = request_blocker(connection, request, now, provider_name=self.provider.name)
            current = handler.is_current(connection, request)
            connection.rollback()
        if blocker is not None or isinstance(key, str):
            code = blocker or str(key)
            ledger.release(self.engine, request_id, code, now)
            return RunResult("released", code)
        if not current:
            ledger.release(self.engine, request_id, SKIP_SOURCE_CHANGED, now)
            return RunResult("released", SKIP_SOURCE_CHANGED)
        try:
            api_key = self.keyring.open(
                envelope=key.envelope,
                nonce=key.nonce,
                stored_key_id=key.key_id,
                workspace_id=request["workspace_id"],
                secret_id=key.secret_id,
            ).decode("utf-8")
        except (KeyUnreadable, UnicodeDecodeError):
            ledger.release(self.engine, request_id, BLOCK_KEY_UNREADABLE, now)
            return RunResult("released", BLOCK_KEY_UNREADABLE)

        if not ledger.mark_sending(self.engine, request_id, self.clock(), timeout_seconds):
            return RunResult("not_queued")
        started = time.monotonic()
        try:
            result = self.provider.complete(
                ProviderRequest(
                    model=str(request["model"]),
                    system_instruction=prepared.payload.system_instruction,
                    input_text=prepared.payload.input_text,
                    response_schema=prepared.payload.response_schema,
                    max_output_tokens=max_output_tokens,
                    timeout_ms=timeout_seconds * 1_000,
                    images=images,
                ),
                api_key=api_key,
            )
        except ProviderFailure as failure:
            del api_key
            return self._record_failure(request, prepared, failure, digest, started, job_id, prices)
        del api_key
        latency_ms = int((time.monotonic() - started) * 1_000)
        finished_at = self.clock()
        usage = result.usage
        actual_usd = None
        price = prices.models.get(str(request["model"])) if prices else None
        if usage is not None and price is not None:
            actual_usd = cost_usd(
                price,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens + (usage.thought_tokens or 0),
            )

        try:
            parsed = parse_output(prepared.output_kind, result.output_text)
            validated = handler.validate(prepared, parsed)
            invalid: InvalidOutput | None = None
        except InvalidOutput as exc:
            invalid, parsed, validated = exc, None, None

        with self.engine.begin() as connection:
            ledger_usage = (
                ledger.Usage(usage.input_tokens, usage.output_tokens, usage.thought_tokens)
                if usage is not None
                else None
            )
            if invalid is not None:
                call_log_id = self._call_log(
                    connection,
                    request,
                    prepared,
                    digest,
                    invalid.code,
                    invalid.code,
                    ledger_usage,
                    actual_usd,
                    latency_ms,
                    job_id,
                )
                record_test_failure(connection, request, finished_at)
                ledger.finish(
                    connection,
                    request_id,
                    state="succeeded",
                    now=finished_at,
                    error_code=invalid.code,
                    usage=ledger_usage,
                    actual_usd=actual_usd,
                    apply_outcome="discarded_invalid",
                )
                return RunResult("succeeded", invalid.code, "discarded_invalid")

            locked = self._row(connection, request_id)
            blocker = request_blocker(
                connection, locked, finished_at, provider_name=self.provider.name
            )
            outcome: str
            call_log_id = self._call_log(
                connection,
                request,
                prepared,
                digest,
                "ok",
                None,
                ledger_usage,
                actual_usd,
                latency_ms,
                job_id,
            )
            if blocker is not None:
                outcome = "discarded_revoked"
            elif not handler.is_current(connection, locked):
                outcome = "discarded_stale"
            else:
                outcome = handler.apply(
                    connection,
                    locked,
                    prepared,
                    validated,
                    call_log_id=call_log_id,
                    now=finished_at,
                )
            if cache_key is not None and parsed is not None:
                ai_cache.store(
                    connection,
                    workspace_id=request["workspace_id"],
                    key=cache_key,
                    hook=str(request["hook"]),
                    model=str(request["model"]),
                    prompt_version=str(request["prompt_version"]),
                    document_hash=prepared.document_hash,
                    normalized_output=handler.normalized(parsed),
                )
            ledger.finish(
                connection,
                request_id,
                state="succeeded",
                now=finished_at,
                usage=ledger_usage,
                actual_usd=actual_usd,
                apply_outcome=outcome,
            )
        return RunResult("succeeded", None, outcome)

    def _apply_cache_hit(
        self,
        connection: Connection,
        request: Any,
        handler: Handler,
        prepared: Prepared,
        hit: tuple[UUID, dict[str, Any]],
        digest: str,
        now: datetime,
    ) -> RunResult:
        cache_id, response = hit
        model = OUTPUT_MODELS[prepared.output_kind]
        try:
            validated = handler.validate(prepared, model.model_validate(response))
        except (InvalidOutput, ValueError):
            validated = None
        ledger.record_cache_hit(connection, request["id"], cache_id=cache_id, now=now)
        call_log_id = self._call_log(
            connection, request, prepared, digest, "cache_hit", None, None, None, 0, None
        )
        if validated is None:
            outcome = "discarded_invalid"
        elif not handler.is_current(connection, request):
            outcome = "discarded_stale"
        else:
            outcome = handler.apply(
                connection, request, prepared, validated, call_log_id=call_log_id, now=now
            )
        ledger.set_apply_outcome(connection, request["id"], outcome)
        return RunResult("succeeded", "cache_hit", outcome)

    def _record_failure(
        self,
        request: Any,
        prepared: Prepared,
        failure: ProviderFailure,
        digest: str,
        started: float,
        job_id: int | None,
        prices: PriceFile | None,
    ) -> RunResult:
        now = self.clock()
        state = {
            "responded": "failed",
            "not_sent": "released",
            "may_have_been_sent": "uncertain",
        }[failure.delivery]
        usage = (
            ledger.Usage(
                failure.usage.input_tokens,
                failure.usage.output_tokens,
                failure.usage.thought_tokens,
            )
            if failure.usage is not None
            else None
        )
        actual_usd = None
        price = prices.models.get(str(request["model"])) if prices else None
        if failure.usage is not None and price is not None:
            actual_usd = cost_usd(
                price,
                input_tokens=failure.usage.input_tokens,
                output_tokens=failure.usage.output_tokens + (failure.usage.thought_tokens or 0),
            )
        with self.engine.begin() as connection:
            if state != "released":
                self._call_log(
                    connection,
                    request,
                    prepared,
                    digest,
                    failure.code,
                    failure.code,
                    usage,
                    actual_usd,
                    int((time.monotonic() - started) * 1_000),
                    job_id,
                )
            record_test_failure(connection, request, now)
            ledger.finish(
                connection,
                request["id"],
                state=state,  # type: ignore[arg-type]
                now=now,
                error_code=failure.code,
                usage=usage,
                actual_usd=actual_usd,
            )
        return RunResult(state, failure.code)

    def _call_log(
        self,
        connection: Connection,
        request: Any,
        prepared: Prepared,
        digest: str,
        outcome_code: str,
        error_code: str | None,
        usage: ledger.Usage | None,
        actual_usd: Any,
        latency_ms: int,
        job_id: int | None,
    ) -> UUID | None:
        if request["hook"] is None:
            return None
        call_log_id = new_id()
        connection.execute(
            sa.insert(metadata.tables["ai_call_log"]).values(
                id=call_log_id,
                workspace_id=request["workspace_id"],
                run_id=request["run_id"],
                document_id=prepared.document_id,
                document_version_id=prepared.document_version_id,
                hook=request["hook"],
                provider=request["provider"],
                model=request["model"],
                prompt_version=request["prompt_version"],
                input_hash=digest,
                outcome=(
                    "ok"
                    if outcome_code == "ok"
                    else "cache_hit"
                    if outcome_code == "cache_hit"
                    else _OUTCOMES.get(outcome_code, "error")
                ),
                error_code=error_code,
                redacted=prepared.redacted,
                tokens_in=usage.input_tokens if usage else None,
                tokens_out=usage.output_tokens if usage else None,
                tokens_thought=usage.thought_tokens if usage else None,
                cost_usd=actual_usd,
                latency_ms=latency_ms,
                job_id=job_id,
                ai_request_id=request["id"],
            )
        )
        return call_log_id


__all__ = ["AiRequestDeferred", "AiRequestRunner", "RunResult"]
