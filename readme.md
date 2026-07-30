# CardiacAI OMOP SQL Agent

A bounded Python agent that converts validated source-schema and
source-to-OMOP mapping specifications into one statically validated SQL file
per OMOP CDM 5.4 table.

The repository supports two independent entry points:

- Local CLI operation for development in VS Code.
- An authenticated HTTP API used by the separate CardiacAI OMOP Agent UI.

The agent does not execute SQL or create a dbt project.

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
                output/{table}.sql
```

Validation and preflight never call OpenAI. Generation requires an explicit
command or authenticated API request and is constrained by the configured
request, retry and output-token limits.

## Repository structure

```text
agent/          Python application, contracts, API and generation loop
docs/           User, technical, deployment and roadmap documentation
examples/       Non-authoritative example artifacts
logs/           Gitignored owner-only local run logs
output/         Gitignored generated SQL
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
- `POST /v1/preflight`
- `POST /v1/generate`
- `GET /v1/generation-options`
- `GET /v1/target-schemas`

The API token and OpenAI key remain server-side. API generation returns SQL
without overwriting locally managed output files.

## Documentation

- [User guide](docs/user_guide.md)
- [Technical guide](docs/technical_guide.md)
- [Vercel deployment guide](docs/vercel_deployment.md)
- [Development roadmap](docs/development_plan.md)
- [Specification directory guide](specs/README.md)

The technical guide is the primary maintainer reference for architecture,
module responsibilities, setup, security boundaries and extension paths.
