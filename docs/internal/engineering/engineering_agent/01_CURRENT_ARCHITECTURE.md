# Current architecture

## Implemented components

### Network service

The network service exposes FastAPI routes for staged network planning. It
accepts complete scenario dictionaries, holds active scenarios and planner
state in memory, builds candidate edges, obtains or accepts link metrics, runs
the RPL solver, evaluates results, and exports serializable scenarios/results.

The hub in `app/main_hub.py` registers a `user_id`, issues an `X-Token`, and
starts a dedicated worker process on the first authenticated planning request.
The SDK sends planning requests under `/{user_id}/...`. Idle workers are reaped;
the current default is 3600 seconds through `PLANNING_MAX_IDLE_SECONDS`.

Worker state is ephemeral. A worker owns:

- defined planning scenarios;
- prepared planners and their computed state;
- scenario-specific portable feature caches;
- reusable features-service client pools.

### Features service

The network service calls an external features service through
`AsyncGeoServicePool`. The configured service URL comes from the request or
`PLANNING_FEATURES_BASE_URL`, `FEATURES_SERVICE_URL`, or `GEO_BASE_URL`.
The features service is responsible for expensive geographic and propagation
feature extraction.

The network service reduces those raw features into configured edge metrics.
Raw features and computed metrics are separate artifacts.

### Planning SDK

The Python package `cisei-planning-sdk` is the notebook/CLI client. It owns local
workspace access and sends JSON over HTTP. The server never resolves a path in
the user's workspace.

The primary public objects are:

- `PlanningClient`: connection, registration, workspace association, project
  opening, scenario definition, and saved-result evaluation.
- `PlanningProject`: conventional project paths and local scenario, feature,
  and result persistence.
- `ScenarioSession`: staged operations on one in-memory server scenario.
- `init_workspace`: creates workspace directories and optionally downloads
  distributed examples without overwriting existing files.

### Local and server state

Local durable state belongs to the workspace. Scenario state held by a worker
is temporary. Reopening a project defines the scenario again and imports its
saved `features.json` when present.

## Implemented planning modes

- `graph`: generic connectivity rules and RPL-based mesh planning.
- `cell`: direct cell-to-client candidates, with optional sector geometry and
  cell-specific reports.

Candidate sector tuning is currently implemented only for `cell` planning.

## Current deployment relationship

Both services listen on port 8080 inside their containers. In the intended
Compose deployment, only the network service is published to the host. The
network service reaches the features service through its internal Docker name,
normally `http://planning-service:8080`.

## Important current limitations

- Authentication is a generated hub token, not the final real-user login.
- Worker memory disappears after reaping or restart.
- The default SDK metric request uses features-service prefix `planning-sdk`
  and pool size 1. Separate planning workers can therefore target the same
  features-service worker identity.
- The service does not publish a formal JSON Schema for scenarios.
- Many planning errors are returned as `{status: "error", kind: "text",
  data: "..."}` rather than typed error objects.
- The source tree currently contains an SDK version mismatch described in the
  package README.

