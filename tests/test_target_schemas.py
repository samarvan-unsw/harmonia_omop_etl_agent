import unittest
from pathlib import Path

import yaml

from agent.contracts import TargetSchemaDocument


class TargetSchemaCollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Load every generated OMOP 5.4 target schema."""
        project_root = Path(__file__).resolve().parents[1]
        cls.target_directory = project_root / "specs" / "target_schema"
        cls.documents = {}
        for path in sorted(cls.target_directory.glob("*.yml")):
            document = TargetSchemaDocument.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            cls.documents[document.target_table] = document

    def test_contains_every_official_table_and_field(self):
        """Pinned OMOP metadata contains 39 tables and 432 fields."""
        self.assertEqual(len(self.documents), 39)
        self.assertEqual(
            sum(len(document.fields) for document in self.documents.values()),
            432,
        )

    def test_filenames_match_target_tables(self):
        """The API resolves each target through its canonical filename."""
        for table_name in self.documents:
            self.assertTrue(
                (self.target_directory / f"{table_name}.yml").is_file()
            )

    def test_foreign_keys_resolve_to_known_fields(self):
        """Every official FK target must exist in the generated collection."""
        fields_by_table = {
            table_name: {field.name for field in document.fields}
            for table_name, document in self.documents.items()
        }
        for document in self.documents.values():
            for field in document.fields:
                if not field.foreign_key:
                    continue
                foreign_key = field.foreign_key
                self.assertIn(foreign_key.table, fields_by_table)
                self.assertIsNotNone(foreign_key.field)
                self.assertIn(
                    foreign_key.field,
                    fields_by_table[foreign_key.table],
                )

    def test_all_documents_are_omop_5_4(self):
        """Prevent mixed CDM versions in the agent-owned target catalog."""
        for document in self.documents.values():
            self.assertEqual(document.cdm_version, "5.4")
