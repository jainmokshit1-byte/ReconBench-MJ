"""h6_vision handler: masked page images of one parser version → checked readings.

Nothing is written to the document. The validated answer is kept only in the
workspace cache (like H1); the review view re-checks it before showing it, and a
person applies a value through the normal correction save. A shadow request
(WI-BE6) only writes its ``reader_comparison`` row: no consensus run, no
suggestion. Once the request is answered the page image bytes are cleared;
their hash and geometry stay.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import shadow, vision
from reconbench_app.ai.consensus import ConsensusEnqueuer
from reconbench_app.ai.contracts import VisionOutput
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import PromptPayload
from reconbench_app.ai.vision_bands import header_band
from reconbench_app.ai.worker.handlers import Prepared, Skip
from reconbench_app.extraction.vision import (
    BAND_DOC_TYPES,
    BAND_TOP,
    DEFAULT_VISION_READER,
    VisionReaderInfo,
    reader_for_provider,
    vision_reader,
)

_LOGGER = logging.getLogger(__name__)


def _subject(
    connection: Connection, request: Any, *, for_update: bool = False
) -> vision.VisionSubject | None:
    return vision.load_subject(
        connection, request["workspace_id"], UUID(str(request["subject_id"])), for_update=for_update
    )


def _band_part(
    doc_type: str, pages: list[vision.PageRow], reader: VisionReaderInfo
) -> PromptPayload | None:
    """The header-band read of page 1 (WI-Q2, D93) for a per-page reader that has one."""

    if reader.band_prompt_variant is None or not pages or doc_type not in BAND_DOC_TYPES:
        return None
    band = header_band(pages[0])
    return vision.band_prompt(doc_type, band, reader) if band is not None else None  # type: ignore[arg-type]


def _current(connection: Connection, subject: vision.VisionSubject | None, request: Any) -> bool:
    if subject is None or not subject.current or subject.doc_type not in vision.WIRED_DOC_TYPES:
        return False
    pages = vision.load_pages(connection, request["workspace_id"], subject.version_id)
    return bool(pages) and vision.fingerprint(pages) == str(request["subject_version"])


class VisionReadHandler:
    kind = vision.HOOK

    def __init__(
        self,
        reader_name: str = DEFAULT_VISION_READER,
        *,
        consensus_enqueue: ConsensusEnqueuer | None = None,
    ) -> None:
        self.reader_name = reader_name
        self.reader = vision_reader(reader_name)
        self.consensus_enqueue = consensus_enqueue

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        # The request names its one provider; that provider's reader prepares it (WI-BE3).
        reader = reader_for_provider(self.reader_name, str(request["provider"]))
        if reader is None:
            return Skip(vision.SKIP_READER_OFF)
        subject = _subject(connection, request)
        if not _current(connection, subject, request):
            return Skip(vision.SKIP_SOURCE_CHANGED)
        assert subject is not None
        if not vision.eligibility(subject, mode=str(request["mode"]), limits=limits).eligible:
            return Skip(vision.SKIP_NOT_ELIGIBLE)
        workspace_id = request["workspace_id"]
        pages = vision.load_pages(connection, workspace_id, subject.version_id, with_bytes=True)
        payload = vision.prompt_for(subject.doc_type, pages, reader)
        parts = vision.page_prompts(subject.doc_type, pages, reader)
        if payload is None or parts is None:
            return Skip(vision.SKIP_PAGES_MISSING)
        page_numbers = [page.page for page in pages]
        merge_outputs = partial(vision.merge_page_answers, pages=page_numbers) if parts else None
        band = _band_part(subject.doc_type, pages, reader) if parts else None
        if band is not None:
            # The band answer adds annotations only; the full-page fields are unchanged.
            parts = (*parts, band)
            merge_outputs = partial(
                vision.merge_answers_with_band,
                pages=page_numbers,
                band_page=pages[0].page,
                band_top=BAND_TOP,
                box_order=reader.box_order,
            )
        local = not reader.external
        return Prepared(
            payload=payload,
            output_kind=vision.HOOK,
            max_output_tokens=limits.for_kind(vision.HOOK, local=local).max_output_tokens,
            cacheable=True,
            redacted=any(page.mask.get("boxes") for page in pages),
            document_id=subject.document_id,
            document_version_id=subject.version_id,
            document_hash=subject.content_hash,
            context={
                "vision": vision.context_for(
                    connection,
                    workspace_id,
                    subject.version_id,
                    subject.doc_type,
                    pages,
                    box_order=reader.box_order,
                )
            },
            parts=parts,
            merge_outputs=merge_outputs,
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        return _current(connection, _subject(connection, request), request)

    def validate(self, prepared: Prepared, parsed: Any) -> vision.VisionValidation:
        if not isinstance(parsed, VisionOutput):
            raise TypeError("unexpected vision output")
        return vision.validate_vision(parsed, prepared.context["vision"])

    def normalized(self, parsed: Any) -> dict[str, Any]:
        return dict(parsed.model_dump())

    def apply(
        self,
        connection: Connection,
        request: Any,
        prepared: Prepared,
        validated: Any,
        *,
        call_log_id: UUID | None,
        now: datetime,
    ) -> str:
        del prepared
        subject = _subject(connection, request, for_update=True)
        current = _current(connection, subject, request)
        if not current:
            if subject is not None:
                vision.purge_version_pages(
                    connection, request["workspace_id"], subject.version_id, now
                )
            return "discarded_stale"
        if request.get("shadow"):
            return self._apply_shadow(connection, request, subject, validated, now=now)
        if call_log_id is None or not validated.accepted:
            if subject is not None:
                vision.purge_version_pages(
                    connection, request["workspace_id"], subject.version_id, now
                )
            return "not_applicable"
        if subject is None or self.consensus_enqueue is None:
            if subject is not None:
                vision.purge_version_pages(
                    connection, request["workspace_id"], subject.version_id, now
                )
            return "applied"
        try:
            # The run row and parse-queue insert share the answer transaction. The
            # image bytes remain transient until that worker finishes the crop pass.
            with connection.begin_nested():
                enqueued = self.consensus_enqueue(
                    connection,
                    request["workspace_id"],
                    subject.version_id,
                    request["id"],
                    call_log_id,
                )
        except Exception as exc:  # noqa: BLE001 - consensus is advisory in this phase.
            _LOGGER.warning("consensus_enqueue_failed error_type=%s", type(exc).__name__)
            enqueued = False
        if not enqueued:
            vision.purge_version_pages(connection, request["workspace_id"], subject.version_id, now)
        return "applied"

    @staticmethod
    def _apply_shadow(
        connection: Connection,
        request: Any,
        subject: vision.VisionSubject | None,
        validated: vision.VisionValidation,
        *,
        now: datetime,
    ) -> str:
        """Shadow (WI-BE6): one ``reader_comparison`` row; no consensus run, no suggestion."""

        assert subject is not None
        recorded = shadow.record(connection, request, subject.version_id, validated.check, now=now)
        vision.purge_version_pages(connection, request["workspace_id"], subject.version_id, now)
        return "applied" if recorded else "not_applicable"


__all__ = ["VisionReadHandler"]
