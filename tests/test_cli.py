import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openai import OpenAIError

from agent.cli import (
    _api_error_message,
    _configured_output_token_ceiling,
    _format_usage,
    main,
)


class ApiErrorMessageTest(unittest.TestCase):
    def test_generic_error_does_not_expose_original_message(self):
        """Raw SDK messages may contain request details and must not be echoed."""
        secret_text = "request failed with key sk-sensitive"

        message = _api_error_message(OpenAIError(secret_text))

        self.assertEqual(message, "OpenAI API request failed.")
        self.assertNotIn(secret_text, message)


class DryRunTest(unittest.TestCase):
    def test_dry_run_prints_preflight_without_running_agent(self):
        """Dry-run should expose cost controls without permitting an API call."""
        output = io.StringIO()

        with (
            patch("sys.argv", ["agent.cli", "person", "--dry-run"]),
            patch("agent.cli.run_agent") as run_agent,
            redirect_stdout(output),
        ):
            main()

        text = output.getvalue()
        run_agent.assert_not_called()
        self.assertIn("Dry run passed for 'person'", text)
        self.assertIn("No API call was made", text)
        self.assertIn("Maximum output tokens per request:", text)
        self.assertIn("Maximum generation attempts: 2", text)
        self.assertIn("Worst-case run output-token ceiling:", text)
        self.assertIn("Target file:", text)
        self.assertIn("Context size:", text)
        self.assertIn("Initial request size:", text)
        self.assertIn("Generation readiness:", text)


class EtlSpecificationCliTest(unittest.TestCase):
    def test_creates_document_without_loading_config_or_calling_api(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch(
                    "sys.argv",
                    [
                        "agent.cli",
                        "person",
                        "--etl-specification",
                        "md",
                    ],
                ),
                patch("agent.cli.ROOT", root),
                patch("agent.cli.validate_specs", return_value=object()),
                patch(
                    "agent.cli.pending_review_fields",
                    return_value=(),
                ),
                patch(
                    "agent.etl_specification.build_etl_specification",
                    return_value=SimpleNamespace(
                        content=b"# person ETL specification\n",
                        file_name="person_etl_specification.md",
                    ),
                ),
                patch("agent.cli.run_agent") as run_agent,
                redirect_stdout(output),
            ):
                main()

            document = (
                root
                / "output"
                / "etl_specifications"
                / "person_etl_specification.md"
            )
            self.assertEqual(
                document.read_bytes(),
                b"# person ETL specification\n",
            )
        run_agent.assert_not_called()
        self.assertIn("No API call was made", output.getvalue())


class CostGuardTest(unittest.TestCase):
    def test_calculates_ceiling_including_retries(self):
        """The conservative ceiling must include every configured retry."""
        config = {
            "max_output_tokens": 200,
            "max_api_retries": 1,
        }

        ceiling = _configured_output_token_ceiling(config, max_iterations=2)

        self.assertEqual(ceiling, 800)

    def test_generate_requires_explicit_run_ceiling(self):
        """Generation must stop locally when no run ceiling is supplied."""
        errors = io.StringIO()

        with (
            patch("sys.argv", ["agent.cli", "person", "--generate"]),
            patch("agent.cli.run_agent") as run_agent,
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as exit_result,
        ):
            main()

        self.assertEqual(exit_result.exception.code, 2)
        run_agent.assert_not_called()
        self.assertIn(
            "--generate requires --max-run-output-tokens",
            errors.getvalue(),
        )

    def test_generate_rejects_ceiling_below_configured_maximum(self):
        """A user ceiling below the configured worst case must block the run."""
        errors = io.StringIO()

        with (
            patch(
                "sys.argv",
                [
                    "agent.cli",
                    "person",
                    "--generate",
                    "--max-run-output-tokens",
                    "1",
                ],
            ),
            patch("agent.cli.run_agent") as run_agent,
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as exit_result,
        ):
            main()

        self.assertEqual(exit_result.exception.code, 2)
        run_agent.assert_not_called()
        self.assertIn(
            "Configured generation can use up to",
            errors.getvalue(),
        )


class UsageFormattingTest(unittest.TestCase):
    def test_formats_all_measured_usage_fields(self):
        """The CLI should distinguish token totals from their subsets."""
        usage = {
            "successful_api_responses": 2,
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "cache_write_input_tokens": 5,
            "output_tokens": 30,
            "reasoning_output_tokens": 10,
            "total_tokens": 130,
        }

        text = _format_usage(usage)

        self.assertIn("input=100", text)
        self.assertIn("cached=20", text)
        self.assertIn("cache_write=5", text)
        self.assertIn("output=30", text)
        self.assertIn("reasoning=10", text)
        self.assertIn("total=130", text)
        self.assertIn("responses=2", text)


class ReviewGateTest(unittest.TestCase):
    def test_generate_stops_before_api_when_reviews_are_pending(self):
        """Pending mapping decisions must block API-backed generation."""
        errors = io.StringIO()

        with (
            patch(
                "sys.argv",
                [
                    "agent.cli",
                    "person",
                    "--generate",
                    "--max-run-output-tokens",
                    "100000",
                ],
            ),
            patch("agent.cli.run_agent") as run_agent,
            patch(
                "agent.cli.pending_review_fields",
                return_value=("race_concept_id",),
            ),
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as exit_result,
        ):
            main()

        self.assertEqual(exit_result.exception.code, 2)
        run_agent.assert_not_called()
        self.assertIn(
            "Generation blocked by pending mapping reviews",
            errors.getvalue(),
        )
        self.assertIn("race_concept_id", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
