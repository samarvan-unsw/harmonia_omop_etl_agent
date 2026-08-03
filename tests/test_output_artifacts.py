import tempfile
import unittest
from pathlib import Path

import yaml

from agent.contracts import TargetSchemaDocument
from agent.output_artifacts import (
    build_dbt_schema_artifacts,
    build_ddl_artifacts,
    build_output_artifacts,
    write_local_artifacts,
)


class OutputArtifactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target_schema = TargetSchemaDocument.model_validate(
            yaml.safe_load(
                (
                    Path(__file__).resolve().parent.parent
                    / "specs"
                    / "target_schema"
                    / "person.yml"
                ).read_text(encoding="utf-8")
            )
        )

    def test_dbt_output_contains_sql_and_enforced_model_contract(self):
        artifacts = build_output_artifacts(
            generated_sql="select 1 as person_id",
            target_schema=self.target_schema,
            output_format="dbt",
            dialect="snowflake",
        )

        self.assertEqual(
            [artifact.file_name for artifact in artifacts],
            ["person.sql", "person.yml"],
        )
        contract = yaml.safe_load(artifacts[1].content)
        model = contract["models"][0]
        self.assertEqual(model["name"], "person")
        self.assertEqual(model["config"]["materialized"], "table")
        self.assertTrue(model["config"]["contract"]["enforced"])
        self.assertEqual(
            model["columns"][0]["constraints"],
            [{"type": "not_null"}, {"type": "primary_key"}],
        )

    def test_plain_sql_output_contains_transformation_and_static_ddl(self):
        artifacts = build_output_artifacts(
            generated_sql="select 1 as person_id",
            target_schema=self.target_schema,
            output_format="sql",
            dialect="postgres",
        )
        by_name = {
            artifact.file_name: artifact.content for artifact in artifacts
        }

        self.assertEqual(
            set(by_name),
            {
                "person.sql",
                "create_tables.sql",
                "primary_keys.sql",
                "foreign_keys.sql",
                "indexes.sql",
            },
        )
        self.assertIn(
            "CREATE TABLE @cdmDatabaseSchema.person",
            by_name["create_tables.sql"],
        )
        self.assertIn(
            "PRIMARY KEY (person_id)",
            by_name["primary_keys.sql"],
        )
        self.assertIn(
            "REFERENCES @cdmDatabaseSchema.CONCEPT (CONCEPT_ID)",
            by_name["foreign_keys.sql"],
        )
        self.assertIn(
            "@cdmDatabaseSchema.person (gender_concept_id ASC)",
            by_name["indexes.sql"],
        )

    def test_standalone_ddl_bundle_has_no_transformation_artifact(self):
        artifacts = build_ddl_artifacts(dialect="snowflake")

        self.assertEqual(
            [artifact.file_name for artifact in artifacts],
            [
                "create_tables.sql",
                "primary_keys.sql",
                "foreign_keys.sql",
                "indexes.sql",
            ],
        )
        self.assertTrue(
            all(artifact.category == "ddl" for artifact in artifacts)
        )

    def test_standalone_dbt_bundle_has_one_contract_per_target(self):
        artifacts = build_dbt_schema_artifacts(dialect="bigquery")

        self.assertEqual(len(artifacts), 39)
        self.assertEqual(artifacts[0].file_name, "person.yml")
        self.assertTrue(
            all(
                artifact.category == "dbt_contract"
                for artifact in artifacts
            )
        )
        person_contract = yaml.safe_load(artifacts[0].content)
        self.assertEqual(
            person_contract["models"][0]["name"],
            "person",
        )
        self.assertEqual(
            person_contract["models"][0]["columns"][0]["data_type"],
            "INT64",
        )

    def test_standalone_dbt_bundle_can_filter_target_tables(self):
        artifacts = build_dbt_schema_artifacts(
            dialect="postgres",
            target_tables={"visit_occurrence", "person"},
        )

        self.assertEqual(
            [artifact.file_name for artifact in artifacts],
            ["person.yml", "visit_occurrence.yml"],
        )

    def test_standalone_dbt_bundle_rejects_unknown_target_table(self):
        with self.assertRaisesRegex(
            ValueError,
            "Unknown OMOP target tables: imaginary_table",
        ):
            build_dbt_schema_artifacts(
                dialect="postgres",
                target_tables={"imaginary_table"},
            )

    def test_athena_ddl_explains_unsupported_constraints_and_indexes(self):
        artifacts = build_output_artifacts(
            generated_sql="select 1 as person_id",
            target_schema=self.target_schema,
            output_format="sql",
            dialect="athena",
        )
        by_name = {
            artifact.file_name: artifact.content for artifact in artifacts
        }

        self.assertIn(
            "does not support primary-key constraints",
            by_name["primary_keys.sql"],
        )
        self.assertIn(
            "CREATE EXTERNAL TABLE IF NOT EXISTS person",
            by_name["create_tables.sql"],
        )
        self.assertIn(
            "Add each environment-specific S3 LOCATION",
            by_name["create_tables.sql"],
        )
        self.assertIn(
            "does not support conventional secondary indexes",
            by_name["indexes.sql"],
        )

    def test_new_dialects_build_deterministic_schema_bundles(self):
        expected_types = {
            "sql_server": "person_id INT NOT NULL",
            "spark": "person_id INT",
            "oracle": "person_id INTEGER NOT NULL",
            "redshift": "person_id INTEGER NOT NULL",
            "synapse": "person_id INT NOT NULL",
        }

        for dialect, expected_type in expected_types.items():
            with self.subTest(dialect=dialect):
                artifacts = build_ddl_artifacts(dialect=dialect)
                by_name = {
                    artifact.file_name: artifact.content
                    for artifact in artifacts
                }

                self.assertEqual(
                    set(by_name),
                    {
                        "create_tables.sql",
                        "primary_keys.sql",
                        "foreign_keys.sql",
                        "indexes.sql",
                    },
                )
                self.assertIn(expected_type, by_name["create_tables.sql"])

    def test_new_dialects_build_typed_dbt_contracts(self):
        expected_types = {
            "sql_server": "INT",
            "spark": "INT",
            "oracle": "INTEGER",
            "redshift": "INTEGER",
            "synapse": "INT",
        }

        for dialect, expected_type in expected_types.items():
            with self.subTest(dialect=dialect):
                artifact = build_dbt_schema_artifacts(
                    dialect=dialect,
                    target_tables={"person"},
                )[0]
                contract = yaml.safe_load(artifact.content)

                self.assertEqual(
                    contract["models"][0]["columns"][0]["data_type"],
                    expected_type,
                )

    def test_local_writer_separates_ddl_from_dbt_contracts(self):
        dbt_artifacts = build_output_artifacts(
            generated_sql="select 1 as person_id",
            target_schema=self.target_schema,
            output_format="dbt",
            dialect="postgres",
        )
        sql_artifacts = build_output_artifacts(
            generated_sql="select 1 as person_id",
            target_schema=self.target_schema,
            output_format="sql",
            dialect="postgres",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            write_local_artifacts(dbt_artifacts, output_dir=output_dir)
            write_local_artifacts(sql_artifacts, output_dir=output_dir)

            self.assertTrue((output_dir / "person.yml").is_file())
            self.assertTrue(
                (output_dir / "ddl" / "create_tables.sql").is_file()
            )
            self.assertFalse((output_dir / "person.sql").exists())


if __name__ == "__main__":
    unittest.main()
