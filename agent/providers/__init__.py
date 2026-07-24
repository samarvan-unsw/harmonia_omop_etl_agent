import os

from .base import AgentProvider
from .codex_provider import CodexProvider


def load_provider(config: dict) -> AgentProvider:
    if config["provider"] == "codex":
        return CodexProvider(
            model=config["model"],
            api_key=os.environ["OPENAI_API_KEY"],

            # Use 2000 if the setting is absent.
            max_output_tokens=config.get("max_output_tokens", 2000),
            # Disable hidden API retries unless explicitly configured.
            max_api_retries=config.get("max_api_retries", 0),
        )
    raise ValueError(f"Unknown provider: {config['provider']}")
