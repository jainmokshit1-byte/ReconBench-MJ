"""Request IDs and a bounded JSON request-body reader for the API scaffold."""

from __future__ import annotations

import json
import re
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_JSON_BODY_BYTES = 1_048_576
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """Set the shared security policy on every HTTP response; CORS stays closed."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        protected_names = {name.lower().encode("ascii") for name in SECURITY_HEADERS}

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in protected_names
                ]
                headers.extend(
                    (name.lower().encode("ascii"), value.encode("latin-1"))
                    for name, value in SECURITY_HEADERS.items()
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RequestIdMiddleware:
    """Bind a safe request ID to structured logs and echo it in every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_id = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"x-request-id"
            ),
            "",
        )
        request_id = supplied_id if _REQUEST_ID.fullmatch(supplied_id) else str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.clear_contextvars()


class JsonBodyLimitMiddleware:
    """Reject JSON request bodies above one MiB, including chunked uploads."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_JSON_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_json(scope):
            await self.app(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self.max_bytes
        ):
            await self._too_large(scope, send)
            return

        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    await self._too_large(scope, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        index = 0

        async def replay_body() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_body, send)

    @staticmethod
    def _is_json(scope: Scope) -> bool:
        content_type = next(
            (
                value.decode("latin-1").split(";", 1)[0].strip().lower()
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-type"
            ),
            "",
        )
        return content_type == "application/json" or content_type.endswith("+json")

    async def _too_large(self, scope: Scope, send: Send) -> None:
        body = json.dumps(
            {
                "type": "about:blank",
                "title": "Request body too large",
                "status": 413,
                "detail": "JSON request bodies may not exceed 1 MiB.",
                "instance": scope.get("path", "/"),
                "code": "request_body_too_large",
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
