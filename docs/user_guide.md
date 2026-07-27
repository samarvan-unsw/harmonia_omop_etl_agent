# Cardiac AI OMOP Agent User Guide

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
| `output/` | Generated SQL |
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

Supported dialects are `snowflake`, `postgres`, `athena` and `bigquery`.

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

## 6. Add or maintain an OMOP target schema

Create one YAML file per OMOP target:

```text
specs/target_schema/{omop_table}.yml
```

Minimal structure:

```yaml
version: 1
cdm_version: "5.4"
target_table: person

fields:
  - name: person_id
    data_type: integer
    required: true
    primary_key: true
    description: Unique person identifier.

  - name: year_of_birth
    data_type: integer
    required: true
```

The target schema controls:

- Output field names and order.
- SQL datatypes.
- Required and optional fields.
- Primary and foreign keys.
- OMOP descriptions and ETL conventions.

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
```

Mapping actions:

| Action | Meaning |
| --- | --- |
| `map` | Map exactly one source field |
| `derive` | Derive the target from one or more source fields |
| `"null"` | Deliberately output a typed `NULL` |
| `skip` | Skip an unavailable optional value and output a typed `NULL` |

Always quote `"null"` because an unquoted YAML `null` means an empty YAML
value.

Rules for missing fields:

- A missing optional target field is automatically emitted as a typed `NULL`.
- A missing required target field fails validation.
- A required target may be deliberately mapped with `action: "null"`.

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

The ceiling limits output tokens, not input-token charges. Keep automatic API
retries disabled while controlling development spending.

## 11. Review the result

A successful `person` run creates or replaces:

```text
output/person.sql
```

The file is promoted only after local SQL validation passes. A failed run does
not replace the last valid output.

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
