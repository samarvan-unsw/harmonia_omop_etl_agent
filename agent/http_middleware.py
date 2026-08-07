"""Enforce HTTP request limits and attach safe request tracing metadata."""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _RequestBodyTooLarge(Exception):
    """Stop request parsing once the configured byte limit is exceeded."""


class RequestPolicyMiddleware:
    """Apply request-size, correlation-ID, and response-header policies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        maximum_request_bytes: int,
        logger: logging.Logger,
    ) -> None:
        if maximum_request_bytes < 1:
            raise ValueError("maximum_request_bytes must be greater than zero")
        self.app = app
        self.maximum_request_bytes = maximum_request_bytes
        self.logger = logger

    @staticmethod
    def _declared_content_length(scope: Scope) -> int | None:
        values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if not values:
            return None
        if len(values) != 1:
            raise ValueError("multiple Content-Length headers")
        try:
            content_length = int(values[0].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid Content-Length header") from error
        if content_length < 0:
            raise ValueError("negative Content-Length header")
        return content_length

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        started_at = perf_counter()
        response_started = False
        response_status = 500
        received_bytes = 0

        async def traced_send(message: Message) -> None:
            nonlocal response_started, response_status
            if message["type"] == "http.response.start":
                response_started = True
                response_status = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower()
                    not in {b"x-request-id", b"x-content-type-options"}
                ]
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.maximum_request_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            content_length = self._declared_content_length(scope)
        except ValueError:
            response = JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header."},
            )
            await response(scope, receive, traced_send)
        else:
            if content_length is not None and (
                content_length > self.maximum_request_bytes
            ):
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large."},
                )
                await response(scope, receive, traced_send)
            else:
                try:
                    await self.app(scope, limited_receive, traced_send)
                except _RequestBodyTooLarge:
                    if response_started:
                        raise
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "Request body is too large."},
                    )
                    await response(scope, receive, traced_send)
                except Exception:
                    self.logger.exception(
                        "request_failed request_id=%s method=%s path=%s "
                        "duration_ms=%d",
                        request_id,
                        scope.get("method", ""),
                        scope.get("path", ""),
                        int((perf_counter() - started_at) * 1_000),
                    )
                    raise

        self.logger.info(
            "request_completed request_id=%s method=%s path=%s status=%d "
            "duration_ms=%d",
            request_id,
            scope.get("method", ""),
            scope.get("path", ""),
            response_status,
            int((perf_counter() - started_at) * 1_000),
        )
