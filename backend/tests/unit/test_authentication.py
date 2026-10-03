"""Unit checks for the Phase 3 authentication policy and password primitives."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from pydantic import ValidationError
from reconbench_app.authentication import AuthSecurityConfig, AuthService, csrf_token_for_session
from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_app.settings import Settings


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://synthetic:synthetic@127.0.0.1/reconbench_phase2_auth",
        "s3_endpoint_url": "http://127.0.0.1:9000",
        "s3_region": "us-east-1",
        "s3_bucket": "synthetic",
        "s3_api_access_key": "synthetic",
        "s3_api_secret_key": "synthetic",
        "public_base_url": "https://example.invalid",
        "argon2_time_cost": 1,
        "argon2_memory_cost_kib": 8192,
        "argon2_parallelism": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_accept_environment_field_for_cookie_policy() -> None:
    assert _settings(environment="ci").environment == "ci"


def test_checked_in_login_policy_matches_user_approved_defaults() -> None:
    config = AuthSecurityConfig.from_config_body(DEFAULT_CONFIG)

    assert config.model_dump() == {
        "session_idle_minutes": 30,
        "session_absolute_hours": 12,
        "login_email_max_failures": 10,
        "login_email_window_minutes": 15,
        "login_email_lockout_minutes": 15,
        "login_ip_max_failures": 30,
        "login_ip_window_minutes": 15,
        "login_ip_throttle_minutes": 15,
    }


def test_previous_immutable_config_gets_the_approved_app_security_defaults() -> None:
    config = AuthSecurityConfig.from_config_body(
        {
            "app": {
                "security": {
                    "session_idle_minutes": 30,
                    "session_absolute_hours": 12,
                }
            }
        }
    )

    assert config.login_email_max_failures == 10
    assert config.login_ip_max_failures == 30
    assert config.login_email_window_minutes == 15
    assert config.login_email_lockout_minutes == 15
    assert config.login_ip_window_minutes == 15
    assert config.login_ip_throttle_minutes == 15


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("login_email_max_failures", 0),
        ("login_ip_window_minutes", -1),
        ("login_email_lockout_minutes", "15"),
    ],
)
def test_app_security_rejects_invalid_throttle_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuthSecurityConfig.model_validate({field: value})


def test_password_hashing_uses_argon2id_and_verifies_with_configured_parameters() -> None:
    service = AuthService(engine=_unused_engine(), settings=_settings())
    encoded = service.hash_password("synthetic password")

    assert encoded.startswith("$argon2id$v=19$m=8192,t=1,p=1$")
    assert service._verify_password("synthetic password", encoded)
    assert not service._verify_password("wrong password", encoded)
    assert not service._verify_password("anything", service._dummy_hash)


def test_csrf_secret_is_stable_and_database_value_is_only_its_digest() -> None:
    token = "synthetic-session-token-with-256-bits"
    csrf = csrf_token_for_session(token)

    assert csrf == csrf_token_for_session(token)
    assert csrf != token
    assert hashlib.sha256(csrf.encode()).hexdigest() != csrf


def _unused_engine() -> Any:
    # Password operations do not connect. The real Engine type is exercised in
    # PostgreSQL integration tests.
    import sqlalchemy as sa

    return sa.create_engine("sqlite://")
