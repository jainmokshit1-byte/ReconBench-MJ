"""Required infrastructure settings fail closed when not configured."""

import pytest
from pydantic import ValidationError
from reconbench_app.settings import Settings


def test_required_settings_fail_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RB_DATABASE_URL",
        "RB_S3_ENDPOINT_URL",
        "RB_S3_REGION",
        "RB_S3_BUCKET",
        "RB_S3_API_ACCESS_KEY",
        "RB_S3_API_SECRET_KEY",
        "RB_PUBLIC_BASE_URL",
    ):
        monkeypatch.setenv(name, "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unknown_extraction_engine_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("RB_DATABASE_URL", "postgresql+psycopg://synthetic:secret@localhost/synthetic"),
        ("RB_S3_ENDPOINT_URL", "http://localhost:9000"),
        ("RB_S3_REGION", "local"),
        ("RB_S3_BUCKET", "synthetic"),
        ("RB_S3_API_ACCESS_KEY", "synthetic"),
        ("RB_S3_API_SECRET_KEY", "synthetic"),
        ("RB_PUBLIC_BASE_URL", "http://localhost:8000"),
    ):
        monkeypatch.setenv(name, value)

    assert Settings(_env_file=None).extraction_engine == "local_hybrid"
    monkeypatch.setenv("RB_EXTRACTION_ENGINE", "bogus")
    with pytest.raises(ValueError, match="RB_EXTRACTION_ENGINE"):
        Settings(_env_file=None)
