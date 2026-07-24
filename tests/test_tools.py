import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.tools import (
    discard_candidate,
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


if __name__ == "__main__":
    unittest.main()
