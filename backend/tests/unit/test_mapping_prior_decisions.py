"""A person's earlier link decisions are scoped to what the person actually changed."""

from __future__ import annotations

from uuid import UUID

from reconbench_app.mapping_service import PriorLinkDecisions

_PO = UUID(int=20)
_OTHER_PO = UUID(int=21)
_GRN = UUID(int=30)


def _record(decisions: PriorLinkDecisions, **overrides: object) -> PriorLinkDecisions:
    row: dict[str, object] = {
        "to_document_id": _PO,
        "link_type": "inv_po",
        "method": "manual",
        "state": "rejected",
        "line_no": None,
        "detail_code": "manual_unlink",
    }
    row.update(overrides)
    decisions.record(**row)  # type: ignore[arg-type]
    return decisions


def test_a_line_unlink_rejects_only_that_line_and_po() -> None:
    decisions = _record(PriorLinkDecisions(), line_no=3)

    assert decisions.rejected_line_po_pairs == {(3, _PO)}
    assert decisions.rejected_po_ids == set()


def test_a_header_unlink_or_change_still_rejects_the_po_for_the_document() -> None:
    decisions = _record(PriorLinkDecisions())
    _record(decisions, to_document_id=_OTHER_PO, detail_code="manual_link_change")

    assert decisions.rejected_po_ids == {_PO, _OTHER_PO}
    assert decisions.rejected_line_po_pairs == set()


def test_a_line_change_rejects_only_the_old_pairing() -> None:
    decisions = _record(PriorLinkDecisions(), line_no=2, detail_code="manual_link_change")

    assert decisions.rejected_line_po_pairs == {(2, _PO)}
    assert decisions.rejected_po_ids == set()


def test_a_manual_link_the_system_superseded_is_not_a_persons_rejection() -> None:
    # Vijayshree line 2 on live: a person's link later replaced by a mapping run.
    decisions = _record(PriorLinkDecisions(), line_no=2, detail_code="superseded_by_resolution")
    _record(decisions, detail_code="superseded_by_resolution")

    assert decisions.rejected_po_ids == set()
    assert decisions.rejected_line_po_pairs == set()


def test_unknown_or_missing_detail_codes_stay_conservative() -> None:
    # Removals saved before detail codes existed keep blocking, as before.
    decisions = _record(PriorLinkDecisions(), detail_code=None)

    assert decisions.rejected_po_ids == {_PO}


def test_confirmed_manual_links_are_kept_and_grn_links_are_ignored() -> None:
    decisions = _record(PriorLinkDecisions(), state="confirmed", detail_code="manual_link_again")
    _record(decisions, to_document_id=_GRN, link_type="inv_grn")
    _record(decisions, to_document_id=_OTHER_PO, method="content")

    assert decisions.manual_po_ids == {_PO}
    assert decisions.rejected_po_ids == set()
    assert decisions.rejected_line_po_pairs == set()
