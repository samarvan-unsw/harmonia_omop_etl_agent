import tempfile
import unittest
from pathlib import Path

import yaml

from agent.contracts import TargetSchemaDocument
from agent.output_artifacts import (
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
