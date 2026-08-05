# CardiacAI OMOP Agent User Guide

## 1. What the agent does

The agent reads validated YAML specifications and generates one SQL model for
one OMOP table:

```text
source schema + target schema + mapping → output/{omop_table}.sql
```

The agent statically validates the SQL, but it does not execute the SQL against
a database.

## 2. Initial setup

Clone the repository and enter the project:

```bash
git clone https://github.com/Cardiac-Analytics-Innovation/cardiac_ai_omop_agent.git
cd cardiac_ai_omop_agent
```

Create and activate a Python virtual environment:

```bash
python3.12 -m venv venv
source venv/bin/activate
```

Install the pinned dependencies:

```bash
python -m pip install -r requirements.txt
```

Create `.env` in the project root and add the API key:

```text
OPENAI_API_KEY=your-api-key
```

Do not commit `.env`. Local validation and dry runs do not require an API call.

## 3. Files users maintain

| File or folder | User responsibility |
| --- | --- |
| `.env` | Store `OPENAI_API_KEY` locally |
| `config.yaml` | Select the model, limits, SQL dialect and reference style |
| `specs/source_schema/` | Describe each available source table or model |
| `specs/target_schema/` | Define the OMOP target fields and requirements |
| `specs/mappings/` | Define source-to-OMOP transformations and joins |

Users should not normally edit:

| File or folder | Purpose |
| --- | --- |
| `agent/` | Agent implementation |
| `output/` | Generated transformations and deterministic schema artifacts |
| `logs/` | Generated run transcripts and token usage |
| `tests/` | Automated tests for the agent implementation |

## 4. Configure the output

Maintain `config.yaml` before generating SQL:

```yaml
provider: codex
model: gpt-5.3-codex

max_output_tokens: 800
max_initial_prompt_characters: 12000
max_api_retries: 0

project_limits:
  allowed_models:
    - gpt-5.3-codex
    - gpt-5.6-terra
    - gpt-5.6-luna
    - gpt-5.6-sol
  maximum_output_tokens_per_request: 4000
  maximum_initial_prompt_characters: 50000
  maximum_generation_attempts: 2
  maximum_api_retries: 2

pricing:
  currency: USD
  verified_on: 2026-07-30
  models:
    gpt-5.3-codex:
      input_usd_per_million_tokens: 1.75
      cached_input_usd_per_million_tokens: 0.175
      cache_write_input_usd_per_million_tokens: 1.75
      output_usd_per_million_tokens: 14.00
    gpt-5.6-terra:
      input_usd_per_million_tokens: 2.50
      cached_input_usd_per_million_tokens: 0.25
      cache_write_input_usd_per_million_tokens: 3.125
      output_usd_per_million_tokens: 15.00
    gpt-5.6-luna:
      input_usd_per_million_tokens: 1.00
      cached_input_usd_per_million_tokens: 0.10
      cache_write_input_usd_per_million_tokens: 1.25
      output_usd_per_million_tokens: 6.00
    gpt-5.6-sol:
      input_usd_per_million_tokens: 5.00
      cached_input_usd_per_million_tokens: 0.50
      cache_write_input_usd_per_million_tokens: 6.25
      output_usd_per_million_tokens: 30.00

source:
  reference_style: dbt_ref

output:
  format: dbt
  dialect: snowflake
```

Supported source styles:

- `relation`: use physical relation names such as `cai_01_patient`.
- `dbt_ref`: use `{{ ref('cai_01_patient') }}`.
- `dbt_source`: use `{{ source('source_name', 'cai_01_patient') }}` and set
  `source.source_name`.

Supported output formats are `sql` and `dbt`. Plain SQL requires
`reference_style: relation`.

The selected format controls the deterministic companion files:

- `dbt`: `{table}.sql` and `{table}.yml`.
- `sql`: `{table}.sql` plus `ddl/create_tables.sql`,
  `ddl/primary_keys.sql`, `ddl/foreign_keys.sql` and `ddl/indexes.sql`.

Only transformation SQL uses the AI provider. PostgreSQL, Snowflake and
BigQuery DDL is copied from pinned OHDSI assets. DDL for the other supported
platforms and all dbt YAML are generated deterministically from the pinned
target schemas.

The official assets retain the `@cdmDatabaseSchema` placeholder; render it
with the deployment schema before execution. Athena also requires
environment-specific S3 `LOCATION` clauses.

Supported dialects are `snowflake`, `postgres`, `athena`, `bigquery`,
`sql_server`, `spark`, `oracle`, `redshift` and `synapse`. The `spark` option
targets portable Databricks / Spark SQL.

## 5. Add or maintain source schemas

Create one YAML file per source model:

```text
specs/source_schema/{source_model}.yml
```

The filename must match the model name. For example:

```text
cai_01_patient → specs/source_schema/cai_01_patient.yml
```

Minimal structure:

```yaml
version: 2

models:
  - name: cai_01_patient
    description: Patient demographic source.
    columns:
      - name: patient_id
        data_type: integer
        description: Unique patient identifier.
        primary_key: true

      - name: year_of_birth
        data_type: integer
        description: Patient year of birth.
```

Declare every source column referenced by a mapping.

Declare known source relationships on the foreign-key column:

```yaml
      - name: patient_id
        data_type: integer
        description: Patient linked to this encounter.
        foreign_key:
          model: cai_01_patient
          field: patient_id
```

Foreign keys are optional metadata, but declaring them allows clients such as
the CardiacAI OMOP Studio source explorer to show trustworthy table
connections. Do not infer or declare a relationship from similar field names
alone.

## 6. Use the OMOP target schemas

The repository includes one generated YAML file for every OMOP CDM 5.4
target:

```text
specs/target_schema/{omop_table}.yml
```

The target schema controls:

- Output field names and order.
- SQL datatypes.
- Required and optional fields.
- Primary and foreign keys.
- OMOP descriptions and ETL conventions.

Do not edit generated target files individually. To refresh the complete
catalog from the pinned official OHDSI metadata, run:

```bash
python scripts/generate_target_schemas.py
```

The source version and normalization decisions are documented in
`specs/target_schema/README.md`.

## 7. Add or maintain the source-to-OMOP mapping

Create one mapping file per OMOP target:

```text
specs/mappings/{omop_table}.yml
```

The mapping filename and `target_table` must match the CLI table argument.

Example:

```yaml
version: 1
target_table: person
notes: Create one OMOP person per reconciled source patient.

source_models:
  - cai_01_patient

joins: []

fields:
  - target_field: person_id
    action: map
    source_fields:
      - model: cai_01_patient
        field: patient_id
    transformation: Cast patient_id to integer.
    comment: Preserve the source identifier for lineage.

  - target_field: year_of_birth
    action: derive
    source_fields:
      - model: cai_01_patient
        field: year_of_birth
    transformation: Cast year_of_birth to integer.

  - target_field: ethnicity_concept_id
    action: "null"
    source_fields: []
    transformation: Set to null because no valid source is available.

change_log:
  - date: 2026-08-04
    description: Added the initial person mapping.
    author: Data team
```

Mapping actions:

| Action | Meaning |
| --- | --- |
| `map` | Map exactly one source field |
| `derive` | Derive the target from one or more source fields |
| `"null"` | Deliberately output a typed `NULL` |

Always quote `"null"` because an unquoted YAML `null` means an empty YAML
value.

`notes`, field-level `comment` values and `change_log` entries are optional,
but they provide the narrative and audit content used by deterministic ETL
specification documents.

Rules for missing fields:

- A missing optional target field is automatically emitted as a typed `NULL`.
- A missing required target field fails validation.
- A required target may be deliberately mapped with `action: "null"`.

Transformation text is optional for a direct mapping with exactly one source
field. When omitted or blank, the agent maps that source field 1:1 and casts
only as needed to conform to the OMOP target datatype. Multi-source derivations
still require transformation instructions unless a mapping table defines the
lookup or each source field belongs to a different declared `union_all` branch.

### Source joins

Declare joins between source models:

```yaml
joins:
  - join_type: left
    left:
      model: source_patient
      field: patient_id
    right:
      model: source_visit
      field: patient_id
```

Only `inner` and `left` joins are supported. Cross joins and undeclared joins
fail SQL validation.

### UNION ALL source models

Use `union_all` when compatible source tables contain row sets that must be
stacked rather than joined:

```yaml
source_models:
  - current_patient
  - historical_patient
joins: []
union_all:
  - current_patient
  - historical_patient
```

Declare at least two models. Generated SQL must contain one explicit,
target-aligned `SELECT` branch per model and combine them with `UNION ALL`.
Plain `UNION` is rejected. A branch uses a typed `NULL` when a target field has
no source mapping for that branch.

### Mapping tables

Add `mapping_table_name` when a field requires a controlled lookup:

```yaml
- target_field: race_concept_id
  action: derive
  source_fields:
    - model: cai_01_patient
      field: indigenous_status
    - model: cai_01_patient
      field: country_of_birth
  transformation: Use the controlled race mapping relation.
  mapping_table_name:
```

A blank or null name is resolved automatically using:

```text
mapping_{target_table}_{target_field}
```

The example becomes:

```text
mapping_person_race_concept_id
```

The mapping relation must contain:

- One same-named lookup column for each declared source field.
- One result column with the target-field name.

The agent generates a left join to the mapping relation. It does not create
mapping files or invent clinical concept values.

### Review gates

Use a review gate for mappings requiring human confirmation:

```yaml
review_required: true
review_status: pending
review_comment: Confirm the supplied concept mapping.
```

Generation is blocked while any review is `pending`. After resolving the
comment and correcting the mapping, change the status to:

```yaml
review_status: approved
```

Approval should only be given after the mapping contains enough information to
produce safe SQL.

## 8. Validate locally

Activate the environment:

```bash
source venv/bin/activate
```

Validate configuration and specifications:

```bash
python -m agent.cli person --validate-only
```

This command does not call the API.

Fix every reported contract, missing-field or review error before continuing.

### Create an ETL specification locally

After validation and review approval, generate deterministic documentation:

```bash
python -m agent.cli person --etl-specification docx
```

Choose `md`, `docx` or `pdf`. The document is written beneath
`output/etl_specifications/` and includes a mapping diagram, field mapping
table, source relationships and change log. Every format uses the same complete
mapping-grid image; Word and PDF put it on a portrait page and use landscape
pages for the tables. It does not call OpenAI.

## 9. Run a dry-run

Preview the generation settings:

```bash
python -m agent.cli person --dry-run
```

This command does not call the API. Confirm that it reports:

```text
Generation readiness: ready
```

Also review:

- Provider and model.
- Output token limit.
- Maximum generation attempts.
- Worst-case output-token ceiling.
- SQL dialect and output format.
- Target output path.
- Initial request size.
- Estimated initial and maximum input tokens.
- Estimated maximum API cost and pricing verification date.

The initial request display is `characters used / configured character limit`.
Context characters are included in that request and are not a separate limit.
The maximum run output is calculated as:

```text
output tokens per response × attempts × (automatic retries + 1)
```

## 10. Generate SQL

For a low-cost first attempt using the current 800-token request limit:

```bash
python -m agent.cli person \
  --generate \
  --max-iterations 1 \
  --max-run-output-tokens 800
```

The run-token ceiling must be at least:

```text
max_output_tokens × max_iterations × (max_api_retries + 1)
```

For two generation attempts with the current configuration:

```bash
python -m agent.cli person \
  --generate \
  --max-iterations 2 \
  --max-run-output-tokens 1600
```

The cost shown by preflight is a conservative estimate, not a billing
guarantee. It assumes the configured attempts and retries could be used.
Keep automatic API retries disabled while controlling development spending.

## 11. Review the result

A successful `person` run always creates or replaces:

```text
output/person.sql
```

dbt output also creates `output/person.yml`. Plain SQL output refreshes the
four files under `output/ddl/`. Files are promoted only after local SQL
validation passes. A failed run does not replace the last valid transformation.

Review the generated SQL for:

- Correct source relations or dbt references.
- Exact target field order.
- Correct joins and mapping-table lookups.
- Correct OMOP concept IDs and transformations.
- Typed nulls.
- No invented source fields or mappings.

The agent does not execute the SQL. Test it separately in a controlled
development environment before production use.

## 12. Review the run log

Every API-backed run writes a protected JSON transcript:

```text
logs/{omop_table}_{timestamp}.json
```

The log records:

- Agent and tool messages.
- Local validation feedback.
- Number of successful API responses.
- Input, cached-input, output, reasoning and total token usage.
- Estimated API cost calculated from that measured usage.

Logs are gitignored. Treat them as potentially sensitive operational data.

## 13. Useful command overrides

Generate plain Snowflake SQL using direct relations:

```bash
python -m agent.cli person \
  --dry-run \
  --output-format sql \
  --source-style relation
```

Generate a dbt model using `ref()`:

```bash
python -m agent.cli person \
  --dry-run \
  --output-format dbt \
  --source-style dbt_ref
```

Use a dbt `source()` reference:

```bash
python -m agent.cli person \
  --dry-run \
  --output-format dbt \
  --source-style dbt_source \
  --source-name cardiac_ai
```

Replace `--dry-run` with `--generate` and add the required token ceiling only
after checking the preflight output.

## 14. Run the automated tests

After changing the agent implementation or contracts:

```bash
python -m unittest discover -s tests -v
```

Specification-only changes should at minimum pass:

```bash
python -m agent.cli person --validate-only
python -m agent.cli person --dry-run
```

## 15. Troubleshooting

### Pending mapping reviews

```text
Generation blocked by pending mapping reviews
```

Resolve the listed review comments and set each reviewed mapping to
`review_status: approved`.

### Run-token ceiling is too low

```text
Configured generation can use up to ... output tokens
```

Increase `--max-run-output-tokens`, reduce `--max-iterations`, or reduce
`max_output_tokens` in `config.yaml`.

### No output was promoted

Inspect the latest JSON file under `logs/`. The tool response lists the exact
SQL-validation failures. Correct the specifications before paying for another
generation attempt.

### API quota or authentication failure

Check that `.env` contains a valid `OPENAI_API_KEY` and that the associated
OpenAI project has available API quota.

### Virtual environment points to an old path

Python virtual environments can contain absolute paths. If the repository is
moved or renamed, recreate it:

```bash
deactivate
python3.12 -m venv --clear venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

## 16. Recommended workflow checklist

For each OMOP table:

1. Create or update every referenced source-schema YAML.
2. Create or verify the OMOP target-schema YAML.
3. Create or update the source-to-OMOP mapping YAML.
4. Resolve mapping comments and approve review gates.
5. Run `--validate-only`.
6. Run `--dry-run` and confirm readiness and cost limits.
7. Run one controlled `--generate` attempt.
8. Review `output/{omop_table}.sql`.
9. Inspect the run log and token usage.
10. Test the SQL in a controlled development environment.
