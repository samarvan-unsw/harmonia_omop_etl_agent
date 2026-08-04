"""Implement the agent provider using OpenAI's Responses API."""

import json
from typing import Any

from openai import OpenAI

from .base import AgentProvider, ProviderResponse, TokenUsage, ToolCall


class CodexProvider(AgentProvider):
    """OpenAI Responses API adapter for the agent's provider contract."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_output_tokens: int = 2000,
        max_api_retries: int = 0,
    ):
        # Reject invalid limits before making an API request.
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        if max_api_retries < 0:
            raise ValueError("max_api_retries cannot be negative")

        self.client = OpenAI(
            api_key=api_key,
            max_retries=max_api_retries,
            timeout=120,
        )
        self.model = model
        self.max_output_tokens = max_output_tokens

        self._previous_response_id = None
        self._consumed_message_count = 0

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        """Convert canonical tools to the Responses API function-tool format."""
        return [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
                "strict": True,
            }
            for tool in tools
        ]

    def _to_openai_input(self, messages: list[dict]) -> list[dict]:
        """Convert only messages not already held by the Responses API."""
        new_messages = messages[self._consumed_message_count :]
        native: list[dict] = []

        for message in new_messages:
            role = message["role"]

            if role == "tool":
                # Tool outputs must reference the call_id returned by the model.
                native.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": message["content"],
                    }
                )
            elif role == "assistant" and self._previous_response_id:
                # This assistant turn is already included through previous_response_id.
                continue
            elif role == "assistant":
                content = message.get("content")
                if content:
                    native.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": content,
                            "phase": (
                                "commentary"
                                if message.get("tool_calls")
                                else "final_answer"
                            ),
                        }
                    )
                for tool_call in message.get("tool_calls") or []:
                    native.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.id,
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments),
                        }
                    )
            else:
                native.append(
                    {
                        "type": "message",
                        "role": role,
                        "content": message["content"],
                    }
                )

        return native

    @staticmethod
    def _parse_arguments(raw_arguments: str, tool_name: str) -> dict[str, Any]:
        """Decode and validate the JSON object supplied to a tool call."""
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError(f"Tool arguments must be an object: {tool_name}")
        return arguments

    @staticmethod
    def _normalize_usage(usage: Any) -> TokenUsage:
        """Flatten OpenAI Responses usage into the provider contract."""
        if usage is None:
            return TokenUsage()

        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            cached_input_tokens=getattr(
                input_details,
                "cached_tokens",
                0,
            ),
            cache_write_input_tokens=getattr(
                input_details,
                "cache_write_tokens",
                0,
            ),
            output_tokens=getattr(usage, "output_tokens", 0),
            reasoning_output_tokens=getattr(
                output_details,
                "reasoning_tokens",
                0,
            ),
            total_tokens=getattr(usage, "total_tokens", 0),
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> ProviderResponse:
        """Generate the next assistant turn and normalize any tool calls."""
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": self._to_openai_input(messages),
            "tools": self._to_openai_tools(tools),

            # Hard output limit for this individual API request.
            "max_output_tokens": self.max_output_tokens,
            "parallel_tool_calls": False,
        }

        if self._previous_response_id:
            request["previous_response_id"] = self._previous_response_id

        response = self.client.responses.create(**request)
        calls = [
            ToolCall(
                id=item.call_id,
                name=item.name,
                arguments=self._parse_arguments(item.arguments, item.name),
            )
            for item in response.output
            if item.type == "function_call"
        ]

        # Save conversation state only after a successful response.
        self._previous_response_id = response.id
        self._consumed_message_count = len(messages)

        return ProviderResponse(
            text=response.output_text or None,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=self._normalize_usage(response.usage),
        )
