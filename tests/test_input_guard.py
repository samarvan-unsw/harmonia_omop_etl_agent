import unittest

from agent.input_guard import (
    InputSizeLimitError,
    enforce_initial_request_limit,
    initial_request_character_count,
)


class InputGuardTest(unittest.TestCase):
    def test_counts_prompts_and_tool_schemas(self):
        """Every locally controlled initial request component must be counted."""
        without_tool = initial_request_character_count(
            system_prompt="system",
            user_prompt="context",
            tools=[],
        )
        with_tool = initial_request_character_count(
            system_prompt="system",
            user_prompt="context",
            tools=[{"name": "write_file"}],
        )

        self.assertGreater(with_tool, without_tool)

    def test_allows_request_within_limit(self):
        """A request at or below the configured cap should pass locally."""
        size = initial_request_character_count("system", "context", [])

        result = enforce_initial_request_limit(
            system_prompt="system",
            user_prompt="context",
            tools=[],
            maximum_characters=size,
        )

        self.assertEqual(result, size)

    def test_rejects_request_above_limit(self):
        """Oversized request material must be rejected before an API call."""
        with self.assertRaisesRegex(
            InputSizeLimitError,
            "exceeds configured limit",
        ):
            enforce_initial_request_limit(
                system_prompt="system",
                user_prompt="large context",
                tools=[],
                maximum_characters=1,
            )


if __name__ == "__main__":
    unittest.main()
