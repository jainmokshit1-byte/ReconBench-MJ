"""h6_vision handler: masked page images of one parser version → checked readings.

Nothing is written to the document. The validated answer is kept only in the
workspace cache (like H1); the review view re-checks it before showing it, and a
person applies a value through the normal correction save. Once the request is
answered the page image bytes are cleared; their hash and geometry stay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import vision
from reconbench_app.ai.contracts import VisionOutput
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.worker.handlers import Prepared, Skip
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader


def _subject(
    connection: Connection, request: Any, *, for_update: bool = False
) -> vision.VisionSubject | None:
    return vision.load_subject(
        connection, request["workspace_id"], UUID(str(request["subject_id"])), for_update=for_update
    )


def _current(connection: Connection, subject: vision.VisionSubject | None, request: Any) -> bool:
    if subject is None or not subject.current or subject.doc_type not in vision.WIRED_DOC_TYPES:
        return False
    pages = vision.load_pages(connection, request["workspace_id"], subject.version_id)
    return bool(pages) and vision.fingerprint(pages) == str(request["subject_version"])


class VisionReadHandler:
    kind = vision.HOOK

    def __init__(self, reader_name: str = DEFAULT_VISION_READER) -> None:
        self.reader = vision_reader(reader_name)

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        if self.reader is None:
            return Skip(vision.SKIP_READER_OFF)
        subject = _subject(connection, request)
        if not _current(connection, subject, request):
            return Skip(vision.SKIP_SOURCE_CHANGED)
        assert subject is not None
        if not vision.eligibility(subject, mode=str(request["mode"]), limits=limits).eligible:
            return Skip(vision.SKIP_NOT_ELIGIBLE)
        workspace_id = request["workspace_id"]
        pages = vision.load_pages(connection, workspace_id, subject.version_id, with_bytes=True)
        payload = vision.prompt_for(subject.doc_type, pages, self.reader.media_resolution)
        if payload is None:
            return Skip(vision.SKIP_PAGES_MISSING)
        return Prepared(
            payload=payload,
            output_kind=vision.HOOK,
            max_output_tokens=limits.for_kind(vision.HOOK).max_output_tokens,
            cacheable=True,
            redacted=any(page.mask.get("boxes") for page in pages),
            document_id=subject.document_id,
            document_version_id=subject.version_id,
            document_hash=subject.content_hash,
            context={
                "vision": vision.context_for(
                    connection, workspace_id, subject.version_id, subject.doc_type, pages
                )
            },
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
        if subject is not None:
            # The answer (or its absence) is final for these images: drop the bytes.
            vision.purge_version_pages(connection, request["workspace_id"], subject.version_id, now)
        if not current:
            return "discarded_stale"
        if call_log_id is None or not validated.accepted:
            return "not_applicable"
        return "applied"


__all__ = ["VisionReadHandler"]
