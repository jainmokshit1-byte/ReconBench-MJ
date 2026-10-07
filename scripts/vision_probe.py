"""Owner-run probe for the vision reader (AC-5): request shape, schema, boxes and cost per page.

Run it before turning page images on, and use its numbers for the cost cap (D-8):

    make vision-probe                                  # dry run: nothing is sent
    PROBE_KEY=... make vision-probe PROBE_ARGS="--send"

Dry run (default): builds a synthetic invoice (digital and a scan-like copy),
renders and masks the pages with the app's own sandbox code, writes the masked
images to ``--out`` for you to look at, and prints the request shape. Nothing
leaves the machine.

``--send`` calls Gemini through the app's guarded adapter (one attempt, no
retries, ``store=false``) and measures, per call: HTTP outcome, whether the
response schema is accepted and the answer parses strictly, validator drops,
accuracy on the synthetic values, how well ``box_2d`` matches the known
positions (``[ymin, xmin, ymax, xmax]`` and the swapped order), input/output/
thought tokens, latency, and the input tokens one extra page costs. A failed
call shows Google's error text, or the answer's status, tokens and row counts
(``scripts/vision_diagnostics.py``, read from the same response, nothing is
sent twice); after an INVALID_REQUEST a minimal schema is tried once.

``--pdf PATH --doc-type invoice|po|grn`` adds your own local file: only counts
are printed (tokens, latency, fields read, drops), never a value. Prices are
never assumed: pass ``--price-file`` (the ``RB_AI_PRICE_FILE`` format) or
``--input-usd-per-m`` / ``--output-usd-per-m`` to see USD per page.

The key comes from ``PROBE_KEY``, or ``--use-app-key`` inside the ai-worker
container (database and keyring of the running app).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from reconbench_app.ai.contracts import InvalidOutput, VisionOutput, parse_output  # noqa: E402
from reconbench_app.ai.limits import AiLimits, load_prices  # noqa: E402
from reconbench_app.ai.prompts import PromptPayload, vision_prompt  # noqa: E402
from reconbench_app.ai.provider import (  # noqa: E402
    ProviderFailure,
    ProviderImage,
    ProviderRequest,
    ProviderResult,
)
from reconbench_app.ai.vision import (  # noqa: E402
    PageRow,
    VisionContext,
    validate_vision,
)
from reconbench_app.extraction.vision_pages import render_vision_pages  # noqa: E402
from reconbench_core.extraction.vision import FIELD_KINDS, normalize_field  # noqa: E402

from scripts.vision_fixtures import (  # noqa: E402
    known_boxes,
    scan_like_pdf,
    synthetic_invoice_pdf,
)

LIMITS = AiLimits()


@dataclass
class Case:
    label: str
    doc_type: str
    rows: list[PageRow]
    resolution: str
    synthetic: bool


def _rows(content: bytes, pages: tuple[int, ...], languages: tuple[str, ...]) -> list[PageRow]:
    return [
        PageRow(p.page, p.sha256, p.mime_type, p.width, p.height, p.render, p.mask, p.data)
        for p in render_vision_pages(content, pages, languages=languages)
    ]


def _payload(case: Case) -> PromptPayload:
    images = tuple(
        ProviderImage(
            caption=f"Page {index}",
            mime_type=row.mime_type,
            data=row.data or b"",
            sha256=row.sha256,
            resolution=case.resolution,  # type: ignore[arg-type]
        )
        for index, row in enumerate(case.rows, start=1)
    )
    return vision_prompt(case.doc_type, images)  # type: ignore[arg-type]


def _request(
    payload: PromptPayload, model: str, schema: dict[str, Any] | None = None
) -> ProviderRequest:
    return ProviderRequest(
        model=model,
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=schema or payload.response_schema,
        max_output_tokens=LIMITS.h6_vision.max_output_tokens,
        timeout_ms=LIMITS.timeout_for("h6_vision") * 1_000,
        images=payload.images,
    )


def _complete(request: ProviderRequest, key: str) -> tuple[ProviderResult | None, Any]:
    """One guarded call; returns the result (or the failure) and the counts-only diagnostics."""

    from reconbench_app.ai.worker.gemini import GeminiProvider

    from scripts.vision_diagnostics import DiagnosticTransport

    transport = DiagnosticTransport()
    try:
        result = GeminiProvider(inner_transport=transport).complete(request, api_key=key)
    except ProviderFailure as failure:
        return None, (failure, dict(transport.info))
    return result, dict(transport.info)


def _iou(a: list[int], b: list[int]) -> float:
    height = min(a[2], b[2]) - max(a[0], b[0])
    width = min(a[3], b[3]) - max(a[1], b[1])
    if height <= 0 or width <= 0:
        return 0.0
    inter = height * width
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area else 0.0


def _synthetic_scores(output: VisionOutput) -> dict[str, Any]:
    known = known_boxes()
    correct = 0
    ious_yx: list[float] = []
    ious_xy: list[float] = []
    for item in output.fields:
        if item.name not in known or item.page != 1:
            continue
        text, box = known[item.name]
        kind = FIELD_KINDS[item.name]
        if normalize_field(kind, item.text) == normalize_field(kind, text):
            correct += 1
        if len(item.box_2d) == 4:
            ious_yx.append(_iou(item.box_2d, box))
            swapped = [item.box_2d[1], item.box_2d[0], item.box_2d[3], item.box_2d[2]]
            ious_xy.append(_iou(swapped, box))
    return {
        "known_fields_correct": f"{correct}/{len(known)}",
        "box_iou_yx": round(sum(ious_yx) / len(ious_yx), 2) if ious_yx else None,
        "box_iou_xy": round(sum(ious_xy) / len(ious_xy), 2) if ious_xy else None,
        "handwritten_seen": any(note.looks_like == "po_number" for note in output.annotations),
        "lines": len(output.lines),
    }


def _usd(tokens_in: int, tokens_out: int, prices: tuple[Decimal, Decimal] | None) -> str | None:
    if prices is None:
        return None
    value = (Decimal(tokens_in) * prices[0] + Decimal(tokens_out) * prices[1]) / Decimal(1_000_000)
    return str(value.quantize(Decimal("0.000001")))


def _call(
    case: Case, model: str, key: str, prices: tuple[Decimal, Decimal] | None
) -> dict[str, Any]:
    from scripts.vision_diagnostics import at_output_cap, looks_like_a_loop

    payload = _payload(case)
    request = _request(payload, model)
    row: dict[str, Any] = {
        "case": case.label,
        "pages": len(case.rows),
        "resolution": case.resolution,
        "image_bytes": sum(len(r.data or b"") for r in case.rows),
    }
    started = time.monotonic()
    result, info = _complete(request, key)
    if result is None:
        failure, details = info
        row.update(outcome=f"failed:{failure.code}", delivery=failure.delivery)
        # Google's error text, or the answer's status, tokens and row counts (no values).
        row["provider"] = details
        if details.get("status") not in {None, "completed"}:
            row["at_output_cap"] = at_output_cap(details, request.max_output_tokens)
            row["loop_suspected"] = looks_like_a_loop(details)
        if failure.code == "INVALID_REQUEST" and failure.delivery == "responded":
            minimal = {
                "type": "object",
                "properties": {"doc_kind": {"type": "string"}},
                "required": ["doc_kind"],
                "additionalProperties": False,
            }
            again, _details = _complete(_request(payload, model, minimal), key)
            row["minimal_schema"] = (
                "accepted (the vision schema is the problem)"
                if again is not None
                else "failed too (not the schema)"
            )
        return row
    row["latency_ms"] = int((time.monotonic() - started) * 1_000)
    row["provider"] = {name: info[name] for name in ("status",) if name in info}
    usage = result.usage
    if usage is not None:
        row.update(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thought_tokens=usage.thought_tokens,
            usd=_usd(usage.input_tokens, usage.output_tokens + (usage.thought_tokens or 0), prices),
        )
    try:
        parsed = parse_output("h6_vision", result.output_text)
        assert isinstance(parsed, VisionOutput)
    except InvalidOutput as error:
        row["outcome"] = f"answer:{error.code}"
        return row
    row["outcome"] = "ok"
    row["read"] = {
        "fields": len(parsed.fields),
        "lines": len(parsed.lines),
        "annotations": len(parsed.annotations),
    }
    try:
        validation = validate_vision(
            parsed,
            VisionContext(case.doc_type, {r.page: r.sent_page for r in case.rows}),  # type: ignore[arg-type]
        )
        row["validator_drops"] = dict(validation.check.dropped)
    except InvalidOutput as error:
        row["validator_drops"] = error.code
    if case.synthetic:
        row["synthetic"] = _synthetic_scores(parsed)
    return row


def _app_key() -> str:
    """The key saved in the running app, opened the way the AI worker does (ai-worker container)."""

    import sqlalchemy as sa
    from reconbench_app.ai.runtime_checks import load_key, load_settings
    from reconbench_app.ai.worker.key_open import Keyring

    engine = sa.create_engine(os.environ["RB_DATABASE_URL"])
    with engine.connect() as connection:
        workspace_id = connection.scalar(sa.text("select workspace_id from ai_setting limit 1"))
        settings = load_settings(connection, workspace_id)
        stored = load_key(connection, settings, datetime.now(UTC)) if settings else "KEY_NOT_SET"
    if isinstance(stored, str):
        raise SystemExit(f"no usable key saved in the app ({stored})")
    return (
        Keyring.load(Path(os.environ["RB_AI_KEYRING_FILE"]))
        .open(
            envelope=stored.envelope,
            nonce=stored.nonce,
            stored_key_id=stored.key_id,
            workspace_id=workspace_id,
            secret_id=stored.secret_id,
        )
        .decode("utf-8")
    )


def _prices(arguments: argparse.Namespace, model: str) -> tuple[Decimal, Decimal] | None:
    if arguments.input_usd_per_m is not None and arguments.output_usd_per_m is not None:
        return Decimal(arguments.input_usd_per_m), Decimal(arguments.output_usd_per_m)
    if arguments.price_file is not None:
        prices = load_prices(arguments.price_file)
        price = prices.models.get(model) if prices else None
        if price is not None:
            return price.input_usd_per_million, price.output_usd_per_million
    return None


def build_cases(arguments: argparse.Namespace) -> list[Case]:
    languages = tuple(arguments.languages.split("+"))
    digital = synthetic_invoice_pdf()
    first = _rows(digital, (1,), languages)
    scan = _rows(scan_like_pdf(digital), (1,), languages)
    cases = [
        Case(f"synthetic digital, 1 page @ {resolution}", "invoice", first, resolution, True)
        for resolution in arguments.resolutions.split(",")
    ]
    cases.append(Case("synthetic scan-like, 1 page @ high", "invoice", scan, "high", True))
    second = [PageRow(2, *[getattr(scan[0], name) for name in PageRow.__slots__][1:])]  # type: ignore[arg-type]
    cases.append(Case("synthetic 2 pages @ high", "invoice", first + second, "high", True))
    for path in arguments.pdf:
        content = Path(path).read_bytes()
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            count = min(len(pdf.pages), LIMITS.h6_vision.max_pages or 8)
        rows = _rows(content, tuple(range(1, count + 1)), languages)
        cases.append(
            Case(
                f"local file #{len(cases)} ({count} pages)", arguments.doc_type, rows, "high", False
            )
        )
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="actually call Gemini (owner only)")
    parser.add_argument("--model", default=os.environ.get("PROBE_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--resolutions", default="medium,high,ultra_high")
    parser.add_argument("--pdf", action="append", default=[], help="a local PDF (metrics only)")
    parser.add_argument("--doc-type", choices=("invoice", "po", "grn"), default="invoice")
    parser.add_argument("--languages", default=os.environ.get("RB_PDF_OCR_LANGUAGES", "eng"))
    parser.add_argument("--out", type=Path, default=ROOT / "work" / "vision-probe")
    parser.add_argument("--use-app-key", action="store_true")
    parser.add_argument("--price-file", type=Path, default=None)
    parser.add_argument("--input-usd-per-m", default=None)
    parser.add_argument("--output-usd-per-m", default=None)
    arguments = parser.parse_args(argv)

    cases = build_cases(arguments)
    arguments.out.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(cases):
        for row in case.rows:
            if case.synthetic or arguments.send is False:
                (arguments.out / f"case{index}-page{row.page}.jpg").write_bytes(row.data or b"")
    print(f"Masked page images written to {arguments.out} (look at them before sending).")
    for case in cases:
        masks = [row.mask.get("counts", {}) for row in case.rows]
        payload = _payload(case)
        shape = ["text"] + [part for _ in payload.images for part in ("text", "image")]
        print(f"- {case.label}: parts={shape} masked={masks}")
    report: dict[str, Any] = {
        "model": arguments.model,
        "sent": arguments.send,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "calls": [],
    }
    if not arguments.send:
        print("Dry run: nothing was sent. Add --send to call Gemini.")
    else:
        key = _app_key() if arguments.use_app_key else os.environ.get("PROBE_KEY", "").strip()
        if not key:
            raise SystemExit("set PROBE_KEY (or use --use-app-key inside the ai-worker container)")
        prices = _prices(arguments, arguments.model)
        for case in cases:
            row = _call(case, arguments.model, key, prices)
            report["calls"].append(row)
            print(json.dumps(row, sort_keys=True))
        one = next(
            (r for r in report["calls"] if r["case"] == "synthetic digital, 1 page @ high"), None
        )
        two = next((r for r in report["calls"] if r["case"] == "synthetic 2 pages @ high"), None)
        if one and two and "input_tokens" in one and "input_tokens" in two:
            extra = two["input_tokens"] - one["input_tokens"]
            report["input_tokens_per_extra_page_high"] = extra
            print(f"Input tokens for one extra page @ high: {extra}")
        del key
    (arguments.out / "probe-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Report: {arguments.out / 'probe-report.json'} (counts only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
