"""Test provider construction without contacting external services."""

import os
import unittest
from unittest.mock import patch

from agent.providers import ProviderConfigurationError, load_provider


class ProviderFactoryTest(unittest.TestCase):
    def test_missing_openai_key_has_a_specific_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ProviderConfigurationError,
                "OPENAI_API_KEY is not configured",
            ):
                load_provider({"provider": "codex", "model": "test-model"})

    def test_openai_key_is_not_passed_with_surrounding_whitespace(self):
        with (
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "  test-secret  "},
                clear=True,
            ),
            patch("agent.providers.CodexProvider") as provider,
        ):
            load_provider({"provider": "codex", "model": "test-model"})

        provider.assert_called_once_with(
            api_key="test-secret",
            max_api_retries=0,
            max_output_tokens=2000,
            model="test-model",
        )
