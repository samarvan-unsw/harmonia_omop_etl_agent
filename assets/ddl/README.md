# OMOP CDM DDL assets

The PostgreSQL, Snowflake and BigQuery files under `5.4/` are unmodified
copies from the OHDSI `CommonDataModel` repository:

- Source commit: `f853f6e39c61b4eb8b3e5287fd573a1ced36c0e4`
- Source path: `inst/ddl/5.4/{dialect}/`
- License: Apache License 2.0

The commit matches the pinned CSV metadata used to generate
`specs/target_schema`.

Each output keeps OHDSI's `@cdmDatabaseSchema` placeholder. Replace or render
that placeholder for the target environment before execution.

OHDSI does not provide an Athena-specific CDM 5.4 DDL in this source commit.
Athena output is therefore generated locally from the pinned target-schema
contracts and clearly identifies unsupported constraints and required
environment-specific S3 locations.
