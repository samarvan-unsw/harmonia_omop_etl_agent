import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import yaml
from httpx import ASGITransport, AsyncClient

from agent.api import app


class ValidationApiTest(unittest.IsolatedAsyncioTestCase):
    API_TOKEN = "test-agent-api-token-that-is-at-least-32-characters"

    @classmethod
    def setUpClass(cls):
        """Load known-good local specifications once for API tests."""
        project_root = Path(__file__).resolve().parents[1]
        specs_dir = project_root / "specs"
        mapping_content = (
            specs_dir / "mappings" / "person.yml"
        ).read_text(encoding="utf-8")
        mapping = yaml.safe_load(mapping_content)
        for field_mapping in mapping["fields"]:
            # Keep review-gate tests deterministic even when the maintained
            # project mapping has already been approved by a user.
            if field_mapping["target_field"] == "race_concept_id":
                field_mapping["review_required"] = True
                field_mapping["review_status"] = "pending"
            if (
                field_mapping.get("review_required")
                and not field_mapping.get("review_comment")
            ):
                field_mapping["review_comment"] = (
                    "Approved API test fixture review."
                )
        cls.mapping_content = yaml.safe_dump(mapping, sort_keys=False)
        cls.source_content = (
            specs_dir / "source_schema" / "cai_01_patient.yml"
        ).read_text(encoding="utf-8")

    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.payload = {
            "omop_table": "person",
            "mapping": {
                "file_name": "person.yml",
                "content": self.mapping_content,
            },
            "source_schemas": [
                {
                    "file_name": "cai_01_patient.yml",
                    "content": self.source_content,
                }
            ],
        }
        self.headers = {
            "Authorization": f"Bearer {self.API_TOKEN}",
        }

    async def asyncTearDown(self):
        await self.client.aclose()

    def approve_mapping_reviews(self):
        """Make the request eligible for generation without changing files."""
        mapping = yaml.safe_load(self.mapping_content)
        for field_mapping in mapping["fields"]:
            if field_mapping.get("review_required"):
                field_mapping["review_status"] = "approved"
        self.payload["mapping"]["content"] = yaml.safe_dump(
            mapping,
            sort_keys=False,
        )

    async def test_health_does_not_require_authentication(self):
        response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    async def test_target_catalog_requires_bearer_token(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get("/v1/target-schemas")

        self.assertEqual(response.status_code, 401)

    async def test_lists_safe_project_generation_options(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/generation-options",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(
            result["allowed_models"],
            [
                "gpt-5.3-codex",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.6-sol",
            ],
        )
        self.assertEqual(
            result["maximum_output_tokens_per_request"],
            4000,
        )
        self.assertEqual(
            result["maximum_initial_prompt_characters"],
            50000,
        )

    async def test_lists_complete_target_catalog_as_json(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/target-schemas",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["cdm_version"], "5.4")
        self.assertEqual(result["table_count"], 39)
        self.assertEqual(result["field_count"], 432)
        self.assertEqual(len(result["tables"]), 39)
        self.assertEqual(
            [table["display_order"] for table in result["tables"]],
            list(range(1, 40)),
        )
        self.assertEqual(result["tables"][0]["target_table"], "person")
        person = next(
            table
            for table in result["tables"]
            if table["target_table"] == "person"
        )
        self.assertEqual(person["field_count"], 18)
        self.assertEqual(person["cdm_schema"], "CDM")

    async def test_downloads_deterministic_ddl_without_generation(self):
        with (
            patch.dict(
                os.environ,
                {"AGENT_API_TOKEN": self.API_TOKEN},
            ),
            patch("agent.api.run_agent_with_specs") as run_agent,
        ):
            first = await self.client.get(
                "/v1/ddl/postgres",
                headers=self.headers,
            )
            second = await self.client.get(
                "/v1/ddl/postgres",
                headers=self.headers,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            first.headers["content-type"],
            "application/zip",
        )
        self.assertIn(
            "omop_cdm_5_4_postgres_ddl.zip",
            first.headers["content-disposition"],
        )
        self.assertEqual(first.content, second.content)
        with ZipFile(BytesIO(first.content)) as archive:
            self.assertEqual(
                archive.namelist(),
                [
                    "create_tables.sql",
                    "primary_keys.sql",
                    "foreign_keys.sql",
                    "indexes.sql",
                ],
            )
            self.assertIn(
                "CREATE TABLE @cdmDatabaseSchema.person",
                archive.read("create_tables.sql").decode("utf-8"),
            )
        run_agent.assert_not_called()

    async def test_ddl_download_requires_bearer_token(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get("/v1/ddl/postgres")

        self.assertEqual(response.status_code, 401)

    async def test_downloads_complete_dbt_contract_bundle_without_generation(
        self,
    ):
        with (
            patch.dict(
                os.environ,
                {"AGENT_API_TOKEN": self.API_TOKEN},
            ),
            patch("agent.api.run_agent_with_specs") as run_agent,
        ):
            response = await self.client.get(
                "/v1/schema-bundle/dbt/bigquery",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "omop_cdm_5_4_bigquery_dbt_contracts.zip",
            response.headers["content-disposition"],
        )
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 39)
            self.assertEqual(archive.namelist()[0], "person.yml")
            contract = yaml.safe_load(
                archive.read("person.yml").decode("utf-8")
            )
            self.assertEqual(
                contract["models"][0]["columns"][0]["data_type"],
                "INT64",
            )
        run_agent.assert_not_called()

    async def test_downloads_selected_dbt_contracts_in_cdm_order(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                (
                    "/v1/schema-bundle/dbt/postgres"
                    "?tables=visit_occurrence&tables=person"
                ),
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(
                archive.namelist(),
                ["person.yml", "visit_occurrence.yml"],
            )

    async def test_dbt_bundle_rejects_unknown_target_table(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/schema-bundle/dbt/postgres?tables=imaginary_table",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)

    async def test_ddl_download_rejects_unknown_dialect(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/ddl/oracle",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)

    async def test_schema_bundle_rejects_unknown_output_format(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/schema-bundle/csv/postgres",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)

    async def test_returns_structured_target_schema_details(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/target-schemas/person",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["target_table"], "person")
        self.assertEqual(result["display_order"], 1)
        self.assertEqual(len(result["fields"]), 18)
        gender_concept = next(
            field
            for field in result["fields"]
            if field["name"] == "gender_concept_id"
        )
        self.assertEqual(
            gender_concept["foreign_key"],
            {
                "table": "concept",
                "field": "concept_id",
                "domain": "Gender",
                "class_name": None,
            },
        )
        self.assertNotIn("version", result)

    async def test_unknown_target_schema_returns_not_found(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.get(
                "/v1/target-schemas/not_a_table",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 404)

    async def test_validation_requires_bearer_token(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/validate",
                json=self.payload,
            )

        self.assertEqual(response.status_code, 401)

    async def test_validation_refuses_unconfigured_authentication(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_API_TOKEN", None)
            response = await self.client.post(
                "/v1/validate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 503)

    async def test_validates_without_calling_openai(self):
        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/validate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["valid"])
        self.assertEqual(result["omop_table"], "person")
        self.assertEqual(result["source_models"], ["cai_01_patient"])
        self.assertEqual(result["target_field_count"], 18)
        self.assertFalse(result["generation_ready"])
        self.assertIn("race_concept_id", result["pending_reviews"])

    async def test_reports_cross_file_validation_failure(self):
        mapping = yaml.safe_load(self.mapping_content)
        mapping["fields"][0]["source_fields"][0]["field"] = "unknown_field"
        self.payload["mapping"]["content"] = yaml.safe_dump(
            mapping,
            sort_keys=False,
        )

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/validate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["valid"])
        self.assertIn(
            "Mapping references unknown source fields",
            result["errors"][0],
        )

    async def test_preflight_reports_cost_ceiling_without_openai(self):
        self.payload["max_iterations"] = 2

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/preflight",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["valid"])
        self.assertFalse(result["generation_ready"])
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["model"], "gpt-5.3-codex")
        self.assertEqual(
            result["maximum_output_tokens_per_request"],
            800,
        )
        self.assertEqual(result["maximum_generation_attempts"], 2)
        self.assertEqual(result["worst_case_output_tokens"], 1600)
        self.assertGreater(result["initial_request_characters"], 0)
        self.assertGreater(result["estimated_initial_input_tokens"], 0)
        self.assertGreater(
            result["estimated_maximum_input_tokens"],
            result["estimated_initial_input_tokens"],
        )
        self.assertGreater(result["estimated_maximum_cost_usd"], 0)
        self.assertEqual(result["cost_currency"], "USD")
        self.assertEqual(result["pricing_verified_on"], "2026-07-30")
        self.assertIn("Pending mapping reviews", result["blockers"][0])

    async def test_preflight_applies_safe_project_generation_settings(self):
        self.payload["max_iterations"] = 1
        self.payload["generation_settings"] = {
            "sql_dialect": "postgres",
            "output_format": "sql",
            "source_reference_style": "relation",
            "source_name": None,
            "model": "gpt-5.3-codex",
            "maximum_output_tokens_per_request": 600,
            "maximum_initial_prompt_characters": 30000,
            "automatic_api_retries": 1,
        }

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/preflight",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["sql_dialect"], "postgres")
        self.assertEqual(result["output_format"], "sql")
        self.assertEqual(result["source_reference_style"], "relation")
        self.assertEqual(result["model"], "gpt-5.3-codex")
        self.assertEqual(
            result["maximum_output_tokens_per_request"],
            600,
        )
        self.assertEqual(result["automatic_api_retries"], 1)
        self.assertEqual(result["maximum_generation_attempts"], 1)
        self.assertEqual(result["worst_case_output_tokens"], 1200)
        self.assertEqual(
            result["maximum_initial_prompt_characters"],
            30000,
        )

    async def test_preflight_rejects_project_setting_above_agent_limit(self):
        self.payload["generation_settings"] = {
            "sql_dialect": "postgres",
            "output_format": "sql",
            "source_reference_style": "relation",
            "source_name": None,
            "maximum_output_tokens_per_request": 4001,
        }

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/preflight",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["valid"])
        self.assertIn("agent limit of 4000", result["errors"][0])

    async def test_rejects_incompatible_project_generation_settings(self):
        self.payload["generation_settings"] = {
            "sql_dialect": "snowflake",
            "output_format": "sql",
            "source_reference_style": "dbt_ref",
            "source_name": None,
        }

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/preflight",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)

    async def test_generation_requires_the_current_confirmed_ceiling(self):
        self.approve_mapping_reviews()
        self.payload["confirmed_output_token_ceiling"] = 400

        with (
            patch.dict(
                os.environ,
                {"AGENT_API_TOKEN": self.API_TOKEN},
            ),
            patch("agent.api.run_agent_with_specs") as run_agent,
        ):
            response = await self.client.post(
                "/v1/generate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["completed"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["output_token_ceiling"], 1600)
        self.assertIn("Run preflight again", result["errors"][0])
        run_agent.assert_not_called()

    async def test_generation_returns_only_validated_sql_and_usage(self):
        self.approve_mapping_reviews()
        self.payload["confirmed_output_token_ceiling"] = 1600
        self.payload["generation_settings"] = {
            "sql_dialect": "postgres",
            "output_format": "sql",
            "source_reference_style": "relation",
            "source_name": None,
        }
        generated_result = {
            "status": "done",
            "iterations": 1,
            "output_sql": "select 1 as person_id",
            "output_artifacts": [
                {
                    "file_name": "person.sql",
                    "content": "select 1 as person_id",
                    "media_type": "application/sql",
                    "category": "transformation",
                },
                {
                    "file_name": "create_tables.sql",
                    "content": "create table person (person_id integer);",
                    "media_type": "application/sql",
                    "category": "ddl",
                },
            ],
            "usage": {
                "successful_api_responses": 1,
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "cache_write_input_tokens": 0,
                "output_tokens": 30,
                "reasoning_output_tokens": 10,
                "total_tokens": 130,
            },
            "transcript": [{"content": "must not be returned"}],
        }

        with (
            patch.dict(
                os.environ,
                {"AGENT_API_TOKEN": self.API_TOKEN},
            ),
            patch(
                "agent.api.run_agent_with_specs",
                return_value=generated_result,
            ) as run_agent,
        ):
            response = await self.client.post(
                "/v1/generate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["completed"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["output_sql"], "select 1 as person_id")
        self.assertEqual(
            [item["file_name"] for item in result["output_artifacts"]],
            ["person.sql", "create_tables.sql"],
        )
        self.assertEqual(result["usage"]["total_tokens"], 130)
        self.assertEqual(result["estimated_actual_cost_usd"], 0.0005635)
        self.assertGreater(result["estimated_maximum_cost_usd"], 0)
        self.assertEqual(result["cost_currency"], "USD")
        self.assertEqual(result["pricing_verified_on"], "2026-07-30")
        self.assertNotIn("transcript", result)
        run_agent.assert_called_once()
        self.assertFalse(
            run_agent.call_args.kwargs["promote_output"],
        )
        self.assertEqual(
            run_agent.call_args.kwargs["config"]["output"]["dialect"],
            "postgres",
        )

    async def test_generation_returns_bounded_validator_diagnostics(self):
        self.approve_mapping_reviews()
        self.payload["confirmed_output_token_ceiling"] = 1600
        failed_result = {
            "status": "max_iterations_reached",
            "iterations": 2,
            "diagnostics": [
                "missing target fields: person_source_value",
                "person_id uses undeclared source fields",
            ],
            "usage": {
                "successful_api_responses": 2,
                "input_tokens": 200,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 80,
                "reasoning_output_tokens": 0,
                "total_tokens": 280,
            },
            "transcript": [{"content": "must not be returned"}],
        }

        with (
            patch.dict(
                os.environ,
                {"AGENT_API_TOKEN": self.API_TOKEN},
            ),
            patch(
                "agent.api.run_agent_with_specs",
                return_value=failed_result,
            ),
        ):
            response = await self.client.post(
                "/v1/generate",
                json=self.payload,
                headers=self.headers,
            )

        result = response.json()
        self.assertFalse(result["completed"])
        self.assertEqual(
            result["status"],
            "max_iterations_reached",
        )
        self.assertEqual(result["errors"], failed_result["diagnostics"])
        self.assertNotIn("transcript", result)

    async def test_request_errors_do_not_echo_submitted_values(self):
        secret_value = "do-not-echo-this-sensitive-value"
        self.payload["unexpected"] = secret_value

        with patch.dict(
            os.environ,
            {"AGENT_API_TOKEN": self.API_TOKEN},
        ):
            response = await self.client.post(
                "/v1/validate",
                json=self.payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret_value, response.text)


if __name__ == "__main__":
    unittest.main()
