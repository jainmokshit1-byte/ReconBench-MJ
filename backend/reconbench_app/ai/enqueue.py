"""Producer-side enqueue of AI hook requests (API and general worker; never the AI worker).

A hook request is created only when every precondition already holds at enqueue
time: the deployment allows business data to leave (``enabled``), AI is on in a
mode that includes the hook, consent is current, a key is usable and limits are
configured. The AI worker repeats all of these checks before sending and before
applying. Creating the ledger row and deferring its job share one transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import h1, h2, h3, h4, ledger, vision
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.prompts import PROMPT_VERSIONS
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION, REDACTION_VERSION
from reconbench_app.ai.runtime_checks import SettingsSnapshot, load_key, load_settings
from reconbench_app.db.ids import new_id
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader

if TYPE_CHECKING:
    from reconbench_app.mapping_service import MappingSnapshot


class AiJobQueue(Protocol):
    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiHookEnqueuer:
    def __init__(
        self,
        *,
        gate: DeploymentGate,
        queue: AiJobQueue,
        limits: Callable[[], LimitsState],
        clock: Callable[[], datetime] = _utcnow,
        vision_reader_name: str = DEFAULT_VISION_READER,
    ) -> None:
        self.gate = gate
        self.queue = queue
        self.limits = limits
        self.clock = clock
        self.vision_reader = vision_reader(vision_reader_name)

    def ready(
        self, connection: Connection, workspace_id: UUID, hook: str
    ) -> tuple[SettingsSnapshot, AiLimits] | None:
        """Settings and limits when ``hook`` may run for business data now, else ``None``."""

        if self.gate.mode != "enabled":
            return None
        limits = self.limits().limits
        if limits is None:
            return None
        settings = load_settings(connection, workspace_id)
        if (
            settings is None
            or settings.provider is None
            or settings.model is None
            or not settings.consent_current
            or not any(name.split(":", 1)[0] == hook for name in settings.hooks)
        ):
            return None
        if hook == vision.HOOK and (
            self.vision_reader is None or self.vision_reader.provider != settings.provider
        ):
            return None
        if isinstance(load_key(connection, settings, self.clock()), str):
            return None
        return settings, limits

    def enqueue(
        self,
        connection: Connection,
        settings: SettingsSnapshot,
        *,
        hook: str,
        subject_type: str,
        subject_id: UUID,
        subject_version: str,
        operation_key: str,
        run_id: UUID | None = None,
        requested_by: UUID | None = None,
    ) -> UUID | None:
        assert settings.provider is not None and settings.model is not None
        request_id = ledger.create_request(
            connection,
            ledger.NewRequest(
                workspace_id=settings.workspace_id,
                call_kind="hook",
                hook=hook,
                mode=settings.mode,
                subject_type=subject_type,
                subject_id=subject_id,
                subject_version=subject_version,
                operation_key=operation_key,
                settings_version=settings.version,
                consent_fingerprint=settings.consent_fingerprint,
                provider=settings.provider,
                model=settings.model,
                prompt_version=PROMPT_VERSIONS[hook],
                schema_version=SCHEMA_VERSIONS[hook],
                redaction_version=IMAGE_MASK_VERSION if hook == vision.HOOK else REDACTION_VERSION,
                run_id=run_id,
                requested_by=requested_by,
            ),
        )
        if request_id is None:
            return None
        job_id = self.queue.enqueue_ai_request(ai_request_id=request_id, connection=connection)
        connection.execute(
            ledger.REQUESTS.update().where(ledger.REQUESTS.c.id == request_id).values(job_id=job_id)
        )
        return request_id

    def vendor_opinions(self, connection: Connection, workspace_id: UUID) -> int:
        """Queue H4 for undecided vendor pairs; PAN-conflict pairs are never queued."""

        ready = self.ready(connection, workspace_id, h4.HOOK)
        if ready is None:
            return 0
        settings, limits = ready
        operation_key = f"{h4.HOOK}:{new_id()}"
        queued = 0
        for suggestion_id in h4.pending_suggestions(connection, workspace_id):
            if queued >= limits.per_operation_calls:
                break
            pair = h4.load_vendor_pair(connection, workspace_id, suggestion_id)
            if pair is None or pair.pan_conflict or not pair.open_at(str(pair.version)):
                continue
            queued += (
                self.enqueue(
                    connection,
                    settings,
                    hook=h4.HOOK,
                    subject_type=h4.SUBJECT_TYPE,
                    subject_id=suggestion_id,
                    subject_version=str(pair.version),
                    operation_key=operation_key,
                )
                is not None
            )
        return queued

    def extraction_suggestions(
        self, connection: Connection, workspace_id: UUID, version_ids: Sequence[UUID]
    ) -> int:
        """Queue H1 for freshly parsed PDF invoice versions that pass the eligibility gate."""

        ready = self.ready(connection, workspace_id, h1.HOOK)
        if ready is None:
            return 0
        settings, limits = ready
        operation_key = f"{h1.HOOK}:{new_id()}"
        queued = 0
        for version_id in version_ids:
            if queued >= limits.per_operation_calls:
                break
            subject = h1.load_subject(connection, workspace_id, version_id)
            if (
                subject is None
                or not subject.current
                or not h1.eligibility(subject, mode=settings.mode, limits=limits).eligible
            ):
                continue
            queued += (
                self.enqueue(
                    connection,
                    settings,
                    hook=h1.HOOK,
                    subject_type=h1.SUBJECT_TYPE,
                    subject_id=version_id,
                    subject_version=subject.content_hash,
                    operation_key=operation_key,
                )
                is not None
            )
        return queued

    def vision_targets(
        self, connection: Connection, workspace_id: UUID, version_ids: Sequence[UUID]
    ) -> list[UUID]:
        """Freshly parsed PDF versions whose pages should be prepared for the vision reader."""

        ready = self.ready(connection, workspace_id, vision.HOOK)
        if ready is None:
            return []
        settings, limits = ready
        targets: list[UUID] = []
        for version_id in version_ids:
            if len(targets) >= limits.per_operation_calls:
                break
            subject = vision.load_subject(connection, workspace_id, version_id)
            if (
                subject is not None
                and subject.current
                and vision.eligibility(subject, mode=settings.mode, limits=limits).eligible
            ):
                targets.append(version_id)
        return targets

    def vision_read(
        self,
        connection: Connection,
        workspace_id: UUID,
        version_id: UUID,
        *,
        subject_version: str,
        skip_code: str | None = None,
    ) -> UUID | None:
        """Queue one vision request after its pages were stored (same transaction).

        With ``skip_code`` the request is recorded as skipped at once (for example the
        pages could not be rendered), so the review view can say why nothing came.
        """

        ready = self.ready(connection, workspace_id, vision.HOOK)
        if ready is None:
            return None
        settings, _limits = ready
        if skip_code is None:
            return self.enqueue(
                connection,
                settings,
                hook=vision.HOOK,
                subject_type=vision.SUBJECT_TYPE,
                subject_id=version_id,
                subject_version=subject_version,
                operation_key=f"{vision.HOOK}:{version_id}",
            )
        assert settings.provider is not None and settings.model is not None
        request_id = ledger.create_request(
            connection,
            ledger.NewRequest(
                workspace_id=workspace_id,
                call_kind="hook",
                hook=vision.HOOK,
                mode=settings.mode,
                subject_type=vision.SUBJECT_TYPE,
                subject_id=version_id,
                subject_version=subject_version,
                operation_key=f"{vision.HOOK}:{version_id}",
                settings_version=settings.version,
                consent_fingerprint=settings.consent_fingerprint,
                provider=settings.provider,
                model=settings.model,
                prompt_version=PROMPT_VERSIONS[vision.HOOK],
                schema_version=SCHEMA_VERSIONS[vision.HOOK],
                redaction_version=IMAGE_MASK_VERSION,
            ),
        )
        if request_id is not None:
            ledger.skip_in(connection, request_id, skip_code, self.clock())
        return request_id

    def po_reranks(
        self, connection: Connection, workspace_id: UUID, snapshot: MappingSnapshot
    ) -> int:
        """Queue H3 for unlinked invoices with at least one same-vendor content candidate."""

        ready = self.ready(connection, workspace_id, h3.HOOK)
        if ready is None:
            return 0
        settings, limits = ready
        sources = {source.document_id: source for source in snapshot.sources}
        operation_key = f"{h3.HOOK}:{new_id()}"
        queued = 0
        for state in h3.open_resolutions(connection, workspace_id):
            if queued >= limits.per_operation_calls:
                break
            source = sources.get(state.invoice_id)
            if source is None or state.input_hash != snapshot.input_hash:
                continue
            pool = h3.build_pool(source, snapshot.purchase_orders, snapshot.config)
            if pool is None:
                continue
            queued += (
                self.enqueue(
                    connection,
                    settings,
                    hook=h3.HOOK,
                    subject_type=h3.SUBJECT_TYPE,
                    subject_id=state.resolution_id,
                    subject_version=pool.fingerprint,
                    operation_key=operation_key,
                )
                is not None
            )
        return queued

    def item_opinions(self, connection: Connection, workspace_id: UUID, run_id: UUID) -> int:
        """Queue H2 for a succeeded run's in-band unsure lines; spec conflicts are never queued."""

        ready = self.ready(connection, workspace_id, h2.HOOK)
        if ready is None:
            return 0
        settings, limits = ready
        lines = h2.eligible_lines(connection, workspace_id, run_id)
        operation_key = f"{h2.HOOK}:{run_id}"
        queued = 0
        for batch in h2.batches(lines, limits.h2_item.max_pairs or h2.MAX_PAIRS):
            if queued >= limits.per_operation_calls:
                break
            queued += (
                self.enqueue(
                    connection,
                    settings,
                    hook=h2.HOOK,
                    subject_type=h2.SUBJECT_TYPE,
                    subject_id=run_id,
                    subject_version=batch.subject_version,
                    operation_key=operation_key,
                    run_id=run_id,
                )
                is not None
            )
        return queued


__all__ = ["AiHookEnqueuer", "AiJobQueue"]
