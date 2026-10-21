"""Durable AI request ledger (decision D8; AI Assist plan §5.2).

States: queued → reserved → sending → succeeded | failed | uncertain;
reserved → released (provably not sent); queued → skipped.
Reservations happen under the workspace row lock in a short transaction; the
row is marked ``sending`` and committed *before* any network I/O, so a crash
after that point is swept to ``uncertain``. Uncertain requests are never retried
automatically; one audited acknowledgement allows exactly one new attempt.
Budgets count actual usage once reported, otherwise the reservation; only
queued, skipped and released rows (and cache hits) count as zero.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai.limits import AiLimits, Reservation
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

REQUESTS = metadata.tables["ai_request"]
LOGS = metadata.tables["ai_call_log"]
LIVE_STATES = ("queued", "reserved", "sending")
RATE_LIMIT_CODES = ("rate_limited", "QUOTA_EXHAUSTED")
# A self-hosted provider that answered 429 or 503 is left alone for its kind's
# ``backoff_seconds`` (WI-BE2), across workspaces: the server is shared.
PROVIDER_BACKOFF_CODES = ("rate_limited", "PROVIDER_UNAVAILABLE")
LEASE_MARGIN = timedelta(seconds=30)

SKIP_QUEUE_TIMEOUT = "QUEUE_TIMEOUT"
SKIP_DAY_BUDGET = "DAY_BUDGET_REACHED"
SKIP_MONTH_BUDGET = "MONTH_BUDGET_REACHED"
SKIP_OPERATION_BUDGET = "OPERATION_BUDGET_REACHED"
SKIP_INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
ERROR_LEASE_EXPIRED = "LEASE_EXPIRED"

_COUNTED_TOKENS = sa.text(
    "CASE WHEN request_state IN ('queued', 'skipped', 'released') OR ai_cache_id IS NOT NULL "
    "THEN 0 WHEN usage_known THEN tokens_input + tokens_output + coalesce(tokens_thought, 0) "
    "ELSE reserved_input_tokens + reserved_output_tokens END"
)
_COUNTED_CALL = sa.text(
    "request_state NOT IN ('queued', 'skipped', 'released') AND ai_cache_id IS NULL"
)


def request_key(
    *,
    call_kind: str,
    hook: str | None,
    subject_type: str,
    subject_id: UUID | None,
    subject_version: str,
    settings_version: int,
    consent_fingerprint: str | None,
    model: str,
) -> str:
    """Identity of one logical request: same subject version under the same settings."""

    document = {
        "call_kind": call_kind,
        "consent_fingerprint": consent_fingerprint,
        "hook": hook,
        "model": model,
        "settings_version": settings_version,
        "subject_id": str(subject_id) if subject_id else None,
        "subject_type": subject_type,
        "subject_version": subject_version,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NewRequest:
    workspace_id: UUID
    call_kind: Literal["hook", "provider_test", "synthetic_smoke"]
    hook: str | None
    mode: str
    subject_type: str
    subject_id: UUID | None
    subject_version: str
    operation_key: str
    settings_version: int
    consent_fingerprint: str | None
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    redaction_version: str
    run_id: UUID | None = None
    requested_by: UUID | None = None
    # Reader and image parameters (vision only, WI-BE5); text hooks send none.
    reader_params: Mapping[str, Any] = field(default_factory=dict)
    # A vision request of the workspace's one reader in ``shadow`` (WI-BE6): its answer
    # goes to ``reader_comparison`` only, never to consensus or a suggestion.
    shadow: bool = False

    @property
    def key(self) -> str:
        return request_key(
            call_kind=self.call_kind,
            hook=self.hook,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            subject_version=self.subject_version,
            settings_version=self.settings_version,
            consent_fingerprint=self.consent_fingerprint,
            model=self.model,
        )


def create_request(connection: Connection, request: NewRequest) -> UUID | None:
    """Insert a queued attempt unless one is live, finished, or unacknowledged.

    A new attempt is created only for a new logical request, or after the latest
    attempt was released, skipped, or acknowledged as uncertain.
    """

    key = request.key
    latest = (
        connection.execute(
            sa.select(REQUESTS.c.attempt_no, REQUESTS.c.request_state, REQUESTS.c.acknowledged_at)
            .where(REQUESTS.c.workspace_id == request.workspace_id, REQUESTS.c.request_key == key)
            .order_by(REQUESTS.c.attempt_no.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    attempt_no = 1
    if latest is not None:
        state = latest["request_state"]
        reusable = state in {"released", "skipped"} or (
            state == "uncertain" and latest["acknowledged_at"] is not None
        )
        if not reusable:
            return None
        attempt_no = int(latest["attempt_no"]) + 1
    request_id = new_id()
    inserted = connection.execute(
        postgres_insert(REQUESTS)
        .values(
            **{
                "id": request_id,
                "workspace_id": request.workspace_id,
                "call_kind": request.call_kind,
                "hook": request.hook,
                "mode": request.mode,
                "subject_type": request.subject_type,
                "subject_id": request.subject_id,
                "subject_version": request.subject_version,
                "run_id": request.run_id,
                "operation_key": request.operation_key,
                "requested_by": request.requested_by,
                "settings_version": request.settings_version,
                "consent_fingerprint": request.consent_fingerprint,
                "provider": request.provider,
                "model": request.model,
                "prompt_version": request.prompt_version,
                "schema_version": request.schema_version,
                "redaction_version": request.redaction_version,
                "reader_params": dict(request.reader_params),
                "shadow": request.shadow,
                "request_key": key,
                "attempt_no": attempt_no,
            }
        )
        .on_conflict_do_nothing()
        .returning(REQUESTS.c.id)
    ).scalar_one_or_none()
    return UUID(str(inserted)) if inserted is not None else None


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    status: Literal["reserved", "skipped", "deferred", "not_queued"]
    code: str | None = None
    retry_after_seconds: int | None = None


def _usage_since(
    connection: Connection,
    workspace_id: UUID,
    since: datetime | None,
    operation_key: str | None,
    provider: str | None = None,
) -> tuple[int, int]:
    conditions = [REQUESTS.c.workspace_id == workspace_id]
    if provider is not None:
        conditions.append(REQUESTS.c.provider == provider)
    if since is not None:
        conditions.append(REQUESTS.c.reserved_at >= since)
    if operation_key is not None:
        conditions.append(REQUESTS.c.operation_key == operation_key)
    row = connection.execute(
        sa.select(
            sa.func.count().filter(_COUNTED_CALL),
            sa.func.coalesce(sa.func.sum(_COUNTED_TOKENS), 0),
        ).where(*conditions)
    ).one()
    return int(row[0]), int(row[1])


def reserve(
    engine: Engine,
    request_id: UUID,
    *,
    reservation: Reservation,
    limits: AiLimits,
    now: datetime,
    timeout_seconds: int | None = None,
    max_concurrency: int | None = None,
    backoff_seconds: int | None = None,
) -> ReserveOutcome:
    """Reserve budget for a queued request; no lock is held once this returns.

    ``timeout_seconds`` is the call timeout of the request's kind (the lease covers it).
    ``max_concurrency`` caps in-flight requests to the request's provider across all
    workspaces (a shared self-hosted server); ``backoff_seconds`` defers a request
    while that provider's last 429/503 answer is that recent.
    """

    with engine.begin() as connection:
        workspace_id = connection.scalar(
            sa.select(REQUESTS.c.workspace_id).where(REQUESTS.c.id == request_id)
        )
        if workspace_id is None:
            return ReserveOutcome("not_queued")
        connection.execute(
            sa.text("SELECT id FROM workspace WHERE id = :id FOR UPDATE"), {"id": workspace_id}
        )
        row = (
            connection.execute(
                sa.select(REQUESTS).where(REQUESTS.c.id == request_id).with_for_update()
            )
            .mappings()
            .one()
        )
        if row["request_state"] != "queued":
            return ReserveOutcome("not_queued")
        if row["created_at"] <= now - timedelta(seconds=limits.queue_timeout_seconds):
            _skip(connection, request_id, SKIP_QUEUE_TIMEOUT, now)
            return ReserveOutcome("skipped", SKIP_QUEUE_TIMEOUT)

        cooldown_until = connection.scalar(
            sa.select(sa.func.max(REQUESTS.c.finished_at)).where(
                REQUESTS.c.workspace_id == workspace_id,
                REQUESTS.c.error_code.in_(RATE_LIMIT_CODES),
            )
        )
        if cooldown_until is not None:
            remaining = cooldown_until + timedelta(seconds=limits.cooldown_seconds) - now
            if remaining > timedelta(0):
                return ReserveOutcome("deferred", "COOLDOWN", int(remaining.total_seconds()) + 1)
        in_flight = int(
            connection.scalar(
                sa.select(sa.func.count()).where(
                    REQUESTS.c.workspace_id == workspace_id,
                    REQUESTS.c.request_state.in_(("reserved", "sending")),
                )
            )
            or 0
        )
        if in_flight >= limits.in_flight:
            return ReserveOutcome("deferred", "IN_FLIGHT_LIMIT", 5)
        provider = row["provider"]
        if backoff_seconds is not None:
            backoff_from = connection.scalar(
                sa.select(sa.func.max(REQUESTS.c.finished_at)).where(
                    REQUESTS.c.provider == provider,
                    REQUESTS.c.error_code.in_(PROVIDER_BACKOFF_CODES),
                )
            )
            if backoff_from is not None:
                remaining = backoff_from + timedelta(seconds=backoff_seconds) - now
                if remaining > timedelta(0):
                    return ReserveOutcome(
                        "deferred", "PROVIDER_BACKOFF", int(remaining.total_seconds()) + 1
                    )
        if max_concurrency is not None:
            # Serialises reservations per provider; taken after the workspace lock.
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"ai_provider_in_flight:{provider}"},
            )
            provider_in_flight = int(
                connection.scalar(
                    sa.select(sa.func.count()).where(
                        REQUESTS.c.provider == provider,
                        REQUESTS.c.request_state.in_(("reserved", "sending")),
                    )
                )
                or 0
            )
            if provider_in_flight >= max_concurrency:
                return ReserveOutcome("deferred", "PROVIDER_IN_FLIGHT_LIMIT", 10)
        minute_calls, _ = _usage_since(connection, workspace_id, now - timedelta(minutes=1), None)
        if minute_calls >= limits.per_minute_calls:
            return ReserveOutcome("deferred", "MINUTE_LIMIT", 15)

        tokens = reservation.input_tokens + reservation.output_tokens
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        day_calls, day_tokens = _usage_since(connection, workspace_id, day_start, None)
        if day_calls + 1 > limits.per_day_calls or day_tokens + tokens > limits.per_day_tokens:
            _skip(connection, request_id, SKIP_DAY_BUDGET, now)
            return ReserveOutcome("skipped", SKIP_DAY_BUDGET)
        if limits.per_month_calls is not None or limits.per_month_tokens is not None:
            month_calls, month_tokens = _usage_since(
                connection, workspace_id, _month_start(now), None
            )
            if (
                limits.per_month_calls is not None and month_calls + 1 > limits.per_month_calls
            ) or (
                limits.per_month_tokens is not None
                and month_tokens + tokens > limits.per_month_tokens
            ):
                _skip(connection, request_id, SKIP_MONTH_BUDGET, now)
                return ReserveOutcome("skipped", SKIP_MONTH_BUDGET)
        operation_calls, operation_tokens = _usage_since(
            connection, workspace_id, None, str(row["operation_key"])
        )
        if (
            operation_calls + 1 > limits.per_operation_calls
            or operation_tokens + tokens > limits.per_operation_tokens
        ):
            _skip(connection, request_id, SKIP_OPERATION_BUDGET, now)
            return ReserveOutcome("skipped", SKIP_OPERATION_BUDGET)

        connection.execute(
            sa.update(REQUESTS)
            .where(REQUESTS.c.id == request_id)
            .values(
                request_state="reserved",
                reserved_at=now,
                lease_expires_at=now
                + timedelta(seconds=timeout_seconds or limits.call_timeout_seconds)
                + LEASE_MARGIN,
                reserved_input_tokens=reservation.input_tokens,
                reserved_output_tokens=reservation.output_tokens,
                reservation_is_estimate=reservation.is_estimate,
                reserved_usd=reservation.usd,
                price_version=reservation.price_version,
            )
        )
    return ReserveOutcome("reserved")


def _month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _skip(connection: Connection, request_id: UUID, code: str, now: datetime) -> None:
    connection.execute(
        sa.update(REQUESTS)
        .where(REQUESTS.c.id == request_id, REQUESTS.c.request_state == "queued")
        .values(
            request_state="skipped", skip_code=code, finished_at=now, apply_outcome="not_applicable"
        )
    )


def skip_in(connection: Connection, request_id: UUID, code: str, now: datetime) -> None:
    """Skip a queued request inside the caller's transaction."""

    _skip(connection, request_id, code, now)


def skip(engine: Engine, request_id: UUID, code: str, now: datetime) -> None:
    with engine.begin() as connection:
        _skip(connection, request_id, code, now)


def release(engine: Engine, request_id: UUID, code: str, now: datetime) -> bool:
    """Reserved → released: the provider was provably never contacted."""

    with engine.begin() as connection:
        result = connection.execute(
            sa.update(REQUESTS)
            .where(REQUESTS.c.id == request_id, REQUESTS.c.request_state == "reserved")
            .values(
                request_state="released",
                error_code=code,
                finished_at=now,
                lease_expires_at=None,
                apply_outcome="not_applicable",
            )
        )
    return bool(result.rowcount)


def mark_sending(engine: Engine, request_id: UUID, now: datetime, lease_seconds: int) -> bool:
    """Commit ``sending`` before network I/O so a crash is visible as uncertain."""

    with engine.begin() as connection:
        result = connection.execute(
            sa.update(REQUESTS)
            .where(REQUESTS.c.id == request_id, REQUESTS.c.request_state == "reserved")
            .values(
                request_state="sending",
                sent_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds) + LEASE_MARGIN,
            )
        )
    return bool(result.rowcount)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thought_tokens: int | None


def finish(
    connection: Connection,
    request_id: UUID,
    *,
    state: Literal["succeeded", "failed", "uncertain", "released"],
    now: datetime,
    error_code: str | None = None,
    usage: Usage | None = None,
    actual_usd: Decimal | None = None,
    apply_outcome: str | None = None,
) -> bool:
    values: dict[str, Any] = {
        "request_state": state,
        "finished_at": now,
        "lease_expires_at": None,
        "error_code": error_code,
    }
    if state == "released":
        # Provably never reached the provider (connection refused before sending).
        values["sent_at"] = None
    if usage is not None:
        values.update(
            usage_known=True,
            tokens_input=usage.input_tokens,
            tokens_output=usage.output_tokens,
            tokens_thought=usage.thought_tokens,
            actual_usd=actual_usd,
        )
    if apply_outcome is not None:
        values["apply_outcome"] = apply_outcome
    elif state != "succeeded":
        values["apply_outcome"] = "not_applicable"
    result = connection.execute(
        sa.update(REQUESTS)
        .where(REQUESTS.c.id == request_id, REQUESTS.c.request_state == "sending")
        .values(**values)
    )
    return bool(result.rowcount)


def record_cache_hit(
    connection: Connection, request_id: UUID, *, cache_id: UUID, now: datetime
) -> bool:
    """Queued → succeeded from a workspace cache entry: no provider call, no budget."""

    result = connection.execute(
        sa.update(REQUESTS)
        .where(REQUESTS.c.id == request_id, REQUESTS.c.request_state == "queued")
        .values(
            request_state="succeeded",
            reserved_at=now,
            sent_at=now,
            finished_at=now,
            usage_known=True,
            tokens_input=0,
            tokens_output=0,
            tokens_thought=0,
            ai_cache_id=cache_id,
        )
    )
    return bool(result.rowcount)


def set_apply_outcome(connection: Connection, request_id: UUID, outcome: str) -> None:
    connection.execute(
        sa.update(REQUESTS)
        .where(REQUESTS.c.id == request_id, REQUESTS.c.apply_outcome == "pending")
        .values(apply_outcome=outcome)
    )


def acknowledge(
    connection: Connection, *, workspace_id: UUID, request_id: UUID, user_id: UUID, now: datetime
) -> bool:
    """Record that a person accepts an uncertain (possibly billed) outcome."""

    result = connection.execute(
        sa.update(REQUESTS)
        .where(
            REQUESTS.c.id == request_id,
            REQUESTS.c.workspace_id == workspace_id,
            REQUESTS.c.request_state == "uncertain",
            REQUESTS.c.acknowledged_at.is_(None),
        )
        .values(acknowledged_by=user_id, acknowledged_at=now)
    )
    return bool(result.rowcount)


@dataclass(frozen=True, slots=True)
class SweepCounts:
    uncertain: int
    released: int
    skipped: int


def sweep_requests(engine: Engine, *, now: datetime, queue_timeout_seconds: int) -> SweepCounts:
    with engine.begin() as connection:
        uncertain = connection.execute(
            sa.update(REQUESTS)
            .where(REQUESTS.c.request_state == "sending", REQUESTS.c.lease_expires_at < now)
            .values(
                request_state="uncertain",
                error_code=ERROR_LEASE_EXPIRED,
                finished_at=now,
                lease_expires_at=None,
                apply_outcome="not_applicable",
            )
        ).rowcount
        released = connection.execute(
            sa.update(REQUESTS)
            .where(REQUESTS.c.request_state == "reserved", REQUESTS.c.lease_expires_at < now)
            .values(
                request_state="released",
                error_code=ERROR_LEASE_EXPIRED,
                finished_at=now,
                lease_expires_at=None,
                apply_outcome="not_applicable",
            )
        ).rowcount
        skipped = connection.execute(
            sa.update(REQUESTS)
            .where(
                REQUESTS.c.request_state == "queued",
                REQUESTS.c.created_at < now - timedelta(seconds=queue_timeout_seconds),
            )
            .values(
                request_state="skipped",
                skip_code=SKIP_QUEUE_TIMEOUT,
                finished_at=now,
                apply_outcome="not_applicable",
            )
        ).rowcount
    return SweepCounts(int(uncertain or 0), int(released or 0), int(skipped or 0))


def sweep_expired_secrets(engine: Engine, *, now: datetime) -> int:
    """Delete session-bound keys whose session ended or whose time limit passed."""

    with engine.begin() as connection:
        expired = list(
            connection.scalars(
                sa.text(
                    "SELECT secret.id FROM secret "
                    "LEFT JOIN session ON session.id = secret.session_id "
                    "WHERE secret.expires_at IS NOT NULL AND ("
                    "secret.expires_at <= :now OR session.id IS NULL "
                    "OR session.revoked_at IS NOT NULL OR session.idle_expires_at <= :now "
                    "OR session.abs_expires_at <= :now) FOR UPDATE OF secret"
                ),
                {"now": now},
            )
        )
        if not expired:
            return 0
        connection.execute(
            sa.text(
                "UPDATE ai_setting SET key_secret_id = NULL, version = version + 1, "
                "updated_at = :now WHERE key_secret_id = ANY(:ids)"
            ),
            {"now": now, "ids": expired},
        )
        clear_provider_config_keys(connection, expired, now=now)
        connection.execute(sa.text("DELETE FROM secret WHERE id = ANY(:ids)"), {"ids": expired})
    return len(expired)


def clear_provider_config_keys(
    connection: Connection, secret_ids: list[Any], *, now: datetime
) -> None:
    """Detach expiring keys from provider cards; the workspace's settings version moves on.

    A card that loses its live key goes back to Not connected; a rotation candidate that
    expires is simply dropped (the live key and the card's state stay).
    """

    connection.execute(
        sa.text(
            "UPDATE ai_provider_config SET pending_key_secret_id = NULL, version = version + 1, "
            "updated_at = :now WHERE pending_key_secret_id = ANY(:ids)"
        ),
        {"now": now, "ids": secret_ids},
    )
    workspaces = list(
        connection.scalars(
            sa.text(
                "UPDATE ai_provider_config SET key_secret_id = NULL, version = version + 1, "
                "last_test_at = NULL, last_test_ok = NULL, connected_at = NULL, "
                "last_error_code = NULL, last_error_at = NULL, updated_at = :now "
                "WHERE key_secret_id = ANY(:ids) RETURNING workspace_id"
            ),
            {"now": now, "ids": secret_ids},
        )
    )
    if workspaces:
        connection.execute(
            sa.text(
                "UPDATE ai_setting SET version = version + 1, updated_at = :now "
                "WHERE workspace_id = ANY(:workspaces)"
            ),
            {"now": now, "workspaces": workspaces},
        )


def usage_summary(
    connection: Connection, workspace_id: UUID, now: datetime, *, provider: str | None = None
) -> dict[str, Any]:
    """Calls and tokens today and this month; ``provider`` narrows every figure to it."""

    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    scope = [REQUESTS.c.workspace_id == workspace_id]
    if provider is not None:
        scope.append(REQUESTS.c.provider == provider)
    day_calls, day_tokens = _usage_since(connection, workspace_id, day_start, None, provider)
    latency = connection.execute(
        sa.select(
            sa.func.percentile_cont(0.5).within_group(LOGS.c.latency_ms),
            sa.func.percentile_cont(0.95).within_group(LOGS.c.latency_ms),
        ).where(
            LOGS.c.workspace_id == workspace_id,
            LOGS.c.created_at >= day_start,
            LOGS.c.latency_ms.is_not(None),
            *([LOGS.c.provider == provider] if provider is not None else []),
        )
    ).one()
    estimated = connection.scalar(
        sa.select(sa.func.count()).where(
            *scope,
            REQUESTS.c.reserved_at >= day_start,
            sa.text("NOT usage_known AND request_state NOT IN ('queued','skipped','released')"),
        )
    )
    uncertain = connection.execute(
        sa.select(REQUESTS.c.id, REQUESTS.c.call_kind, REQUESTS.c.hook, REQUESTS.c.finished_at)
        .where(
            *scope,
            REQUESTS.c.request_state == "uncertain",
            REQUESTS.c.acknowledged_at.is_(None),
        )
        .order_by(REQUESTS.c.finished_at.desc())
        .limit(50)
    ).mappings()
    usd = connection.scalar(
        sa.select(
            sa.func.sum(sa.func.coalesce(REQUESTS.c.actual_usd, REQUESTS.c.reserved_usd))
        ).where(
            *scope,
            REQUESTS.c.reserved_at >= day_start,
            sa.text("request_state NOT IN ('queued','skipped','released')"),
        )
    )
    month_start = _month_start(now)
    month_calls, month_tokens = _usage_since(connection, workspace_id, month_start, None, provider)
    return {
        "day_start": day_start,
        "calls_today": day_calls,
        "tokens_today": day_tokens,
        "month_start": month_start,
        "calls_this_month": month_calls,
        "tokens_this_month": month_tokens,
        "calls_with_estimated_usage": int(estimated or 0),
        "usd_today": usd,
        "uncertain_requests": [dict(row) for row in uncertain],
        "latency_ms_p50_today": None if latency[0] is None else int(latency[0]),
        "latency_ms_p95_today": None if latency[1] is None else int(latency[1]),
    }


__all__ = [
    "ERROR_LEASE_EXPIRED",
    "SKIP_DAY_BUDGET",
    "SKIP_MONTH_BUDGET",
    "SKIP_INPUT_TOO_LARGE",
    "SKIP_OPERATION_BUDGET",
    "SKIP_QUEUE_TIMEOUT",
    "NewRequest",
    "ReserveOutcome",
    "SweepCounts",
    "Usage",
    "acknowledge",
    "clear_provider_config_keys",
    "create_request",
    "finish",
    "mark_sending",
    "record_cache_hit",
    "release",
    "request_key",
    "reserve",
    "set_apply_outcome",
    "skip",
    "skip_in",
    "sweep_expired_secrets",
    "sweep_requests",
    "usage_summary",
]
