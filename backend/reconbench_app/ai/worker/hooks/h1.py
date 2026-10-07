"""H1 handler: grounded extraction suggestions for one parser version of a PDF invoice.

Nothing is written to the document. A valid answer is kept only as the
validated, normalised output in the workspace cache (always stored for H1), and
the review view re-grounds it against the current page text before showing it.
A person applies a suggestion through the normal correction save.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import h1
from reconbench_app.ai.contracts import H1Output
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import h1_prompt
from reconbench_app.ai.validators import H1Validation, validate_h1
from reconbench_app.ai.worker.handlers import Prepared, Skip


def _subject(
    connection: Connection, request: Any, *, for_update: bool = False
) -> h1.H1Subject | None:
    return h1.load_subject(
        connection,
        request["workspace_id"],
        UUID(str(request["subject_id"])),
        for_update=for_update,
    )


def _current(subject: h1.H1Subject | None, request: Any) -> bool:
    return (
        subject is not None
        and subject.current
        and subject.doc_type in h1.WIRED_DOC_TYPES
        and subject.content_hash == str(request["subject_version"])
    )


class ExtractionSuggestionHandler:
    kind = h1.HOOK

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        subject = _subject(connection, request)
        if subject is None or not _current(subject, request):
            return Skip(h1.SKIP_SOURCE_CHANGED)
        if not h1.eligibility(subject, mode=str(request["mode"]), limits=limits).eligible:
            return Skip(h1.SKIP_NOT_ELIGIBLE)
        pages = h1.redacted_pages(
            h1.load_pages(connection, request["workspace_id"], subject.version_id)
        )
        return Prepared(
            payload=h1_prompt(
                subject.doc_type, [(number, page.text) for number, page in pages.items()]
            ),
            output_kind=h1.HOOK,
            max_output_tokens=limits.h1_extract.max_output_tokens,
            cacheable=True,
            redacted=any(page.spans for page in pages.values()),
            document_id=subject.document_id,
            document_version_id=subject.version_id,
            document_hash=subject.content_hash,
            context={"doc_type": subject.doc_type, "pages": pages},
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        return _current(_subject(connection, request), request)

    def validate(self, prepared: Prepared, parsed: Any) -> H1Validation:
        if not isinstance(parsed, H1Output):
            raise TypeError("unexpected H1 output")
        return validate_h1(
            parsed, doc_type=prepared.context["doc_type"], pages=prepared.context["pages"]
        )

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
        del prepared, now
        if not _current(_subject(connection, request, for_update=True), request):
            return "discarded_stale"
        # Suggestions stay in the cache; a person applies them through a correction save.
        if call_log_id is None or not validated.accepted:
            return "not_applicable"
        return "applied"


__all__ = ["ExtractionSuggestionHandler"]
