"""Shared SQL-dialect identifiers, names, and datatype mappings."""

import re
from typing import Literal, get_args


SqlDialect = Literal[
    "snowflake",
    "postgres",
    "athena",
    "bigquery",
    "sql_server",
    "spark",
    "oracle",
    "redshift",
    "synapse",
]

SUPPORTED_SQL_DIALECTS: tuple[str, ...] = get_args(SqlDialect)

# Public API/config identifiers remain stable even where sqlglot uses a
# different dialect name.
SQLGLOT_DIALECTS: dict[str, str] = {
    "snowflake": "snowflake",
    "postgres": "postgres",
    "athena": "athena",
    "bigquery": "bigquery",
    "sql_server": "tsql",
    "spark": "spark",
    "oracle": "oracle",
    "redshift": "redshift",
    "synapse": "tsql",
}

SQL_DIALECT_PROMPT_NAMES: dict[str, str] = {
    "snowflake": "Snowflake SQL",
    "postgres": "PostgreSQL",
    "athena": "Amazon Athena SQL",
    "bigquery": "GoogleSQL for BigQuery",
    "sql_server": "Microsoft SQL Server T-SQL",
    "spark": "Databricks / Spark SQL",
    "oracle": "Oracle SQL",
    "redshift": "Amazon Redshift SQL",
    "synapse": "Azure Synapse T-SQL",
}

VARCHAR_PATTERN = re.compile(r"^varchar(?:\((\d+)\))?$", re.IGNORECASE)

PLATFORM_DATA_TYPES: dict[str, dict[str, str]] = {
    "snowflake": {
        "integer": "INTEGER",
        "float": "FLOAT",
        "date": "DATE",
        "datetime": "TIMESTAMP_NTZ",
    },
    "postgres": {
        "integer": "INTEGER",
        "float": "DOUBLE PRECISION",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    },
    "athena": {
        "integer": "INT",
        "float": "DOUBLE",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    },
    "bigquery": {
        "integer": "INT64",
        "float": "FLOAT64",
        "date": "DATE",
        "datetime": "DATETIME",
    },
    "sql_server": {
        "integer": "INT",
        "float": "FLOAT",
        "date": "DATE",
        "datetime": "DATETIME2",
    },
    "spark": {
        "integer": "INT",
        "float": "DOUBLE",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    },
    "oracle": {
        "integer": "INTEGER",
        "float": "FLOAT",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    },
    "redshift": {
        "integer": "INTEGER",
        "float": "DOUBLE PRECISION",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    },
    "synapse": {
        "integer": "INT",
        "float": "FLOAT",
        "date": "DATE",
        "datetime": "DATETIME2",
    },
}


def sqlglot_dialect(dialect: str) -> str:
    """Resolve a public dialect identifier to sqlglot's parser name."""
    try:
        return SQLGLOT_DIALECTS[dialect]
    except KeyError as error:
        raise ValueError(f"Unsupported SQL dialect: {dialect}") from error


def sql_dialect_prompt_name(dialect: str) -> str:
    """Return an unambiguous dialect name for generation instructions."""
    try:
        return SQL_DIALECT_PROMPT_NAMES[dialect]
    except KeyError as error:
        raise ValueError(f"Unsupported SQL dialect: {dialect}") from error


def platform_data_type(data_type: str, dialect: str) -> str:
    """Convert a canonical OMOP datatype to a physical platform type."""
    if dialect not in PLATFORM_DATA_TYPES:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")

    normalized = data_type.strip().lower()
    varchar_match = VARCHAR_PATTERN.fullmatch(normalized)
    if varchar_match:
        length = varchar_match.group(1)
        if dialect in {"athena", "bigquery", "spark"}:
            return "STRING"
        if dialect == "oracle":
            return f"VARCHAR2({length})" if length else "CLOB"
        if dialect == "redshift":
            return f"VARCHAR({length})" if length else "VARCHAR(65535)"
        if dialect == "synapse":
            return f"VARCHAR({length})" if length else "VARCHAR(8000)"
        if dialect == "sql_server":
            return f"VARCHAR({length})" if length else "VARCHAR(MAX)"
        return f"VARCHAR({length})" if length else "VARCHAR"

    try:
        return PLATFORM_DATA_TYPES[dialect][normalized]
    except KeyError as error:
        raise ValueError(
            f"Unsupported OMOP data type '{data_type}' for {dialect}"
        ) from error
