"""Revision 0029 (Gemini key moves to its card, key tier; WI-PV2): move, verify, downgrade.

Each test clones the disposable database, rewinds the clone to revision 0028 by removing the
0029 objects, seeds made-up workspaces with a dummy sealed key (sealed with a throwaway
keyring generated for the test) and runs the real upgrade or downgrade on the clone. The
source database is never changed and nothing here touches a live database. The dummy key
value must never appear in migration output, logs or error text.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.db.ids import new_id
from sqlalchemy.engine import Engine, make_url

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
MODEL = "gemini-3.5-flash-lite"
DUMMY_KEY = "AIzaSyD-synthetic-pv2-move-0000000000000"
OTHER_KEY = "AIzaSyD-synthetic-pv2-other-000000000000"
VLLM_KEY = "synthetic-vllm-key-pv2-0000"
TESTED_AT = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)

_REWIND_0029 = (
    # 0036 (waiting for reading, PD-2f) is removed first; the upgrade re-applies it.
    "ALTER TABLE document DROP CONSTRAINT document_reading_state_ck, "
    "DROP COLUMN reading_failure_code, DROP COLUMN reading_state",
    # 0035 (scanned-invoice switch and reading method, PD-2c) is removed next; the upgrade
    # re-applies it.
    "ALTER TABLE document_version DROP CONSTRAINT document_version_reading_method_ck, "
    "DROP COLUMN reading_method",
    "ALTER TABLE document_text DROP COLUMN scanned",
    "ALTER TABLE ai_setting DROP CONSTRAINT ai_setting_scanned_invoice_reader_ck, "
    "DROP COLUMN scanned_invoice_reader",
    # 0034 (provider capabilities, PD-1b) is removed next; the upgrade re-applies it.
    "ALTER TABLE ai_provider_config DROP CONSTRAINT ai_provider_config_context_window_ck, "
    "DROP COLUMN context_window, DROP COLUMN supports_json_schema, DROP COLUMN supports_vision",
    # 0030 (last-manager lock, WI-PV3) is removed next; the upgrade re-applies it.
    "DROP TRIGGER app_user_last_manager ON app_user",
    "DROP FUNCTION app_user_last_manager_guard()",
    "ALTER TABLE ai_provider_config DROP CONSTRAINT ai_provider_config_pending_key_ck, "
    "DROP CONSTRAINT ai_provider_config_key_tier_provider_ck, "
    "DROP CONSTRAINT ai_provider_config_key_tier_ck, "
    "DROP COLUMN pending_key_secret_id, DROP COLUMN key_tier",
    "UPDATE alembic_version SET version_num = '0028'",
)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[Keyring, SealingPublicKey]:
    directory = tmp_path_factory.mktemp("pv2-keys")
    generate(directory, "k1")
    return (
        Keyring.load(directory / KEYRING_NAME),
        SealingPublicKey.from_file(directory / PUBLIC_KEY_NAME),
    )


class Clone:
    def __init__(self, engine: Engine, url: str) -> None:
        self.engine = engine
        self.url = url
        self.workspaces: dict[str, UUID] = {}
        self.secrets: dict[str, UUID] = {}
        self.user_id: UUID | None = None


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


def _secret(
    connection: sa.Connection,
    public_key: SealingPublicKey,
    workspace_id: UUID,
    user_id: UUID,
    plaintext: str,
) -> UUID:
    secret_id = new_id()
    sealed = seal(
        plaintext.encode(), public_key=public_key, workspace_id=workspace_id, secret_id=secret_id
    )
    connection.execute(
        sa.text(
            "INSERT INTO secret (id, workspace_id, purpose, ciphertext, nonce, key_id, key_hint, "
            "created_by) VALUES (:id, :w, 'ai_api_key', :c, :n, :k, :h, :u)"
        ),
        {
            "id": secret_id,
            "w": workspace_id,
            "c": sealed.envelope,
            "n": sealed.nonce,
            "k": sealed.key_id,
            "h": key_hint(plaintext),
            "u": user_id,
        },
    )
    return secret_id


def _workspace(connection: sa.Connection, label: str) -> UUID:
    workspace_id = new_id()
    connection.execute(
        sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name)"),
        {"id": workspace_id, "name": f"Synthetic 0029 {label}"},
    )
    return workspace_id


def _setting(
    connection: sa.Connection,
    workspace_id: UUID,
    user_id: UUID,
    *,
    secret_id: UUID | None,
    tested_ok: bool | None,
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO ai_setting (id, workspace_id, mode, provider, model, consent_given, "
            "consent_fingerprint, consent_by, consent_at, consent_text_version, version, "
            "key_secret_id, key_storage, last_test_at, last_test_ok) VALUES (:id, :w, "
            "'ai_first', 'gemini', :model, true, :fp, :u, :at, 'consent.v1', 5, :s, 'saved', "
            ":tat, :tok)"
        ),
        {
            "id": new_id(),
            "w": workspace_id,
            "model": MODEL,
            "fp": "a" * 64,
            "u": user_id,
            "at": TESTED_AT,
            "s": secret_id,
            "tat": TESTED_AT if tested_ok is not None else None,
            "tok": tested_ok,
        },
    )


def _card(
    connection: sa.Connection,
    workspace_id: UUID,
    user_id: UUID,
    provider: str,
    *,
    secret_id: UUID | None = None,
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, model, "
            "base_url, key_secret_id, updated_by) VALUES (gen_random_uuid(), :w, "
            "CAST(:p AS ai_provider), true, :m, :b, :s, :u)"
        ),
        {
            "w": workspace_id,
            "p": provider,
            "m": MODEL if provider == "gemini" else "qwen3.5:9b",
            "b": "http://vllm.example.test/vllm/v1" if provider == "vllm" else None,
            "s": secret_id,
            "u": user_id,
        },
    )


@contextmanager
def _clone_at_0028(public_key: SealingPublicKey, *, conflict: bool = False) -> Iterator[Clone]:
    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("migration checks require a disposable reconbench_phase2_* database")
    clone_name = f"reconbench_phase2_m29_{uuid4().hex[:10]}"
    admin = sa.create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{clone_name}" TEMPLATE "{source_name}"')
    clone_url = source_url.set(database=clone_name).render_as_string(hide_password=False)
    engine = sa.create_engine(clone_url)
    previous = os.environ.get("RB_MIGRATOR_DATABASE_URL")
    try:
        clone = Clone(engine, clone_url)
        with engine.begin() as connection:
            heads = set(MigrationContext.configure(connection).get_current_heads())
            assert heads == {"0036"}, heads
            for statement in _REWIND_0029:
                connection.exec_driver_sql(statement)
            # Cards of the source database keep no key from other tests.
            connection.execute(sa.text("UPDATE ai_provider_config SET key_secret_id = NULL"))
            connection.execute(sa.text("UPDATE ai_setting SET key_secret_id = NULL"))
            user_id = UUID(str(connection.scalar(sa.text("SELECT id FROM app_user LIMIT 1"))))
            clone.user_id = user_id

            def add(label: str) -> UUID:
                workspace_id = _workspace(connection, label)
                clone.workspaces[label] = workspace_id
                return workspace_id

            # A: a legacy Gemini key on ai_setting, a passing test, a card without a key.
            a = add("legacy_key")
            clone.secrets["legacy_key"] = _secret(connection, public_key, a, user_id, DUMMY_KEY)
            _setting(connection, a, user_id, secret_id=clone.secrets["legacy_key"], tested_ok=True)
            _card(connection, a, user_id, "gemini")
            # B: Gemini without a key.
            b = add("no_key")
            _setting(connection, b, user_id, secret_id=None, tested_ok=None)
            _card(connection, b, user_id, "gemini")
            # C: a vLLM key on its card must stay untouched; Gemini has none.
            c = add("vllm_key")
            clone.secrets["vllm_key"] = _secret(connection, public_key, c, user_id, VLLM_KEY)
            _setting(connection, c, user_id, secret_id=None, tested_ok=None)
            _card(connection, c, user_id, "gemini")
            _card(connection, c, user_id, "vllm", secret_id=clone.secrets["vllm_key"])
            # D: a legacy key and no card at all (0026 made one for each Gemini row; defensive).
            d = add("no_card")
            clone.secrets["no_card"] = _secret(connection, public_key, d, user_id, DUMMY_KEY)
            _setting(connection, d, user_id, secret_id=clone.secrets["no_card"], tested_ok=False)
            if conflict:
                # E: the card already holds a different key than the legacy pointer.
                e = add("conflict")
                clone.secrets["conflict_legacy"] = _secret(
                    connection, public_key, e, user_id, DUMMY_KEY
                )
                clone.secrets["conflict_card"] = _secret(
                    connection, public_key, e, user_id, OTHER_KEY
                )
                _setting(
                    connection,
                    e,
                    user_id,
                    secret_id=clone.secrets["conflict_legacy"],
                    tested_ok=None,
                )
                _card(connection, e, user_id, "gemini", secret_id=clone.secrets["conflict_card"])
        os.environ["RB_MIGRATOR_DATABASE_URL"] = clone_url
        yield clone
    finally:
        if previous is None:
            os.environ.pop("RB_MIGRATOR_DATABASE_URL", None)
        else:
            os.environ["RB_MIGRATOR_DATABASE_URL"] = previous
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{clone_name}" WITH (FORCE)')
        admin.dispose()


def _pointer(connection: sa.Connection, workspace_id: UUID) -> tuple[UUID | None, UUID | None]:
    """``(ai_setting.key_secret_id, gemini card key_secret_id)``."""

    legacy = connection.scalar(
        sa.text("SELECT key_secret_id FROM ai_setting WHERE workspace_id = :w"),
        {"w": workspace_id},
    )
    card = connection.scalar(
        sa.text(
            "SELECT key_secret_id FROM ai_provider_config WHERE workspace_id = :w "
            "AND provider = 'gemini'"
        ),
        {"w": workspace_id},
    )
    return legacy, card


def _digest(connection: sa.Connection, secret_id: UUID) -> tuple[bytes, bytes, str, str]:
    row = connection.execute(
        sa.text("SELECT ciphertext, nonce, key_id, key_hint FROM secret WHERE id = :id"),
        {"id": secret_id},
    ).one()
    return bytes(row[0]), bytes(row[1]), str(row[2]), str(row[3])


def _open(keyring: Keyring, connection: sa.Connection, workspace_id: UUID, secret_id: UUID) -> str:
    ciphertext, nonce, key_id, _hint = _digest(connection, secret_id)
    return keyring.open(
        envelope=ciphertext,
        nonce=nonce,
        stored_key_id=key_id,
        workspace_id=workspace_id,
        secret_id=secret_id,
    ).decode("utf-8")


def test_key_moves_to_the_card_and_decrypts(
    keys: tuple[Keyring, SealingPublicKey],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    keyring, public_key = keys
    caplog.set_level("DEBUG")
    with _clone_at_0028(public_key) as clone:
        with clone.engine.connect() as connection:
            before = {
                label: _digest(connection, secret_id) for label, secret_id in clone.secrets.items()
            }
        command.upgrade(_config(), "0029")
        with clone.engine.connect() as connection:
            for label in ("legacy_key", "no_card"):
                workspace_id = clone.workspaces[label]
                legacy, card = _pointer(connection, workspace_id)
                # The pointer moved; the secret row is the same row, byte for byte.
                assert legacy is None, label
                assert card == clone.secrets[label], label
                assert _digest(connection, card) == before[label], label
                # Round trip: the worker's keyring still opens the moved key.
                assert _open(keyring, connection, workspace_id, card) == DUMMY_KEY
            assert _pointer(connection, clone.workspaces["no_key"]) == (None, None)
            # A vLLM key stays on its card and untouched.
            vllm = connection.scalar(
                sa.text(
                    "SELECT key_secret_id FROM ai_provider_config WHERE workspace_id = :w "
                    "AND provider = 'vllm'"
                ),
                {"w": clone.workspaces["vllm_key"]},
            )
            assert vllm == clone.secrets["vllm_key"]
            assert _digest(connection, vllm) == before["vllm_key"]
            # Every existing card, including the live Gemini card, migrates as tier = free.
            tiers = connection.scalars(
                sa.text(
                    "SELECT DISTINCT key_tier FROM ai_provider_config WHERE workspace_id = ANY(:w)"
                ),
                {"w": list(clone.workspaces.values())},
            ).all()
            assert set(tiers) == {"free"}
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM ai_provider_config "
                        "WHERE pending_key_secret_id IS NOT NULL"
                    )
                )
                == 0
            )
            # Connection state implied by the legacy passing test; none for a failed or no test.
            connected = {
                label: connection.scalar(
                    sa.text(
                        "SELECT connected_at FROM ai_provider_config WHERE workspace_id = :w "
                        "AND provider = 'gemini'"
                    ),
                    {"w": workspace_id},
                )
                for label, workspace_id in clone.workspaces.items()
            }
            assert connected["legacy_key"] == TESTED_AT
            assert connected["no_key"] is None and connected["no_card"] is None
            # The defensive card for a workspace that had none carries the owner cap values.
            cap = connection.execute(
                sa.text(
                    "SELECT monthly_cost_cap_inr, doc_cost_flag_inr, enabled FROM "
                    "ai_provider_config WHERE workspace_id = :w AND provider = 'gemini'"
                ),
                {"w": clone.workspaces["no_card"]},
            ).one()
            assert (cap[0], cap[1], cap[2]) == (2500, 20, True)
            # Constraints: the tier values, vLLM stays free, a pending key is never the live one.
            with pytest.raises(sa.exc.IntegrityError), clone.engine.begin() as bad:
                bad.execute(
                    sa.text(
                        "UPDATE ai_provider_config SET key_tier = 'gold' WHERE provider = 'gemini' "
                        "AND workspace_id = :w"
                    ),
                    {"w": clone.workspaces["no_key"]},
                )
            with pytest.raises(sa.exc.IntegrityError), clone.engine.begin() as bad:
                bad.execute(
                    sa.text(
                        "UPDATE ai_provider_config SET key_tier = 'paid' WHERE provider = 'vllm' "
                        "AND workspace_id = :w"
                    ),
                    {"w": clone.workspaces["vllm_key"]},
                )
            with pytest.raises(sa.exc.IntegrityError), clone.engine.begin() as bad:
                bad.execute(
                    sa.text(
                        "UPDATE ai_provider_config SET pending_key_secret_id = key_secret_id "
                        "WHERE provider = 'gemini' AND workspace_id = :w"
                    ),
                    {"w": clone.workspaces["legacy_key"]},
                )
    # No key value in migration output or logs.
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        assert DUMMY_KEY not in text and OTHER_KEY not in text and VLLM_KEY not in text


def test_a_conflicting_card_key_aborts_and_changes_nothing(
    keys: tuple[Keyring, SealingPublicKey],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _keyring, public_key = keys
    caplog.set_level("DEBUG")
    with _clone_at_0028(public_key, conflict=True) as clone:
        with clone.engine.connect() as connection:
            before = {
                label: _pointer(connection, workspace_id)
                for label, workspace_id in clone.workspaces.items()
            }
        with pytest.raises(Exception) as caught:  # noqa: PT011 -- any failure must roll back
            command.upgrade(_config(), "0029")
        assert DUMMY_KEY not in str(caught.value) and OTHER_KEY not in str(caught.value)
        assert "verification failed" in str(caught.value)
        with clone.engine.connect() as connection:
            # The whole migration rolled back: still 0028, pointers unchanged, no new columns.
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0028"
            after = {
                label: _pointer(connection, workspace_id)
                for label, workspace_id in clone.workspaces.items()
            }
            assert after == before
            assert not connection.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name = 'ai_provider_config' AND column_name = 'key_tier'"
                )
            )
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        assert DUMMY_KEY not in text and OTHER_KEY not in text


def test_downgrade_moves_the_key_back_and_deletes_pending_candidates(
    keys: tuple[Keyring, SealingPublicKey],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    keyring, public_key = keys
    caplog.set_level("DEBUG")
    with _clone_at_0028(public_key) as clone:
        command.upgrade(_config(), "0029")
        with clone.engine.begin() as connection:
            a = clone.workspaces["legacy_key"]
            before = _digest(connection, clone.secrets["legacy_key"])
            # A rotation candidate waiting on the card.
            pending = _secret(connection, public_key, a, clone.user_id, OTHER_KEY)  # type: ignore[arg-type]
            connection.execute(
                sa.text(
                    "UPDATE ai_provider_config SET pending_key_secret_id = :p, key_tier = 'paid' "
                    "WHERE workspace_id = :w AND provider = 'gemini'"
                ),
                {"p": pending, "w": a},
            )
        command.downgrade(_config(), "0028")
        with clone.engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0028"
            for label in ("legacy_key", "no_card"):
                workspace_id = clone.workspaces[label]
                legacy, card = _pointer(connection, workspace_id)
                assert legacy == clone.secrets[label] and card is None, label
                assert _open(keyring, connection, workspace_id, legacy) == DUMMY_KEY
            assert _digest(connection, clone.secrets["legacy_key"]) == before
            # The vLLM key never moved; the pending candidate is gone with its column.
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT key_secret_id FROM ai_provider_config WHERE workspace_id = :w "
                        "AND provider = 'vllm'"
                    ),
                    {"w": clone.workspaces["vllm_key"]},
                )
                == clone.secrets["vllm_key"]
            )
            assert not connection.scalar(
                sa.text("SELECT count(*) FROM secret WHERE id = :p"), {"p": pending}
            )
            assert not connection.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name = "
                    "'ai_provider_config' AND column_name IN ('key_tier', 'pending_key_secret_id')"
                )
            )
        # Upgrading again lands in the same place (the move is repeatable).
        command.upgrade(_config(), "0029")
        with clone.engine.connect() as connection:
            legacy, card = _pointer(connection, clone.workspaces["legacy_key"])
            assert legacy is None and card == clone.secrets["legacy_key"]
            assert _open(keyring, connection, clone.workspaces["legacy_key"], card) == DUMMY_KEY
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        assert DUMMY_KEY not in text and OTHER_KEY not in text and VLLM_KEY not in text


def test_downgrade_refuses_when_both_locations_hold_a_key(
    keys: tuple[Keyring, SealingPublicKey],
) -> None:
    _keyring, public_key = keys
    with _clone_at_0028(public_key) as clone:
        command.upgrade(_config(), "0029")
        with clone.engine.begin() as connection:
            a = clone.workspaces["legacy_key"]
            spare = _secret(connection, public_key, a, clone.user_id, OTHER_KEY)  # type: ignore[arg-type]
            connection.execute(
                sa.text("UPDATE ai_setting SET key_secret_id = :s WHERE workspace_id = :w"),
                {"s": spare, "w": a},
            )
        with pytest.raises(Exception) as caught:  # noqa: PT011
            command.downgrade(_config(), "0028")
        assert "downgrade refused" in str(caught.value)
        assert OTHER_KEY not in str(caught.value) and DUMMY_KEY not in str(caught.value)
        with clone.engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0029"
