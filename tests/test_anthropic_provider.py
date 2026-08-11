"""Test Claude message and tool normalization without network access."""

import unittest
from types import SimpleNamespace

from agent.providers.anthropic_provider import AnthropicProvider
from agent.providers.base import TokenUsage, ToolCall


class FakeMessages:
    def __init__(self):
        self.request = None

    def create(self, **request):
        self.request = request
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Writing SQL"),
                SimpleNamespace(
                    type="tool_use",
                    id="tool_1",
                    name="write_file",
                    input={"path": "person.sql", "content": "select 1"},
                ),
            ],
            usage=SimpleNamespace(input_tokens=120, output_tokens=40),
        )


class AnthropicProviderTest(unittest.TestCase):
    def test_normalizes_tool_calls_and_usage(self):
        provider = AnthropicProvider(
            model="claude-sonnet-4-6",
            api_key="test-key",
            max_output_tokens=300,
            max_api_retries=0,
        )
        fake_messages = FakeMessages()
        provider.client = SimpleNamespace(messages=fake_messages)

        response = provider.complete(
            system="Test instructions",
            messages=[{"role": "user", "content": "Create SQL"}],
            tools=[
                {
                    "name": "write_file",
                    "description": "Write SQL",
                    "input_schema": {"type": "object"},
                }
            ],
        )

        self.assertEqual(response.text, "Writing SQL")
        self.assertEqual(
            response.tool_calls,
            [
                ToolCall(
                    id="tool_1",
                    name="write_file",
                    arguments={"path": "person.sql", "content": "select 1"},
                )
            ],
        )
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual(
            response.usage,
            TokenUsage(input_tokens=120, output_tokens=40, total_tokens=160),
        )
        self.assertEqual(fake_messages.request["max_tokens"], 300)

    def test_preserves_assistant_tool_use_and_groups_tool_results(self):
        calls = [
            ToolCall(id="tool_1", name="read_file", arguments={"path": "a"}),
            ToolCall(id="tool_2", name="read_file", arguments={"path": "b"}),
        ]

        native = AnthropicProvider._to_anthropic_messages(
            [
                {"role": "user", "content": "Read both"},
                {"role": "assistant", "content": None, "tool_calls": calls},
                {"role": "tool", "tool_call_id": "tool_1", "content": "A"},
                {"role": "tool", "tool_call_id": "tool_2", "content": "B"},
            ]
        )

        self.assertEqual(
            [message["role"] for message in native], ["user", "assistant", "user"]
        )
        self.assertEqual(len(native[-1]["content"]), 2)


if __name__ == "__main__":
    unittest.main()
