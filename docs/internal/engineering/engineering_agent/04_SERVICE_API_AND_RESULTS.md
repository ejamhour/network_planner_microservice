# Network-service API and planning results

## Authentication and routing

The client registers through `POST /register?user_id=...`. The response contains
the user ID and token. Authenticated planning calls use the `X-Token` header and
the path prefix `/{user_id}`. SDK and example distribution routes are mounted
on the hub without that user prefix.

The planning worker uses response envelopes:

- success: `{status: "OK", kind: "json", data: ...}`;
- planning failure: `{status: "error", kind: "text", data: "message"}`.

The SDK raises a runtime exception when the envelope status is not OK. HTTP
transport errors are raised by `requests` before envelope processing.

## Staged API

The implemented planning stages are:

1. **Define** — `POST /planning/scenarios/define`
2. **Validate without storing** — `POST /planning/scenarios/validate`
3. **Upload scenario-local configuration** —
   `POST /planning/scenarios/{id}/configuration`
4. **Build candidates** —
   `POST /planning/scenarios/{id}/candidate_edges/build`
5. **Optionally tune cell candidates** —
   `POST /planning/scenarios/{id}/candidate_edges/tune`
6. **Compute or assign metrics** —
   `POST /planning/scenarios/{id}/metrics/compute`
7. **Solve** — `POST /planning/scenarios/{id}/solution/solve`
8. **Evaluate** — `GET /planning/scenarios/{id}/evaluation`
9. **Export result or scenario** — corresponding `/export/result` and
   `/export/scenario` GET endpoints.

There are read endpoints for scenarios, candidates, metrics, and solution, plus
scenario deletion and feature-cache import/export endpoints.

`POST /planning/workflow/run` executes a one-request shortcut using an embedded
scenario dictionary. It can build, optionally tune, compute/assign metrics,
solve, and optionally embed evaluation. It does not implement local project
persistence.

## Candidate and metric controls

Candidate construction accepts planner `graph` or `cell`, preservation of
manual edges, primary technology, sector awareness, optional distance limit,
and optional neighbor degree.

Metric computation either accepts explicit `{src, dst, metric}` records or
calls the features service. Feature-service controls include URL, worker prefix,
pool size, timeout, and whether expanded features appear in the response.

## Exported result

The persisted planning result is execution output rather than scenario input.
Its principal fields are:

- `working_crs`;
- `counts` for sites, RPL nodes, candidate edges, metric edges, planned nodes,
  and planned edges;
- `rpl_nodes`;
- `candidate_edges`;
- `metric_edges`;
- optional `edge_features`;
- `planned_nodes`;
- `planned_edges`;
- optional embedded `evaluation`.

Raw features are excluded by default. Portable raw-feature persistence belongs
in `features.json`, separately from `result.json`.

## Deterministic evaluation

Evaluation of a live or serialized result produces:

- `quality_summary`;
- `quality`, one row per interface;
- `service`, assignments for unconnected interfaces of the primary technology;
- `result_edges`, planned edges enriched with endpoint metadata.

Quality classification uses a caller-provided `rank_threshold`:

- connected interface: `connected`;
- no finite planned rank: `unserved`;
- rank at or below threshold: `good`;
- finite rank above threshold: `poor`.

The summary's `success` is true only when there is at least one target and no
poor or unserved target. This is a narrow deterministic criterion, not a full
statement that a design satisfies capacity, redundancy, cost, or business
requirements.

## API work needed for AI correction

**Gap:** error responses are currently text, so an assistant SDK cannot reliably
map failures to fields or decide whether to retry. A future contract should add
stable error codes, stage, field/path, affected entity, retryability, and
human-readable detail while preserving compatibility for existing clients.

