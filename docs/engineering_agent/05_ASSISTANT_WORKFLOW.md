# Proposed assistant planning workflow

This document describes the agreed target workflow. It is a design contract,
not an implemented feature of the current SDK.

## Component roles

### Existing planning SDK

Continues to provide deterministic workspace/project access and network-service
calls. It should remain usable without AI.

### Assistant SDK

Runs locally in the user's Python/Jupyter environment and imports the planning
SDK. It owns:

- reading raw site data and local catalogues;
- maintaining the assistant conversation/session identifier;
- sending structured context to the shared assistant service;
- validating returned proposals;
- returning recoverable errors to the AI for correction;
- asking the user only for missing domain information or authorization;
- creating a new project directory for each accepted revision;
- executing through the planning SDK;
- sending structured results for AI evaluation.

### Shared assistant/MCP service

Provides access to the AI and current planning knowledge/tools. It is external
to individual Jupyter containers and does not write local user files. It returns
structured project proposals, questions, evaluations, and revision proposals.

MCP is an integration mechanism rather than the source of intelligence. A first
manual prototype can use the same instructions and file exchange without MCP.

## End-to-end loop

1. The user places a minimal site CSV in `workspace/data` with site identity,
   coordinates, and mounting height.
2. The user describes the technology, radio types, planning objectives, and
   which sites use which templates.
3. The assistant SDK submits the prompt, normalized site data, relevant schema
   version, and available catalogues. The AI asks for information that cannot
   be inferred safely.
4. The AI returns a structured proposal containing scenario configuration,
   normalized sites/template assignments, assumptions, and warnings.
5. The assistant SDK validates the proposal. Recoverable errors return directly
   to the AI until the proposal is valid or a bounded retry limit is reached.
6. After user authorization, the SDK creates a new project folder and writes
   `scenario.toml` plus `nodes.csv` or embedded sites.
7. The user executes the project through a simplified assistant SDK operation,
   which delegates planning to the existing SDK and persists features/results.
8. The assistant SDK submits the scenario, objectives, structured evaluation,
   and relevant result records to the AI.
9. The AI returns a human-readable assessment, unmet objectives, unserved or
   weak sites, and proposed modifications with reasons.
10. The user rejects, edits, or authorizes the proposal. Authorization produces
    a new project folder, and the cycle repeats.

## User-visible versus machine-visible failures

Recoverable configuration and validation failures go to the AI correction loop,
not directly to the user. Examples include unknown profile references, missing
scenario fields, invalid enum/value shapes, and inconsistent generated IDs.

The user must still be informed when:

- required planning intent is missing;
- constraints conflict;
- authentication or a service is unavailable;
- the input file cannot be read safely;
- bounded AI correction attempts fail;
- a proposed change requires authorization.

Messages shown to the user should explain the planning decision needed rather
than expose a Python exception.

## Evaluation contract

The AI should receive structured scenario and result data, not depend on a plot.
At minimum the evaluation context should include:

- planning objectives and acceptance thresholds;
- scenario and template assignments;
- deterministic quality summary and per-interface quality;
- served/unserved assignments;
- selected parent links and edge metrics;
- gateway/root roles, relay capability, frequency, mounting heights, and link
  limits;
- differences from the preceding revision.

Every recommendation should return:

- evidence from the result;
- proposed scenario/site change;
- expected effect;
- assumptions and possible tradeoffs;
- whether explicit user authorization is required.

## Revision behavior

Each authorized revision creates a new project folder. A proposal should carry
an immutable proposal ID and identify its parent project/revision. The SDK should
write only validated, authorized proposals and record enough metadata to compare
the generated project with its parent.

Exact naming, metadata filename, and retention policy remain open design items.

