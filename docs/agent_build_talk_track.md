# How to Build a Controlled AI Agent

## Purpose

This guide explains a reusable approach for building small, production-minded
AI agents. It is domain-neutral and can be applied to agents that create code,
transform documents, analyse specifications, prepare reports or support other
bounded workflows.

The central principle is:

> An agent is not just a model and a prompt. It is a controlled software
> system that gives a model a bounded objective, trusted context, restricted
> tools, validation, state and human oversight.

## The basic agent formula

```text
Agent = model
      + instructions
      + context
      + tools
      + orchestration loop
      + deterministic guardrails
      + state and audit history
```

The model provides reasoning and generation. The surrounding application owns
authority, security, workflow order, validation and persistence.

## First decide whether an agent is appropriate

An agent is useful when a task:

- requires interpretation rather than only fixed rules;
- varies enough that conventional templates become difficult to maintain;
- can be divided into bounded actions;
- produces an output that can be checked; and
- benefits from iteration after receiving feedback.

Use conventional software instead when the complete solution can be expressed
reliably with deterministic rules. Do not introduce an agent merely because a
model can perform the task.

## Reference architecture

```text
User or system request
          │
          ▼
Input contract and authorization
          │
          ▼
Deterministic pre-validation
          │
          ▼
Trusted context assembly
          │
          ▼
System instructions + bounded model request
          │
          ▼
Restricted tool call or candidate output
          │
          ▼
Deterministic output validation
          │
     ┌────┴────┐
     │         │
   valid     invalid
     │         │
     │         ▼
     │    bounded feedback
     │    and retry, if allowed
     ▼
Human approval when required
          │
          ▼
Safe persistence + audit record
```

This pattern keeps the probabilistic part inside a deterministic process.

## The main design pillars

### 1. Define one bounded objective

Write a precise statement describing:

- what the agent receives;
- what it must produce;
- what counts as success;
- what it may change or access;
- what it must never do; and
- when a human must intervene.

Prefer a narrow objective such as “produce one validated transformation file”
over a broad objective such as “manage the entire data platform.”

Narrow agents are easier to secure, test, operate and improve.

### 2. Separate the model from the application

The application should control:

- authentication and authorization;
- input loading and validation;
- prompt and context construction;
- tool definitions and permissions;
- workflow state;
- retry and cost limits;
- output validation;
- persistence; and
- logging and monitoring.

The model should be treated as one replaceable component. Use a provider
interface so the rest of the application is not tightly coupled to one model
SDK.

### 3. Contracts: Create strict input and output contracts

Use typed schemas for every important boundary:

- configuration;
- user-maintained specifications;
- API requests and responses;
- tool arguments and results;
- model output; and
- persisted run records.

Contracts should reject unknown properties, unsafe identifiers, oversized
documents and invalid field combinations.

Structured contracts are more reliable than asking the model to infer the
shape of unstructured files.

### 4. Guardrail: Distinguish trusted instructions from untrusted data

System instructions define the agent’s role and rules. User content,
descriptions, comments and uploaded documents are data—even when they contain
text that looks like instructions.

Use explicit boundaries around untrusted content and state that it must not
override system rules, request secrets, expand tool access or change the
agent’s role.

Validate and minimise context before sending it to the model.

### 5. Prompt design: Use prompts for judgement, not guarantees

The prompt is a core behaviour layer. It should clearly define:

- the agent's role and bounded objective;
- the expected output;
- the available tools and when to use them;
- how to handle ambiguity or missing information;
- prohibited behaviour; and
- the conditions for stopping or escalating.

Keep prompts concise, version-controlled and testable. Separate stable system
instructions from task-specific context, and clearly delimit untrusted data.
Never include secrets in prompts.

Do not use the prompt as the only enforcement mechanism. Permissions,
contracts, budgets, validation and publishing rules must remain in application
code.

> Prompt for judgement; use code for guarantees.

### 6. Tools: Give the minimum necessary tools

Tools turn model output into actions, so they are an important security
boundary.

For each tool, define:

- a narrow purpose;
- a strict argument schema;
- allowed targets;
- size and timeout limits;
- safe error messages;
- whether the operation is read-only or mutating; and
- whether human approval is required.

Prefer specialised tools such as `write_candidate_report` over unrestricted
filesystem or shell access.

The application must validate tool arguments itself. Never rely on the model
to obey a path, identifier or permission convention.

### 7. Orchestration: Keep orchestration deterministic

Represent the workflow as an explicit state machine or bounded loop. A common
sequence is:

1. validate inputs;
2. assemble the minimum context;
3. request a candidate output;
4. validate the candidate;
5. return bounded, actionable errors;
6. retry only when permitted; and
7. promote or publish only after validation succeeds.

The model can choose content or a permitted tool, but it should not control
authentication, limits, acceptance criteria or workflow completion.

### 8. Validation: Use deterministic checks wherever possible

Known rules belong in code, not in model judgement.

Examples include:

- schema validation;
- required-field checks;
- identifier and path safety;
- syntax parsing;
- reference and lineage checks;
- output completeness;
- policy checks;
- duplicate detection;
- file-size limits; and
- checksums or version matching.

Model-based review may supplement these checks, but it should not replace
rules that can be tested deterministically.

### 9. Human in the loop: Treat human review as part of the architecture

Define which decisions can be automated and which need approval. Human review
is particularly important when an output:

- affects clinical, financial or legal decisions;
- changes external systems;
- cannot be fully validated automatically;
- contains ambiguous domain interpretation; or
- is expensive or difficult to reverse.

Store the approval decision with the exact input and output versions it
applies to. A changed input should invalidate stale approval when relevant.

### 10. Cost: Control cost, latency and retries

Set explicit limits for:

- input size;
- output tokens;
- generation attempts;
- provider retries;
- tool calls;
- execution time; and
- concurrent runs.

Provide a free preflight stage that reports readiness and the worst-case run
ceiling before a paid request.

Retries should target temporary failures. Invalid output should consume a
separate, small generation-attempt allowance rather than uncontrolled API
retries.

### 11. Make runs reproducible and auditable

Record enough information to explain what happened without storing secrets or
unnecessary sensitive content.

A run record normally includes:

- run identifier and timestamps;
- status and terminal reason;
- input specification versions or hashes;
- model and relevant settings;
- validator results;
- output artifact versions;
- token usage and estimated cost; and
- approval or promotion status.

Avoid logging API keys, credentials, raw authorization headers or sensitive
prompt content.

### 12. Agent loop: Design for failure and recovery

Define behaviour for:

- malformed input;
- unavailable model providers;
- authentication failures;
- rate limits and exhausted quota;
- timeouts;
- malformed tool arguments;
- invalid model output;
- concurrent updates;
- persistence failures; and
- partial external actions.

Use idempotency keys for externally triggered runs. Keep candidate output
separate from accepted output so a failed run cannot overwrite the last valid
result.

### 13. Keep the user interface optional

When practical, keep the agent engine independent from its UI. The engine can
provide a CLI and a versioned API, while a separate application manages
authentication, projects and a non-technical user experience.

This separation allows:

- local engineering use without the UI;
- independent deployment and scaling;
- multiple future clients;
- clearer ownership boundaries; and
- one authoritative implementation of agent rules.

The UI should call the agent rather than copy its validation or prompting
logic.

## Step-by-step implementation plan

### Step 1: Write the objective and authority boundary

Produce a short design note containing:

- the exact outcome;
- users and triggering systems;
- allowed data and actions;
- prohibited actions;
- success criteria; and
- required approvals.

Do this before selecting a framework or model.

### Step 2: Define the contracts

Create typed models for configuration, input specifications, tool calls,
candidate output and final responses.

Add positive and negative fixtures. Confirm that unsafe, incomplete and
unexpected input fails with useful messages.

### Step 3: Build deterministic validation first

Implement every acceptance rule that does not need AI. This creates a stable
definition of “valid” before generation is introduced.

Validation should be callable independently and should not require an API key.

### Step 4: Build context assembly

Load only the files and records required for the selected task. Normalise their
order, remove irrelevant fields and calculate the request size.

Make context construction deterministic so the same inputs produce the same
request structure.

### Step 5: Write the system instructions

The system prompt should state:

- the role and single objective;
- exact output requirements;
- allowed tools;
- prohibited actions;
- how to handle missing information;
- how untrusted content must be treated; and
- when to stop.

Keep domain data outside the system instructions and place it in a clearly
delimited context block.

### Step 6: Define restricted tools

Start with the smallest toolset possible. Validate all arguments in application
code and test path traversal, invalid identifiers, oversized content and
unauthorized targets.

If the agent only needs to return structured text, it may not need any mutating
tool at all.

### Step 7: Implement the bounded loop

Add one model request and validate the response. Then add a small revision
allowance only if validator feedback can materially improve the result.

Do not start with an open-ended autonomous loop.

### Step 8: Add preflight and explicit execution controls

Preflight should confirm:

- input validity;
- unresolved human reviews;
- model and provider availability;
- prompt size;
- maximum attempts and retries;
- output-token ceiling; and
- estimated cost where pricing is available.

Require explicit authorization before expensive or externally mutating work.

### Step 9: Add safe persistence

Version inputs and store accepted outputs separately from candidates. Use
transactions or atomic promotion where possible.

Persist terminal failure status as well as success so operators can understand
missing outputs.

### Step 10: Add the API and UI

Expose narrow, versioned endpoints such as:

- validate;
- preflight;
- generate;
- inspect status; and
- download accepted artifacts.

Authenticate every non-public endpoint. Keep server-owned limits authoritative
even when the UI offers configurable choices.

### Step 11: Add operational controls

Add:

- structured logs;
- request and run identifiers;
- latency and error metrics;
- provider usage metrics;
- concurrency limits;
- health checks; and
- alerts for repeated failures or unusual cost.

Define who owns the agent and how incidents are handled.

### Step 12: Release gradually

Begin with low-risk users and a narrow task set. Review failures and accepted
outputs, then expand scope only after evidence shows that the controls are
working.

Do not broaden tool authority merely because the model performs well in a
small demonstration.

## Testing strategy

Most tests should not call a live model.

### Contract tests

Test valid, missing, malformed, oversized and unexpected inputs.

### Tool security tests

Test unauthorized targets, unsafe paths, malformed arguments, timeouts and
size limits.

### Orchestration tests

Use a fake provider to return:

- valid output;
- invalid output followed by valid revision;
- malformed tool arguments;
- empty output;
- provider errors; and
- exhausted attempts.

Confirm that invalid candidates never become accepted output.

### Validator tests

Create focused fixtures for each acceptance rule and error message.

### API tests

Test authentication, authorization, request limits, safe error responses and
idempotent behaviour.

### End-to-end tests

Use a small number of representative tasks in a controlled environment. Live
model tests should be bounded and separated from the main unit-test suite
because they are slower, cost money and may vary.

### Adversarial tests

Include prompt-injection text in uploaded content, attempts to access secrets,
tool-argument manipulation and requests to exceed the assigned role.

## Security checklist

- Keep secrets in an approved secret manager or environment configuration.
- Never expose provider keys to the browser.
- Authenticate and authorize every agent action.
- Validate all external input at the boundary.
- Treat uploaded content as untrusted.
- Use least-privilege service accounts and tools.
- Restrict filesystem, network and database targets.
- Avoid sensitive prompt and response logging.
- Redact provider errors before returning them to users.
- Limit document size, token usage, runtime and concurrency.
- Require approval for destructive or consequential actions.
- Document data retention and deletion behaviour.

## Common anti-patterns

### “The prompt will keep it safe”

Prompts are behavioural guidance, not a security boundary. Enforce permissions
and limits in code.

### Giving the model a general shell

Broad tools create unnecessary authority. Replace them with narrow,
purpose-built operations.

### Letting the model decide whether its own output is valid

Use independent validators and explicit acceptance criteria.

### Retrying until something works

Unbounded retries hide defects and increase cost. Use small, separate limits
for transient API failures and output revision attempts.

### Mixing editable drafts with accepted outputs

Keep candidates, approved artifacts and historical versions separate.

### Duplicating agent rules in the UI

This creates inconsistent sources of truth. The UI may provide fast local
feedback, but authoritative rules should remain in the agent service.

### Starting with multiple agents

Multi-agent designs add coordination, latency, cost and failure modes. Start
with one bounded agent. Add another only when responsibilities are genuinely
independent and the benefit is measurable.

## A practical maturity model

### Level 1: Prototype

- one bounded task;
- manually supplied context;
- no external mutations;
- human review of every result; and
- basic output validation.

### Level 2: Controlled internal tool

- typed contracts;
- restricted tools;
- deterministic preflight and validation;
- bounded cost and retries;
- authentication;
- versioned inputs and outputs; and
- test coverage for failure paths.

### Level 3: Production service

- strong authorization and tenant isolation;
- idempotency and concurrency handling;
- monitoring and alerting;
- operational ownership;
- retention and recovery procedures;
- quality evaluation against representative datasets; and
- staged releases and rollback.

Move between levels based on risk and evidence, not feature count.

## How to explain agent development to another person

Use this short talk track:

1. **Start with the problem.** Explain why fixed rules alone are insufficient.
2. **Define the boundary.** State exactly what the agent may and may not do.
3. **Show the contracts.** Explain how structured inputs reduce ambiguity.
4. **Explain the prompt.** Show how it guides judgement without replacing code.
5. **Show the workflow.** Emphasise that application code controls the loop.
6. **Show the guardrails.** Demonstrate validation before and after the model.
7. **Show a failure.** Explain how invalid output is rejected safely.
8. **Show the audit record.** Connect output to input versions and usage.
9. **Acknowledge limitations.** Describe where human judgement remains.

The most important statement to communicate is:

> Use AI for the part that requires interpretation. Keep authority,
> validation, security, workflow control and reproducibility in conventional
> software.

## Design review questions

Before approving an agent, ask:

- Is the objective narrow and measurable?
- Could deterministic software solve the whole task more reliably?
- What information is trusted, and what is untrusted?
- What is the maximum authority of each tool?
- How is output validated independently?
- What prevents an invalid candidate from being published?
- When is human approval required?
- What is the worst-case cost and runtime?
- How are retries and concurrency bounded?
- Can a run be reproduced from recorded versions?
- What sensitive information is processed or logged?
- How does the system recover from partial failure?
- Who owns monitoring, incidents and future changes?

If these questions do not have clear answers, the agent is not ready for
production use.

## Closing statement

> A good agent is not the most autonomous one. It is the smallest system that
> uses model reasoning where it adds value while remaining controlled,
> testable, observable and accountable.
