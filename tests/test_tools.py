import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.providers.base import ToolCall
from agent.tools import (
    TOOL_SCHEMAS,
    _configured_output_dir,
    discard_candidate,
    dispatch,
    promote_file,
    read_file,
    write_file,
)


class OutputToolSafetyTest(unittest.TestCase):
    def setUp(self):
        """Redirect generated files to an isolated temporary directory."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name).resolve()
        self.output_patch = patch("agent.tools.OUTPUT_DIR", self.output_dir)
        self.output_patch.start()

    def tearDown(self):
        """Restore the real output location and remove temporary files."""
        self.output_patch.stop()
        self.temporary_directory.cleanup()

    def test_allows_top_level_sql_file(self):
        """A valid candidate should be readable without changing final output."""
        result = write_file("person.sql", "SELECT 1 AS person_id")

        self.assertTrue(result.startswith("Wrote "))
        self.assertEqual(read_file("person.sql"), "SELECT 1 AS person_id")
        self.assertFalse((self.output_dir / "person.sql").exists())

    def test_promotes_candidate_to_final_file(self):
        """Only an explicit promotion should replace the final SQL file."""
        final_file = self.output_dir / "person.sql"
        final_file.write_text("SELECT 'old'", encoding="utf-8")
        write_file("person.sql", "SELECT 'validated'")

        promote_file("person.sql")

        self.assertEqual(
            final_file.read_text(encoding="utf-8"),
            "SELECT 'validated'",
        )
        self.assertFalse((self.output_dir / ".person.default.candidate").exists())

    def test_discards_candidate_without_changing_final_file(self):
        """Discarding invalid SQL should preserve the last valid output."""
        final_file = self.output_dir / "person.sql"
        final_file.write_text("SELECT 'old'", encoding="utf-8")
        write_file("person.sql", "SELECT 'invalid'")

        discard_candidate("person.sql")

        self.assertEqual(final_file.read_text(encoding="utf-8"), "SELECT 'old'")
        self.assertEqual(read_file("person.sql"), "SELECT 'old'")

    def test_rejects_parent_directory_traversal(self):
        """A filename cannot escape through a parent-directory component."""
        with self.assertRaisesRegex(ValueError, "top-level SQL filename"):
            write_file("../person.sql", "SELECT 1")

    def test_rejects_nested_path(self):
        """Generated SQL must be placed directly in output."""
        with self.assertRaisesRegex(ValueError, "top-level SQL filename"):
            write_file("nested/person.sql", "SELECT 1")

    def test_rejects_non_sql_file(self):
        """The agent cannot create configuration or arbitrary text files."""
        with self.assertRaisesRegex(ValueError, "top-level SQL filename"):
            write_file("config.yaml", "provider: codex")

    def test_rejects_empty_sql(self):
        """Whitespace-only output is not a valid generated artifact."""
        with self.assertRaisesRegex(ValueError, "SQL content cannot be empty"):
            write_file("person.sql", "   ")

    def test_accepts_absolute_scratch_output_override(self):
        """Hosted functions may isolate candidates in writable scratch space."""
        scratch_dir = self.output_dir / "scratch"
        with patch.dict(
            os.environ,
            {"AGENT_OUTPUT_DIR": str(scratch_dir)},
        ):
            self.assertEqual(_configured_output_dir(), scratch_dir)

    def test_rejects_relative_or_broad_output_override(self):
        """A deployment variable cannot redirect writes to an unsafe target."""
        with (
            patch.dict(
                os.environ,
                {"AGENT_OUTPUT_DIR": "relative/output"},
            ),
            self.assertRaisesRegex(ValueError, "absolute path"),
        ):
            _configured_output_dir()

        with (
            patch.dict(os.environ, {"AGENT_OUTPUT_DIR": "/"}),
            self.assertRaisesRegex(ValueError, "too broad"),
        ):
            _configured_output_dir()

    def test_exposes_only_candidate_writes_to_the_model(self):
        """Generated or promoted SQL must not be readable through model tools."""
        self.assertEqual(
            [schema["name"] for schema in TOOL_SCHEMAS],
            ["write_file"],
        )

    def test_dispatch_rejects_model_read_attempts(self):
        """A fabricated read tool call must fail closed."""
        result = dispatch(
            ToolCall(
                id="call-1",
                name="read_file",
                arguments={"path": "person.sql"},
            )
        )

        self.assertEqual(result, "ERROR: unknown tool: read_file")


if __name__ == "__main__":
    unittest.main()
