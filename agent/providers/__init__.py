"""Construct the model provider selected by the validated agent configuration."""

import os

from .base import AgentProvider
from .codex_provider import CodexProvider


class ProviderConfigurationError(RuntimeError):
    """Raised when a selected provider lacks required server configuration."""


def load_provider(config: dict) -> AgentProvider:
    if config["provider"] == "codex":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ProviderConfigurationError(
                "OPENAI_API_KEY is not configured."
            )
        return CodexProvider(
            model=config["model"],
            api_key=api_key,

            # Use 2000 if the setting is absent.
            max_output_tokens=config.get("max_output_tokens", 2000),
            # Disable hidden API retries unless explicitly configured.
            max_api_retries=config.get("max_api_retries", 0),
        )
    raise ValueError(f"Unknown provider: {config['provider']}")
