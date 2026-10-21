"""Per-item tolerant vision parse and the vLLM schema length limits (D73).

Synthetic answers only (``SYN-…`` values, made-up boxes); no provider is called.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel
from reconbench_app.ai import vision
from reconbench_app.ai.contracts import (
    InvalidOutput,
    VisionAnnotationItem,
    VisionFieldItem,
    VisionLineItem,
    VisionOutput,
    VisionPageInfo,
    VisionUnreadableItem,
    parse_output,
    vision_response_schema,
    vision_response_schema_v3,
)
from reconbench_app.ai.vision import VisionContext, validate_vision
from reconbench_core.extraction.vision import SentPage

BOX = [100, 100, 140, 400]
PAGES = {1: SentPage(1)}


def _answer(page: int = 1) -> dict[str, Any]:
    return {
        "doc_kind": "invoice",
        "pages": [{"page": page, "kind": "invoice", "title": "TAX INVOICE"}],
        "fields": [
            {"name": "doc_no", "text": "SYN-INV-0001", "label": "Invoice No", "page": page,
             "box_2d": BOX},
            {"name": "doc_date", "text": "12/04/2026", "label": None, "page": page,
             "box_2d": [150, 100, 190, 400]},
        ],
        "lines": [
            {"page": page, "box_2d": [300, 50, 330, 950], "description": "SYN Widget",
             "quantity": "2", "amount": "200.00", "rate": "100.00"},
        ],
        "annotations": [
            {"kind": "handwritten", "text": "SYN-PO-7", "looks_like": "po_ref", "page": page,
             "box_2d": [600, 100, 640, 300]},
        ],
        "unreadable": [],
    }  # fmt: skip


def _parse(answer: dict[str, Any]) -> VisionOutput:
    parsed = parse_output("h6_vision", json.dumps(answer))
    assert isinstance(parsed, VisionOutput)
    return parsed


def test_a_clean_answer_parses_exactly_as_before_with_nothing_dropped() -> None:
    parsed = _parse(_answer())
    assert parsed.schema_dropped == {}
    assert parsed == VisionOutput.model_validate(_answer())
    assert parsed.model_dump() == VisionOutput.model_validate(_answer()).model_dump()


# --------------------------------------------------------------------------- dropped items


def _broken(list_name: str, index: int, change: dict[str, Any]) -> dict[str, Any]:
    answer = _answer()
    answer[list_name][index] = {**answer[list_name][index], **change}
    return answer


@pytest.mark.parametrize(
    ("list_name", "change", "reason"),
    [
        ("fields", {"text": "S" * 501}, "schema:fields.*.text: string_too_long"),
        ("fields", {"page": 0}, "schema:fields.*.page: greater_than_equal"),
        ("fields", {"stray": "x"}, "schema:fields.*.stray: extra_forbidden"),
        ("lines", {"description": "D" * 501}, "schema:lines.*.description: string_too_long"),
        ("lines", {"hsn_sac": 8471}, "schema:lines.*.hsn_sac: string_type"),
        ("annotations", {"kind": ""}, "schema:annotations.*.kind: string_too_short"),
        ("annotations", {"box_2d": []}, "schema:annotations.*.box_2d: too_short"),
    ],
)
def test_one_bad_item_is_dropped_and_counted_and_the_rest_is_kept(
    list_name: str, change: dict[str, Any], reason: str
) -> None:
    clean = _parse(_answer())
    parsed = _parse(_broken(list_name, 0, change))
    assert parsed.schema_dropped == {reason: 1}
    assert len(getattr(parsed, list_name)) == len(getattr(clean, list_name)) - 1
    for other in {"fields", "lines", "annotations"} - {list_name}:
        assert getattr(parsed, other) == getattr(clean, other)
    assert parsed.unreadable == []
    # Never truncated or repaired: no kept item carries the broken value.
    dumped = json.dumps(parsed.model_dump())
    assert "S" * 501 not in dumped and "D" * 501 not in dumped and "8471" not in dumped


def test_a_bad_unreadable_item_and_a_non_object_item_are_dropped() -> None:
    answer = _answer()
    answer["unreadable"] = [
        {"name": "po_ref", "page": 0, "box_2d": BOX},
        {"name": "irn", "page": 1, "box_2d": BOX},
    ]
    answer["lines"].append("SYN row as text")
    parsed = _parse(answer)
    assert parsed.schema_dropped == {
        "schema:unreadable.*.page: greater_than_equal": 1,
        "schema:lines.*: model_type": 1,
    }
    assert [item.name for item in parsed.unreadable] == ["irn"]
    assert len(parsed.lines) == 1


def test_each_bad_item_counts_once_under_its_first_broken_rule() -> None:
    answer = _answer()
    answer["fields"] = [
        {**answer["fields"][0], "text": "S" * 501, "label": "L" * 201},
        {**answer["fields"][1], "text": "S" * 501},
    ]
    parsed = _parse(answer)
    assert parsed.fields == []
    assert parsed.schema_dropped == {"schema:fields.*.text: string_too_long": 2}


# --------------------------------------------------------------------------- empty text


def test_an_empty_field_text_becomes_unreadable_never_a_value() -> None:
    answer = _broken("fields", 0, {"text": ""})
    answer["unreadable"] = [{"name": "irn", "page": 1, "box_2d": BOX}]
    parsed = _parse(answer)
    assert [item.name for item in parsed.fields] == ["doc_date"]
    assert [(item.name, item.page, item.box_2d) for item in parsed.unreadable] == [
        ("irn", 1, BOX),
        ("doc_no", 1, BOX),
    ]
    assert parsed.schema_dropped == {"schema:fields.*.text: string_too_short": 1}

    validation = validate_vision(parsed, VisionContext("invoice", PAGES, as_of=date(2026, 4, 15)))
    check = validation.check
    assert check.first("doc_no") is None
    assert {item.name for item in check.unreadable} == {"irn", "doc_no"}
    assert check.dropped["schema:fields.*.text: string_too_short"] == 1


def test_an_empty_text_with_another_broken_rule_is_only_dropped() -> None:
    parsed = _parse(_broken("fields", 0, {"text": "", "page": 0}))
    assert parsed.unreadable == []
    assert parsed.schema_dropped == {"schema:fields.*.text: string_too_short": 1}
    assert [item.name for item in parsed.fields] == ["doc_date"]


def test_an_empty_annotation_text_is_dropped_not_made_unreadable() -> None:
    parsed = _parse(_broken("annotations", 0, {"text": ""}))
    assert parsed.annotations == [] and parsed.unreadable == []
    assert parsed.schema_dropped == {"schema:annotations.*.text: string_too_short": 1}


# --------------------------------------------------------------------------- whole-answer rejects


def _without(name: str) -> dict[str, Any]:
    answer = _answer()
    del answer[name]
    return answer


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps([_answer()]),
        json.dumps(_without("lines")),
        json.dumps(_without("doc_kind")),
        json.dumps({**_answer(), "fields": "SYN"}),
        json.dumps({**_answer(), "doc_kind": 7}),
        json.dumps({**_answer(), "doc_kind": ""}),
        json.dumps({**_answer(), "verdict": "approved"}),
        json.dumps({**_answer(), "pages": [{"page": 1, "kind": ""}]}),
        json.dumps({**_answer(), "annotations": [_answer()["annotations"][0]] * 61}),
        '{"doc_kind": "invoice", "doc_kind": "other"}',
    ],
    ids=[
        "not_json", "not_object", "missing_lines", "missing_doc_kind", "fields_not_list",
        "doc_kind_type", "doc_kind_empty", "extra_top_key", "bad_page_info", "list_cap",
        "duplicate_key",
    ],
)  # fmt: skip
def test_a_top_level_error_still_rejects_the_whole_answer(text: str) -> None:
    with pytest.raises(InvalidOutput) as caught:
        parse_output("h6_vision", text)
    assert caught.value.code == "invalid_output"


def test_text_hooks_stay_strict_for_one_bad_item() -> None:
    suggestion = {"scope": "header", "field": "doc_no", "value": "", "page": 1, "quote": "q"}
    with pytest.raises(InvalidOutput):
        parse_output("h1_extract", json.dumps({"suggestions": [suggestion]}))


# --------------------------------------------------------------------------- both providers


def test_the_vllm_page_merge_uses_the_same_rule_and_counts_once() -> None:
    first = _broken("fields", 0, {"text": ""})
    second = _answer(page=2)
    second["lines"][0]["description"] = "D" * 501
    merged_text = vision.merge_page_answers([json.dumps(first), json.dumps(second)], pages=[1, 2])
    merged = parse_output("h6_vision", merged_text)
    assert isinstance(merged, VisionOutput)
    assert merged.schema_dropped == {
        "schema:fields.*.text: string_too_short": 1,
        "schema:lines.*.description: string_too_long": 1,
    }
    assert [(item.name, item.page) for item in merged.unreadable] == [("doc_no", 1)]
    assert [line.page for line in merged.lines] == [1]
    # The Gemini path (one answer, no merge) gives the same result for the same items.
    gemini = _parse(first)
    assert gemini.schema_dropped == {"schema:fields.*.text: string_too_short": 1}
    assert [item.name for item in gemini.unreadable] == ["doc_no"]
    # A page with a top-level error still loses the document.
    with pytest.raises(InvalidOutput):
        vision.merge_page_answers([json.dumps(first), json.dumps(_without("lines"))], pages=[1, 2])


# --------------------------------------------------------------------------- schema dialects

_MODELS: dict[str, type[BaseModel]] = {
    "": VisionOutput,
    "pages": VisionPageInfo,
    "fields": VisionFieldItem,
    "lines": VisionLineItem,
    "annotations": VisionAnnotationItem,
    "unreadable": VisionUnreadableItem,
}


def _limits(model: type[BaseModel], name: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for meta in model.model_fields[name].metadata:
        if getattr(meta, "min_length", None) is not None:
            found["minLength"] = meta.min_length
        if getattr(meta, "max_length", None) is not None:
            found["maxLength"] = meta.max_length
    return found


def _string_nodes(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    objects = {"": schema} | {
        name: child["items"]
        for name, child in schema["properties"].items()
        if child.get("type") == "array" and child["items"].get("type") == "object"
    }
    for owner, node in objects.items():
        for key, prop in node["properties"].items():
            inner = prop["anyOf"][0] if "anyOf" in prop else prop
            if inner.get("type") == "string":
                nodes[(owner, key)] = inner
    return nodes


def test_the_vllm_schema_carries_the_pydantic_length_limits() -> None:
    nodes = _string_nodes(vision_response_schema("openai_json_schema"))
    free_text = 0
    for (owner, key), node in nodes.items():
        if "enum" in node:
            assert "minLength" not in node and "maxLength" not in node, (owner, key)
            continue
        free_text += 1
        expected = _limits(_MODELS[owner], key)
        assert {k: v for k, v in node.items() if k.endswith("Length")} == expected, (owner, key)
    assert free_text >= 20
    assert nodes[("fields", "text")] == {"type": "string", "minLength": 1, "maxLength": 500}
    assert nodes[("lines", "description")] == {"type": "string", "maxLength": 500}


def test_the_gemini_schema_has_no_length_limits_and_is_unchanged() -> None:
    gemini = vision_response_schema("gemini")
    text = json.dumps(gemini, sort_keys=True)
    assert "minLength" not in text and "maxLength" not in text
    expected = vision_response_schema_v3()
    expected["properties"]["unreadable"] = gemini["properties"]["unreadable"]
    expected["required"].append("unreadable")
    assert gemini == expected
    assert vision_response_schema() == gemini
