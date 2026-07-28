# Development Roadmap

The current repository is a working bounded agent, not an unfinished dbt
project. This roadmap records optional improvements without changing the
supported workflow described in the user and technical guides.

## Near-term improvements

- Record source-schema, mapping, target-schema and SQL hashes in secure run
  logs for stronger reproducibility.
- Add a mapping coverage report to the CLI and HTTP validation response.
- Validate source foreign-key targets across the complete project catalog.
- Add multi-process generation locking if the API is scaled beyond one
  process.

## Later improvements

- Add optional warehouse-backed compilation and data-quality validation.
- Add price-aware estimates alongside measured provider token usage.
- Add another provider only when there is a tested operational requirement.
- Support explicitly modelled composite source relationships.

## Guardrails for future work

- Keep the Python agent as the source of truth for contracts, prompts,
  validation and generation limits.
- Keep the UI in its independent repository and communicate through the
  versioned HTTP API.
- Preserve local CLI operation when extending hosted functionality.
- Reject invalid specifications before any paid provider request.
- Add tests for every contract or validation change.
