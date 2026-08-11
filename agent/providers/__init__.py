"""Construct the model provider selected by the validated agent configuration."""

import os

from .base import AgentProvider
from .anthropic_provider import AnthropicProvider
from .codex_provider import CodexProvider


class ProviderConfigurationError(RuntimeError):
    """Raised when a selected provider lacks required server configuration."""


def load_provider(
    config: dict,
    *,
    api_key: str | None = None,
) -> AgentProvider:
    """Build the selected adapter from an ephemeral or local credential."""
    provider_name = config["provider"]
    if provider_name == "codex":
        resolved_api_key = (
            api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        ).strip()
        if not resolved_api_key:
            raise ProviderConfigurationError(
                "OPENAI_API_KEY is not configured."
            )
        return CodexProvider(
            model=config["model"],
            api_key=resolved_api_key,

            # Use 2000 if the setting is absent.
            max_output_tokens=config.get("max_output_tokens", 2000),
            # Disable hidden API retries unless explicitly configured.
            max_api_retries=config.get("max_api_retries", 0),
        )
    if provider_name == "anthropic":
        resolved_api_key = (
            api_key
            if api_key is not None
            else os.getenv("ANTHROPIC_API_KEY", "")
        ).strip()
        if not resolved_api_key:
            raise ProviderConfigurationError(
                "ANTHROPIC_API_KEY is not configured."
            )
        return AnthropicProvider(
            model=config["model"],
            api_key=resolved_api_key,
            max_output_tokens=config.get("max_output_tokens", 2000),
            max_api_retries=config.get("max_api_retries", 0),
        )
    raise ValueError(f"Unknown provider: {provider_name}")
