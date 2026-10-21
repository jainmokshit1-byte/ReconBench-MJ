from __future__ import annotations

import json
import sys
from copy import deepcopy
from typing import Any

import pytest
from scripts.merge_accuracy_corrections import (
    CorrectionMergeError,
    main,
    merge_corrections,
)


def _truth() -> dict[str, object]:
    return {
        "format": "reconbench-accuracy-truth/v2",
        "config": {"run_date": "2026-10-08", "buyer_entities": []},
        "files": [{"id": "file-1", "path": "synthetic/invoice.pdf", "zone": "invoice"}],
        "sets": [
            {
                "id": "set-1",
                "documents": [
                    {
                        "id": "truth-invoice-1",
                        "doc_type": "invoice",
                        "sources": [{"file": "file-1"}],
                        "header": {"doc_no": "OLD-1", "vendor_name": "Synthetic Vendor"},
                        "lines": [
                            {
                                "description": "Synthetic item",
                                "quantity": "1",
                                "rate": "10",
                                "amount": "10",
                            }
                        ],
                        "links": [],
                        "expected_status": ["Needs Review"],
                    }
                ],
            }
        ],
    }


def _patch() -> dict[str, object]:
    return {
        "format": "reconbench-accuracy-corrections/v1",
        "documents": [
            {
                "application_document_id": "app-document-1",
                "source_file_name": "synthetic-invoice.pdf",
                "doc_type": "invoice",
                "current_doc_no": "NEW-1",
                "header": {"doc_no": "NEW-1", "grand_total": "11.00"},
                "lines": [{"line_no": 1, "fields": {"amount": "11.00"}}],
            }
        ],
    }


def test_merge_updates_only_explicit_fields_and_preserves_truth_metadata() -> None:
    truth = _truth()
    original = deepcopy(truth)

    updated, documents, fields = merge_corrections(
        truth,
        _patch(),
        {
            "app-document-1": {
                "truth_document_id": "truth-invoice-1",
                "lines": {"1": 1},
            }
        },
    )

    assert (documents, fields) == (1, 3)
    assert updated["sets"][0]["documents"][0]["header"] == {
        "doc_no": "NEW-1",
        "vendor_name": "Synthetic Vendor",
        "grand_total": "11.00",
    }
    assert updated["sets"][0]["documents"][0]["lines"][0]["amount"] == "11.00"
    assert updated["sets"][0]["documents"][0]["links"] == []
    assert truth["sets"][0]["documents"][0]["header"]["doc_no"] == "OLD-1"
    assert original["sets"][0]["documents"][0]["header"]["doc_no"] == "OLD-1"


@pytest.mark.parametrize(
    ("mapping", "patch", "message"),
    [
        ({}, _patch(), "no truth document mapping"),
        (
            {"app-document-1": {"truth_document_id": "missing", "lines": {"1": 1}}},
            _patch(),
            "was not found",
        ),
        (
            {"app-document-1": "truth-invoice-1"},
            {"format": "other", "documents": []},
            "must declare format",
        ),
    ],
)
def test_merge_rejects_missing_or_invalid_mapping(
    mapping: dict[str, Any], patch: dict[str, object], message: str
) -> None:
    with pytest.raises(CorrectionMergeError, match=message):
        merge_corrections(_truth(), patch, mapping)


def test_merge_rejects_wrong_document_type_and_line_out_of_range() -> None:
    wrong_type = _patch()
    wrong_type["documents"][0]["doc_type"] = "po"  # type: ignore[index]
    with pytest.raises(CorrectionMergeError, match="type mismatch"):
        merge_corrections(
            _truth(),
            wrong_type,
            {"app-document-1": {"truth_document_id": "truth-invoice-1"}},
        )

    out_of_range = _patch()
    out_of_range["documents"][0]["lines"][0]["line_no"] = 2  # type: ignore[index]
    with pytest.raises(CorrectionMergeError, match="outside the 1 truth lines"):
        merge_corrections(
            _truth(),
            out_of_range,
            {
                "app-document-1": {
                    "truth_document_id": "truth-invoice-1",
                    "lines": {"2": 2},
                }
            },
        )

    boolean_line = _patch()
    boolean_line["documents"][0]["lines"][0]["line_no"] = True  # type: ignore[index]
    with pytest.raises(CorrectionMergeError, match="positive integers"):
        merge_corrections(
            _truth(),
            boolean_line,
            {
                "app-document-1": {
                    "truth_document_id": "truth-invoice-1",
                    "lines": {"1": 1},
                }
            },
        )


def test_merge_rejects_fields_outside_the_benchmark_contract() -> None:
    patch = _patch()
    patch["documents"][0]["header"]["ship_to"] = "Synthetic address"  # type: ignore[index]
    with pytest.raises(CorrectionMergeError, match="unsupported truth header fields"):
        merge_corrections(
            _truth(),
            patch,
            {"app-document-1": {"truth_document_id": "truth-invoice-1"}},
        )


def test_merge_requires_explicit_line_mapping_and_supports_explicit_append() -> None:
    patch = _patch()
    with pytest.raises(CorrectionMergeError, match="no explicit truth line mapping"):
        merge_corrections(
            _truth(),
            patch,
            {"app-document-1": {"truth_document_id": "truth-invoice-1"}},
        )
    with pytest.raises(CorrectionMergeError, match="line mappings.*truth positions"):
        merge_corrections(
            _truth(),
            patch,
            {
                "app-document-1": {
                    "truth_document_id": "truth-invoice-1",
                    "lines": {"1": 0},
                }
            },
        )

    patch["documents"][0]["lines"][0]["line_no"] = 2  # type: ignore[index]
    updated, documents, fields = merge_corrections(
        _truth(),
        patch,
        {
            "app-document-1": {
                "truth_document_id": "truth-invoice-1",
                "lines": {"2": "append"},
            }
        },
    )

    lines = updated["sets"][0]["documents"][0]["lines"]  # type: ignore[index]
    assert (documents, fields) == (1, 3)
    assert len(lines) == 2  # type: ignore[arg-type]
    assert lines[1] == {"amount": "11.00"}  # type: ignore[index]

    duplicate = _patch()
    duplicate["documents"][0]["lines"].append(  # type: ignore[index]
        {"line_no": 2, "fields": {"amount": "12.00"}}
    )
    with pytest.raises(CorrectionMergeError, match="multiple application lines map"):
        merge_corrections(
            _truth(),
            duplicate,
            {
                "app-document-1": {
                    "truth_document_id": "truth-invoice-1",
                    "lines": {"1": "append", "2": 2},
                }
            },
        )


def test_cli_writes_a_new_truth_file_and_keeps_the_source_unchanged(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "truth.json"
    patch_path = tmp_path / "corrections.json"
    mapping = tmp_path / "mapping.json"
    output = tmp_path / "truth-with-corrections.json"
    source.write_text(json.dumps(_truth()), encoding="utf-8")
    original_source = source.read_text(encoding="utf-8")
    patch_path.write_text(json.dumps(_patch()), encoding="utf-8")
    mapping.write_text(
        json.dumps(
            {
                "app-document-1": {
                    "truth_document_id": "truth-invoice-1",
                    "lines": {"1": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge_accuracy_corrections.py",
            "--truth",
            str(source),
            "--corrections",
            str(patch_path),
            "--mapping",
            str(mapping),
            "--output",
            str(output),
        ],
    )

    assert main() == 0

    assert source.read_text(encoding="utf-8") == original_source
    merged = json.loads(output.read_text(encoding="utf-8"))
    assert merged["sets"][0]["documents"][0]["header"]["doc_no"] == "NEW-1"
    assert capsys.readouterr().out.strip() == "Updated truth documents: 1; corrected fields: 3"


def test_cli_refuses_to_replace_an_existing_output_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "truth.json"
    patch_path = tmp_path / "corrections.json"
    mapping = tmp_path / "mapping.json"
    output = tmp_path / "truth-with-corrections.json"
    source.write_text(json.dumps(_truth()), encoding="utf-8")
    patch_path.write_text(json.dumps(_patch()), encoding="utf-8")
    mapping.write_text(
        json.dumps({"app-document-1": {"truth_document_id": "truth-invoice-1"}}),
        encoding="utf-8",
    )
    output.write_text("do not replace", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge_accuracy_corrections.py",
            "--truth",
            str(source),
            "--corrections",
            str(patch_path),
            "--mapping",
            str(mapping),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        main()

    assert output.read_text(encoding="utf-8") == "do not replace"
