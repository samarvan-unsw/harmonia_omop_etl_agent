"""Test HTTP request limits independently from FastAPI request parsing."""

import json
import logging
import unittest
from collections.abc import Iterable

from starlette.types import Message, Receive, Scope, Send

from agent.http_middleware import RequestPolicyMiddleware


class RequestPolicyMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _invoke(
        *,
        body_chunks: Iterable[bytes] = (),
        headers: list[tuple[bytes, bytes]] | None = None,
        maximum_bytes: int = 8,
    ) -> list[Message]:
        chunks = list(body_chunks)
        incoming = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": position < len(chunks) - 1,
            }
            for position, chunk in enumerate(chunks)
        ] or [
            {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }
        ]
        sent: list[Message] = []

        async def receive() -> Message:
            return incoming.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        async def echo_app(
            _scope: Scope,
            app_receive: Receive,
            app_send: Send,
        ) -> None:
            body = b""
            while True:
                message = await app_receive()
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            await app_send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await app_send(
                {"type": "http.response.body", "body": body}
            )

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/test",
            "raw_path": b"/test",
            "query_string": b"",
            "root_path": "",
            "headers": headers or [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        middleware = RequestPolicyMiddleware(
            echo_app,
            maximum_request_bytes=maximum_bytes,
            logger=logging.getLogger("test.request_policy"),
        )
        await middleware(scope, receive, send)
        return sent

    async def test_rejects_streamed_body_without_content_length(self):
        messages = await self._invoke(
            body_chunks=[b"12345", b"6789"],
        )

        self.assertEqual(messages[0]["status"], 413)
        self.assertEqual(
            json.loads(messages[1]["body"]),
            {"detail": "Request body is too large."},
        )

    async def test_rejects_oversized_declared_content_length_early(self):
        messages = await self._invoke(
            headers=[(b"content-length", b"9")],
        )

        self.assertEqual(messages[0]["status"], 413)

    async def test_rejects_invalid_content_length(self):
        messages = await self._invoke(
            headers=[(b"content-length", b"not-a-number")],
        )

        self.assertEqual(messages[0]["status"], 400)

    async def test_passes_bounded_stream_and_adds_security_headers(self):
        messages = await self._invoke(
            body_chunks=[b"1234", b"5678"],
        )

        self.assertEqual(messages[0]["status"], 200)
        headers = dict(messages[0]["headers"])
        self.assertRegex(
            headers[b"x-request-id"].decode("ascii"),
            r"^[a-f0-9]{32}$",
        )
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertEqual(messages[1]["body"], b"12345678")
