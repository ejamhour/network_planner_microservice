# Cell execution and saved results

Cell uses its own SDK/API namespace. It does not call project.graph or require a
Graph execution first. The metric/solver transport protocol is implemented once
for sibling Graph and Cell interfaces; the existing Cell engine owns its current
candidate graph, placements and primary technology.

## Sequence

After sync, validate, build and activate:

1. project.cell.build_candidate_edges(primary_tech="lte", ...).
2. Optionally project.cell.tune_sectors(...), obeying profile auto_tune policy.
3. project.cell.compute_metrics(...) until complete=true.
4. project.cell.solve(...).
5. project.cell.get_solution(), get_result() or save_result().
6. Optionally project.save_placements(), then project.close().

The [placement contract](antenna-placements.toml.md) defines fixed/automatic
profiles, previews and complete placement output. No source file is rewritten.
Directional/omni automatic mounting/orientation methods remain unfinished and
are not enabled by these Cell execution calls.

## API and SDK

Worker paths are prefixed by /{user_id}/projects/{project_id}/cell and require
X-Token plus X-Project-Token:

| Method | Path | SDK v2 |
| --- | --- | --- |
| POST | /sectors/tune | project.cell.tune_sectors(...) |
| POST | /metrics/compute | project.cell.compute_metrics(...) |
| GET | /metrics | project.cell.get_metrics() |
| POST | /solution/solve | project.cell.solve(...) |
| GET | /solution | project.cell.get_solution() |

GET /projects/{project_id}/cell/result on the hub requires only X-Project-Token.
It retrieves saved output without a worker or activation. SDK v2 get_result
accepts optional parameter_hash. save_result(path=None, parameter_hash=None)
also atomically writes local result.json and adds local_path to its report.

Metric and solve options follow the [execution protocol](graph-execution.md):
explicit candidate costs or feature-service computation, optional bounded metric
batches, max_edge_metric and parent_set_size. Calculations remain on the server.
Cell feature metrics read the engine's effective orientations, not the original
compiled starting angles. Raw feature caches stay on the server.

Reports use valid, issues, input identity and stage cell_metrics, cell_solve or
cell_solution. Missing/wrong-planner candidates, incomplete metrics, outdated
inputs and missing solution produce structured issues. Network/storage failures
remain HTTP errors. Applying tuning or rebuilding candidates invalidates metric/
solution readiness; a preview without applying or pruning does not.

## Saved output and effective placements

The server atomically saves the existing network result format with:

- project_run.planner = cell and input/execution metadata.
- antenna_placements: a complete effective placement document, using the same
  placements/interface-ID mapping as the TOML contract.
- cell.primary_tech and cell.sectors.
- cell.client_service and cell.cell_load from the existing CellPlanningSolution
  report implementation.

The selected fixed/automatic profile definitions and effective per-interface
antenna profiles remain in the existing antennas catalog. The placement snapshot
preserves applied azimuths, tilt, height and unchanged interfaces; it contains no
unapplied suggestions. Cell solution inspection also returns this snapshot and
the Cell report block. project.save_placements() produces the equivalent separate
TOML output while the project is active.

Reuse requires matching input identity, planner kind, effective placements,
candidates, actual metric values, metric execution options and solve options.
Changing only antenna orientation invalidates an older execution even if its
candidate edges and supplied costs are unchanged. Exact retries reuse saved
output. Source input changes make old output stale without deleting it.

The project keeps one latest successful result.json, not independent result
histories per planner. Retrieving it through another planner namespace returns
result_planner_mismatch. get_result retrieves that last successful execution;
it is not proof that newly tuned active state has been solved. Use get_solution
for current execution readiness, and parameter_hash to request an exact saved
execution. Old successful results remain available after a failed update.

Close and worker loss preserve saved results. Retrieval restores no runtime
solver objects, performs no RF calculation and does not rerun planning. Cell
evaluation/viewers, feature-cache disk restoration and multiple-result history
remain separate work.
