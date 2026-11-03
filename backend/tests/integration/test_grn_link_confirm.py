"""A person can confirm (or remove) an invoice→GRN link; it can never be moved elsewhere.

A suggested invoice→GRN link keeps a case in review (LINK_INFERRED) until a person
confirms it, and the Mapping page had no way to do so (b18). Synthetic documents only.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.db.metadata import metadata
from reconbench_app.mapping_service import MappingService
from reconbench_core.mapping.links import ProposedLink
from reconbench_core.schema import LinkLevel, LinkMethod, LinkState, LinkType
from sqlalchemy.engine import Engine
from tests.integration.test_flow_three_way_match import _document_id, _match_and_run, _upload_set
from tests.integration.test_phase4_task_411_413 import scratch_engine

__all__ = ["scratch_engine"]


def _suggested_grn_link(engine: Engine, workspace_id: UUID, invoice_id: UUID, grn_id: UUID) -> UUID:
    service = MappingService(engine)
    with engine.begin() as connection:
        return service._append_link(
            connection,
            workspace_id=workspace_id,
            proposal=ProposedLink(
                from_document_id=invoice_id,
                to_document_id=grn_id,
                link_type=LinkType.INV_GRN,
                level=LinkLevel.HEADER,
                method=LinkMethod.LABELED,
                confidence=Decimal("0.80"),
                state=LinkState.SUGGESTED,
                evidence="Synthetic handwritten GRN reference",
                via_document_id=grn_id,
            ),
            old=None,
            detail_code=None,
        )


def _link(engine: Engine, link_id: UUID) -> sa.RowMapping:
    link = metadata.tables["link"]
    with engine.connect() as connection:
        return connection.execute(sa.select(*link.c).where(link.c.id == link_id)).mappings().one()


def test_a_suggested_grn_link_can_be_confirmed_and_keeps_its_evidence(
    scratch_engine: Engine, tmp_path: Path
) -> None:
    actor, _ingestion, invoice_id, _queue = _upload_set(scratch_engine, tmp_path, "grn-confirm")
    _match_and_run(scratch_engine, actor)
    grn_id = _document_id(scratch_engine, actor, "grn")
    link_id = _suggested_grn_link(scratch_engine, actor.workspace_id, invoice_id, grn_id)
    before = _link(scratch_engine, link_id)

    confirmed = MappingService(scratch_engine).edit_link(
        actor, link_id, action="confirm", expected_version=int(before["version_no"])
    )

    assert confirmed is not None
    assert (confirmed["state"], confirmed["link_type"], confirmed["to_document_id"]) == (
        "confirmed",
        "inv_grn",
        grn_id,
    )
    assert confirmed["evidence"] == {"via_document_id": str(grn_id)}
    case = _match_and_run(scratch_engine, actor)
    assert "LINK_INFERRED" not in {code.value for code in case.reason_codes}


def test_a_grn_link_cannot_be_moved_to_another_document(
    scratch_engine: Engine, tmp_path: Path
) -> None:
    actor, _ingestion, invoice_id, _queue = _upload_set(scratch_engine, tmp_path, "grn-change")
    _match_and_run(scratch_engine, actor)
    grn_id = _document_id(scratch_engine, actor, "grn")
    po_id = _document_id(scratch_engine, actor, "po")
    link_id = _suggested_grn_link(scratch_engine, actor.workspace_id, invoice_id, grn_id)
    version = int(_link(scratch_engine, link_id)["version_no"])

    with pytest.raises(ValueError, match="only be confirmed or removed"):
        MappingService(scratch_engine).edit_link(
            actor, link_id, action="change", expected_version=version, to_document_id=po_id
        )
