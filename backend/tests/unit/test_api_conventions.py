"""Focused coverage for shared HTTP and keyset pagination conventions."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import boto3
import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reconbench_app.api.application import create_app
from reconbench_app.api.conventions import (
    ApiProblem,
    Money,
    ProblemFieldError,
    Quantity,
    accepted_job_response,
    conditional_json_response,
    etag_for_version,
    problem_response,
    require_if_match,
)
from reconbench_app.api.pagination import KeysetField, paginate_keyset
from reconbench_app.authentication import AuthContext, AuthenticationCheck
from reconbench_app.authorization import requires
from reconbench_app.db.repository import VersionConflict
from reconbench_app.idempotency import (
    IdempotentResult,
    execute_idempotent,
    idempotent_json_response,
    validate_idempotency_key,
)
from reconbench_app.json_serialization import DecimalJSONResponse
from reconbench_app.settings import Settings
from sqlalchemy.orm import Session


def _request(if_none_match: str | None = None) -> Request:
    headers = [] if if_none_match is None else [(b"if-none-match", if_none_match.encode())]
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/resource",
            "raw_path": b"/api/v1/resource",
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


def test_problem_details_are_rfc9457_and_validation_does_not_echo_values() -> None:
    response = problem_response(
        status=422,
        title="Invalid request",
        detail="One or more request fields are invalid.",
        instance="/api/v1/test",
        code="invalid_request",
        errors=[
            ProblemFieldError(
                location=["password"], code="invalid_field", detail="This field is invalid."
            )
        ],
    )
    assert response.media_type == "application/problem+json"
    assert response.status_code == 422
    body = response.body.decode("utf-8")
    assert '"type":"about:blank"' in body
    assert '"code":"invalid_request"' in body
    assert '"location":["password"]' in body
    assert "submitted-secret" not in body


def test_etag_helpers_require_if_match_and_return_conditional_304() -> None:
    etag = etag_for_version(7)
    assert etag == '"7"'
    assert require_if_match(etag) == 7
    with pytest.raises(ApiProblem) as missing:
        require_if_match(None)
    assert (missing.value.status, missing.value.code) == (428, "if_match_required")

    body = {"amount": Decimal("25.50")}
    first = conditional_json_response(_request(), body, etag)
    unchanged = conditional_json_response(_request(f"W/{etag}"), body, etag)
    assert first.status_code == 200
    assert first.headers["etag"] == etag
    assert b'"amount":"25.50"' in first.body
    assert unchanged.status_code == 304
    assert unchanged.body == b""
    assert unchanged.headers["etag"] == etag


def test_job_acceptance_has_202_and_same_origin_resource_url() -> None:
    response = accepted_job_response(
        resource_url="/api/v1/runs/run-123",
        content={"status": "queued", "resource_url": "/api/v1/runs/run-123"},
    )
    assert response.status_code == 202
    assert response.headers["location"] == "/api/v1/runs/run-123"
    assert b'"status":"queued"' in response.body
    with pytest.raises(ValueError, match="same-origin"):
        accepted_job_response(resource_url="https://outside.invalid/job", content={})


def test_shared_money_and_quantity_types_serialize_as_decimal_strings_and_dates_iso() -> None:
    class ResponseModel(BaseModel):
        amount: Money
        quantity: Quantity
        occurred_at: datetime
        invoice_date: date

    model = ResponseModel(
        amount=Decimal("123456.70"),
        quantity=Decimal("3.2500"),
        occurred_at=datetime.fromisoformat("2026-10-02T10:30:00+05:30"),
        invoice_date=date(2026, 10, 2),
    )
    assert model.model_dump(mode="json") == {
        "amount": "123456.70",
        "quantity": "3.2500",
        "occurred_at": "2026-10-02T10:30:00+05:30",
        "invoice_date": "2026-10-02",
    }
    direct = DecimalJSONResponse(
        content={"amount": Decimal("0.10"), "invoice_date": date(2026, 10, 2)}
    )
    assert direct.body == b'{"amount":"0.10","invoice_date":"2026-10-02"}'


def test_version_conflict_carries_current_representation() -> None:
    current = {"version": 4, "amount": Decimal("19.00")}
    conflict = VersionConflict(current)
    assert conflict.current == current


def test_keyset_pages_stay_stable_when_rows_are_inserted() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    documents = sa.Table(
        "synthetic_document",
        metadata,
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("document_number", sa.String(), primary_key=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
    )
    metadata.create_all(engine)
    fields = (
        KeysetField("invoice_date", documents.c.invoice_date),
        KeysetField("document_number", documents.c.document_number, unique=True),
    )
    workspace_id = uuid4()
    with Session(engine) as session:
        session.execute(
            documents.insert(),
            [
                {"invoice_date": date(2026, 9, 1), "document_number": f"INV-{n:03}", "amount": n}
                for n in (1, 2, 4)
            ]
            + [{"invoice_date": date(2026, 9, 2), "document_number": "INV-005", "amount": 5}],
        )
        session.commit()

        first = paginate_keyset(
            session,
            sa.select(documents),
            fields=fields,
            limit=2,
            count=True,
            context={"workspace_id": workspace_id, "status": "accepted"},
        )
        assert [row["document_number"] for row in first.items] == ["INV-001", "INV-002"]
        assert first.total_count == 4
        assert first.next_cursor is not None
        assert first.response_body()["total_count"] == 4

        no_count = paginate_keyset(
            session,
            sa.select(documents),
            fields=fields,
            limit=1,
            context={"workspace_id": workspace_id, "status": "accepted"},
        )
        assert "total_count" not in no_count.response_body()

        session.execute(
            documents.insert().values(
                invoice_date=date(2026, 9, 1), document_number="INV-003", amount=3
            )
        )
        session.commit()
        second = paginate_keyset(
            session,
            sa.select(documents),
            fields=fields,
            cursor=first.next_cursor,
            limit=2,
            count=True,
            context={"workspace_id": workspace_id, "status": "accepted"},
        )
        assert [row["document_number"] for row in second.items] == ["INV-003", "INV-004"]
        assert second.total_count == 5

        with pytest.raises(ValueError, match="does not match"):
            paginate_keyset(
                session,
                sa.select(documents),
                fields=fields,
                cursor=first.next_cursor,
                context={"workspace_id": workspace_id, "status": "failed"},
            )
    engine.dispose()


def test_keyset_requires_unique_business_tie_break_and_bounded_limit() -> None:
    table = sa.table("documents", sa.column("id", sa.String()), sa.column("number", sa.String()))
    with pytest.raises(ValueError, match="identifier"):
        KeysetField("id", table.c.id)
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(ValueError, match="unique business key"), Session(engine) as session:
        paginate_keyset(
            session,
            sa.select(table),
            fields=[KeysetField("number", table.c.number)],
        )
    engine.dispose()


def test_http_layer_maps_conflicts_and_if_match_to_problem_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url="postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/synthetic",
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    auth_context = AuthContext(
        session_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        display_name="Synthetic Manager",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(
        auth_context, True
    )

    @app.get("/api/v1/conflict-probe")
    @requires("data.view")
    def conflict_probe() -> None:
        raise VersionConflict({"version": 4, "amount": Decimal("20.50")})

    @app.put("/api/v1/if-match-probe")
    @requires("data.view")
    def if_match_probe(request: Request) -> dict[str, bool]:
        require_if_match(request.headers.get("If-Match"))
        return {"updated": True}

    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        client.cookies.set("rb_session", "synthetic-token", path="/api/v1")
        conflict = client.get("/api/v1/conflict-probe")
        missing = client.put("/api/v1/if-match-probe", headers={"X-CSRF-Token": "synthetic-csrf"})

    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["code"] == "version_conflict"
    assert conflict.json()["current"] == {"version": 4, "amount": "20.50"}
    assert missing.status_code == 428
    assert missing.json()["code"] == "if_match_required"


def test_openapi_describes_auth_routes_and_problem_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/synthetic",
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        schema = client.app.openapi()
    assert set(schema["paths"]) >= {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
    }
    assert "ProblemDetails" in schema["components"]["schemas"]
    assert "429" in schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    media = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]["401"]["content"]
    assert "application/problem+json" in media


def test_idempotency_header_is_required_and_auth_routes_are_excluded() -> None:
    assert validate_idempotency_key("  run-try-1  ") == "run-try-1"
    for value in (None, "", "has space", "li\nne", "x" * 201):
        with pytest.raises(ApiProblem):
            validate_idempotency_key(value)

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with Session(engine) as session:
        for endpoint in (
            "POST /auth/login",
            "POST /api/v1/auth/logout",
            "POST /api/v1/auth/login?continue=/home",
            "POST /auth/logout/",
        ):
            with pytest.raises(ValueError, match="must not be applied"):
                execute_idempotent(
                    session,
                    workspace_id=uuid4(),
                    user_id=uuid4(),
                    endpoint=endpoint,
                    key="auth-key",
                    request_body={},
                    operation=lambda _: pytest.fail("auth endpoints must never execute here"),
                )
    engine.dispose()


def test_idempotent_job_result_rebuilds_the_same_202_location() -> None:
    resource_id = uuid4()
    result = IdempotentResult(
        202, {"status": "queued", "amount": Decimal("2.50")}, "runs", resource_id
    )
    initial = idempotent_json_response(result)
    replay = idempotent_json_response(
        IdempotentResult(202, result.body, "runs", resource_id, replayed=True)
    )
    assert initial.status_code == replay.status_code == 202
    assert initial.headers["location"] == replay.headers["location"]
    assert initial.headers["location"] == f"/api/v1/runs/{resource_id}"
    assert initial.body == replay.body == b'{"status":"queued","amount":"2.50"}'
    with pytest.raises(ValueError, match="require a durable resource"):
        idempotent_json_response(IdempotentResult(202, {"status": "queued"}))
