"""Vision reader in the accuracy bench (AC-5): synthetic documents, recorded answers, no network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from reconbench_app.ai.provider import ProviderRequest, ProviderResult, ProviderUsage
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.parsers import ParserHints
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, FileFormat
from scripts import accuracy_vision
from scripts.accuracy_bench import (
    _HEADER_FIELDS,
    TruthDocument,
    _normal,
    _same,
    extraction_summary,
    score_document,
)
from scripts.vision_fixtures import (
    BUYER_GSTIN,
    GRAND,
    INVOICE_NO,
    SUPPLIER_GSTIN,
    recorded_response,
    synthetic_invoice_pdf,
)

MODEL = "gemini-3.8-flash"


def _target() -> accuracy_vision.VisionTarget:
    content = synthetic_invoice_pdf()
    result = parse_pdf_isolated(
        content, ParserHints(doc_type=DocType.INVOICE, file_format=FileFormat.PDF)
    )
    [document] = result.documents
    return accuracy_vision.VisionTarget(
        file_id="F1",
        index=0,
        truth_id="T1",
        doc_type=DocType.INVOICE,
        document=document,
        content=content,
        pages=accuracy_vision.document_pages(document, len(result.page_texts)),
    )


def _prepare(targets: list[accuracy_vision.VisionTarget], tmp_path: Path) -> None:
    accuracy_vision.prepare(
        targets,
        root=Path(__file__).resolve().parents[3],
        pages_cache=tmp_path / "pages",
        tool_versions={"tesseract": "synthetic", "languages": "eng"},
        languages=("eng",),
        refresh=False,
        model=MODEL,
        workers=1,
    )


def _truth() -> TruthDocument:
    return TruthDocument(
        doc_id="T1",
        set_id="S1",
        doc_type=DocType.INVOICE,
        files=("F1",),
        header={
            "doc_no": INVOICE_NO,
            "vendor_gstin": SUPPLIER_GSTIN,
            "grand_total": GRAND,
        },
        lines=None,
        links=(),
        expected_status=(),
        # Truth v2 keeps handwritten refs beside the header, not in it.
        annotation_refs=("PO/26-27/04-777", "GRN/26-27/04-999"),
    )


def test_pages_are_cached_and_the_request_key_is_stable(tmp_path: Path) -> None:
    first, second = _target(), _target()
    _prepare([first], tmp_path)
    _prepare([second], tmp_path)  # served from the page cache
    assert first.outcome == second.outcome == "missing"
    assert first.key is not None and first.key == second.key
    assert len(list((tmp_path / "pages").glob("*.jpg"))) == 1


def test_recorded_answer_is_scored_alone_and_against_ocr(tmp_path: Path) -> None:
    target = _target()
    _prepare([target], tmp_path)
    answers = tmp_path / "vision"
    answers.mkdir()
    assert target.key is not None
    (answers / f"{target.key}.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "output_text": json.dumps(recorded_response()),
                "usage": {"input_tokens": 1400, "output_tokens": 900, "thought_tokens": 50},
            }
        ),
        encoding="utf-8",
    )
    run = accuracy_vision.VisionRun("cached", MODEL, [target])
    accuracy_vision.check_answers(
        [target], answers, (BuyerEntityHint(gstin=BUYER_GSTIN, name="Buyer"),), run.usage
    )
    assert target.outcome == "read"
    block = accuracy_vision.summary(
        run,
        {"T1": _truth()},
        score_document,
        extraction_summary,
        _normal,
        _same,
        _HEADER_FIELDS,
    )
    assert block["outcomes"] == {"read": 1}
    assert block["usage"] == {"input_tokens": 1400, "output_tokens": 900, "thought_tokens": 50}
    assert block["alone"]["all"]["header_correct"].startswith("3/3")
    assert block["annotation_refs_found"] == "1/2"
    assert block["handwritten_refs"] == {"expected": 2, "missed": 1, "typed": 1}
    assert block["line_cells"]["rows"] == 2
    assert block["line_cells"]["with_description"] == block["line_cells"]["with_amount"] == 2
    assert block["failures"] == []
    assert block["output_tokens_per_call"] == {
        "answers": 1,
        "max": 950,
        "over_4096": 0,
        "over_8192": 0,
        "over_16384": 0,
    }
    assert sum(block["agreement"]["all"].values()) == 3
    assert "email" in block["masked_regions"]
    # Counts only: no document value is in the report.
    assert INVOICE_NO not in json.dumps(block) and SUPPLIER_GSTIN not in json.dumps(block)


def test_send_records_answers_and_keeps_failures_out(tmp_path: Path) -> None:
    from reconbench_app.ai.provider import ProviderFailure

    first, second = _target(), _target()
    _prepare([first, second], tmp_path)
    seen: list[ProviderRequest] = []

    def complete(request: ProviderRequest, key: str) -> ProviderResult:
        assert key == "AIzaSyD-synthetic-bench-key-000000000"
        seen.append(request)
        if len(seen) == 1:  # the first copy fails; the second (same key) is then sent
            raise ProviderFailure("rate_limited", delivery="responded")
        return ProviderResult(json.dumps(recorded_response()), ProviderUsage(1400, 900, None))

    counts = accuracy_vision.send(
        [first, second],
        tmp_path / "vision",
        model=MODEL,
        api_key="AIzaSyD-synthetic-bench-key-000000000",
        complete=complete,
        sleep=lambda seconds: None,
    )
    assert counts == {"sent": 1, "failed:rate_limited": 1}
    assert len(seen[0].images) == 1 and seen[0].images[0].mime_type == "image/jpeg"
    stored: dict[str, Any] = json.loads(
        next((tmp_path / "vision").glob("*.json")).read_text(encoding="utf-8")
    )
    assert stored["usage"]["input_tokens"] == 1400 and stored["pages"] == 1


def _vision_request() -> ProviderRequest:
    return ProviderRequest(
        model=MODEL,
        system_instruction="synthetic",
        input_text="synthetic",
        response_schema={"type": "object"},
        max_output_tokens=1_000,
        timeout_ms=60_000,
    )


def test_diagnostics_keep_google_error_text_but_no_key() -> None:
    import httpx
    from reconbench_app.ai.provider import ProviderFailure
    from reconbench_app.ai.worker.gemini import GeminiProvider
    from scripts.vision_diagnostics import DiagnosticTransport

    key = "AIzaSyD-synthetic-bench-key-000000000"

    def handler(request: httpx.Request) -> httpx.Response:
        message = f"Request contains an invalid argument. key {key} size 123456789"
        return httpx.Response(
            400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": message}}
        )

    transport = DiagnosticTransport(httpx.MockTransport(handler))
    try:
        GeminiProvider(inner_transport=transport).complete(_vision_request(), api_key=key)
    except ProviderFailure as failure:
        assert failure.code == "INVALID_REQUEST"  # the adapter behaves as in the app
    else:
        raise AssertionError("expected a failure")
    info = transport.info
    assert info["http"] == 400 and info["error_status"] == "INVALID_ARGUMENT"
    assert info["error_message"].startswith("Request contains an invalid argument.")
    assert key not in json.dumps(info) and "123456789" not in json.dumps(info)


def test_an_incomplete_answer_reports_status_tokens_and_a_loop_without_values() -> None:
    import httpx
    from reconbench_app.ai.provider import ProviderFailure
    from reconbench_app.ai.worker.gemini import GeminiProvider
    from scripts.vision_diagnostics import DiagnosticTransport

    row = '{"page":1,"box_2d":[%d,1,2,3],"description":"SYNTHETIC SECRET ROW","amount":"9.00"}'
    partial = '{"doc_kind":"invoice","lines":[' + ",".join(row % i for i in range(30))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "status": "incomplete",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": partial}]}],
                "usage": {"total_input_tokens": 2400, "total_output_tokens": 990},
            },
        )

    transport = DiagnosticTransport(httpx.MockTransport(handler))
    try:
        GeminiProvider(inner_transport=transport).complete(_vision_request(), api_key="AIza-x")
    except ProviderFailure as failure:
        entry = accuracy_vision.failure_entry(failure, transport.info, pages=2, cap=1_000)
    else:
        raise AssertionError("expected a failure")
    assert entry["code"] == "invalid_output" and entry["status"] == "incomplete"
    assert entry["output_tokens"] == 990 and entry["at_output_cap"] is True
    assert entry["partial_rows"] == 30 and entry["distinct_rows"] == 1
    assert entry["loop_suspected"] is True
    assert "SECRET" not in json.dumps(entry)
    summary = accuracy_vision._failure_summary([entry, entry])
    assert summary == [{**entry, "calls": 2}]


def test_after_failure_stops_only_when_the_rest_cannot_succeed() -> None:
    after = accuracy_vision._after_failure
    assert after("rate_limited", "limit: 20 requests per day on Free Tier", 1)[0] == "daily_quota"
    assert after("rate_limited", "per minute", 1) == (None, 60.0)
    assert after("rate_limited", "", 3)[0] == "rate_limited"
    assert after("INVALID_REQUEST", "Request blocked due to copyright/recitation", 5) == (None, 0.0)
    assert after("INVALID_REQUEST", "Invalid JSON schema", 1) == (None, 0.0)
    assert after("INVALID_REQUEST", "Invalid JSON schema", 2)[0] == "request_rejected"
    assert after("PROVIDER_UNAVAILABLE", "high demand", 4) == (None, 30.0)
    assert after("PROVIDER_UNAVAILABLE", "high demand", 5)[0] == "provider_down"
    assert after("bad_key", "", 1)[0] == "bad_key"
    assert after("invalid_output", "", 9) == (None, 0.0)  # per-document: keep going


def test_send_stops_at_the_daily_quota(tmp_path: Path) -> None:
    from reconbench_app.ai.provider import ProviderFailure

    targets = [_target() for _ in range(3)]
    _prepare(targets, tmp_path)
    for index, target in enumerate(targets):  # three different requests
        target.key = f"{target.key}-{index}"
    calls: list[ProviderRequest] = []

    def complete(request: ProviderRequest, key: str) -> ProviderResult:
        calls.append(request)
        raise ProviderFailure("rate_limited", delivery="responded")

    failures: list[dict[str, Any]] = []
    original = accuracy_vision._after_failure
    try:
        accuracy_vision._after_failure = lambda code, message, streak: original(  # type: ignore[assignment]
            code, "20 requests per day", streak
        )
        counts = accuracy_vision.send(
            targets,
            tmp_path / "vision",
            model=MODEL,
            api_key="AIzaSyD-synthetic-bench-key-000000000",
            complete=complete,
            failures=failures,
            sleep=lambda seconds: None,
        )
    finally:
        accuracy_vision._after_failure = original  # type: ignore[assignment]
    assert len(calls) == 1
    assert counts == {"failed:rate_limited": 1, "stopped:daily_quota": 2}


def test_degenerate_answer_counts_show_where_it_ran_away() -> None:
    from scripts.vision_diagnostics import looks_like_a_loop, row_counts

    field = '{"name":"other_ref","text":"SYNTHETIC","page":1,"box_2d":[1,2,3,4]}'
    repeated = '{"doc_kind":"invoice","pages":[],"fields":[' + ",".join([field] * 50)
    counts = row_counts(repeated)
    assert counts["last_section"] == "fields" and counts["objects"] == 50
    assert counts["distinct_objects"] == 1 and looks_like_a_loop(counts)
    runaway_box = '{"fields":[{"name":"doc_no","text":"X","page":1,"box_2d":[' + "1," * 40
    counts = row_counts(runaway_box)
    assert counts["longest_int_array"] >= 40 and looks_like_a_loop(counts)
    runaway_text = '{"fields":[{"name":"amount_in_words","text":"' + "A" * 3_000
    counts = row_counts(runaway_text)
    assert counts["tail_chars"] == 3_000 and looks_like_a_loop(counts)
    assert "SYNTHETIC" not in json.dumps(counts)


def test_truth_loader_reads_handwritten_refs_beside_the_header(tmp_path: Path) -> None:
    from scripts.accuracy_bench import TRUTH_FORMAT, load_truth

    path = tmp_path / "truth.json"
    path.write_text(
        json.dumps(
            {
                "format": TRUTH_FORMAT,
                "config": {"run_date": "2026-10-07"},
                "files": [{"id": "F1", "path": "invoice/a.pdf", "zone": "invoice"}],
                "sets": [
                    {
                        "id": "S1",
                        "documents": [
                            {
                                "id": "T1",
                                "doc_type": "invoice",
                                "sources": [{"file": "F1"}],
                                "header": {"doc_no": INVOICE_NO},
                                "annotation_refs": ["PO/26-27/04-777"],
                                "marketplace_order_no": ["407-0000000-0000000"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    [document] = load_truth(path).documents
    assert document.annotation_refs == ("PO/26-27/04-777",)
    assert document.marketplace_order_no == ("407-0000000-0000000",)
    assert "annotation_refs" not in document.header


def test_short_partial_refs_do_not_count() -> None:
    assert accuracy_vision._ref_hit(["PO/26-27/04-077"], ["04-077"]) == (1, 1)
    assert accuracy_vision._ref_hit(["PO/26-27/04-077"], ["077"]) == (0, 1)
    assert accuracy_vision._ref_hit(["04-555"], ["PO 26-27/04-555"]) == (1, 1)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_probe_dry_run_writes_masked_images_and_sends_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from reconbench_app.ai.worker import gemini
    from scripts import vision_probe

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the dry run must not create a provider client")

    monkeypatch.setattr(gemini.GeminiProvider, "__init__", forbidden)
    monkeypatch.delenv("PROBE_KEY", raising=False)
    assert vision_probe.main(["--out", str(tmp_path), "--resolutions", "high"]) == 0
    report = json.loads((tmp_path / "probe-report.json").read_text(encoding="utf-8"))
    assert report["sent"] is False and report["calls"] == []
    assert len(list(tmp_path.glob("*.jpg"))) >= 3
