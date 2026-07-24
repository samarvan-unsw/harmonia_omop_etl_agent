import unittest
from types import SimpleNamespace

from agent.providers.codex_provider import CodexProvider
from agent.providers.base import TokenUsage


class FakeResponses:
    def __init__(self):
        self.request = None

    def create(self, **request):
        self.request = request
        return SimpleNamespace(
            id="response_test",
            output=[],
            output_text="OK",
            usage=SimpleNamespace(
                input_tokens=100,
                input_tokens_details=SimpleNamespace(
                    cached_tokens=20,
                    cache_write_tokens=5,
                ),
                output_tokens=30,
                output_tokens_details=SimpleNamespace(
                    reasoning_tokens=10,
                ),
                total_tokens=130,
            ),
        )


class CodexProviderUsageTest(unittest.TestCase):
    def test_normalizes_responses_api_usage_without_network_access(self):
        """Successful response usage should use the provider-neutral contract."""
        provider = CodexProvider(
            model="test-model",
            api_key="test-key",
            max_output_tokens=200,
            max_api_retries=0,
        )
        fake_responses = FakeResponses()
        provider.client = SimpleNamespace(responses=fake_responses)

        response = provider.complete(
            system="Test instructions",
            messages=[{"role": "user", "content": "Test input"}],
            tools=[],
        )

        self.assertEqual(
            response.usage,
            TokenUsage(
                input_tokens=100,
                cached_input_tokens=20,
                cache_write_input_tokens=5,
                output_tokens=30,
                reasoning_output_tokens=10,
                total_tokens=130,
            ),
        )
        self.assertEqual(fake_responses.request["max_output_tokens"], 200)

    def test_missing_usage_is_recorded_as_zero(self):
        """A provider response without usage must remain loggable."""
        self.assertEqual(
            CodexProvider._normalize_usage(None),
            TokenUsage(),
        )


if __name__ == "__main__":
    unittest.main()
