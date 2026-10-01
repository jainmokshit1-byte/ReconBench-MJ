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
