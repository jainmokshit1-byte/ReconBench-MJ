"""Counts-only integration checks for AC-6a bench shadow scoring."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from reconbench_core.extraction.vision import AcceptedField, Box, VisionCheck
from reconbench_core.schema import DocType, Document
from scripts import accuracy_bench, accuracy_consensus, accuracy_vision
from scripts.accuracy_bench import TruthDocument, _normal, _same, extraction_summary, score_document
from scripts.vision_fixtures import GRAND, INVOICE_NO, SUPPLIER_GSTIN


def _field(value: Any) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": "0.65",
        "source": "rule",
        "evidence": {},
    }


def _target() -> accuracy_vision.VisionTarget:
    document = Document.model_validate(
        {
            "header": {
                "doc_type": "invoice",
                "doc_no": _field(INVOICE_NO),
                "vendor_gstin": _field(SUPPLIER_GSTIN),
                "vendor_name": _field("Synthetic Reader Agreement Supplier Ltd"),
                "grand_total": _field(Decimal(GRAND)),
                "extras": {"review_fields": []},
            },
            "needs_verification": False,
            "reason_codes": [],
        }
    )
    accepted = (
        AcceptedField("doc_no", "doc_no", INVOICE_NO, INVOICE_NO, 1, Box(100, 100, 120, 300)),
        AcceptedField(
            "supplier_gstin",
            "gstin",
            SUPPLIER_GSTIN,
            SUPPLIER_GSTIN,
            1,
            Box(150, 100, 170, 300),
        ),
        AcceptedField(
            "supplier_name",
            "text",
            "Synthetic Reader Agreement Supplier Ltd",
            "Synthetic Reader Agreement Supplier Ltd",
            1,
            Box(175, 100, 195, 300),
        ),
        AcceptedField("grand_total", "money", GRAND, Decimal(GRAND), 1, Box(200, 100, 220, 300)),
    )
    check = VisionCheck("invoice", accepted, (), (), (), {})
    target = accuracy_vision.VisionTarget(
        file_id="F1",
        index=0,
        truth_id="T1",
        doc_type=DocType.INVOICE,
        document=document,
        content=b"synthetic only",
        pages=(1,),
        ocr_pages=True,
    )
    target.validation = SimpleNamespace(check=check)
    return target


def _truth(*, total: str = GRAND) -> TruthDocument:
    return TruthDocument(
        doc_id="T1",
        set_id="S1",
        doc_type=DocType.INVOICE,
        files=("F1",),
        header={"doc_no": INVOICE_NO, "grand_total": total},
        lines=None,
        links=(),
        expected_status=(),
    )


def _summary(target: accuracy_vision.VisionTarget, truth: TruthDocument) -> dict[str, Any]:
    return accuracy_consensus.summary(
        accuracy_vision.VisionRun("cached", "synthetic-model", [target]),
        {"T1": truth},
        score_document,
        extraction_summary,
        _normal,
        _same,
        buyer_entities=(),
        run_date=date(2026, 10, 8),
        require_crop=False,
    )


def test_shadow_block_counts_l1_correct_and_wrong_per_field_type() -> None:
    target = _target()
    block = _summary(target, _truth(total="150.00"))
    assert block["by_field_type"]["doc_no"]["l1_correct"] == 1
    assert block["by_field_type"]["doc_no"]["l1_wrong"] == 0
    assert block["by_field_type"]["amount"]["l1_correct"] == 0
    assert block["by_field_type"]["amount"]["l1_wrong"] == 1
    assert block["rules"]["excluded"] == 1
    # A shadow false-safe is attributed to every field type auto-verified in
    # that document, even when the wrong value is a different type.
    assert block["gate"]["doc_no"] == "fail"
    assert block["gate"]["amount"] == "fail"


def test_gate_is_no_evidence_without_scored_l1_and_fail_on_any_wrong() -> None:
    target = _target()
    target.validation = SimpleNamespace(
        check=VisionCheck(
            "invoice",
            (
                AcceptedField(
                    "supplier_name",
                    "text",
                    "Synthetic Supplier Limited",
                    "Synthetic Supplier Limited",
                    1,
                    Box(100, 100, 120, 300),
                ),
            ),
            (),
            (),
            (),
            {},
        )
    )
    empty = _summary(target, _truth())
    assert empty["gate"]["doc_no"] == "no_evidence"
    wrong = _summary(_target(), _truth(total="150.00"))
    assert wrong["gate"]["amount"] == "fail"


def test_consensus_block_holds_no_document_values() -> None:
    target = _target()
    block = _summary(target, _truth(total="150.00"))
    serialized = str(block)
    assert INVOICE_NO not in serialized
    assert SUPPLIER_GSTIN not in serialized
    assert "Synthetic Reader Agreement Supplier Ltd" not in serialized
    assert "150.00" not in serialized
    assert GRAND not in serialized


def test_reader_fingerprint_ignores_consensus_modules(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path
    for relative in accuracy_bench._CODE_PATHS:
        target = root / relative
        if target.suffix == ".py":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic code\n", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
    extraction = root / "backend" / "reconbench_core" / "extraction"
    (extraction / "vision.py").write_text("vision reader\n", encoding="utf-8")
    (extraction / "consensus.py").write_text("first consensus\n", encoding="utf-8")
    (extraction / "extract.py").write_text("first rules\n", encoding="utf-8")
    monkeypatch.setattr(accuracy_bench, "ROOT", root)
    monkeypatch.setattr(accuracy_bench, "_tool_version", lambda _command: "synthetic-tool")
    first = accuracy_bench.reader_fingerprint(("eng",), "local_hybrid")
    (extraction / "consensus.py").write_text("second consensus\n", encoding="utf-8")
    second = accuracy_bench.reader_fingerprint(("eng",), "local_hybrid")
    assert first == second
    (extraction / "extract.py").write_text("changed rules\n", encoding="utf-8")
    third = accuracy_bench.reader_fingerprint(("eng",), "local_hybrid")
    assert third["extraction_code"] != second["extraction_code"]
