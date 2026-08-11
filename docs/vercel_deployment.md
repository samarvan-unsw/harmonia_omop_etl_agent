# Vercel Agent API Deployment

The Python agent API deploys as a separate Vercel project from the Next.js UI.
The local CLI workflow remains unchanged.

## Deployment contents

`Dockerfile.vercel` packages only:

- The `agent/` Python package.
- Agent-owned OMOP target schemas.
- `config.yaml`.
- Pinned runtime dependencies.

Local source schemas, mappings, generated output, logs, tests, documentation,
virtual environments and `.env` files are excluded from the image.

Vercel Functions have a read-only application filesystem. The container sets
`AGENT_OUTPUT_DIR` to an application-specific directory under `/tmp` for
short-lived SQL candidates. Validated API output is returned to the UI and
stored in Supabase; container files are not persistent.

## Create the Vercel project

1. Push this repository to GitHub.
2. In Vercel, create a new project from the agent repository.
3. Keep the repository root as the Vercel root directory.
4. Confirm that Vercel detects `Dockerfile.vercel`.
5. Enable Fluid Compute.

## Configure secrets

Add these server-side environment variables to the agent Vercel project:

```text
AGENT_API_TOKEN=<a private token of at least 32 characters>
```

Never prefix this variable with `NEXT_PUBLIC_`. Configure production and
preview environments deliberately; use separate tokens when isolation is
required.

No provider key is required in Vercel for bring-your-own-key runs. The UI
forwards the user-entered OpenAI or Anthropic key to `/v1/generate` in a
private header for that request only; the agent does not persist or log it.
`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` remain supported for local CLI use.

## Connect the UI

In the UI Vercel project, set:

```text
AGENT_API_BASE_URL=https://<agent-project>.vercel.app
AGENT_API_TOKEN=<the same private token used by the agent API>
```

Redeploy the UI after changing environment variables.

## Verify

The health check is free and does not call OpenAI:

```bash
curl https://<agent-project>.vercel.app/health
```

Expected:

```json
{"service":"cardiac-ai-omop-agent","status":"ok"}
```

Then run Validate and Preflight; neither calls OpenAI. Generate only after
token-ceiling confirmation.

## Local development

```bash
source venv/bin/activate
python -m agent.cli person --dry-run
uvicorn agent.api:app --reload --env-file .env
```

Without `AGENT_OUTPUT_DIR`, the CLI continues to use `output/`.

## Production limitation

The current generation semaphore is process-local. Vercel may start multiple
function instances, so enforce a distributed queue or lock before enabling
concurrent multi-user generation. Keep an OpenAI project hard spend limit even
when per-run token ceilings are enabled.
