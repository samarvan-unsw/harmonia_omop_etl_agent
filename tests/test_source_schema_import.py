"""Test deterministic normalization of source-schema metadata."""

import unittest

import yaml

from agent.contracts import SourceSchemaDocument
from agent.source_schema_import import (
    SourceSchemaImportError,
    normalize_source_schema_files,
)


class SourceSchemaImportTest(unittest.TestCase):
    def test_normalizes_dbt_metadata_and_relationships(self):
        content = """
version: 2
models:
  - name: patient
    description: Patient demographic record.
    meta:
      semantic:
        entity: Patient
        grain: One row per patient.
        primary_key:
          columns: [patient_id]
        column_catalog:
          - name: patient_id
            role: identifier
            semantic_type: identifier
            filterable: true
        referenced_by:
          - model: encounter
            field: patient_id
    columns:
      - name: patient_id
        description: Local patient identifier.
        meta:
          semantic:
            data_type: integer
            identifier_scope: hospital
            synonyms: [MRN, patient number]
            counting:
              default: count_distinct
            source:
              model: raw_patient
              column: person_id
      - name: sex
        data_type: varchar
        meta:
          semantic:
            terminology:
              status: local
              vocabulary: administrative sex
            allowed_values: [Male, Female, Unknown]
  - name: encounter
    columns:
      - name: encounter_id
        data_type: bigint
        tests: [unique, not_null]
      - name: patient_id
        data_type: integer
        tests:
          - relationships:
              arguments:
                to: ref('patient')
                field: patient_id
"""

        result = normalize_source_schema_files([("schema.yml", content)])

        self.assertEqual(result.model_count, 2)
        patient_document = next(
            item for item in result.documents if item.model_name == "patient"
        )
        encounter_document = next(
            item for item in result.documents if item.model_name == "encounter"
        )
        patient = SourceSchemaDocument.model_validate(
            yaml.safe_load(patient_document.content)
        ).models[0]
        encounter = SourceSchemaDocument.model_validate(
            yaml.safe_load(encounter_document.content)
        ).models[0]

        self.assertTrue(patient.columns[0].primary_key)
        self.assertEqual(patient.columns[0].data_type, "integer")
        self.assertEqual(patient.columns[0].semantic.role, "identifier")
        self.assertTrue(patient.columns[0].semantic.filterable)
        self.assertEqual(
            patient.columns[0].semantic.default_aggregation,
            "count_distinct",
        )
        self.assertEqual(patient.columns[0].semantic.source.field, "person_id")
        self.assertEqual(patient.columns[1].semantic.allowed_values[0], "Male")
        self.assertEqual(
            encounter.columns[1].foreign_key.model,
            "patient",
        )
        self.assertEqual(encounter.semantic.alternate_keys[0].columns, ["encounter_id"])
        self.assertEqual(patient_document.primary_key_count, 1)
        self.assertEqual(encounter_document.foreign_key_count, 1)
        self.assertTrue(
            any("tests" in path for path in encounter_document.ignored_paths)
        )

    def test_normalizes_dbt_source_tables_and_generic_fields(self):
        dbt_source = """
version: 2
sources:
  - name: ehr
    tables:
      - name: diagnosis
        description: Diagnoses from the source system.
        columns:
          - name: diagnosis_id
            datatype: NUMBER
            is_primary_key: true
"""
        generic = """
name: medication
comment: Medication records.
fields:
  - name: patient-id
    type: INTEGER
    foreign_key:
      table: patient
      column: patient_id
"""

        result = normalize_source_schema_files(
            [("sources.yaml", dbt_source), ("generic.yml", generic)]
        )

        self.assertEqual(result.model_count, 2)
        diagnosis = next(
            item for item in result.documents if item.model_name == "diagnosis"
        )
        medication = next(
            item for item in result.documents if item.model_name == "medication"
        )
        self.assertEqual(diagnosis.detected_format, "dbt_source")
        self.assertEqual(medication.detected_format, "generic_yaml")
        self.assertIn("name: patient_id", medication.content)
        self.assertTrue(any("Renamed" in warning for warning in medication.warnings))

    def test_rejects_duplicate_models_across_files(self):
        content = "version: 2\nmodels:\n- name: patient\n  columns: []\n"

        with self.assertRaisesRegex(
            SourceSchemaImportError,
            "declared more than once",
        ):
            normalize_source_schema_files(
                [("one.yml", content), ("two.yml", content)]
            )

    def test_rejects_unrecognised_document(self):
        with self.assertRaisesRegex(SourceSchemaImportError, "does not contain"):
            normalize_source_schema_files([("bad.yml", "version: 2\n")])


if __name__ == "__main__":
    unittest.main()
