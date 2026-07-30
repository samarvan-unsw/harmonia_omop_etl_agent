import unittest

from agent.costing import (
    estimate_serialized_tokens,
    estimated_usage_cost_usd,
    maximum_generation_cost,
)
from agent.providers.base import TokenUsage


class CostingTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "model": "gpt-5.3-codex",
            "max_output_tokens": 800,
            "max_api_retries": 0,
            "pricing": {
                "models": {
                    "gpt-5.3-codex": {
                        "input_usd_per_million_tokens": 1.75,
                        "cached_input_usd_per_million_tokens": 0.175,
                        "cache_write_input_usd_per_million_tokens": 1.75,
                        "output_usd_per_million_tokens": 14.0,
                    }
                }
            },
        }

    def test_estimates_serialized_request_tokens(self):
        tokens = estimate_serialized_tokens(
            '{"instructions":"Generate SQL","input":[]}',
            "gpt-5.3-codex",
        )

        self.assertGreater(tokens, 0)

    def test_calculates_conservative_maximum_cost(self):
        estimate = maximum_generation_cost(
            config=self.config,
            initial_input_tokens=1_000,
            max_iterations=2,
            maximum_output_tokens=1_600,
        )

        self.assertEqual(estimate.maximum_input_tokens, 2_800)
        self.assertEqual(estimate.maximum_cost_usd, 0.0273)

    def test_calculates_measured_usage_cost_by_token_class(self):
        usage = TokenUsage(
            input_tokens=100,
            cached_input_tokens=20,
            cache_write_input_tokens=5,
            output_tokens=30,
            reasoning_output_tokens=10,
            total_tokens=130,
        )

        self.assertEqual(
            estimated_usage_cost_usd(usage, self.config),
            0.0005635,
        )

    def test_inconsistent_input_details_do_not_underestimate(self):
        usage = TokenUsage(
            input_tokens=10,
            cached_input_tokens=8,
            cache_write_input_tokens=8,
        )

        self.assertEqual(
            estimated_usage_cost_usd(usage, self.config),
            0.0000175,
        )


if __name__ == "__main__":
    unittest.main()
