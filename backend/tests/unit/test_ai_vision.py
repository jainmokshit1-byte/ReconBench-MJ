"""Vision reader (h6_vision, AC-5) without a database or provider.

Synthetic documents only (``scripts/vision_fixtures.py``); provider traffic goes
through ``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
from typing import Any

import httpx
import pytest
from PIL import Image
from reconbench_app.ai.consent import (
    DATA_SENT,
    HOOK_LABELS,
    HOOKS_BY_MODE,
    consent_fingerprint,
    hooks_for_mode,
)
from reconbench_app.ai.contracts import (
    SCHEMA_VERSIONS,
    InvalidOutput,
    VisionOutput,
    parse_output,
    vision_response_schema,
)
from reconbench_app.ai.fixtures import PROVIDER_TEST, SYNTHETIC_FIXTURE_DIGESTS, plain_schema
from reconbench_app.ai.gate import payload_digest
from reconbench_app.ai.limits import AiLimits, estimate_reservation
from reconbench_app.ai.prompts import PROMPT_VERSIONS, prompt_version, vision_prompt
from reconbench_app.ai.provider import ProviderImage, ProviderRequest
from reconbench_app.ai.redaction import WordBox, image_mask_regions
from reconbench_app.ai.vision import (
    PageRow,
    VisionContext,
    VisionFacts,
    fingerprint,
    provider_images,
    validate_vision,
    vision_eligibility,
)
from reconbench_app.ai.worker.gemini import GeminiProvider
from reconbench_app.extraction.vision import UnknownVisionReader, vision_reader
from reconbench_app.extraction.vision_pages import render_vision_pages, vision_pages_isolated
from reconbench_core.extraction.vision import SentPage
from reconbench_core.parsers.types import BuyerEntityHint
from scripts.vision_fixtures import (
    BUYER_GSTIN,
    EMAIL,
    GRAND,
    INVOICE_NO,
    PHONE,
    SUPPLIER_GSTIN,
    recorded_response,
    scan_like_pdf,
    synthetic_invoice_pdf,
)

API_KEY = "AIzaSyD-synthetic-vision-key-0000000000"
MODEL = "gemini-3.8-flash"


# --------------------------------------------------------------------------- masking


def _row(y: int, *texts: str) -> list[WordBox]:
    words: list[WordBox] = []
    x = 10
    for text in texts:
        width = 12 * len(text)
        words.append(WordBox(text, x, y, x + width, y + 20))
        x += width + 10
    return words


def _categories(words: list[WordBox]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for region in image_mask_regions(words, width=2000, height=3000):
        counts[region.category] = counts.get(region.category, 0) + 1
    return counts


@pytest.mark.parametrize(
    ("texts", "category"),
    [
        (("Email:", "accounts@synthetic.example"), "email"),
        (("UPI:", "synthetic@okbank"), "upi"),
        (("Mobile", "No:", "98765", "43210"), "phone"),
        (("Tel:", "022-24567890"), "phone"),
        (("A/c", "No.", "123456789012"), "bank_account"),
        (("Account", "Number:", "1234", "5678", "9012"), "bank_account"),
        (("IFSC", "Code:", "SBIN0001234"), "ifsc"),
        (("IFSC:", "SBINO001234"), "ifsc"),
        (("PAN:", "AAPFU0939F"), "pan"),
    ],
)
def test_sensitive_words_are_masked(texts: tuple[str, ...], category: str) -> None:
    assert category in _categories(_row(100, *texts))


@pytest.mark.parametrize(
    "texts",
    [
        ("GSTIN:", SUPPLIER_GSTIN),
        ("Invoice", "No:", "1610357353"),
        ("Invoice", "No:", INVOICE_NO),
        ("Grand", "Total:", "1,23,900.00"),
        ("Date:", "02/04/2026"),
        ("E-Way", "Bill", "No:", "123456789012"),
        ("Buyer's", "Order", "No:", "PO/26-27/04-901"),
    ],
)
def test_fields_to_read_are_never_masked(texts: tuple[str, ...]) -> None:
    assert _categories(_row(100, *texts)) == {}


def test_account_number_under_its_label_is_masked() -> None:
    words = _row(100, "Account", "No.") + [WordBox("123456789012", 10, 130, 200, 150)]
    assert _categories(words) == {"bank_account": 1}


def test_a_signature_column_heading_is_not_masked() -> None:
    words = _row(100, "Item", "Ref") + _row(300, "Description", "Qty", "Rate", "Signature")
    assert _categories(words) == {}


def test_signature_band_stops_at_the_company_line() -> None:
    words = _row(100, "for", "ACME", "TRADERS") + [
        WordBox("Authorised", 10, 300, 130, 320),
        WordBox("Signatory", 140, 300, 250, 320),
    ]
    [region] = image_mask_regions(words, width=2000, height=3000, padding=0)
    assert region.category == "signature"
    assert region.y0 >= 120 and region.y1 <= 300  # below "for ACME", above the label


# --------------------------------------------------------------------------- contract


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def test_vision_schema_avoids_keywords_gemini_rejects() -> None:
    schema = vision_response_schema()
    keys = {key for node in _walk(schema) for key in node}
    assert not keys & {"maxItems", "minItems", "$ref", "$defs", "anyOf", "oneOf"}
    assert json.loads(json.dumps(schema)) == schema
    assert SCHEMA_VERSIONS["h6_vision"] == "vision.schema.v4"
    assert prompt_version("h6_vision") == "vision.prompt.v4.gemini"
    assert "h6_vision" not in PROMPT_VERSIONS  # one version per reader (WI-BE4)
    line = schema["properties"]["lines"]["items"]
    assert line["required"] == ["page", "box_2d", "description", "quantity", "amount"]
    names = list(line["properties"])
    assert names[2:6] == ["description", "quantity", "rate", "amount"]  # scored cells first
    assert "received_qty" in names


def test_recorded_answer_parses_strictly_and_extra_keys_are_rejected() -> None:
    answer = recorded_response()
    parsed = parse_output("h6_vision", json.dumps(answer))
    assert isinstance(parsed, VisionOutput)
    tampered = {**answer, "status": "approved"}
    with pytest.raises(InvalidOutput):
        parse_output("h6_vision", json.dumps(tampered))
    # Far beyond a box: that field is dropped and counted, never used (D73).
    answer["fields"][0]["box_2d"] = list(range(17))
    parsed = parse_output("h6_vision", json.dumps(answer))
    assert isinstance(parsed, VisionOutput)
    assert parsed.schema_dropped == {"schema:fields.*.box_2d: too_long": 1}
    assert len(parsed.fields) == len(recorded_response()["fields"]) - 1


def test_a_malformed_box_drops_only_its_item(invoice_pdf: bytes) -> None:
    rows = _rows(invoice_pdf)
    answer = recorded_response()
    answer["lines"][0]["box_2d"] = [*answer["lines"][0]["box_2d"], 5, 6]
    parsed = parse_output("h6_vision", json.dumps(answer))
    assert isinstance(parsed, VisionOutput)
    validation = validate_vision(parsed, VisionContext("invoice", {1: rows[0].sent_page}))
    assert validation.check.dropped.get("bad_box") == 1
    assert len(validation.document.lines) == 1


def _lines_dropped(answer: dict[str, Any]) -> tuple[int, dict[str, int]]:
    parsed = parse_output("h6_vision", json.dumps(answer))
    assert isinstance(parsed, VisionOutput)
    return len(parsed.lines), dict(parsed.schema_dropped)


def test_a_line_row_without_a_description_key_is_dropped_on_its_own() -> None:
    answer = recorded_response()
    rows = len(answer["lines"])
    del answer["lines"][0]["description"]
    assert _lines_dropped(answer) == (rows - 1, {"schema:lines.*.description: missing": 1})
    answer["lines"][0]["description"] = ""  # printed row without a description: allowed
    assert _lines_dropped(answer) == (rows, {})
    del answer["lines"][0]["amount"]  # amount is a required key since v3
    assert _lines_dropped(answer) == (rows - 1, {"schema:lines.*.amount: missing": 1})
    answer["lines"][0]["amount"] = ""
    answer["lines"][0]["uom"] = "x" * 120  # a long unit no longer rejects the answer
    assert isinstance(parse_output("h6_vision", json.dumps(answer)), VisionOutput)


def test_text_hook_digests_and_the_fixture_allowlist_are_unchanged() -> None:
    digest = payload_digest(
        system_instruction=PROVIDER_TEST.system_instruction,
        input_text=PROVIDER_TEST.input_text,
        response_schema=plain_schema(PROVIDER_TEST),
    )
    assert digest == SYNTHETIC_FIXTURE_DIGESTS[PROVIDER_TEST.fixture_id]


def _image(page: int = 1, data: bytes = b"synthetic-jpeg") -> ProviderImage:
    return ProviderImage(
        f"Page {page}", "image/jpeg", data, hashlib.sha256(data).hexdigest(), "high"
    )


def test_digest_covers_image_hashes_and_resolution() -> None:
    first = vision_prompt("invoice", [_image()])
    second = vision_prompt("invoice", [_image(data=b"another-page")])
    digests = {
        payload_digest(
            system_instruction=payload.system_instruction,
            input_text=payload.input_text,
            response_schema=payload.response_schema,
            images=payload.image_digest_parts(),
        )
        for payload in (first, second)
    }
    assert len(digests) == 2
    # The prompt text carries no document content, only page numbers.
    assert "page 1" in first.input_text.lower() or "pages 1" in first.input_text
    assert "TAX INVOICE" in first.system_instruction


# --------------------------------------------------------------------------- consent


def test_vision_replaces_h1_only_when_turned_on() -> None:
    for mode, hooks in HOOKS_BY_MODE.items():
        assert hooks_for_mode(mode) == hooks
        with_vision = hooks_for_mode(mode, vision=True)
        assert not any(hook.startswith("h1_extract") for hook in with_vision)
        assert any(hook.startswith("h6_vision") for hook in with_vision)
        for hook in with_vision:
            assert hook in HOOK_LABELS and hook in DATA_SENT
        assert consent_fingerprint(
            provider="gemini", model=MODEL, mode=mode
        ) != consent_fingerprint(provider="gemini", model=MODEL, mode=mode, vision=True)
    assert "best effort" in DATA_SENT["h6_vision:fallback"]
    assert "GSTINs" in DATA_SENT["h6_vision:all_pdfs"]


def test_vision_schema_v2_needs_fresh_consent_only_with_vision_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reconbench_app.ai import consent

    before = {
        vision: consent_fingerprint(provider="gemini", model=MODEL, mode="ai_first", vision=vision)
        for vision in (False, True)
    }
    monkeypatch.setattr(
        consent, "SCHEMA_VERSIONS", {**SCHEMA_VERSIONS, "h6_vision": "vision.schema.v1"}
    )
    monkeypatch.setattr(
        consent,
        "prompt_version",
        lambda hook, *, reader: (
            "vision.prompt.v1" if hook == "h6_vision" else PROMPT_VERSIONS[hook]
        ),
    )
    after = {
        vision: consent_fingerprint(provider="gemini", model=MODEL, mode="ai_first", vision=vision)
        for vision in (False, True)
    }
    assert before[False] == after[False]  # vision off: the old consent stays valid
    assert before[True] != after[True]  # vision on: v2 asks for consent again


def test_reader_registry_and_kill_switch() -> None:
    assert vision_reader("off") is None
    reader = vision_reader("vision_gemini")
    assert reader is not None and reader.external and reader.provider == "gemini"
    with pytest.raises(UnknownVisionReader):
        vision_reader("vision_magic")


# --------------------------------------------------------------------------- eligibility


def _facts(**overrides: Any) -> VisionFacts:
    values: dict[str, Any] = {
        "doc_type": "invoice",
        "file_format": "pdf",
        "extraction_method": "pdf_ocr",
        "needs_verification": True,
        "page_count": 2,
    }
    values.update(overrides)
    return VisionFacts(**values)


@pytest.mark.parametrize(
    ("facts", "mode", "reason"),
    [
        (_facts(), "rules_only", "MODE_OFF"),
        (_facts(file_format="xml", extraction_method="xml_profile"), "ai_first", "NOT_PDF"),
        (_facts(extraction_method="manual"), "ai_first", "NOT_PDF"),
        (_facts(page_count=9), "ai_first", "TOO_MANY_PAGES"),
        (_facts(page_count=0), "ai_first", "NO_PAGES"),
        (_facts(needs_verification=False), "ai_assisted", "NOT_NEEDED"),
    ],
)
def test_ineligible_versions(facts: VisionFacts, mode: str, reason: str) -> None:
    assert vision_eligibility(facts, mode=mode, max_pages=8).reason == reason


@pytest.mark.parametrize("doc_type", ["invoice", "po", "grn"])
def test_invoice_po_and_grn_pdfs_are_eligible(doc_type: str) -> None:
    facts = _facts(doc_type=doc_type, needs_verification=False)
    assert vision_eligibility(facts, mode="ai_first", max_pages=8).eligible
    assert vision_eligibility(_facts(doc_type=doc_type), mode="ai_assisted", max_pages=8).eligible


def test_vision_limits_and_image_token_reservation() -> None:
    limits = AiLimits()
    kind = limits.for_kind("h6_vision")
    assert kind.max_pages == 8 and limits.timeout_for("h6_vision") == 120
    assert limits.timeout_for("h1_extract") == limits.call_timeout_seconds
    assert limits.per_month_calls is None and limits.per_month_tokens is None
    reservation = estimate_reservation(
        payload_bytes=1_000,
        max_output_tokens=kind.max_output_tokens,
        limits=limits,
        model=MODEL,
        prices=None,
        image_tokens=2 * (kind.image_tokens_estimate or 0),
    )
    assert reservation.input_tokens == 1_000 + 512 + 2 * 1_200
    assert reservation.is_estimate


# --------------------------------------------------------------------------- adapter


def test_gemini_request_carries_text_then_each_captioned_image() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "model": MODEL,
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "{}"}]}],
                "usage": {"total_input_tokens": 2600, "total_output_tokens": 40},
            },
        )

    payload = vision_prompt("grn", [_image(1), _image(2, b"page-two")])
    provider = GeminiProvider(inner_transport=httpx.MockTransport(handler))
    provider.complete(
        ProviderRequest(
            model=MODEL,
            system_instruction=payload.system_instruction,
            input_text=payload.input_text,
            response_schema=payload.response_schema,
            max_output_tokens=16_384,
            timeout_ms=120_000,
            images=payload.images,
        ),
        api_key=API_KEY,
    )
    [request] = seen
    assert request.headers["x-goog-api-key"] == API_KEY
    body = json.loads(request.read())
    assert API_KEY not in json.dumps(body)
    assert body["store"] is False and body["system_instruction"] == payload.system_instruction
    # The pinned SDK sends a content list as one user_input step (shape the probe checks live).
    [step] = body["input"]
    assert step["type"] == "user_input"
    parts = step["content"]
    assert [part["type"] for part in parts] == ["text", "text", "image", "text", "image"]
    assert parts[1]["text"] == "Page 1" and parts[3]["text"] == "Page 2"
    assert base64.b64decode(parts[2]["data"]) == b"synthetic-jpeg"
    assert parts[2]["mime_type"] == "image/jpeg" and parts[2]["resolution"] == "high"


# --------------------------------------------------------------------------- pages


@pytest.fixture(scope="module")
def invoice_pdf() -> bytes:
    return synthetic_invoice_pdf()


def _ink(image: Image.Image, box: list[int]) -> float:
    """Share of dark pixels inside a 0–1000 box."""

    ymin, xmin, ymax, xmax = box
    crop = image.convert("L").crop(
        (
            xmin * image.width // 1000,
            ymin * image.height // 1000,
            -(-xmax * image.width // 1000),
            -(-ymax * image.height // 1000),
        )
    )
    pixels = crop.tobytes()
    return sum(value < 60 for value in pixels) / max(1, len(pixels))


def test_digital_page_is_masked_and_deterministic(invoice_pdf: bytes) -> None:
    [page] = render_vision_pages(invoice_pdf, (1,))
    [again] = render_vision_pages(invoice_pdf, (1,))
    assert page.sha256 == again.sha256 == hashlib.sha256(page.data).hexdigest()
    assert max(page.width, page.height) == 2048 and page.mime_type == "image/jpeg"
    assert page.render["source"] == "text_layer" and page.render["rotation"] == 0
    counts = page.mask["counts"]
    assert {"email", "phone", "bank_account", "ifsc", "signature"} <= set(counts)
    image = Image.open(io.BytesIO(page.data))
    for box in page.mask["boxes"]:
        assert _ink(image, box) > 0.9  # blacked out
    # The GSTIN line is untouched (mostly white paper).
    response = recorded_response()
    gstin_box = next(f["box_2d"] for f in response["fields"] if f["name"] == "supplier_gstin")
    assert _ink(image, gstin_box) < 0.5


def test_isolated_render_matches_the_in_process_render(invoice_pdf: bytes) -> None:
    pages = vision_pages_isolated(invoice_pdf, (1,))
    assert pages is not None
    assert pages[0].sha256 == render_vision_pages(invoice_pdf, (1,))[0].sha256
    assert vision_pages_isolated(invoice_pdf, (2,)) is None  # no such page


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_scanned_page_is_straightened_and_masked(invoice_pdf: bytes) -> None:
    [page] = render_vision_pages(scan_like_pdf(invoice_pdf), (1,))
    assert page.render["source"] == "ocr"
    assert {"email", "phone"} <= set(page.mask["counts"])


# --------------------------------------------------------------------------- checking


def _rows(invoice_pdf: bytes) -> list[PageRow]:
    [page] = render_vision_pages(invoice_pdf, (1,))
    return [
        PageRow(
            page.page,
            page.sha256,
            page.mime_type,
            page.width,
            page.height,
            page.render,
            page.mask,
            page.data,
        )
    ]


def test_recorded_answer_survives_the_checks(invoice_pdf: bytes) -> None:
    rows = _rows(invoice_pdf)
    images = provider_images(rows, "high")
    assert images is not None and images[0].caption == "Page 1"
    context = VisionContext(
        doc_type="invoice",
        pages={row.page: row.sent_page for row in rows},
        buyer_entities=(BuyerEntityHint(gstin=BUYER_GSTIN, name="Synthetic Buyer"),),
    )
    validation = validate_vision(VisionOutput.model_validate(recorded_response()), context)
    header = validation.document.header
    assert header.doc_no is not None and header.doc_no.value == INVOICE_NO
    assert header.vendor_gstin is not None and header.vendor_gstin.value == SUPPLIER_GSTIN
    assert header.grand_total is not None and str(header.grand_total.value) == GRAND
    assert len(validation.document.lines) == 2
    assert validation.document.needs_verification


def test_a_reading_inside_a_mask_is_dropped(invoice_pdf: bytes) -> None:
    rows = _rows(invoice_pdf)
    mask = rows[0].mask["boxes"][0]
    answer = recorded_response()
    answer["fields"] = [{"name": "other_ref", "text": PHONE, "page": 1, "box_2d": mask}]
    answer["lines"], answer["annotations"] = [], []
    context = VisionContext("invoice", {1: rows[0].sent_page})
    with pytest.raises(InvalidOutput) as error:
        validate_vision(VisionOutput.model_validate(answer), context)
    assert error.value.code == "ungrounded"


def test_missing_or_tampered_bytes_block_sending(invoice_pdf: bytes) -> None:
    rows = _rows(invoice_pdf)
    gone = [PageRow(*[getattr(rows[0], name) for name in PageRow.__slots__][:-1], None)]  # type: ignore[arg-type]
    assert provider_images(gone, "high") is None
    tampered = [
        PageRow(
            rows[0].page,
            rows[0].sha256,
            rows[0].mime_type,
            rows[0].width,
            rows[0].height,
            rows[0].render,
            rows[0].mask,
            b"x" + (rows[0].data or b""),
        )
    ]
    assert provider_images(tampered, "high") is None


def test_fingerprint_follows_page_hashes(invoice_pdf: bytes) -> None:
    rows = _rows(invoice_pdf)
    assert fingerprint(rows) == fingerprint(list(reversed(rows)))
    assert EMAIL not in json.dumps(rows[0].mask)
    assert SentPage(1, ()).masks == ()
