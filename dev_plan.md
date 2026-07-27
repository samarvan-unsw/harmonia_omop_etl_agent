
High-level fix plan

1. Make the application runnable
   Fix missing imports and undefined functions.
   Implement TOOL_SCHEMAS and tool dispatch.
   Correct provider loading.
   Move config.yaml to the expected root location.
   Install and pin dependencies.
   Remove duplicated context code from the provider.
   Add the missing Anthropic provider or remove the unsupported option.
   Outcome: python -m agent.cli person starts successfully.
2. Define strict specification contracts
   Create validated YAML formats for:
   Source tables: relation name, columns, SQL types, descriptions, keys.
   Mappings: source fields, joins, transformations, defaults and target fields.
   OMOP targets: required columns, types, nullability, keys, CDM version and row grain.
   Add preflight validation that rejects unknown tables, fields, invalid joins, duplicate targets and missing required OMOP fields before contacting the model.
   Outcome: bad specifications fail early with useful errors.
3. Establish one output convention
   Recommended structure:
   output/dbt_project.yml
   output/models/staging/
   output/models/marts/omop/
   output/models/schema.yml
   Treat output/ as a complete generated dbt project. Keep specifications and handwritten application code outside it.
   Outcome: generated SQL has one clear location and can be validated directly.
4. Rebuild the generation loop
   Use an application-controlled state machine:
   Validate specifications.
   Build relevant context.
   Ask the provider to generate files.
   Validate requested file paths.
   Write into a temporary run directory.
   Run dbt validation and capture exit codes.
   Return errors to the model for correction.
   Stop after success or the configured retry limit.
   Promote successful files into output/.
   Do not let the model decide whether compilation happened or whether the run succeeded.
   Outcome: predictable generate → validate → revise behaviour.
5. Harden providers and tools
   Move OpenAI to a supported model and the Responses API.
   Give every provider the same canonical request/response contract.
   Add API timeout, retry and malformed-response handling.
   Replace command prefix matching with structured, fixed dbt commands.
   Restrict writes to the expected model files.
   Ensure dbt Core from the active venv is used rather than Fusion.
   Prevent concurrent runs from overwriting the same table.
   Outcome: safer and provider-independent execution.
6. Add meaningful validation
   Use multiple validation levels:
   YAML structure and cross-reference validation.
   OMOP required-column and data-type validation.
   dbt parse and dbt compile.
   Warehouse validation against a controlled development target.
   dbt tests for uniqueness, nullability, accepted values and relationships.
   Mapping coverage report showing mapped, defaulted and unresolved OMOP fields.
   Compilation alone should not be treated as proof of a correct transformation.
7. Add tests and operational controls
   Minimum tests:
   Source and mapping loader tests.
   Invalid field/table detection.
   Context selection tests.
   Path traversal and command restriction tests.
   Mocked provider retry-loop tests.
   Successful and failed dbt-run integration tests.
   Also add:
   .gitignore for .env, venv, logs and generated artifacts as appropriate.
   Redacted structured logs.
   Run IDs and specification/model version hashes.
   Dependency pins and documented setup commands.
   Clear failure messages and cleanup of unsuccessful temporary output.
   Suggested implementation order
   Runnable imports, configuration and dependencies.
   Specification contracts and the corrected person.yml.
   Minimal output dbt project.
   Deterministic generation loop.
   OpenAI provider correction.
   dbt validation and tests.
   Logging, security and operational hardening.
   The main open design decision is whether the cai_* inputs are existing dbt models or physical database tables. That determines whether generated staging SQL should use ref() or source().

Future improvements (not essential for the prototype)

- Add reproducibility metadata to each secure run log:
  - Run ID and timestamp.
  - Provider and model.
  - SQL dialect and output format.
  - Source-schema, mapping and target-schema file hashes.
  - Generated SQL file hash.
- Add price-aware cost estimates alongside measured token usage.
- Add specification versioning and a mapping coverage report.
- Add optional warehouse-based data-quality validation when executable data
  access becomes available.
- Implement and test additional providers only when they are needed.
