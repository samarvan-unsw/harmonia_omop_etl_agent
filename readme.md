# CardiacAI OMOP SQL Agent

A bounded Python agent that converts validated source-schema and
source-to-OMOP mapping specifications into validated transformation SQL and
deterministic schema artifacts for OMOP CDM 5.4.

The repository supports two independent entry points:

- Local CLI operation for development in VS Code.
- An authenticated HTTP API used by the separate CardiacAI OMOP Agent UI.

The agent does not execute SQL or create a dbt project.

It can also convert bounded OHDSI WhiteRabbit `.xlsx` scan reports into
validated source-schema YAML and aggregate source-profile metadata without an
OpenAI request. Per-table value-frequency sheets are deliberately ignored.

## Architecture

```text
source schemas + mapping + agent-owned OMOP target
                         │
                         ▼
              deterministic validation
                         │
                         ▼
              bounded OpenAI generation
                         │
                         ▼
                 static SQL validation
                         │
                         ▼
        output/{table}.sql + schema artifacts
```

Validation and preflight never call OpenAI. Generation requires an explicit
command or authenticated API request and is constrained by the configured
request, retry and output-token limits.

## Repository structure

```text
agent/          Python application, contracts, API and generation loop
assets/         Pinned official OHDSI DDL assets
docs/           User, technical, deployment and roadmap documentation
examples/       Non-authoritative example artifacts
logs/           Gitignored owner-only local run logs
output/         Gitignored transformations, dbt contracts and DDL
scripts/        Maintainer utilities
specs/          Source schemas, mappings and OMOP target schemas
tests/          Network-free unit and integration tests
config.yaml     Agent-owned model, cost and output controls
requirements.txt
```

## Requirements

- Python 3.12.12, recorded in `.python-version`.
- An OpenAI API key only for SQL generation.
- No database or dbt installation is required.

## Local setup

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --requirement requirements.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` only when generation is required. Never commit
the `.env` file.

## Common commands

Validate one OMOP table without an API call:

```bash
python -m agent.cli person --validate-only
```

Create ETL documentation locally without an API call:

```bash
python -m agent.cli person --etl-specification docx
```

Choose `md`, `docx` or `pdf`. The file is written beneath
`output/etl_specifications/`.

Inspect generation readiness, the worst-case token ceiling and the estimated
maximum API cost without an API call:

```bash
python -m agent.cli person --dry-run
```

Explicitly permit bounded SQL generation:

```bash
python -m agent.cli person \
  --generate \
  --max-run-output-tokens 1600
```

Run the complete network-free test suite:

```bash
python -m unittest discover -s tests -v
```

## Specifications

Each requested OMOP table uses:

```text
specs/source_schema/{source_model}.yml
specs/mappings/{omop_table}.yml
specs/target_schema/{omop_table}.yml
```

Users maintain source schemas and mappings. The complete target catalog is
generated from pinned official OHDSI metadata and should not be edited one
file at a time.

Important mapping rules:

- Missing optional OMOP fields become typed NULL expressions.
- Missing required OMOP fields fail validation.
- Users may explicitly assign a required field `action: "null"`.
- Unknown source models, source fields and OMOP target fields fail validation.
- Pending mapping reviews block generation.
- Lookup relation names default to
  `mapping_{target_table}_{target_field}` when explicitly requested without a
  name.

## HTTP API

Generate a private token and place it in `.env` as `AGENT_API_TOKEN`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Start the local API:

```bash
uvicorn agent.api:app --reload --env-file .env
```

Check health:

```bash
curl http://127.0.0.1:8000/health
```

Versioned endpoints:

- `POST /v1/validate`
- `POST /v1/etl-specification/{output_format}`
- `POST /v1/etl-specification-bundle/{output_format}`
- `POST /v1/preflight`
- `POST /v1/generate`
- `GET /v1/generation-options`
- `GET /v1/schema-bundle/{output_format}/{sql_dialect}`
- `GET /v1/ddl/{sql_dialect}`
- `GET /v1/target-schemas`

The API token and OpenAI key remain server-side. API generation returns a
bounded output bundle without overwriting locally managed output files.
The schema-bundle endpoint returns either the four deterministic OMOP DDL
files for `sql` output or one dbt model-contract YAML per OMOP table for `dbt`
output. Repeated `tables` query parameters can limit dbt output to selected
targets; omitting them returns all 39. It does not call OpenAI or consume model
tokens.
The `/v1/ddl` route remains as a backward-compatible SQL-only endpoint.
The ETL specification endpoint accepts `md`, `docx` or `pdf`, validates the
submitted mapping and source schemas, and creates a mapping diagram,
field-level ETL table and change log. Markdown, Word and PDF reuse the same
complete mapping-grid image. Word and PDF place that image on a portrait page
and use landscape pages with repeating headers for the field tables. The
bundle endpoint packages separate documents for two to 50 tables in one ZIP.
Both are deterministic and do not call OpenAI.

## Documentation

- [User guide](docs/user_guide.md)
- [Technical guide](docs/technical_guide.md)
- [Vercel deployment guide](docs/vercel_deployment.md)
- [Development roadmap](docs/development_plan.md)
- [Specification directory guide](specs/README.md)

The technical guide is the primary maintainer reference for architecture,
module responsibilities, setup, security boundaries and extension paths.
