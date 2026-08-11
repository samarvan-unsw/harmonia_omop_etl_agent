"""Implement the agent provider using Anthropic's Messages API."""

from typing import Any

from anthropic import Anthropic

from .base import AgentProvider, ProviderResponse, TokenUsage, ToolCall


class AnthropicProvider(AgentProvider):
    """Anthropic Messages API adapter for the agent provider contract."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_output_tokens: int = 2000,
        max_api_retries: int = 0,
    ):
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        if max_api_retries < 0:
            raise ValueError("max_api_retries cannot be negative")

        self.client = Anthropic(
            api_key=api_key,
            max_retries=max_api_retries,
            timeout=120,
        )
        self.model = model
        self.max_output_tokens = max_output_tokens

    @staticmethod
    def _assistant_content(message: dict) -> list[dict[str, Any]]:
        """Convert one canonical assistant turn to Claude content blocks."""
        content: list[dict[str, Any]] = []
        text = message.get("content")
        if text:
            content.append({"type": "text", "text": text})
        for tool_call in message.get("tool_calls") or []:
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.arguments,
                }
            )
        return content

    @classmethod
    def _to_anthropic_messages(cls, messages: list[dict]) -> list[dict]:
        """Convert canonical turns and group adjacent tool results."""
        native: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                native.append({"role": "user", "content": pending_tool_results.copy()})
                pending_tool_results.clear()

        for message in messages:
            role = message["role"]
            if role == "tool":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )
                continue

            flush_tool_results()
            if role == "assistant":
                content = cls._assistant_content(message)
                if content:
                    native.append({"role": "assistant", "content": content})
            else:
                native.append({"role": "user", "content": message["content"]})

        flush_tool_results()
        return native

    @staticmethod
    def _normalize_usage(usage: Any) -> TokenUsage:
        """Flatten Anthropic Messages usage into the shared contract."""
        if usage is None:
            return TokenUsage()
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0)
        cache_write_tokens = getattr(
            usage,
            "cache_creation_input_tokens",
            0,
        )
        return TokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cache_read_tokens,
            cache_write_input_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> ProviderResponse:
        """Generate the next Claude turn and normalize any tool calls."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=system,
            messages=self._to_anthropic_messages(messages),
            tools=tools,
        )

        text_blocks: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text" and block.text:
                text_blocks.append(block.text)
            elif block.type == "tool_use":
                if not isinstance(block.input, dict):
                    raise ValueError(f"Tool arguments must be an object: {block.name}")
                calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        return ProviderResponse(
            text="\n".join(text_blocks) or None,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=self._normalize_usage(response.usage),
        )
