# Specifications

The agent uses three YAML specification collections:

```text
source_schema/   User-maintained source tables and fields
mappings/        User-maintained source-to-OMOP decisions
target_schema/   Agent-owned generated OMOP CDM 5.4 reference
```

Source-schema filenames must match their model names. Mapping filenames must
match their OMOP target tables. Do not edit individual generated target-schema
files; follow `target_schema/README.md` to regenerate the complete catalog.
