"""WI-EV1 evaluation harness: one provider per run, repeats, vision_eval counts, no network.

Synthetic documents and truth only; provider answers are recorded files or an
``httpx.MockTransport`` standing in for the vLLM server.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from reconbench_app.ai.provider import ProviderFailure
from reconbench_app.extraction.vision import vision_reader
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.parsers import ParserHints
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, FileFormat
from scripts import accuracy_bench, accuracy_vision, accuracy_vision_eval
from scripts.accuracy_bench import (
    _CRITICAL,
    _HEADER_FIELDS,
    TruthDocument,
    _normal,
    _same,
    score_document,
)
from scripts.vision_fixtures import (
    BUYER_GSTIN,
    GRAND,
    INVOICE_NO,
    LINES,
    SUPPLIER_GSTIN,
    TAXABLE,
    recorded_response,
    synthetic_invoice_pdf,
)

GEMINI = "gemini-3.8-flash"
QWEN = "qwen3.5:9b"
VLLM_URL = "http://10.0.0.5/vllm/v1"
BUYERS = (BuyerEntityHint(gstin=BUYER_GSTIN, name="Buyer"),)
VLLM = accuracy_vision.VisionArm(provider="vllm", base_url=VLLM_URL)


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
        scan_type="digital",
        ocr_text="\n".join(page.content for page in result.page_texts),
    )


def _prepare(
    targets: list[accuracy_vision.VisionTarget],
    tmp_path: Path,
    arm: accuracy_vision.VisionArm = accuracy_vision.DEFAULT_ARM,
    model: str = GEMINI,
) -> None:
    accuracy_vision.prepare(
        targets,
        root=Path(__file__).resolve().parents[3],
        pages_cache=tmp_path / "pages",
        tool_versions={"tesseract": "synthetic", "languages": "eng"},
        languages=("eng",),
        refresh=False,
        model=model,
        workers=1,
        arm=arm,
    )


def _record(directory: Path, key: str, answer: dict[str, Any], **extra: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.json").write_text(
        json.dumps({"model": GEMINI, "output_text": json.dumps(answer), **extra}),
        encoding="utf-8",
    )


def _truth(**header: Any) -> TruthDocument:
    return TruthDocument(
        doc_id="T1",
        set_id="S1",
        doc_type=DocType.INVOICE,
        files=("F1",),
        header={
            "doc_no": INVOICE_NO,
            "vendor_gstin": SUPPLIER_GSTIN,
            "grand_total": GRAND,
            "taxable_total": TAXABLE,
            **header,
        },
        lines=tuple(
            {"description": description, "hsn_sac": hsn, "quantity": qty, "amount": amount}
            for description, hsn, qty, _rate, amount in LINES
        ),
        links=(),
        expected_status=(),
    )


WRONG_NO = "SYN-INV-0043"
"""A well-formed but wrong invoice number: it passes every standalone check."""


def _answer(
    values: dict[str, str] | None = None,
    *,
    drop: tuple[str, ...] = (),
    unreadable: tuple[str, ...] = (),
) -> dict[str, Any]:
    answer = recorded_response()
    fields = []
    for item in answer["fields"]:
        if item["name"] in drop:
            continue
        if item["name"] in (values or {}):
            item = {**item, "text": (values or {})[item["name"]]}
        fields.append(item)
    answer["fields"] = fields
    answer["unreadable"] = [
        {"name": name, "page": 1, "box_2d": [100, 100, 120, 300]} for name in unreadable
    ]
    return answer


# --------------------------------------------------------------------------- arms and keys


def test_default_arm_is_the_app_reader_and_keeps_every_key(tmp_path: Path) -> None:
    assert accuracy_vision.DEFAULT_ARM.reader() == vision_reader("vision_gemini")
    plain, armed = _target(), _target()
    _prepare([plain], tmp_path)
    accuracy_vision.prepare(
        [armed],
        root=Path(__file__).resolve().parents[3],
        pages_cache=tmp_path / "pages",
        tool_versions={"tesseract": "synthetic", "languages": "eng"},
        languages=("eng",),
        refresh=False,
        model=GEMINI,
        workers=1,
    )
    assert plain.key == armed.key and plain.legacy_key == armed.legacy_key
    assert plain.repeat_keys == () and plain.parts == ()
    assert accuracy_vision.repeat_key("k" * 64, 0) == "k" * 64


def test_repeat_and_reader_settings_change_the_key(tmp_path: Path) -> None:
    base, repeated, medium, qwen = _target(), _target(), _target(), _target()
    _prepare([base], tmp_path)
    _prepare([repeated], tmp_path, accuracy_vision.VisionArm(repeats=3))
    _prepare([medium], tmp_path, accuracy_vision.VisionArm(thinking="medium"))
    _prepare([qwen], tmp_path, VLLM, QWEN)
    assert repeated.key == base.key  # repeat 0 reuses the recorded answer
    assert len(set(repeated.repeat_keys)) == 2 and base.key not in repeated.repeat_keys
    assert repeated.repeat_keys == tuple(
        accuracy_vision.repeat_key(str(base.key), index) for index in (1, 2)
    )
    assert medium.key != base.key
    assert qwen.key not in {base.key, medium.key}
    assert len(qwen.parts) == len(qwen.rows) == 1  # one page per call
    assert not accuracy_vision.compat_v1_applies(VLLM)


@pytest.mark.parametrize(
    ("arm", "model", "message"),
    [
        (accuracy_vision.VisionArm(strategy="crops"), GEMINI, "WI-BE8"),
        (accuracy_vision.VisionArm(repeats=0), GEMINI, "--repeat"),
        (accuracy_vision.VisionArm(base_url=VLLM_URL), GEMINI, "vllm only"),
        (accuracy_vision.VisionArm(thinking="off"), GEMINI, "minimal, low"),
        (accuracy_vision.VisionArm(sampling="qwen_card_nonthinking"), GEMINI, "provider_default"),
        (accuracy_vision.VisionArm(), QWEN, "not a Gemini model"),
        (accuracy_vision.VisionArm(provider="vllm", thinking="low"), QWEN, "off only"),
        (
            accuracy_vision.VisionArm(provider="vllm", sampling="provider_default"),
            QWEN,
            "qwen_card",
        ),
        (accuracy_vision.VisionArm(provider="vllm"), GEMINI, "is a Gemini model"),
        (
            accuracy_vision.VisionArm(provider="vllm", base_url="http://evil.example/v1"),
            QWEN,
            "RB_AI_VLLM_ENDPOINTS",
        ),
    ],
)
def test_unrunnable_or_mixed_settings_are_refused(
    arm: accuracy_vision.VisionArm, model: str, message: str
) -> None:
    with pytest.raises(accuracy_vision.ArmRefused, match=message):
        accuracy_vision.check_arm(arm, allowlist=VLLM_URL, model=model)


def test_allowlisted_base_url_is_normalised() -> None:
    arm = accuracy_vision.VisionArm(provider="vllm", base_url="HTTP://10.0.0.5:80/vllm/v1/")
    checked = accuracy_vision.check_arm(arm, allowlist=f"{VLLM_URL}, http://x.test/v1", model=QWEN)
    assert checked.base_url == VLLM_URL


def test_primary_sim_and_crops_are_refused_before_anything_is_read(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-truth.json")
    with pytest.raises(SystemExit, match="WI-BE7"):
        accuracy_bench.main(["--truth", missing, "--vision-primary-sim"])
    with pytest.raises(SystemExit, match="WI-BE8"):
        accuracy_bench.main(["--truth", missing, "--vision-strategy", "crops"])
    with pytest.raises(SystemExit):  # removed policies are not even parsed
        accuracy_bench.main(["--truth", missing, "--agreement-policy", "llm_x_llm"])
    with pytest.raises(SystemExit):
        accuracy_bench.main(["--truth", missing, "--second-vision-report", "x.json"])


# --------------------------------------------------------------------------- vLLM sending


def _server(seen: list[httpx.Request], *, window: int = 8192) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": QWEN, "max_model_len": window}]})
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        content = [part for part in body["messages"][1]["content"] if part["type"] == "image_url"]
        assert len(content) == 1
        return httpx.Response(
            200,
            json={
                "model": QWEN,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(recorded_response())},
                    }
                ],
                "usage": {"prompt_tokens": 3900, "completion_tokens": 700},
            },
        )

    return httpx.MockTransport(handler)


def test_vllm_arm_calls_only_the_allowlisted_server_and_never_builds_gemini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reconbench_app.ai.worker import gemini

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a Gemini client was built")

    monkeypatch.setattr(gemini.GeminiProvider, "__init__", refuse)
    arm = accuracy_vision.VisionArm(provider="vllm", base_url=VLLM_URL, repeats=2)
    target = _target()
    _prepare([target], tmp_path, arm, QWEN)
    seen: list[httpx.Request] = []
    counts = accuracy_vision.send(
        [target],
        tmp_path / "vision",
        model=QWEN,
        api_key="",
        arm=arm,
        vllm_transport=_server(seen),
        sleep=lambda seconds: None,
    )
    assert counts == {"sent": 2}
    assert {str(request.url) for request in seen} == {
        f"{VLLM_URL}/models",
        f"{VLLM_URL}/chat/completions",
    }
    posts = [request for request in seen if request.method == "POST"]
    assert len(posts) == 2  # one page, two repeats
    body = json.loads(posts[0].content)
    assert body["model"] == QWEN and body["top_k"] == 20  # qwen_card_nonthinking
    assert "Authorization" not in posts[0].headers
    for key in (target.key, *target.repeat_keys):
        stored = json.loads((tmp_path / "vision" / f"{key}.json").read_text(encoding="utf-8"))
        assert stored["provider"] == "vllm" and stored["calls"] == 1
    run = accuracy_vision.VisionRun("provider", QWEN, [target], arm=arm)
    accuracy_vision.check_answers(
        [target], tmp_path / "vision", BUYERS, run.usage, arm=arm, repeat_usage=run.repeat_usage
    )
    assert target.outcome == "read" and len(target.repeat_validations) == 1
    assert run.usage["input_tokens"] == run.repeat_usage["input_tokens"] == 3900


def test_send_from_repeat_sends_only_the_later_repeats(tmp_path: Path) -> None:
    arm = accuracy_vision.VisionArm(
        provider="vllm", base_url=VLLM_URL, repeats=3, send_from_repeat=2
    )
    target = _target()
    _prepare([target], tmp_path, arm, QWEN)
    seen: list[httpx.Request] = []
    counts = accuracy_vision.send(
        [target],
        tmp_path / "vision",
        model=QWEN,
        api_key="",
        arm=arm,
        vllm_transport=_server(seen),
        sleep=lambda seconds: None,
    )
    assert counts == {"sent": 1}
    assert sum(request.method == "POST" for request in seen) == 1
    stored = [
        (tmp_path / "vision" / f"{key}.json").is_file() for key in (target.key, *target.repeat_keys)
    ]
    assert stored == [False, False, True]
    # Same keys as a plain --repeat 3 arm: the option only decides what is sent.
    assert target.repeat_keys == tuple(
        accuracy_vision.repeat_key(target.key, index) for index in (1, 2)
    )
    for bad in (-1, 3):
        with pytest.raises(accuracy_vision.ArmRefused, match="--send-from-repeat"):
            accuracy_vision.check_arm(
                accuracy_vision.VisionArm(
                    provider="vllm", base_url=VLLM_URL, repeats=3, send_from_repeat=bad
                ),
                allowlist=VLLM_URL,
                model=QWEN,
            )


def test_vllm_context_too_small_sends_nothing(tmp_path: Path) -> None:
    target = _target()
    _prepare([target], tmp_path, VLLM, QWEN)
    seen: list[httpx.Request] = []
    failures: list[dict[str, Any]] = []
    counts = accuracy_vision.send(
        [target],
        tmp_path / "vision",
        model=QWEN,
        api_key="",
        arm=VLLM,
        failures=failures,
        vllm_transport=_server(seen, window=4096),
    )
    assert counts == {"skipped:CONTEXT_TOO_SMALL": 1}
    assert [request.method for request in seen] == ["GET"]
    assert failures == [
        {"code": "CONTEXT_TOO_SMALL", "pages": 1, "document": "F1#0", "truth_id": "T1"}
    ]


def test_vllm_without_a_base_url_refuses_to_send(tmp_path: Path) -> None:
    target = _target()
    arm = accuracy_vision.VisionArm(provider="vllm")
    _prepare([target], tmp_path, arm, QWEN)
    with pytest.raises(accuracy_vision.ArmRefused, match="--vision-base-url"):
        accuracy_vision.send([target], tmp_path / "vision", model=QWEN, api_key="", arm=arm)


def test_thinking_leak_is_a_failure_with_its_code_only(tmp_path: Path) -> None:
    target = _target()
    _prepare([target], tmp_path, VLLM, QWEN)

    def complete(request: Any, key: str) -> Any:
        raise ProviderFailure("invalid_output", delivery="responded", detail="thinking_leak")

    failures: list[dict[str, Any]] = []
    counts = accuracy_vision.send(
        [target], tmp_path / "vision", model=QWEN, api_key="", arm=VLLM,
        complete=complete, failures=failures,
    )  # fmt: skip
    assert counts == {"failed:invalid_output": 1}
    assert failures[0]["code"] == "invalid_output" and failures[0]["detail"] == "thinking_leak"
    assert failures[0]["document"] == "F1#0" and failures[0]["truth_id"] == "T1"


# --------------------------------------------------------------------------- vision_eval


def _evaluated(
    tmp_path: Path,
    answers: list[dict[str, Any]],
    *,
    policy: str = "single",
    truth: TruthDocument | None = None,
    price_file: Path | None = None,
    latencies: tuple[int, ...] = (),
) -> dict[str, Any]:
    arm = accuracy_vision.VisionArm(repeats=len(answers))
    target = _target()
    _prepare([target], tmp_path, arm)
    assert target.key is not None
    keys = (target.key, *target.repeat_keys)
    for index, (key, answer) in enumerate(zip(keys, answers, strict=True)):
        extra: dict[str, Any] = {"usage": {"input_tokens": 1000, "output_tokens": 500}}
        if index < len(latencies):
            extra["latency_ms"] = latencies[index]
        _record(tmp_path / "vision", key, answer, **extra)
    run = accuracy_vision.VisionRun("cached", GEMINI, [target], arm=arm)
    accuracy_vision.check_answers(
        [target], tmp_path / "vision", BUYERS, run.usage, arm=arm, repeat_usage=run.repeat_usage
    )
    run.failures.append({"code": "rate_limited", "pages": 1})
    return accuracy_vision_eval.evaluate(
        run,
        {"T1": truth or _truth()},
        normal=_normal,
        same=_same,
        header_fields=_HEADER_FIELDS,
        critical=_CRITICAL,
        score_document=score_document,
        policy=policy,
        price_file=price_file,
    )


def test_correct_reading_scores_every_header_and_line_cell(tmp_path: Path) -> None:
    block = _evaluated(tmp_path, [recorded_response()], latencies=(4000,))
    llm = block["accuracy"]["all_pdf"]["llm"]
    assert llm["header"]["accuracy"] == "4/4"
    assert llm["line:amount"]["accuracy"] == f"{len(LINES)}/{len(LINES)}"
    assert llm["line:hsn_sac"]["correct"] == llm["line:description"]["correct"] == len(LINES)
    assert "line:uom" not in llm  # truth has no UOM, so it is not scored
    assert "ocr" in block["accuracy"]["all_pdf"]  # the rules reader on the same documents
    assert "scan_page" not in block["accuracy"]  # a digital PDF
    assert block["accuracy"]["scan_type:digital"]["llm"]["header"]["total"] == 4
    assert block["wrong_when_answered"] == {
        "all_answered": 4,
        "all_wrong": 0,
        "critical_answered": 3,
        "critical_wrong": 0,
    }
    assert block["not_in_ocr_text"] == {"answered": 4, "not_in_ocr_text": 0}
    assert block["false_safe"]["simulated_flags_kept"] == 0
    # Clearing review on the accepted header lets the rules reader's line misses through.
    assert block["false_safe"]["review_cleared_documents"] == 1
    assert block["false_safe"]["simulated_review_cleared"] == 1
    assert block["latency_ms"] == {"answers": 1, "p50": 4000, "p95": 4000}
    assert block["tokens"] == {"input": 1000, "output": 500, "thought": 0}
    assert block["cost"] == {"usd": None, "price_version": None}
    assert block["failures_by_code"] == {"call:rate_limited": 1}
    assert block["arm"]["provider"] == "gemini" and block["agreement_policy"] == "single"


def test_wrong_and_missing_values_are_counted_without_values(tmp_path: Path) -> None:
    answer = _answer(
        {"grand_total": "999999.00"}, drop=("doc_no", "taxable_total"), unreadable=("doc_no",)
    )
    block = _evaluated(tmp_path, [answer])
    llm = block["accuracy"]["all_pdf"]["llm"]["header"]
    assert (llm["correct"], llm["wrong"], llm["missing"]) == (1, 1, 2)
    assert block["unreadable_to_null"] == {
        "listed_unreadable": 1,
        "null_answers": 2,
        "silently_omitted": 1,
    }
    assert block["wrong_when_answered"]["all_wrong"] == 1
    assert block["wrong_when_answered"]["critical_answered"] == 2  # GSTIN and total
    assert block["not_in_ocr_text"] == {"answered": 2, "not_in_ocr_text": 1}
    assert block["agree_wrong_llm_x_ocr"] == {"agree_correct": 1, "disagree": 1}
    text = json.dumps(block)
    for value in (INVOICE_NO, SUPPLIER_GSTIN, GRAND, "999999", '"F1"', '"T1"'):
        assert value not in text


def test_policies_choose_the_auto_accept_subset(tmp_path: Path) -> None:
    answer = _answer({"doc_no": WRONG_NO})
    single = _evaluated(tmp_path / "a", [answer])["auto_accept"]
    paired = _evaluated(tmp_path / "b", [answer], policy="llm_x_ocr")["auto_accept"]
    assert single["doc_no"] == {
        "accepted": 1,
        "accepted_correct": 0,
        "coverage": "1/1",
        "precision": "0/1",
        "truth_fields": 1,
        "wilson95_lower": 0.0,
    }
    # OCR read the right number, so the wrong LLM number is no candidate under llm_x_ocr.
    assert paired["doc_no"]["accepted"] == 0 and paired["doc_no"]["wilson95_lower"] is None
    assert single["amount"] == paired["amount"]  # the totals agree with OCR
    assert paired["amount"]["wilson95_lower"] == accuracy_vision_eval.wilson_lower(2, 2)


def test_a_total_that_fails_its_checks_is_never_auto_accepted(tmp_path: Path) -> None:
    block = _evaluated(tmp_path, [_answer({"grand_total": "999999.00"})])
    assert block["auto_accept"]["amount"]["accepted"] == 1  # taxable only
    assert block["false_safe"]["review_cleared_documents"] == 0  # the total was not accepted


def test_false_safe_under_simulation_counts_a_wrong_accepted_value(tmp_path: Path) -> None:
    header_only = replace(_truth(), lines=None)
    right = _evaluated(tmp_path / "r", [recorded_response()], truth=header_only)["false_safe"]
    assert right["review_cleared_documents"] == 1 and right["simulated_review_cleared"] == 0
    wrong = _evaluated(tmp_path / "w", [_answer({"doc_no": WRONG_NO})], truth=header_only)
    assert wrong["false_safe"]["documents"] == 1 and wrong["false_safe"]["rules"] == 0
    assert wrong["false_safe"]["simulated_flags_kept"] == 0  # the rules flag still holds it
    assert wrong["false_safe"]["simulated_review_cleared"] == 1  # wrong number let through
    paired = _evaluated(
        tmp_path / "p", [_answer({"doc_no": WRONG_NO})], policy="llm_x_ocr", truth=header_only
    )
    assert paired["false_safe"]["review_cleared_documents"] == 0
    assert paired["false_safe"]["simulated_review_cleared"] == 0


def test_repeat_variance_is_the_share_of_fields_that_change(tmp_path: Path) -> None:
    block = _evaluated(
        tmp_path,
        [recorded_response(), recorded_response(), _answer({"doc_no": WRONG_NO})],
        latencies=(1000, 2000, 9000),
    )
    variance = block["repeat_variance"]
    assert variance["documents"] == 1 and variance["fields_changed"] == 1
    assert variance["share_changed"] == f"1/{variance['fields_compared']}"
    assert block["latency_ms"] == {"answers": 3, "p50": 2000, "p95": 9000}
    assert block["tokens"]["input"] == 3000  # all repeats
    assert block["arm"]["repeats"] == 3


def test_cost_comes_only_from_the_price_file(tmp_path: Path) -> None:
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "price_version": "synthetic-1",
                "models": {
                    GEMINI: {"input_usd_per_million": "1.00", "output_usd_per_million": "2.00"}
                },
            }
        ),
        encoding="utf-8",
    )
    block = _evaluated(tmp_path, [recorded_response()], price_file=prices)
    assert block["cost"] == {"usd": "0.0020", "price_version": "synthetic-1"}


def test_statistics() -> None:
    assert accuracy_vision_eval.wilson_lower(0, 0) is None
    assert accuracy_vision_eval.wilson_lower(10, 10) == pytest.approx(0.7225, abs=1e-4)
    assert accuracy_vision_eval.wilson_lower(0, 5) == 0.0
    assert accuracy_vision_eval.percentile([], 0.5) is None
    values = list(range(1, 21))
    assert accuracy_vision_eval.percentile(values, 0.5) == 10
    assert accuracy_vision_eval.percentile(values, 0.95) == 19
    assert Decimal(TAXABLE) > 0
    with pytest.raises(ValueError, match="agreement policy"):
        accuracy_vision_eval.evaluate(
            accuracy_vision.VisionRun("cached", GEMINI, []),
            {},
            normal=_normal,
            same=_same,
            header_fields=_HEADER_FIELDS,
            critical=_CRITICAL,
            score_document=score_document,
            policy="llm_x_llm",
        )


# --------------------------------------------------------------------------- bench output


def _bench(tmp_path: Path, *extra: str) -> dict[str, Any]:
    samples = tmp_path / "samples"
    samples.mkdir(exist_ok=True)
    (samples / "inv.pdf").write_bytes(synthetic_invoice_pdf())
    truth = {
        "format": "reconbench-accuracy-truth/v2",
        "config": {"run_date": "2026-04-30", "buyer_entities": [{"gstin": BUYER_GSTIN}]},
        "files": [{"id": "F1", "path": "inv.pdf", "zone": "invoice"}],
        "sets": [
            {
                "id": "S1",
                "documents": [
                    {
                        "id": "T1",
                        "doc_type": "invoice",
                        "sources": [{"file": "F1"}],
                        "header": {"doc_no": INVOICE_NO, "grand_total": GRAND},
                    }
                ],
            }
        ],
    }
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    target = _target()
    _prepare([target], tmp_path)
    assert target.key is not None
    _record(tmp_path / "vision", target.key, recorded_response(), latency_ms=3000)
    out = tmp_path / "report.json"
    accuracy_bench.main(
        [
            *("--truth", str(tmp_path / "truth.json"), "--samples", str(samples)),
            *("--cache", str(tmp_path / "cache"), "--out", str(out)),
            *("--vision-cache", str(tmp_path / "vision"), "--vision-pages", str(tmp_path / "pg")),
            *("--vision-model", GEMINI),
            *extra,
        ]
    )
    report: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return report


def test_default_bench_output_is_unchanged_and_eval_is_a_separate_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RB_BENCH_VISION_MODEL", raising=False)
    default = _bench(tmp_path)
    evaluated = _bench(tmp_path, "--vision-eval", "--agreement-policy", "llm_x_ocr")
    assert "vision_eval" not in default and "vision_eval_hash" not in default
    for name in ("report_hash", "vision_hash", "consensus_hash"):
        assert evaluated[name] == default[name]
    assert evaluated["vision"] == default["vision"]
    block = evaluated["vision_eval"]
    assert block["agreement_policy"] == "llm_x_ocr" and block["documents_scored"] == 1
    assert evaluated["vision_eval_hash"] == accuracy_bench._sha256(
        accuracy_bench._canonical_json(block)
    )
    assert INVOICE_NO not in json.dumps(block)


# --------------------------------------------------------------------------- selection


def _selectable(file_id: str, truth_id: str | None, key: str) -> accuracy_vision.VisionTarget:
    target = accuracy_vision.VisionTarget.__new__(accuracy_vision.VisionTarget)
    target.file_id, target.index, target.truth_id, target.key = file_id, 0, truth_id, key
    return target


def test_a_call_limit_spends_each_slot_on_a_different_truth_invoice() -> None:
    truth = {name: replace(_truth(), doc_id=name, lines=None) for name in ("A", "B", "C")}
    missing = [
        _selectable("F1", "A", "k1"),
        _selectable("F2", "A", "k2"),  # another file of invoice A: no slot
        _selectable("F3", "A", "k1"),  # an exact copy of F1: free, reuses its answer
        _selectable("F4", "B", "k4"),  # B already answered elsewhere: no slot
        _selectable("F5", "C", "k5"),
        _selectable("F6", None, "k6"),
        _selectable("F7", None, "k7"),
    ]
    chosen = accuracy_bench._priority_subset(missing, truth, 3, frozenset({"B"}))
    assert [target.file_id for target in chosen] == ["F1", "F3", "F5", "F6"]
    # Without a limit every missing request is kept, as before.
    assert len(accuracy_bench._priority_subset(missing, truth, None, frozenset({"B"}))) == 7


# --------------------------------------------------------------------------- unusable answers


def _item_errors_page() -> str:
    answer = recorded_response()
    answer["lines"][0]["hsn_sac"] = 8471  # a number where the schema wants text
    answer["fields"][0]["stray"] = "x"  # a key the schema does not have
    return json.dumps(answer)


def _bad_page() -> str:
    """A page whose top level breaks the schema: the whole answer is unusable (D73)."""

    answer = json.loads(_item_errors_page())
    answer["doc_kind"] = 8471  # a number where the schema wants text
    answer["pages"] = "1"  # not a list
    return json.dumps(answer)


def test_parse_errors_name_the_broken_rule_without_values() -> None:
    errors = accuracy_vision.parse_errors(_bad_page())
    # Item errors on the same page are dropped item by item, so only the top level shows.
    assert errors == ["doc_kind: string_type", "pages: list_type"]
    assert accuracy_vision.parse_errors(_item_errors_page()) == []
    assert accuracy_vision.parse_errors(json.dumps(recorded_response())) == []
    assert accuracy_vision.parse_errors("") == ["output is not a single JSON object"]
    assert "8471" not in json.dumps(errors)


def _vllm_answers(texts: list[str]) -> Any:
    replies = iter(texts)

    def complete(request: Any, key: str) -> Any:
        from reconbench_app.ai.provider import ProviderResult, ProviderUsage

        return ProviderResult(next(replies), ProviderUsage(3000, 600, None))

    return complete


def test_a_failed_merge_keeps_page_answers_and_the_rule_and_retry_sends_it_once(
    tmp_path: Path,
) -> None:
    first, copy = _target(), _target()
    _prepare([first, copy], tmp_path, VLLM, QWEN)
    cache = tmp_path / "vision"
    counts = accuracy_vision.send(
        [first], cache, model=QWEN, api_key="", arm=VLLM, complete=_vllm_answers([_bad_page()])
    )
    assert counts == {"sent": 1}
    stored = json.loads((cache / f"{first.key}.json").read_text(encoding="utf-8"))
    assert stored["output_text"] == "" and stored["page_outputs"] == [_bad_page()]
    assert stored["merge_error"] == {
        "pages": [{"page": 1, "errors": accuracy_vision.parse_errors(_bad_page())}]
    }
    run = accuracy_vision.VisionRun("cached", QWEN, [first], arm=VLLM)
    accuracy_vision.check_answers([first], cache, BUYERS, run.usage, arm=VLLM)
    assert first.outcome == "invalid_output"
    block = accuracy_vision_eval.evaluate(
        run, {"T1": _truth()}, normal=_normal, same=_same, header_fields=_HEADER_FIELDS,
        critical=_CRITICAL, score_document=score_document,
    )  # fmt: skip
    assert block["invalid_answer_rules"] == {"doc_kind: string_type": 1, "pages: list_type": 1}

    # Without retry an answered key is never sent again; with it, once (the copy reuses it).
    retry = accuracy_vision.unusable_keys([first, copy], cache)
    assert retry == {first.key}
    good = json.dumps(recorded_response())
    assert accuracy_vision.send([first], cache, model=QWEN, api_key="", arm=VLLM,
                                complete=_vllm_answers([])) == {}  # fmt: skip
    counts = accuracy_vision.send(
        [first, copy], cache, model=QWEN, api_key="", arm=VLLM,
        complete=_vllm_answers([good]), retry=retry,
    )  # fmt: skip
    assert counts == {"sent": 1}
    stored = json.loads((cache / f"{first.key}.json").read_text(encoding="utf-8"))
    assert "page_outputs" not in stored
    assert accuracy_vision.parse_errors(stored["output_text"]) == []
    assert accuracy_vision.unusable_keys([first, copy], cache) == frozenset()


def test_a_kept_failed_merge_is_merged_again_offline_with_bad_items_dropped(
    tmp_path: Path,
) -> None:
    """A D71 record (empty answer + ``page_outputs``) whose pages break only item rules is
    re-scored from the kept page answers without a request (D73)."""

    first = _target()
    _prepare([first], tmp_path, VLLM, QWEN)
    cache = tmp_path / "vision"
    accuracy_vision.send(
        [first], cache, model=QWEN, api_key="", arm=VLLM, complete=_vllm_answers([_bad_page()])
    )
    path = cache / f"{first.key}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["page_outputs"] = [_item_errors_page()]  # as recorded under the strict parse
    path.write_text(json.dumps(stored), encoding="utf-8")

    assert accuracy_vision.stored_answer(stored, first) != ""
    assert accuracy_vision.unusable_keys([first], cache) == frozenset()
    run = accuracy_vision.VisionRun("cached", QWEN, [first], arm=VLLM)
    accuracy_vision.check_answers([first], cache, BUYERS, run.usage, arm=VLLM)
    assert first.outcome == "read" and first.validation is not None
    dropped = first.validation.check.dropped
    assert dropped["schema:fields.*.stray: extra_forbidden"] == 1
    assert dropped["schema:lines.*.hsn_sac: string_type"] == 1
    assert first.parse_errors == []  # the old merge error no longer counts
    block = accuracy_vision_eval.evaluate(
        run, {"T1": _truth()}, normal=_normal, same=_same, header_fields=_HEADER_FIELDS,
        critical=_CRITICAL, score_document=score_document,
    )  # fmt: skip
    assert block["invalid_answer_rules"] == {}
    # The record itself is not rewritten and nothing was sent.
    assert json.loads(path.read_text(encoding="utf-8"))["output_text"] == ""
