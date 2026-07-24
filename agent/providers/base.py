from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class TokenUsage:
    """Provider-neutral token usage for one successful response."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Aggregate usage across successful provider responses."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=(
                self.cached_input_tokens + other.cached_input_tokens
            ),
            cache_write_input_tokens=(
                self.cache_write_input_tokens
                + other.cache_write_input_tokens
            ),
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=(
                self.reasoning_output_tokens
                + other.reasoning_output_tokens
            ),
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class ProviderResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use"
    usage: TokenUsage = field(default_factory=TokenUsage)


class AgentProvider(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> ProviderResponse:
        """
        messages: canonical [{"role": "user"|"assistant"|"tool", "content": ...}]
        tools:    canonical [{"name": ..., "description": ..., "input_schema": {...}}]
        Returns a ProviderResponse regardless of vendor response shape.
        """
        ...
