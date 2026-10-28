"""PD-2b: an unverified AI value never reaches Matched; rules-read invoices keep their gate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate
from reconbench_core.recon.engine import _gate_confidence, _unverified_ai_values
from reconbench_core.schema import (
    CaseStatus,
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    LinkState,
    ReasonCode,
)
from tests.unit import test_phase4_reconciliation as recon_case

_INVOICE = recon_case._document(recon_case._invoice_xml("INV-PD2B-1", 10), DocType.INVOICE)
_PO = recon_case._record(
    1,
    recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO),
    DocType.PO,
)
_GRN = recon_case._record(
    2, recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN), DocType.GRN
)
_NAMES = tuple(
    [
        name
        for name in type(_INVOICE.header).model_fields
        if isinstance(getattr(_INVOICE.header, name), FieldValue)
    ]
    + [
        f"line {line.line_no} {name}"
        for line in _INVOICE.lines
        for name in type(line).model_fields
        if isinstance(getattr(line, name), FieldValue)
    ]
)
_AI_READ = st.sampled_from(
    [
        (FieldOrigin.AI, FieldSource.AI),
        (FieldOrigin.AI, FieldSource.RULE),
        (FieldOrigin.OCR, FieldSource.AI),
        (FieldOrigin.ERP_IMPORT, FieldSource.AI),
        (None, FieldSource.AI),
    ]
)
_CONFIDENCE = st.one_of(
    st.none(),
    st.sampled_from([Decimal("0.7"), Decimal("0.95"), Decimal("1")]),
    st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2, allow_nan=False),
)


def _with(document: Document, values: Mapping[str, FieldValue[object] | None]) -> Document:
    header = document.header.model_copy(
        update={name: value for name, value in values.items() if not name.startswith("line ")}
    )
    lines = tuple(
        line.model_copy(
            update={
                name.split(" ", 2)[2]: value
                for name, value in values.items()
                if name.startswith(f"line {line.line_no} ")
            }
        )
        for line in document.lines
    )
    return document.model_copy(update={"header": header, "lines": lines})


def _value(name: str) -> FieldValue[object]:
    if name.startswith("line "):
        _, line_no, field_name = name.split(" ", 2)
        line = next(line for line in _INVOICE.lines if line.line_no == int(line_no))
        value = getattr(line, field_name)
    else:
        value = getattr(_INVOICE.header, name)
    assert isinstance(value, FieldValue)
    return value


@st.composite
def _ai_read_invoice(draw: st.DrawFn) -> tuple[Document, set[str]]:
    """Some values read by AI and not verified by a person; the rest anything at all."""

    ai_names = draw(st.sets(st.sampled_from(_NAMES), min_size=1))
    values: dict[str, FieldValue[object]] = {}
    for name in _NAMES:
        if name in ai_names:
            origin, source = draw(_AI_READ)
            verification = draw(
                st.sampled_from([FieldVerification.NONE, FieldVerification.SYSTEM_CHECK])
            )
        else:
            origin = draw(st.sampled_from([FieldOrigin.ERP_IMPORT, FieldOrigin.MANUAL]))
            source = FieldSource.MANUAL if origin is FieldOrigin.MANUAL else FieldSource.RULE
            verification = draw(st.sampled_from(list(FieldVerification)))
        values[name] = _value(name).model_copy(
            update={
                "origin": origin,
                "source": source,
                "confidence": draw(_CONFIDENCE),
                "verification": verification,
            }
        )
    return _with(_INVOICE, values), ai_names


@given(
    case=_ai_read_invoice(),
    link_state=st.sampled_from([LinkState.AUTO, LinkState.CONFIRMED]),
    gate=st.sampled_from([Decimal("0"), Decimal("0.70")]),
)
@settings(max_examples=200, deadline=None)
def test_an_unverified_ai_value_is_never_matched(
    case: tuple[Document, set[str]], link_state: LinkState, gate: Decimal
) -> None:
    invoice, ai_names = case
    record = recon_case._record(3, invoice, DocType.INVOICE)
    link = recon_case._link(11, record.document_id, _PO.document_id)
    snapshot = ReconciliationSnapshot(
        documents=(_PO, _GRN, record),
        links=(
            recon_case._link(10, _GRN.document_id, _PO.document_id),
            link.model_copy(update={"state": link_state}),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(extraction_gate=gate), date(2026, 2, 2))[0]

    # A zero gate shows that confidence is not what keeps the case out of Matched.
    assert result.status not in {CaseStatus.MATCHED, CaseStatus.MATCHED_FLAGS}
    assert ReasonCode.AI_VALUE_UNVERIFIED in result.reason_codes
    assert set(_unverified_ai_values(invoice)) == ai_names


@st.composite
def _rules_read_invoice(draw: st.DrawFn) -> Document:
    """Read by rules, nothing verified or typed in by a person: the gate is unchanged."""

    values: dict[str, FieldValue[object] | None] = {}
    for name in _NAMES:
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            values[name] = None
            continue
        values[name] = _value(name).model_copy(
            update={
                "origin": draw(st.sampled_from([FieldOrigin.OCR, FieldOrigin.ERP_IMPORT, None])),
                "source": FieldSource.RULE,
                "confidence": draw(_CONFIDENCE),
                "verification": draw(
                    st.sampled_from([FieldVerification.NONE, FieldVerification.SYSTEM_CHECK])
                ),
            }
        )
    return _with(_INVOICE, values)


@given(invoice=_rules_read_invoice())
@settings(max_examples=300, deadline=None)
def test_a_rules_read_invoice_keeps_its_gate_and_has_no_ai_check(invoice: Document) -> None:
    assert _gate_confidence(invoice) == invoice.confidence
    assert _unverified_ai_values(invoice) == ()
