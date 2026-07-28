# OMOP CDM 5.4 target schemas

The YAML files in this directory are generated from the official OHDSI
CommonDataModel metadata:

- `OMOP_CDMv5.4_Table_Level.csv`
- `OMOP_CDMv5.4_Field_Level.csv`

Generation is pinned to OHDSI/CommonDataModel commit
`f853f6e39c61b4eb8b3e5287fd573a1ced36c0e4`. Source checksums and URLs are
defined in `scripts/generate_target_schemas.py`.

Regenerate and validate all files from the repository root:

```bash
python scripts/generate_target_schemas.py
```

Identifiers and datatypes are normalized to lowercase. Embedded HTML layout
tags and source line endings are normalized to readable text. The SQL
Server-oriented `varchar(MAX)` metadata type is represented as portable
`varchar`; all other datatype widths are retained. Metadata-only identifier
quotes are removed, so the official `"offset"` field is stored as `offset`;
generated SQL must quote it when required by the configured dialect.

The generator preserves table and field guidance, ETL conventions, required
flags, primary keys, foreign-key targets, vocabulary domains and applicable
concept classes. Metadata columns whose official value is empty or `NA`
throughout the source are omitted.
