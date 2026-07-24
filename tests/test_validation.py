import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from agent.validation import (
    SpecValidationError,
    pending_review_fields,
    validate_specs,
)


class MappingRuleValidationTest(unittest.TestCase):
    def setUp(self):
        """Create an isolated copy of the PERSON specifications."""
        project_root = Path(__file__).resolve().parents[1]
        source_specs = project_root / "specs"

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.specs_dir = Path(self.temporary_directory.name)

        for section, filename in (
            ("source_schema", "cai_01_patient.yml"),
            ("mappings", "person.yml"),
            ("target_schema", "person.yml"),
        ):
            destination = self.specs_dir / section
            destination.mkdir(parents=True)
            shutil.copy2(
                source_specs / section / filename,
                destination / filename,
            )

    def tearDown(self):
        """Remove temporary specification files."""
        self.temporary_directory.cleanup()

    def _remove_mapping(self, target_field: str) -> None:
        """Remove one target-field mapping from the temporary mapping."""
        mapping_path = self.specs_dir / "mappings" / "person.yml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        mapping["fields"] = [
            field
            for field in mapping["fields"]
            if field["target_field"] != target_field
        ]
        mapping_path.write_text(
            yaml.safe_dump(mapping, sort_keys=False),
            encoding="utf-8",
        )

    def _replace_source_model(self, model_name: str) -> None:
        """Replace the declared model name in the temporary mapping."""
        mapping_path = self.specs_dir / "mappings" / "person.yml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        mapping["source_models"] = [model_name]
        mapping_path.write_text(
            yaml.safe_dump(mapping, sort_keys=False),
            encoding="utf-8",
        )

    def test_missing_optional_field_is_allowed(self):
        """An omitted optional target field should not fail validation."""
        result = validate_specs("person", self.specs_dir)
        mapped_fields = {
            field.target_field for field in result.mapping.fields
        }

        self.assertNotIn("location_id", mapped_fields)

    def test_missing_required_field_is_rejected(self):
        """An omitted required target field should fail validation."""
        self._remove_mapping("person_id")

        with self.assertRaisesRegex(
            SpecValidationError,
            "Required OMOP fields are not mapped: person_id",
        ):
            validate_specs("person", self.specs_dir)

    def test_required_field_explicitly_mapped_to_null_is_allowed(self):
        """A deliberate null action should count as a required-field mapping."""
        result = validate_specs("person", self.specs_dir)
        ethnicity_mapping = next(
            field
            for field in result.mapping.fields
            if field.target_field == "ethnicity_concept_id"
        )

        self.assertEqual(ethnicity_mapping.action, "null")

    def test_pending_review_fields_are_reported(self):
        """Structurally valid pending reviews should remain visible."""
        mapping_path = self.specs_dir / "mappings" / "person.yml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        race_mapping = next(
            field
            for field in mapping["fields"]
            if field["target_field"] == "race_concept_id"
        )
        race_mapping["review_status"] = "pending"
        mapping_path.write_text(
            yaml.safe_dump(mapping, sort_keys=False),
            encoding="utf-8",
        )

        result = validate_specs("person", self.specs_dir)

        self.assertEqual(
            pending_review_fields(result),
            ("race_concept_id",),
        )

    def test_invents_name_for_explicit_null_mapping_table(self):
        """An explicit null lookup name should receive a deterministic name."""
        result = validate_specs("person", self.specs_dir)
        mappings = {
            field.target_field: field
            for field in result.mapping.fields
        }

        self.assertEqual(
            mappings["gender_concept_id"].mapping_table_name,
            "mapping_person_gender_concept_id",
        )
        self.assertEqual(
            mappings["race_concept_id"].mapping_table_name,
            "mapping_person_race_concept_id",
        )
        self.assertIsNone(mappings["person_id"].mapping_table_name)

    def test_invents_name_for_explicit_blank_mapping_table(self):
        """A blank lookup name should behave like an explicit YAML null."""
        mapping_path = self.specs_dir / "mappings" / "person.yml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        race_mapping = next(
            field
            for field in mapping["fields"]
            if field["target_field"] == "race_concept_id"
        )
        race_mapping["mapping_table_name"] = "   "
        mapping_path.write_text(
            yaml.safe_dump(mapping, sort_keys=False),
            encoding="utf-8",
        )

        result = validate_specs("person", self.specs_dir)
        validated_race_mapping = next(
            field
            for field in result.mapping.fields
            if field.target_field == "race_concept_id"
        )

        self.assertEqual(
            validated_race_mapping.mapping_table_name,
            "mapping_person_race_concept_id",
        )

    def test_review_required_without_status_is_rejected(self):
        """Every required review must explicitly be pending or approved."""
        mapping_path = self.specs_dir / "mappings" / "person.yml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        del mapping["fields"][1]["review_status"]
        mapping_path.write_text(
            yaml.safe_dump(mapping, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SpecValidationError,
            "review_required requires review_status",
        ):
            validate_specs("person", self.specs_dir)

    def test_rejects_unsafe_omop_table_name(self):
        """A table argument cannot contain path or traversal characters."""
        with self.assertRaisesRegex(
            SpecValidationError,
            "OMOP table must use lowercase letters",
        ):
            validate_specs("../person", self.specs_dir)

    def test_rejects_unsafe_source_model_name(self):
        """A mapping cannot use a source model name as a filesystem path."""
        self._replace_source_model("../cai_01_patient")

        with self.assertRaisesRegex(
            SpecValidationError,
            "Invalid mapping contract",
        ):
            validate_specs("person", self.specs_dir)


if __name__ == "__main__":
    unittest.main()
