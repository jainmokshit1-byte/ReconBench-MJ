"""PD-1b: the company server's Test checks its capabilities, and AI reading needs both.

Runs Test end to end on a disposable database: the settings service queues the text and
image tests, the AI runner sends them to a stub vLLM server (``httpx.MockTransport``) and
the card records ``supports_json_schema``, ``supports_vision`` and ``context_window``.
Switching AI reading on with the company server is refused until both checks passed.
"""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.settings_service import (
    AiServiceConfig,
    AiSettingsError,
    AiSettingsService,
    AiSettingsUpdate,
)
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.authorization import ActorContext
from reconbench_app.db.metadata import metadata
from sqlalchemy.engine import Engine
from tests.scenarios.test_provider_isolation import (
    APPROVAL,
    VLLM_MODEL,
    VLLM_URL,
    MockVllmServer,
    RecordingQueue,
    _setup_dual_provider_workspace,
    engine,
    keys,
)

__all__ = ["engine", "keys"]  # module fixtures shared with the isolation suite

CONFIGS = metadata.tables["ai_provider_config"]
REQUESTS = metadata.tables["ai_request"]
SETTINGS = metadata.tables["ai_setting"]


def _answer(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": VLLM_MODEL,
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    )


class _World:
    def __init__(
        self, engine: Engine, keys: tuple[Keyring, SealingPublicKey], server: MockVllmServer
    ) -> None:
        keyring, public_key = keys
        with engine.begin() as connection:
            self.workspace_id, user_id = _setup_dual_provider_workspace(
                connection, public_key, active_provider="vllm"
            )
            # AI reading off and AI off, so switching it on needs no consent here.
            connection.execute(
                sa.update(SETTINGS)
                .where(SETTINGS.c.workspace_id == self.workspace_id)
                .values(
                    mode="rules_only",
                    reader_strategy="off",
                    primary_reader=None,
                    consent_given=False,
                    consent_fingerprint=None,
                )
            )
        self.engine = engine
        self.server = server
        self.gemini = ScriptedProvider([])
        self.gemini.name = "gemini"
        gate = DeploymentGate.from_values(
            "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
        )
        self.runner = AiRequestRunner(
            engine=engine,
            keyring=keyring,
            gate=gate,
            limits=lambda: LimitsState(AiLimits()),
            prices=lambda: None,
            providers={
                "gemini": self.gemini,
                "vllm": OpenAICompatProvider(
                    VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(server)
                ),
            },
            handlers=default_handlers(),
        )
        config = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
        self.queue = RecordingQueue()
        self.service = AiSettingsService(engine, config, self.queue)
        self.actor = ActorContext(self.workspace_id, user_id, "manager")

    def test(self) -> dict[str, object]:
        """Press Test on the company server card and let the AI worker run every request."""

        queued = self.service.request_provider_test(self.actor, provider="vllm", ip="127.0.0.1")
        for request_id in (queued["request_id"], queued["image_request_id"]):
            self.runner.run(request_id)  # type: ignore[arg-type]
        return queued

    def card(self) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(CONFIGS).where(
                        CONFIGS.c.workspace_id == self.workspace_id, CONFIGS.c.provider == "vllm"
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    def vllm_view(self) -> dict[str, object]:
        view = self.service.view(self.actor)
        return next(item for item in view["providers"] if item["provider"] == "vllm")

    def switch_reading_on(self, **extra: object) -> dict[str, object]:
        version = int(self.service.view(self.actor)["version"])
        return self.service.update(
            self.actor,
            session_id=self.workspace_id,
            expected_version=version,
            body=AiSettingsUpdate(reader_strategy="single", primary_reader="vllm", **extra),
        )


def test_a_passing_test_records_all_three_capabilities_and_allows_ai_reading(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    server = MockVllmServer([_answer('{"status":"ok"}'), _answer('{"number":"472"}')])
    world = _World(engine, keys, server)

    with pytest.raises(AiSettingsError) as refused:
        world.switch_reading_on()
    assert refused.value.code == "provider_capability_missing"

    queued = world.test()
    assert world.queue.request_ids == [queued["request_id"], queued["image_request_id"]]
    card = world.card()
    assert card["supports_json_schema"] is True
    assert card["supports_vision"] is True
    assert card["context_window"] == 8192
    assert card["connected_at"] is not None and card["last_error_code"] is None
    assert world.vllm_view()["capabilities"] == {
        "vision": True,
        "json_schema": True,
        "context_window": 8192,
        "can_read_pages": True,
    }
    # One logged request per check: two completions, GET /models, nothing to Gemini.
    assert len(server.completions) == 2
    assert server.health_probes >= 1
    assert world.gemini.calls == []
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(REQUESTS.c.operation_key, REQUESTS.c.prompt_version, REQUESTS.c.apply_outcome)
            .where(REQUESTS.c.workspace_id == world.workspace_id)
            .order_by(REQUESTS.c.created_at)
        ).all()
    assert [row.prompt_version for row in rows] == ["provider_test.v1", "provider_image_test.v1"]
    assert [row.apply_outcome for row in rows] == ["applied", "applied"]
    assert rows[1].operation_key.startswith("provider_test_image:")
    # The image request carries the synthetic PNG and the strict schema.
    image_body = server.completions[1].read().decode()
    assert "data:image/png;base64," in image_body and '"strict":true' in image_body

    view = world.switch_reading_on()
    assert view["reader_strategy"] == "single" and view["primary_reader"] == "vllm"


@pytest.mark.parametrize(
    ("image_answer", "expected"),
    [
        (httpx.Response(400, json={"error": {"message": "image input not supported"}}), False),
        (_answer('{"number":"427"}'), False),
        (httpx.ReadTimeout("slow"), None),
    ],
    ids=["server_refuses_images", "wrong_number", "timeout"],
)
def test_a_failed_image_test_blocks_ai_reading_but_not_the_connection(
    engine: Engine,
    keys: tuple[Keyring, SealingPublicKey],
    image_answer: httpx.Response | Exception,
    expected: bool | None,
) -> None:
    server = MockVllmServer([_answer('{"status":"ok"}'), image_answer])
    world = _World(engine, keys, server)
    world.test()

    card = world.card()
    assert card["supports_vision"] is expected
    assert card["supports_json_schema"] is True and card["context_window"] == 8192
    # The image test never touches the connection state.
    assert card["connected_at"] is not None and card["last_error_code"] is None
    assert world.vllm_view()["connection"]["state"] == "connected"  # type: ignore[index]

    with pytest.raises(AiSettingsError) as refused:
        world.switch_reading_on()
    assert refused.value.code == "provider_capability_missing"
    assert world.gemini.calls == []


def test_a_server_that_rejects_the_json_schema_is_not_capable(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    server = MockVllmServer(
        [
            httpx.Response(400, json={"error": {"message": "response_format not supported"}}),
            _answer('{"number":"472"}'),
        ]
    )
    world = _World(engine, keys, server)
    world.test()

    card = world.card()
    assert card["supports_json_schema"] is False and card["supports_vision"] is True
    assert card["last_error_code"] == "INVALID_REQUEST"  # the text test is the connection test
    with pytest.raises(AiSettingsError) as refused:
        world.switch_reading_on()
    assert refused.value.code == "provider_capability_missing"


def test_a_new_model_resets_the_capabilities_and_cannot_switch_reading_on(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    server = MockVllmServer([_answer('{"status":"ok"}'), _answer('{"number":"472"}')])
    world = _World(engine, keys, server)
    world.test()
    assert world.vllm_view()["capabilities"]["can_read_pages"] is True  # type: ignore[index]

    # Changing the model in the same save as switching reading on is refused.
    with pytest.raises(AiSettingsError) as refused:
        world.switch_reading_on(providers=[{"provider": "vllm", "model": "qwen3.5:27b"}])
    assert refused.value.code == "provider_capability_missing"
    assert world.card()["supports_vision"] is True  # the refused save changed nothing

    # Changing the model alone puts every capability back to "not checked".
    version = int(world.service.view(world.actor)["version"])
    world.service.update(
        world.actor,
        session_id=world.workspace_id,
        expected_version=version,
        body=AiSettingsUpdate(providers=[{"provider": "vllm", "model": "qwen3.5:27b"}]),
    )
    card = world.card()
    assert (card["supports_vision"], card["supports_json_schema"], card["context_window"]) == (
        None,
        None,
        None,
    )
    with pytest.raises(AiSettingsError) as refused_again:
        world.switch_reading_on()
    assert refused_again.value.code == "provider_capability_missing"


def test_the_image_test_alone_and_no_image_test_for_gemini(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    server = MockVllmServer([_answer('{"number":"472"}')])
    world = _World(engine, keys, server)
    queued = world.service.request_provider_test(
        world.actor, provider="vllm", kind="image", ip="127.0.0.1"
    )
    assert queued["request_id"] == queued["image_request_id"]
    world.runner.run(queued["request_id"])  # type: ignore[arg-type]
    card = world.card()
    assert card["supports_vision"] is True and card["supports_json_schema"] is None
    assert card["connected_at"] is None  # an image test never connects a card

    with pytest.raises(AiSettingsError) as refused:
        world.service.request_provider_test(
            world.actor, provider="gemini", kind="image", ip="127.0.0.1"
        )
    assert refused.value.code == "image_test_not_available"
