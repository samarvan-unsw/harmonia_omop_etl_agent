# OMOP SQL Agent

This small agent converts validated source-schema and mapping specifications
into one SQL file per OMOP table.

For example, a `person` run produces:

```text
output/person.sql
```

The project does not create or execute a dbt project. Generated SQL receives
local static validation but is not run against a database.

## Inputs

Each OMOP table uses three specification types:

```text
specs/
├── source_schema/   # Source model names, columns, types and descriptions
├── mappings/        # Source-to-OMOP mappings, joins and transformations
└── target_schema/   # OMOP fields, types, requirements and keys
```

The mapping lists its `source_models`. Each model is loaded from a YAML file
with the same name, such as:

```text
cai_01_patient → specs/source_schema/cai_01_patient.yml
```

Mapping rules:

- Missing optional OMOP fields are emitted as a `NULL` cast to the target
  datatype.
- Missing required OMOP fields fail validation.
- A required field may be explicitly assigned `action: "null"` by the user.
- Quote `"null"` because unquoted YAML `null` is interpreted as an empty value.
- Unknown source models, source fields and OMOP target fields fail validation.
- A field that needs a lookup declares `mapping_table_name`. An explicit blank
  or null value is resolved to `mapping_{target_table}_{target_field}`; omitting
  the property means no mapping table is required.
- Mapping-table names are logical relation names only. The agent does not create
  lookup CSV files or invent clinical mapping values.
- A mapping with `review_required: true` must declare `review_status` as
  `pending` or `approved`.
- Any `pending` review blocks generation. Resolve the comment, update the
  mapping if needed, and set `review_status: approved` before using the API.

## Configuration

`config.yaml` controls:

- OpenAI model
- Maximum output tokens per API request
- Maximum initial prompt characters
- Automatic API retries
- Source reference style: `relation`, `dbt_ref` or `dbt_source`
- Output format: `sql` or `dbt`
- SQL dialect

Development defaults intentionally limit spending:

```yaml
max_output_tokens: 800
max_initial_prompt_characters: 12000
max_api_retries: 0
```

The CLI allows at most two generation attempts by default. Increase these
limits deliberately if a complete SQL file cannot fit within the development
cap. The character limit includes the initial system prompt, specification
context and tool schemas. It is a deterministic size guard, not an exact token
or dollar estimate.

## Commands

Activate the environment:

```bash
source venv/bin/activate
```

Validate configuration and specifications locally:

```bash
python -m agent.cli person
```

This is the default behaviour and does not call the API.

Preview the validated generation settings and cost controls:

```bash
python -m agent.cli person --dry-run
```

This preflight also makes no API call.

Explicitly permit SQL generation:

```bash
python -m agent.cli person --generate --max-run-output-tokens 400
```

Generation first requires every mapping review to be approved.
The explicit ceiling must be at least:
`max_output_tokens × max_iterations × (max_api_retries + 1)`. It limits
generated tokens, including reasoning tokens, but not billed input tokens.
Use an OpenAI project hard spend limit when an exact monthly dollar cap is
required. The generated file must pass static SQL validation for syntax,
one-statement structure, explicit fields, exact target coverage,
target-schema order, mapping actions, source-field lineage, source relations
and declared joins. Null expressions must be cast to the target datatype.
Cross joins and undeclared source relations are rejected.

After generation, the CLI prints measured token usage and the secure JSON
transcript records per-response and run totals for input, cached input,
cache-write input, output, reasoning output and total tokens. Cached input and
reasoning output are subsets of the corresponding totals, not extra tokens.

Run the local test suite:

```bash
python -m unittest discover -s tests -v
```

## Main components

| Path | Purpose |
| --- | --- |
| `agent/contracts.py` | Strict configuration and specification contracts |
| `agent/validation.py` | Cross-file source, mapping and target validation |
| `agent/context.py` | Compact validated prompt context |
| `agent/prompts.py` | Fixed prompt rules and untrusted-data boundaries |
| `agent/input_guard.py` | Local initial-request size enforcement |
| `agent/providers/` | Provider interface and OpenAI Responses API adapter |
| `agent/tools.py` | Restricted reads and writes for `output/{table}.sql` |
| `agent/sql_validation.py` | Deterministic static SQL validation |
| `agent/loop.py` | Bounded generate, write, validate and revise loop |
| `agent/cli.py` | Safe-by-default command-line entry point |
| `logs/` | Secure transcripts with measured per-run token usage |
| `output/` | Final generated SQL files |

Specification text is passed to the model inside an untrusted-data boundary.
Only the fixed system prompt controls tools, output paths and agent behaviour.
