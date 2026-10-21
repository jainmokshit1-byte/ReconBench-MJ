"""Separate AI providers (LLM plan WI-BE3): consent mapping, reader choice, card checks.

Offline and synthetic: no database, no network, no provider call.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from reconbench_app.ai.consent import consent_fingerprint, consent_text, data_destination
from reconbench_app.ai.endpoint_policy import EndpointNotAllowed
from reconbench_app.ai.runtime_checks import ProviderConfig, SettingsSnapshot, fingerprint_for
from reconbench_app.ai.settings_service import AiSettingsUpdate, ProviderConfigUpdate
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.extraction.vision import reader_for_provider

MODEL = "gemini-3.8-flash"
LOCAL_MODEL = "qwen3.5:9b"
URL = "http://vllm.example.test/vllm/v1"


def _snapshot(**overrides: object) -> SettingsSnapshot:
    values: dict[str, object] = {
        "workspace_id": uuid4(),
        "mode": "ai_assisted",
        "provider": "gemini",
        "model": MODEL,
        "version": 3,
        "consent_given": True,
        "consent_fingerprint": None,
        "key_secret_id": None,
        "key_storage": "saved",
    }
    values.update(overrides)
    return SettingsSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["ai_assisted", "ai_first"])
@pytest.mark.parametrize("auto_verify", [(), ("doc_no",)])
def test_legacy_mapping_keeps_the_fingerprint_byte_identical(
    mode: str, auto_verify: tuple[str, ...]
) -> None:
    for strategy, primary, vision in (("off", None, False), ("single", "gemini", True)):
        assert fingerprint_for(
            provider="gemini",
            model=MODEL,
            mode=mode,
            reader_strategy=strategy,
            primary_reader=primary,
            auto_verify_field_types=auto_verify,
            vllm_model=LOCAL_MODEL,  # an unused vLLM card never changes Gemini's consent
            vllm_base_url=URL,
        ) == consent_fingerprint(
            provider="gemini",
            model=MODEL,
            mode=mode,
            vision=vision,
            auto_verify_field_types=auto_verify,
        )


def test_the_legacy_call_without_new_keywords_matches_the_off_mapping() -> None:
    # Callers that never pass the WI-BE3 keywords keep exactly their old hash.
    legacy = consent_fingerprint(provider="gemini", model=MODEL, mode="ai_assisted")
    assert legacy == fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_assisted",
        reader_strategy="off",
        primary_reader=None,
    )


def test_company_server_shadow_and_merge_each_need_their_own_consent() -> None:
    gemini_single = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="single",
        primary_reader="gemini",
    )
    local = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="single",
        primary_reader="vllm",
        vllm_model=LOCAL_MODEL,
        vllm_base_url=URL,
    )
    other_url = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="single",
        primary_reader="vllm",
        vllm_model=LOCAL_MODEL,
        vllm_base_url="http://other.example.test/v1",
    )
    other_model = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="single",
        primary_reader="vllm",
        vllm_model="qwen3.5:4b",
        vllm_base_url=URL,
    )
    shadow = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="shadow",
        primary_reader="vllm",
        vllm_model=LOCAL_MODEL,
        vllm_base_url=URL,
    )
    off = fingerprint_for(
        provider="gemini", model=MODEL, mode="ai_first", reader_strategy="off", primary_reader=None
    )
    merge = fingerprint_for(
        provider="gemini",
        model=MODEL,
        mode="ai_first",
        reader_strategy="single",
        primary_reader="gemini",
        apply_mode="merge",
    )
    assert len({gemini_single, local, other_url, other_model, shadow, off, merge}) == 7


def test_consent_text_and_destinations_name_the_company_server() -> None:
    legacy = consent_text(provider="Google Gemini", model=MODEL)
    assert (
        consent_text(
            provider="Google Gemini",
            model=MODEL,
            reader_strategy="single",
            vision_provider="gemini",
        )
        == legacy
    )
    local = consent_text(
        provider="Google Gemini",
        model=MODEL,
        reader_strategy="single",
        vision_provider="vllm",
        vision_model=LOCAL_MODEL,
        vision_base_url=URL,
    )
    assert local.startswith(legacy) and URL in local and "not the company network" in local
    shadow = consent_text(
        provider="Google Gemini",
        model=MODEL,
        reader_strategy="shadow",
        vision_provider="vllm",
        vision_model=LOCAL_MODEL,
        vision_base_url=URL,
    )
    assert "never shown as suggestions" in shadow
    assert data_destination("gemini").startswith("Google Gemini API")
    assert data_destination("vllm", URL).startswith(f"Company vLLM server at {URL}")


def test_each_provider_uses_its_own_reader() -> None:
    gemini = reader_for_provider("vision_gemini", "gemini")
    local = reader_for_provider("vision_gemini", "vllm")
    assert gemini is not None and gemini.name == "vision_gemini"
    assert local is not None and local.name == "vision_openai_compat" and not local.external
    configured_local = reader_for_provider("vision_openai_compat", "gemini")
    assert configured_local is not None and configured_local.name == "vision_gemini"
    assert reader_for_provider("off", "gemini") is None
    assert reader_for_provider("vision_gemini", None) is None
    assert reader_for_provider("vision_gemini", "claude") is None


def test_snapshot_routes_only_the_vision_hook_to_the_chosen_provider() -> None:
    card = ProviderConfig("vllm", True, LOCAL_MODEL, URL, None, 1)
    local = _snapshot(reader_strategy="single", primary_reader="vllm", vllm=card)
    assert local.target("h6_vision") == ("vllm", LOCAL_MODEL)
    assert local.target("h1_extract") == ("gemini", MODEL)
    assert local.hooks[0] == "h6_vision:fallback"
    # A row written only with the old flag reads as single + Gemini.
    legacy = _snapshot(vision_enabled=True)
    assert (legacy.strategy, legacy.vision_provider) == ("single", "gemini")
    assert legacy.target("h6_vision") == ("gemini", MODEL)
    # Shadow never replaces H1 (its answers are not shown).
    shadow = _snapshot(reader_strategy="shadow", primary_reader="vllm", vllm=card)
    assert shadow.hooks[0] == "h1_extract:fallback"
    consented = replace(local, consent_fingerprint=local.expected_fingerprint)
    assert consented.consent_current
    moved = replace(consented, vllm=replace(card, base_url="http://other.example.test/v1"))
    assert not moved.consent_current


@pytest.mark.parametrize(
    "card",
    [
        {"provider": "gemini", "base_url": URL},
        {"provider": "gemini", "model": LOCAL_MODEL},
        {"provider": "gemini", "remove_key": True},
        {"provider": "vllm", "model": "has space"},
        {"provider": "vllm", "model": "x" * 129},
        {"provider": "vllm", "api_key": "short"},
        {"provider": "vllm", "api_key": "long-enough-key-123", "remove_key": True},
        {"provider": "vllm", "limits": {"timeout_seconds": 301}},
        {"provider": "vllm", "limits": {"unknown": 1}},
        {"provider": "vllm", "extra": 1},
        {"provider": "openai"},
    ],
)
def test_invalid_cards_are_refused(card: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProviderConfigUpdate.model_validate(card)


def test_valid_cards_and_strategy_fields() -> None:
    card = ProviderConfigUpdate.model_validate(
        {
            "provider": "vllm",
            "enabled": True,
            "model": "Qwen/Qwen3.5-9B",
            "base_url": URL,
            "api_key": "local-key-0000",
            "limits": {"max_concurrency": 1, "timeout_seconds": 300},
        }
    )
    assert "local-key-0000" not in repr(card)
    body = AiSettingsUpdate.model_validate(
        {"providers": [{"provider": "vllm", "model": LOCAL_MODEL}], "reader_strategy": "shadow"}
    )
    assert body.reader_strategy == "shadow"
    for invalid in (
        {"reader_strategy": "dual_agree"},
        {"secondary_reader": "vllm"},
        {"providers": [{"provider": "vllm"}, {"provider": "vllm"}]},
    ):
        with pytest.raises(ValidationError):
            AiSettingsUpdate.model_validate(invalid)


def test_provider_for_a_workspace_url_is_allowlisted_and_cached() -> None:
    seen: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": LOCAL_MODEL, "max_model_len": 8192}]})

    default = "http://default.example.test/v1"
    provider = OpenAICompatProvider(
        default, frozenset({default, URL}), inner_transport=httpx.MockTransport(server)
    )
    sibling = provider.for_base_url("HTTP://VLLM.EXAMPLE.TEST/vllm/v1/")
    assert sibling.base_url == URL and provider.for_base_url(URL) is sibling
    assert provider.for_base_url(default) is provider
    assert sibling.health().ok and seen == [f"{URL}/models"]
    assert sibling.context_window(LOCAL_MODEL) == 8192
    assert provider.context_window(LOCAL_MODEL) == 8192 and len(seen) == 2
    with pytest.raises(EndpointNotAllowed):
        provider.for_base_url("http://169.254.169.254/v1")
    assert len(seen) == 2
