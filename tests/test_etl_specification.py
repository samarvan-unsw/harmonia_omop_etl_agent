"""Verify deterministic ETL specification content and file renderers."""

import unittest
from io import BytesIO
from pathlib import Path

import yaml
from docx import Document

from agent.etl_specification import (
    build_etl_specification,
    build_etl_specification_document,
)
from agent.validation import validate_spec_contents, validate_specs


class EtlSpecificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.specs = validate_specs("person", cls.root / "specs")

    def test_document_uses_target_order_and_explicit_nulls(self):
        document = build_etl_specification_document(self.specs)

        self.assertEqual(document.target_table, "person")
        self.assertEqual(document.cdm_version, "5.4")
        self.assertEqual(
            [row.destination_field for row in document.rows],
            [field.name for field in self.specs.target_schema.fields],
        )
        person_id = document.rows[0]
        self.assertEqual(
            person_id.source_field,
            "cai_01_patient.patient_id",
        )
        self.assertIn("Cast patient_id", person_id.logic)
        location = next(
            row
            for row in document.rows
            if row.destination_field == "location_id"
        )
        self.assertEqual(location.source_field, "—")
        self.assertIn("Set NULL", location.logic)

    def test_mapping_notes_comments_and_change_log_are_documented(self):
        mapping = yaml.safe_load(
            (self.root / "specs" / "mappings" / "person.yml").read_text(
                encoding="utf-8"
            )
        )
        mapping["notes"] = "One row per reconciled source patient."
        mapping["fields"][0]["comment"] = "Preserve source lineage."
        mapping["change_log"] = [
            {
                "date": "2026-08-04",
                "description": "Added ETL documentation metadata.",
                "author": "Data team",
            }
        ]
        specs = validate_spec_contents(
            "person",
            yaml.safe_dump(mapping, sort_keys=False),
            {
                "cai_01_patient.yml": (
                    self.root
                    / "specs"
                    / "source_schema"
                    / "cai_01_patient.yml"
                ).read_text(encoding="utf-8")
            },
            (
                self.root / "specs" / "target_schema" / "person.yml"
            ).read_text(encoding="utf-8"),
        )

        document = build_etl_specification_document(specs)

        self.assertEqual(
            document.table_notes,
            "One row per reconciled source patient.",
        )
        self.assertIn("Preserve source lineage.", document.rows[0].comment)
        self.assertEqual(
            document.changes,
            ((
                "2026-08-04",
                "Added ETL documentation metadata.",
                "Data team",
            ),),
        )

    def test_renders_markdown_word_and_pdf_without_ai(self):
        markdown = build_etl_specification(self.specs, "md")
        word = build_etl_specification(self.specs, "docx")
        pdf = build_etl_specification(self.specs, "pdf")

        self.assertEqual(markdown.file_name, "person_etl_specification.md")
        markdown_text = markdown.content.decode("utf-8")
        self.assertIn("```mermaid", markdown_text)
        self.assertIn(
            "| Destination Field | Source field | Logic | Comment field |",
            markdown_text,
        )
        self.assertIn("## Change log", markdown_text)

        self.assertTrue(word.content.startswith(b"PK\x03\x04"))
        word_document = Document(BytesIO(word.content))
        self.assertEqual(
            word_document.paragraphs[0].text,
            "person ETL specification",
        )
        self.assertGreaterEqual(len(word_document.tables), 2)
        self.assertEqual(
            [cell.text for cell in word_document.tables[0].rows[0].cells],
            [
                "Destination Field",
                "Source field",
                "Logic",
                "Comment field",
            ],
        )
        self.assertEqual(
            word.content,
            build_etl_specification(self.specs, "docx").content,
        )

        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(
            pdf.content,
            build_etl_specification(self.specs, "pdf").content,
        )


if __name__ == "__main__":
    unittest.main()
