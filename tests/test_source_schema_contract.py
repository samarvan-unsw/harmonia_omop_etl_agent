import unittest

from pydantic import ValidationError

from agent.contracts import SourceSchemaDocument


class SourceSchemaContractTest(unittest.TestCase):
    def test_accepts_declared_foreign_key(self):
        """Source columns may identify the model and field they reference."""
        document = SourceSchemaDocument.model_validate(
            {
                "version": 2,
                "models": [
                    {
                        "name": "encounter",
                        "columns": [
                            {
                                "name": "patient_id",
                                "foreign_key": {
                                    "model": "patient",
                                    "field": "patient_id",
                                },
                            }
                        ],
                    }
                ],
            }
        )

        foreign_key = document.models[0].columns[0].foreign_key

        self.assertIsNotNone(foreign_key)
        self.assertEqual(foreign_key.model, "patient")
        self.assertEqual(foreign_key.field, "patient_id")

    def test_rejects_invalid_foreign_key_identifier(self):
        """Foreign-key targets follow the source identifier contract."""
        with self.assertRaises(ValidationError):
            SourceSchemaDocument.model_validate(
                {
                    "version": 2,
                    "models": [
                        {
                            "name": "encounter",
                            "columns": [
                                {
                                    "name": "patient_id",
                                    "foreign_key": {
                                        "model": "patient table",
                                        "field": "patient_id",
                                    },
                                }
                            ],
                        }
                    ],
                }
            )

    def test_rejects_unsupported_contract_version(self):
        """Only the documented source-schema contract version is accepted."""
        with self.assertRaises(ValidationError):
            SourceSchemaDocument.model_validate(
                {"version": 1, "models": [{"name": "patient"}]}
            )

    def test_rejects_duplicate_models(self):
        """A document cannot silently redefine a source model."""
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            SourceSchemaDocument.model_validate(
                {
                    "version": 2,
                    "models": [
                        {"name": "patient"},
                        {"name": "patient"},
                    ],
                }
            )

    def test_rejects_duplicate_columns(self):
        """A model cannot silently redefine a source field."""
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            SourceSchemaDocument.model_validate(
                {
                    "version": 2,
                    "models": [
                        {
                            "name": "patient",
                            "columns": [
                                {"name": "patient_id"},
                                {"name": "patient_id"},
                            ],
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
