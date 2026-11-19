# Graph execution and saved results

These calls follow source sync, validation, compilation, activation and Graph
candidate generation. See [project lifecycle](project-session.md). They reuse
the existing Graph metric/solver implementations. Cell and Mesh have their own
interfaces; completing Graph is not a preparation stage for those planners.

## API and SDK methods

Worker calls use both X-Token (session) and X-Project-Token (project recovery):

| Method | Worker path, prefixed with /{user_id}/projects/{project_id}/graph | SDK v2 |
| --- | --- | --- |
| POST | /metrics/compute | project.graph.compute_metrics(...) |
| GET | /metrics | project.graph.get_metrics() |
| POST | /solution/solve | project.graph.solve(...) |
| GET | /solution | project.graph.get_solution() |

Saved output uses GET /projects/{project_id}/graph/result on the hub with only
X-Project-Token. It does not register, activate or start a worker. SDK v2 exposes
project.graph.get_result(parameter_hash=None) and save_result(path=None,
parameter_hash=None). save_result downloads and writes local result.json
atomically, returning the retrieval report plus local_path. An invalid report
does not modify the local file. Local filesystem failures remain exceptions.

## Metrics

compute_metrics accepts the existing metric request options: geo_base_url,
geo_user_prefix, geo_pool_size, geo_timeout, bidirectional,
assume_symmetric_features, batch_offset and batch_size. The default computes all
candidates in one request, on the server. The features URL may use the existing
server environment configuration. SDK v2 returns metric tables, not raw feature
caches; metric calculation and feature-service access remain server-owned.

Alternatively, edge_metrics accepts records with src, dst and metric. These must
identify unique generated candidates with finite nonnegative costs. Providing
them bypasses the feature service. A subset is allowed but cannot be solved until
every candidate has a metric. Unknown candidates, duplicate records and invalid
costs return metric_computation_failed. HTTP request-model errors return 422.

With batch_size, a call processes one batch, returning batch.offset,
batch.next_offset, batch.total_links and batch.complete. Continue explicitly
until complete=true. Readiness checks actual metric coverage, not just the last
offset. Updating metrics invalidates current solution readiness, including after
a failed update. Regenerating candidates clears the Graph run state entirely.

Stage reports use the standard status OK / kind json / data envelope. Successful
metric reports contain valid=true, stage=graph_metrics, issues, input identity,
counts, metrics and complete. get_metrics inspects state without recomputing.
Missing activation/candidates, another planner's graph, missing metrics, outdated
inputs and incomplete metrics return structured feedback instead of implicit
stage execution. Another active project remains HTTP 409; service/transport and
unexpected server failures remain HTTP errors.

## Solve and provenance

solve accepts max_edge_metric (optional finite nonnegative eligibility limit)
and parent_set_size (positive integer, default 1). It requires complete metrics,
calls the existing GraphPlanner solver, and atomically writes result.json to the
server project folder. It returns stage=graph_solve with counts, planned_nodes,
planned_edges, input identity, saved_result, parameter_hash and reused.
get_solution inspects the current worker's successful execution without solving.

The saved artifact preserves the existing Graph output structure and adds:

```text
project_run
  planner: graph
  inputs: project_id, input_revision, input_hash
  parameter_hash
  parameters
    candidates: candidate-generation options
    candidate_hash
    metric_hash
    metric_requests: execution/batch options, excluding supplied metric arrays
    solve: max_edge_metric, parent_set_size
```

An identical solve reuses saved output only when synchronized inputs, candidate
graph, actual metric values, recorded metric options and solver options match.
Changed values/options require a new execution. Parameter hashes identify
execution settings/data; input identity is checked separately. Only the latest
successful result is retained, not a history of every parameter combination.

Cell now implements the same stage protocol in its own namespace; see
[Cell execution](cell-execution.md). SDK/server execution plumbing is reused,
but Cell is not a Graph SDK subclass and does not call Graph routes. Execution
identity also includes planner kind and an effective-placement hash. Saved
results include the effective antenna_placements snapshot; a saved execution
belonging to another planner returns result_planner_mismatch.

## Retrieval, changes and failures

get_result returns stage=result_retrieval, valid, issues, input identity,
parameter_hash and result. Without a parameter hash it returns the latest saved
execution for the current inputs, whose parameters remain visible in metadata.
Supplying a hash requires that exact execution. A prior successful execution is
not proof that the active worker's newly requested parameters have been solved.

Missing/corrupt output, old input identity or mismatched parameters return
result_missing, invalid_saved_result, result_outdated or
result_parameters_mismatch. Input sync and failed solves do not delete old
output; changed inputs make it ineligible. A byte-identical sync keeps it current.
Failed result publication preserves the prior file and returns HTTP 500 with
project_storage_error. Concurrent input changes block publishing old-input output.

Saved results survive explicit close and worker reset/timeout. Retrieval does
not reconstruct candidates, metrics or solver objects; get_solution requires a
current active Graph execution. Raw feature caches are excluded from saved
results. Durable feature-cache storage, evaluation/viewers, checkpoint restoration
and multiple-result history remain separate work.
