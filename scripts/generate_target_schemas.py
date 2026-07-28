"""Generate OMOP CDM 5.4 target-schema YAML from pinned OHDSI metadata."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.contracts import TargetSchemaDocument  # noqa: E402


SOURCE_REPOSITORY = "https://github.com/OHDSI/CommonDataModel"
SOURCE_COMMIT = "f853f6e39c61b4eb8b3e5287fd573a1ced36c0e4"
SOURCE_BASE_URL = (
    "https://raw.githubusercontent.com/OHDSI/CommonDataModel/"
    f"{SOURCE_COMMIT}/inst/csv"
)
FIELD_FILE = "OMOP_CDMv5.4_Field_Level.csv"
TABLE_FILE = "OMOP_CDMv5.4_Table_Level.csv"
EXPECTED_SHA256 = {
    FIELD_FILE: "04fff2823b78ec3b1d9d8696c7573358a3df1dfd88829a3b9e39601dbefdf7d3",
    TABLE_FILE: "76942316bb64861757aa35624225015ed927e7c515141e56186eeb745a540bb1",
}
EXPECTED_TABLE_COUNT = 39
EXPECTED_FIELD_COUNT = 432
TARGET_DIRECTORY = PROJECT_ROOT / "specs" / "target_schema"


class FoldedString(str):
    """Text rendered as a readable folded YAML block."""


class TargetSchemaDumper(yaml.SafeDumper):
    """Safe YAML dumper with readable long-form metadata."""


TargetSchemaDumper.add_representer(
    FoldedString,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str",
        value,
        style=">",
    ),
)


def _download_csv(file_name: str) -> list[dict[str, str]]:
    """Download and verify one immutable metadata source."""
    request = Request(
        f"{SOURCE_BASE_URL}/{file_name}",
        headers={"User-Agent": "cardiacai-omop-agent-schema-generator"},
    )
    with urlopen(request, timeout=30) as response:
        content = response.read()

    digest = hashlib.sha256(content).hexdigest()
    if digest != EXPECTED_SHA256[file_name]:
        raise ValueError(
            f"{file_name} SHA-256 mismatch: expected "
            f"{EXPECTED_SHA256[file_name]}, received {digest}"
        )

    return list(
        csv.DictReader(
            io.StringIO(content.decode("utf-8-sig")),
        )
    )


def _text(value: str) -> FoldedString:
    """Convert official HTML fragments and line endings to readable text."""
    normalized = value.strip()
    if normalized in {"", "NA"}:
        return FoldedString("")

    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"<br\s*/?>",
        "\n\n",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"<li[^>]*>",
        "- ",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"</li>",
        "\n",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"</?[^>]+>", "", normalized)
    normalized = normalized.replace("/li>", "")

    cleaned_lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in normalized.splitlines()
    ]
    compact_lines = []
    for line in cleaned_lines:
        if line or not compact_lines or compact_lines[-1]:
            compact_lines.append(line)
    return FoldedString("\n".join(compact_lines).strip())


def _optional_text(value: str) -> str | None:
    """Return None for official NA markers."""
    normalized = value.strip()
    return None if normalized in {"", "NA"} else normalized


def _yes_no(value: str, label: str) -> bool:
    """Parse the official Yes/No flags strictly."""
    normalized = value.strip().casefold()
    if normalized == "yes":
        return True
    if normalized == "no":
        return False
    raise ValueError(f"{label} must be Yes or No, received {value!r}")


def _data_type(value: str) -> str:
    """Normalize metadata types for portable generated SQL."""
    normalized = value.strip().casefold()
    return "varchar" if normalized == "varchar(max)" else normalized


def _identifier(value: str) -> str:
    """Normalize official identifiers while removing metadata quoting."""
    normalized = value.strip()
    if (
        len(normalized) >= 2
        and normalized.startswith('"')
        and normalized.endswith('"')
    ):
        normalized = normalized[1:-1]
    return normalized.casefold()


def _foreign_key(row: dict[str, str]) -> dict[str, str] | None:
    """Build a complete FK target from one official field row."""
    if not _yes_no(
        row["isForeignKey"],
        f"{row['cdmTableName']}.{row['cdmFieldName']}.isForeignKey",
    ):
        return None

    foreign_key = {
        "table": _identifier(row["fkTableName"]),
        "field": _identifier(row["fkFieldName"]),
    }
    domain = _optional_text(row["fkDomain"])
    class_name = _optional_text(row["fkClass"])
    if domain:
        foreign_key["domain"] = domain
    if class_name:
        foreign_key["class_name"] = class_name
    return foreign_key


def _field_document(row: dict[str, str]) -> dict:
    """Convert one official field row into the strict target contract."""
    field = {
        "name": _identifier(row["cdmFieldName"]),
        "data_type": _data_type(row["cdmDatatype"]),
        "required": _yes_no(
            row["isRequired"],
            f"{row['cdmTableName']}.{row['cdmFieldName']}.isRequired",
        ),
        "primary_key": _yes_no(
            row["isPrimaryKey"],
            f"{row['cdmTableName']}.{row['cdmFieldName']}.isPrimaryKey",
        ),
    }
    foreign_key = _foreign_key(row)
    if foreign_key:
        field["foreign_key"] = foreign_key
    field["description"] = _text(row["userGuidance"])
    field["etl_convention"] = _text(row["etlConventions"])
    return field


def _table_document(
    row: dict[str, str],
    fields: list[dict[str, str]],
    display_order: int,
) -> dict:
    """Convert one official table row and its ordered fields."""
    threshold = _optional_text(
        row["measurePersonCompletenessThreshold"]
    )
    document = {
        "version": 1,
        "display_order": display_order,
        "cdm_version": "5.4",
        "target_table": _identifier(row["cdmTableName"]),
        "cdm_schema": row["schema"].strip(),
        "required": _yes_no(
            row["isRequired"],
            f"{row['cdmTableName']}.isRequired",
        ),
        "concept_prefix": _optional_text(row["conceptPrefix"]),
        "measure_person_completeness": _yes_no(
            row["measurePersonCompleteness"],
            f"{row['cdmTableName']}.measurePersonCompleteness",
        ),
        "measure_person_completeness_threshold": (
            float(threshold) if threshold is not None else None
        ),
        "description": _text(row["tableDescription"]),
        "user_guidance": _text(row["userGuidance"]),
        "etl_convention": _text(row["etlConventions"]),
        "fields": fields,
    }
    TargetSchemaDocument.model_validate(document)
    return document


def _serialize(document: dict) -> str:
    """Serialize one validated target schema deterministically."""
    generated_header = (
        "# Generated from OHDSI CommonDataModel metadata.\n"
        f"# Source commit: {SOURCE_COMMIT}\n"
        "# Regenerate with: python scripts/generate_target_schemas.py\n"
    )
    content = yaml.dump(
        document,
        Dumper=TargetSchemaDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return generated_header + content


def main() -> None:
    """Download, validate and atomically publish every target schema."""
    table_rows = _download_csv(TABLE_FILE)
    field_rows = _download_csv(FIELD_FILE)
    if len(table_rows) != EXPECTED_TABLE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_TABLE_COUNT} tables, got {len(table_rows)}"
        )
    if len(field_rows) != EXPECTED_FIELD_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_FIELD_COUNT} fields, got {len(field_rows)}"
        )

    fields_by_table: dict[str, list[dict]] = defaultdict(list)
    for row in field_rows:
        fields_by_table[row["cdmTableName"]].append(
            _field_document(row)
        )

    documents = {}
    for display_order, row in enumerate(table_rows, start=1):
        table_name = row["cdmTableName"]
        fields = fields_by_table.pop(table_name, [])
        if not fields:
            raise ValueError(f"{table_name} has no field metadata")
        documents[table_name.casefold()] = _table_document(
            row,
            fields,
            display_order,
        )
    if fields_by_table:
        raise ValueError(
            "Field metadata exists for unknown tables: "
            + ", ".join(sorted(fields_by_table))
        )

    for table_name, document in documents.items():
        for field in document["fields"]:
            foreign_key = field.get("foreign_key")
            if not foreign_key:
                continue
            foreign_table = foreign_key["table"]
            foreign_field = foreign_key["field"]
            if foreign_table not in documents:
                raise ValueError(
                    f"{table_name}.{field['name']} references unknown "
                    f"table {foreign_table}"
                )
            known_fields = {
                item["name"]
                for item in documents[foreign_table]["fields"]
            }
            if foreign_field not in known_fields:
                raise ValueError(
                    f"{table_name}.{field['name']} references unknown "
                    f"field {foreign_table}.{foreign_field}"
                )

    TARGET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".target_schema_",
        dir=TARGET_DIRECTORY.parent,
    ) as temporary_directory:
        staging = Path(temporary_directory)
        for table_name, document in documents.items():
            (staging / f"{table_name}.yml").write_text(
                _serialize(document),
                encoding="utf-8",
            )

        generated_names = {
            f"{table_name}.yml" for table_name in documents
        }
        for existing in TARGET_DIRECTORY.glob("*.yml"):
            if existing.name not in generated_names:
                existing.unlink()
        for file_name in sorted(generated_names):
            os.replace(
                staging / file_name,
                TARGET_DIRECTORY / file_name,
            )

    print(
        f"Generated {len(documents)} OMOP CDM 5.4 target schemas "
        f"with {len(field_rows)} fields."
    )


if __name__ == "__main__":
    main()
