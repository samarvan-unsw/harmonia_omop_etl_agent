import copy
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

from agent.contracts import AgentConfig
from agent.preflight import (
    build_generation_preflight,
    configured_output_token_ceiling,
    generation_readiness_blockers,
)
from agent.validation import validate_specs


class GenerationPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        cls.specs = copy.deepcopy(
            validate_specs("person", project_root / "specs")
        )
        # Exercise the review blocker independently of the maintained
        # mapping's current approval status.
        for field_mapping in cls.specs.mapping.fields:
            if field_mapping.target_field == "race_concept_id":
                field_mapping.review_required = True
                field_mapping.review_status = "pending"
        raw_config = yaml.safe_load(
            (project_root / "config.yaml").read_text(encoding="utf-8")
        )
        cls.config = AgentConfig.model_validate(raw_config).model_dump()

    def test_calculates_the_same_bounded_request_information_as_cli(self):
        result = build_generation_preflight(
            "person",
            self.specs,
            self.config,
            max_iterations=2,
        )

        self.assertGreater(result.context_characters, 0)
        self.assertGreater(result.initial_request_characters, 0)
        self.assertGreater(result.estimated_initial_input_tokens, 0)
        self.assertGreater(
            result.estimated_maximum_input_tokens,
            result.estimated_initial_input_tokens,
        )
        self.assertGreater(result.estimated_maximum_cost_usd, 0)
        self.assertEqual(result.output_token_ceiling, 1600)
        self.assertIn("race_concept_id", result.pending_reviews)
        self.assertFalse(result.generation_ready)

    def test_reports_exact_actionable_readiness_blockers(self):
        result = build_generation_preflight(
            "person",
            self.specs,
            self.config,
            max_iterations=2,
        )
        limited = replace(
            result,
            input_limit_exceeded=True,
            maximum_initial_prompt_characters=(
                result.initial_request_characters - 100
            ),
        )

        blockers = generation_readiness_blockers(limited)

        self.assertIn("race_concept_id", blockers[0])
        self.assertIn(
            str(result.initial_request_characters),
            blockers[1],
        )
        self.assertIn("by 100", blockers[1])

    def test_rejects_non_positive_generation_attempts(self):
        with self.assertRaisesRegex(
            ValueError,
            "max_iterations must be greater than zero",
        ):
            configured_output_token_ceiling(
                self.config,
                max_iterations=0,
            )


if __name__ == "__main__":
    unittest.main()
