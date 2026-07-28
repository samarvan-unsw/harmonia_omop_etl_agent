# Generated output

Successful local CLI generation writes one SQL file per OMOP table here:

```text
output/{omop_table}.sql
```

Generated SQL is intentionally ignored by Git because it may contain
environment-specific relation names or sensitive implementation details. A
non-authoritative example is available under `examples/generated/`.
