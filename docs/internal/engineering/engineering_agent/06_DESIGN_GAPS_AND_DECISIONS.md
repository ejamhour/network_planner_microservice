# Design decisions and unresolved gaps

## Confirmed decisions

- The planning client connects to a workspace; a project becomes active through
  `open_project`.
- A client may work with several projects sequentially.
- Features and results belong to a project.
- Shared antenna and metric libraries remain at workspace level.
- Custom antenna/metric uploads affect only an in-memory scenario and are not a
  permanent server-library update.
- Raw geographic features are persisted separately from computed metrics.
- A planning revision creates a new project folder.
- The shared AI/MCP service does not write user files.
- The assistant SDK owns local I/O and delegates deterministic planning to the
  existing SDK/network service.
- Recoverable generated-configuration errors return to the AI correction loop.
- A Custom GPT with Markdown knowledge is suitable for designing and manually
  testing the workflow before MCP automation.

## Highest-priority gaps

### Formal scenario schema

There is no published versioned JSON Schema. The schema is distributed among
`PlanningScenario`, supporting dataclasses, planner validation, and examples.
The team should define a machine-readable schema from the implementation and
verify it against the canonical loader.

### Structured error model

Planning endpoints currently reduce most failures to text. Define a compatible
error object with at least code, stage, path, entity, message, retryable flag,
and optional suggestions.

### Assistant response contracts

Define versioned structures for:

- missing-information questions;
- project proposals;
- validation feedback;
- project creation acknowledgement;
- result-evaluation requests;
- recommendations and revision proposals.

### Identity and sessions

Separate these identities:

- authenticated human/account;
- assistant conversation;
- local workspace/project revision;
- network-service worker;
- features-service worker pool.

The current generated hub token and shared default feature-worker prefix are not
the final multi-user design.

### Catalogue discovery

The assistant needs current built-in and workspace-supplied antenna/metric
catalogues. Define how the local SDK sends workspace additions and how the
service publishes built-in catalogue metadata without transferring executable
implementation details unnecessarily.

### Minimal CSV normalization

The desired raw CSV lacks `device_profile`, but the executable project CSV needs
it. Define accepted aliases, coordinate validation, mounting-height units,
duplicate handling, template assignment, and the normalized CSV output.

### Objective model

Define machine-readable objectives for coverage, rank, hops, redundancy,
capacity, gateway count, cost, required sites, and sites restricted to leaf or
gateway roles. The AI cannot determine acceptance reliably from prose alone.

### Revision metadata and comparison

Define project naming, parent identifiers, proposal IDs, assumptions, user
authorization record, timestamps, schema/software versions, and comparison
rules. Avoid putting transient conversation text into `scenario.toml`.

### Compatibility

Resolve the current SDK version mismatch. Future exchanges should carry schema,
assistant-protocol, SDK, and network-service versions. Define which combinations
are accepted and how incompatibility is reported.

## Suggested implementation order

1. Resolve versioning and publish a scenario schema snapshot.
2. Exercise the full workflow manually with this engineering agent.
3. Define structured proposal, error, objective, and evaluation contracts.
4. Implement the assistant SDK with local I/O and bounded correction retries.
5. Add shared assistant/MCP endpoints for live schema/catalogue/validation.
6. Add authentication and session isolation suitable for multiple users.
7. Evaluate the workflow on LTE cell, Wi-SUN mesh, custom antenna, and custom
   metric cases before broad deployment.

