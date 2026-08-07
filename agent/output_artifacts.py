"""Deterministic output artifacts that complement generated transformation SQL."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from .contracts import TargetField, TargetSchemaDocument
from .dialects import SUPPORTED_SQL_DIALECTS, platform_data_type
from .yaml_loader import load_yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
TARGET_SCHEMA_DIR = ROOT_DIR / "specs" / "target_schema"
OUTPUT_DIR = ROOT_DIR / "output"
DDL_ASSET_DIR = ROOT_DIR / "assets" / "ddl" / "5.4"
DDL_FILE_NAMES = (
    "create_tables.sql",
    "primary_keys.sql",
    "foreign_keys.sql",
    "indexes.sql",
)
DDL_ASSET_SHA256 = {
    "postgres/create_tables.sql": (
        "ae99be6e79edfad5f17ef71edda176281b45e3aa9e400e7a9f829103f5ec4771"
    ),
    "postgres/foreign_keys.sql": (
        "dedae8072ef585e25e0ab2624f557e37e5ddd2d51e75810af58b02e990a4f293"
    ),
    "postgres/indexes.sql": (
        "8a3537f971c75e9e33c3d1d13b041d4e5de8532dc1607bc31349af3679a66eec"
    ),
    "postgres/primary_keys.sql": (
        "ffe6cc10f04a713ea86825dccfc1d8b8a981ba6037fc69cb9df4c80ce2f1970d"
    ),
    "snowflake/create_tables.sql": (
        "c5438ebd1940aeaa16ab0bb4285e4c4db377fbbb44a84e5992bc466c4e73d66a"
    ),
    "snowflake/foreign_keys.sql": (
        "97f7d5a5bc2688b66bf44cb25704f59f66b2ae22784430d01d61ced09022640b"
    ),
    "snowflake/indexes.sql": (
        "d9093ded49b7db7cdba3170048775033881bd80ccdcad894e906d37f969f028c"
    ),
    "snowflake/primary_keys.sql": (
        "f9808b3f49aed5dab2b07e21002e616c2da2304e936f754902755ecf1c4202f8"
    ),
    "bigquery/create_tables.sql": (
        "bfc16130bf06efcef0a1810edfcc48b238d097867f792728460ae6031400a200"
    ),
    "bigquery/foreign_keys.sql": (
        "11c1b01546468685dbc3e78c6a0afddc7b087838c44f01598a658c5172bd7274"
    ),
    "bigquery/indexes.sql": (
        "af5dd9e39d89463e9b9d9e8455a62e06ac8d3d3b0ae25c22936105199528b9d5"
    ),
    "bigquery/primary_keys.sql": (
        "4cc0823772e3a9cf27b081f01da63c0146f8b54302baebfa5d02de384e0c0504"
    ),
}


@dataclass(frozen=True)
class OutputArtifact:
    """One bounded file returned to a caller or written by the local CLI."""

    file_name: str
    content: str
    media_type: str
    category: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _load_target_schemas(
    target_schema_dir: Path = TARGET_SCHEMA_DIR,
) -> list[TargetSchemaDocument]:
    """Load the pinned OMOP catalog in its documented display order."""
    schemas: list[TargetSchemaDocument] = []
    for path in sorted(target_schema_dir.glob("*.yml")):
        try:
            data = load_yaml(path.read_text(encoding="utf-8"))
            schemas.append(TargetSchemaDocument.model_validate(data))
        except (OSError, ValidationError, yaml.YAMLError) as error:
            raise ValueError(
                f"Invalid target schema used for output artifacts: {path.name}"
            ) from error

    if not schemas:
        raise ValueError("No OMOP target schemas are available")

    versions = {schema.cdm_version for schema in schemas}
    if len(versions) != 1:
        raise ValueError("OMOP target schemas must use one CDM version")
    return sorted(
        schemas,
        key=lambda schema: (schema.display_order, schema.target_table),
    )


def _platform_data_type(data_type: str, dialect: str) -> str:
    """Convert the canonical OMOP type to a supported physical type."""
    return platform_data_type(data_type, dialect)


def _safe_object_name(
    prefix: str,
    *parts: str,
    max_length: int = 63,
) -> str:
    """Build a deterministic identifier within a platform length limit."""
    value = "_".join((prefix, *parts)).lower()
    if len(value) <= max_length:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{value[:max_length - 11]}_{digest}"


def _identifier_max_length(dialect: str) -> int:
    """Return a conservative identifier limit for generated objects."""
    return {
        "oracle": 30,
        "postgres": 63,
        "redshift": 127,
        "sql_server": 128,
        "synapse": 128,
    }.get(dialect, 63)


def _header(title: str, dialect: str, cdm_version: str) -> list[str]:
    return [
        f"-- {title}",
        f"-- OMOP CDM version: {cdm_version}",
        f"-- SQL dialect: {dialect}",
        "-- Generated deterministically from specs/target_schema.",
        "-- Execute in the intended target schema or dataset.",
        "",
    ]


def _create_tables_sql(
    schemas: list[TargetSchemaDocument],
    dialect: str,
) -> str:
    lines = _header(
        "Create OMOP CDM tables",
        dialect,
        schemas[0].cdm_version,
    )
    if dialect == "athena":
        lines.extend(
            [
                "-- Athena tables use Parquet. Add each environment-specific "
                "S3 LOCATION before execution.",
                "",
            ]
        )
    if dialect == "spark":
        lines.extend(
            [
                "-- Spark tables use Parquet for portability across Spark "
                "and Databricks.",
                "",
            ]
        )
    for schema in schemas:
        create_kind = (
            "CREATE EXTERNAL TABLE"
            if dialect == "athena"
            else "CREATE TABLE"
        )
        if_not_exists = (
            " IF NOT EXISTS"
            if dialect in {"athena", "redshift", "spark"}
            else ""
        )
        lines.append(f"{create_kind}{if_not_exists} {schema.target_table} (")
        columns = []
        for field in schema.fields:
            data_type = _platform_data_type(field.data_type, dialect)
            required = (
                " NOT NULL"
                if field.required and dialect not in {"athena", "spark"}
                else ""
            )
            columns.append(f"    {field.name} {data_type}{required}")
        lines.append(",\n".join(columns))
        if dialect == "athena":
            terminator = ") STORED AS PARQUET;"
        elif dialect == "spark":
            terminator = ") USING PARQUET;"
        else:
            terminator = ");"
        lines.extend([terminator, ""])
    return "\n".join(lines).rstrip() + "\n"


def _primary_keys_sql(
    schemas: list[TargetSchemaDocument],
    dialect: str,
) -> str:
    lines = _header(
        "Add OMOP CDM primary keys",
        dialect,
        schemas[0].cdm_version,
    )
    if dialect == "athena":
        lines.append(
            "-- Amazon Athena does not support primary-key constraints."
        )
        return "\n".join(lines).rstrip() + "\n"
    if dialect in {"spark", "synapse"}:
        lines.append(
            f"-- {dialect} primary-key constraints are not emitted because "
            "portable enforcement is unavailable."
        )
        return "\n".join(lines).rstrip() + "\n"

    if dialect == "redshift":
        lines.extend(
            [
                "-- Redshift records primary keys as optimizer metadata; it "
                "does not enforce them.",
                "",
            ]
        )

    for schema in schemas:
        fields = [
            field.name for field in schema.fields if field.primary_key
        ]
        if not fields:
            continue
        constraint = _safe_object_name(
            "pk",
            schema.target_table,
            max_length=_identifier_max_length(dialect),
        )
        suffix = " NOT ENFORCED" if dialect == "bigquery" else ""
        lines.extend(
            [
                f"ALTER TABLE {schema.target_table}",
                f"    ADD CONSTRAINT {constraint} PRIMARY KEY "
                f"({', '.join(fields)}){suffix};",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _foreign_keys_sql(
    schemas: list[TargetSchemaDocument],
    dialect: str,
) -> str:
    lines = _header(
        "Add OMOP CDM foreign keys",
        dialect,
        schemas[0].cdm_version,
    )
    if dialect == "athena":
        lines.append(
            "-- Amazon Athena does not support foreign-key constraints."
        )
        return "\n".join(lines).rstrip() + "\n"
    if dialect in {"spark", "synapse"}:
        lines.append(
            f"-- {dialect} foreign-key constraints are not emitted because "
            "portable enforcement is unavailable."
        )
        return "\n".join(lines).rstrip() + "\n"

    if dialect == "redshift":
        lines.extend(
            [
                "-- Redshift records foreign keys as optimizer metadata; it "
                "does not enforce them.",
                "",
            ]
        )

    for schema in schemas:
        for field in schema.fields:
            foreign_key = field.foreign_key
            if foreign_key is None or foreign_key.field is None:
                continue
            constraint = _safe_object_name(
                "fk",
                schema.target_table,
                field.name,
                max_length=_identifier_max_length(dialect),
            )
            suffix = " NOT ENFORCED" if dialect == "bigquery" else ""
            lines.extend(
                [
                    f"ALTER TABLE {schema.target_table}",
                    f"    ADD CONSTRAINT {constraint} FOREIGN KEY "
                    f"({field.name})",
                    f"    REFERENCES {foreign_key.table} "
                    f"({foreign_key.field}){suffix};",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _indexes_sql(
    schemas: list[TargetSchemaDocument],
    dialect: str,
) -> str:
    lines = _header(
        "Create OMOP CDM foreign-key indexes",
        dialect,
        schemas[0].cdm_version,
    )
    if dialect == "athena":
        lines.append(
            "-- Amazon Athena does not support conventional secondary "
            "indexes; no index statements are emitted."
        )
        return "\n".join(lines).rstrip() + "\n"
    if dialect not in {"postgres", "sql_server", "oracle"}:
        lines.append(
            f"-- Portable secondary-index DDL is not emitted for {dialect}."
        )
        return "\n".join(lines).rstrip() + "\n"

    for schema in schemas:
        for field in schema.fields:
            if field.foreign_key is None:
                continue
            index_name = _safe_object_name(
                "idx",
                schema.target_table,
                field.name,
                max_length=_identifier_max_length(dialect),
            )
            if_not_exists = " IF NOT EXISTS" if dialect == "postgres" else ""
            lines.extend(
                [
                    f"CREATE INDEX{if_not_exists} {index_name}",
                    f"    ON {schema.target_table} ({field.name});",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _dbt_column(field: TargetField, dialect: str) -> dict:
    column: dict = {
        "name": field.name,
        "data_type": _platform_data_type(field.data_type, dialect),
        "description": field.description,
    }
    constraints: list[dict[str, str]] = []
    if field.required and dialect not in {"athena", "spark"}:
        constraints.append({"type": "not_null"})
    if field.primary_key and dialect not in {"athena", "spark", "synapse"}:
        constraints.append({"type": "primary_key"})
    if constraints:
        column["constraints"] = constraints
    return column


def _dbt_model_yml(
    target_schema: TargetSchemaDocument,
    dialect: str,
) -> str:
    document = {
        "version": 2,
        "models": [
            {
                "name": target_schema.target_table,
                "description": target_schema.description,
                "config": {
                    "materialized": "table",
                    "contract": {"enforced": True},
                },
                "columns": [
                    _dbt_column(field, dialect)
                    for field in target_schema.fields
                ],
            }
        ],
    }
    return yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def _official_ddl_artifacts(dialect: str) -> list[OutputArtifact]:
    """Load pinned, unmodified OHDSI DDL for an officially supplied dialect."""
    dialect_dir = DDL_ASSET_DIR / dialect
    artifacts: list[OutputArtifact] = []
    for file_name in DDL_FILE_NAMES:
        path = dialect_dir / file_name
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(
                f"Pinned OHDSI DDL is unavailable: {dialect}/{file_name}"
            ) from error
        if not content.strip() or len(content) > 1_000_000:
            raise ValueError(
                f"Pinned OHDSI DDL is invalid: {dialect}/{file_name}"
            )
        relative_name = f"{dialect}/{file_name}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != DDL_ASSET_SHA256.get(relative_name):
            raise ValueError(
                f"Pinned OHDSI DDL checksum mismatch: {relative_name}"
            )
        artifacts.append(
            OutputArtifact(
                file_name=file_name,
                content=content,
                media_type="application/sql",
                category="ddl",
            )
        )
    return artifacts


def build_output_artifacts(
    *,
    generated_sql: str,
    target_schema: TargetSchemaDocument,
    output_format: str,
    dialect: str,
    target_schema_dir: Path = TARGET_SCHEMA_DIR,
) -> list[OutputArtifact]:
    """Build the complete output bundle without another AI request."""
    artifacts = [
        OutputArtifact(
            file_name=f"{target_schema.target_table}.sql",
            content=generated_sql,
            media_type="application/sql",
            category="transformation",
        )
    ]

    if output_format == "dbt":
        artifacts.append(
            OutputArtifact(
                file_name=f"{target_schema.target_table}.yml",
                content=_dbt_model_yml(target_schema, dialect),
                media_type="application/yaml",
                category="dbt_contract",
            )
        )
        return artifacts

    if output_format != "sql":
        raise ValueError(f"Unsupported output format: {output_format}")

    artifacts.extend(
        build_ddl_artifacts(
            dialect=dialect,
            target_schema_dir=target_schema_dir,
        )
    )
    return artifacts


def build_ddl_artifacts(
    *,
    dialect: str,
    target_schema_dir: Path = TARGET_SCHEMA_DIR,
) -> list[OutputArtifact]:
    """Build the complete OMOP DDL bundle without an AI request."""
    if dialect not in SUPPORTED_SQL_DIALECTS:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")

    if dialect in {"postgres", "snowflake", "bigquery"}:
        return _official_ddl_artifacts(dialect)

    schemas = _load_target_schemas(target_schema_dir)
    ddl_files = (
        ("create_tables.sql", _create_tables_sql(schemas, dialect)),
        ("primary_keys.sql", _primary_keys_sql(schemas, dialect)),
        ("foreign_keys.sql", _foreign_keys_sql(schemas, dialect)),
        ("indexes.sql", _indexes_sql(schemas, dialect)),
    )
    return [
        OutputArtifact(
            file_name=file_name,
            content=content,
            media_type="application/sql",
            category="ddl",
        )
        for file_name, content in ddl_files
    ]


def build_dbt_schema_artifacts(
    *,
    dialect: str,
    target_tables: set[str] | None = None,
    target_schema_dir: Path = TARGET_SCHEMA_DIR,
) -> list[OutputArtifact]:
    """Build deterministic dbt contracts for all or selected OMOP tables."""
    if dialect not in SUPPORTED_SQL_DIALECTS:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")

    schemas = _load_target_schemas(target_schema_dir)
    if target_tables is not None:
        known_tables = {schema.target_table for schema in schemas}
        unknown_tables = sorted(target_tables - known_tables)
        if unknown_tables:
            raise ValueError(
                "Unknown OMOP target tables: "
                + ", ".join(unknown_tables)
            )
        schemas = [
            schema
            for schema in schemas
            if schema.target_table in target_tables
        ]
        if not schemas:
            raise ValueError("Select at least one OMOP target table.")

    return [
        OutputArtifact(
            file_name=f"{schema.target_table}.yml",
            content=_dbt_model_yml(schema, dialect),
            media_type="application/yaml",
            category="dbt_contract",
        )
        for schema in schemas
    ]


def build_schema_artifacts(
    *,
    output_format: str,
    dialect: str,
    target_tables: set[str] | None = None,
    target_schema_dir: Path = TARGET_SCHEMA_DIR,
) -> list[OutputArtifact]:
    """Build a complete no-AI schema bundle for SQL or dbt projects."""
    if output_format == "sql":
        return build_ddl_artifacts(
            dialect=dialect,
            target_schema_dir=target_schema_dir,
        )
    if output_format == "dbt":
        return build_dbt_schema_artifacts(
            dialect=dialect,
            target_tables=target_tables,
            target_schema_dir=target_schema_dir,
        )
    raise ValueError(f"Unsupported output format: {output_format}")


def write_local_artifacts(
    artifacts: list[OutputArtifact],
    output_dir: Path = OUTPUT_DIR,
) -> None:
    """Persist deterministic artifacts after transformation validation."""
    ddl_dir = output_dir / "ddl"
    output_dir.mkdir(parents=True, exist_ok=True)

    for artifact in artifacts:
        if artifact.category == "transformation":
            continue
        target_dir = ddl_dir if artifact.category == "ddl" else output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact.file_name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(artifact.content, encoding="utf-8")
        temporary.replace(target)
