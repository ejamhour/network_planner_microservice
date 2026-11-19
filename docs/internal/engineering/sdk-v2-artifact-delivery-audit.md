# SDK v2 artifact-delivery and reopening audit

Date: 2026-11-19. Scope: implemented SDK v2 calls, their server adapters and
reopening after loss/deletion of server storage. This is an audit, not a behavior
change or a claim that the artifact lifecycle is implemented.

## Controlling decisions

The latest user clarification supersedes the earlier close/retention discussion:

- Local source inputs are authoritative. Input sync goes client to server.
- No general server-to-client project sync is to be created.
- Each operation must return its generated/updated artifacts, and the SDK must
  save them before reporting that operation complete to its caller.
- Explicit close performs no artifact collection or downloads. Once the operation
  delivery guarantees exist, it releases resources and deletes server storage.
- Session timeout/disconnection must not delete the project. Server copies must
  remain recoverable after failed delivery/local saving.
- Reopening must cover an ongoing project at supported durable operation/batch
  boundaries, not just the last final solution. Arbitrary in-flight Python objects
  are not a promised checkpoint.

Current code does not meet this lifecycle. Its planning calculations work, but
successful API/SDK return does not generally mean locally durable output. The
existing 240-test planning regression baseline did not establish this guarantee.
Do not enable server deletion on close before the gaps below are resolved.

The published project-session contract and older migration notes still describe
close as retaining server files. That describes current implementation but is
superseded as the desired policy by the clarification above. Update the lifecycle
contract with the delivery changes; do not treat the old retention text as user
approval for permanent two-copy project management.

## Per-call inventory

Paths below omit the existing user/session prefix for worker calls. Graph/Cell
mean separate bound interfaces and endpoints, not one calling the other's API.

| SDK call / API | Server effect or artifact | Returned now | SDK saves locally now | Finding |
| --- | --- | --- | --- | --- |
| open_project; POST /projects or /projects/{id}/resume | Creates/recovers disk identity and workspace | Project ID/path/time; credential only on creation | .session.json atomically | Recovery identity is saved; it is not a compiled/planning checkpoint. Session file records no artifact delivery receipts. |
| sync; POST /projects/{id}/sync | Adds/updates uploaded inputs, private SQLite hash/revision registry | Input revision/hash and file actions | Inputs already local; sync_report only in memory | Input direction is correct. All selected bytes still upload, even if unchanged. Input manifest/identity are not saved as a durable checkpoint record. |
| validate; POST /projects/{id}/validate | Reads uploaded syntax; constructs no scenario | Structured issues/success report | Nothing automatically | No new planning artifact is required just for syntax inspection. Saving diagnostics is optional, not the principal recovery gap. |
| build; POST /projects/{id}/build | Atomically creates compiled_scenario.json, including build report | Report, relative compiled filename and input identity; not compiled content | Nothing | Blocking gap: local compiled artifact is never delivered/saved by build. |
| activate; POST /projects/{id}/activate | Loads current compiled artifact into worker | Stage report, identity, reused flag | Nothing | Loads compiled inputs only. Does not restore metric progress, current candidates, Cell edits, Mesh engine or features. |
| graph/cell/mesh.build_candidate_edges; POST /projects/{id}/candidate_edges/build | Replaces worker candidate graph, stores selection/options, invalidates execution readiness | Candidate records, counts, diagnostics, identity; Cell sectors | Nothing | Data is returned but is not durable locally. No saved stage/parameter marker or supported candidate-checkpoint restore path. Cheap deterministic rebuilding could be a documented recovery policy, but it is not checkpoint restoration today. |
| cell.tune_sectors; POST /projects/{id}/cell/sectors/tune | Applies permitted per-interface orientation changes and/or removes empty interfaces; rebuilds candidates when applied | Rotation/prune/sector reports and candidate count | Nothing | Blocking ongoing-state gap: no complete effective placement artifact or modified scenario checkpoint in this response. Later explicit export is not an automatic guarantee. |
| graph/cell.compute_metrics; POST /projects/{id}/{planner}/metrics/compute | Updates metric/feature maps, raw feature cache and ExecutionState; can finish one bounded batch | Metric tables, completion/batch fields, identity | Nothing | Blocking gap: raw cache updates/cursor are not returned; completed/partial metric state and progress are not saved locally. Explicitly supplied costs also need durable recovery. |
| mesh.add_repeaters; POST /projects/{id}/mesh/add_repeaters | Creates Mesh engine, selected infrastructure, expanded state, iterations and features in worker memory | Existing Mesh preparation result wrapped in a report | Nothing | Result is returned but not saved by this method. Current basic project route does not persist a preparation checkpoint file on the server either. Feature cache is omitted. |
| graph/cell.solve; POST /projects/{id}/{planner}/solution/solve | Atomically writes server result.json; keeps current execution metadata | Solution subset, effective placement snapshot, identity, parameter hash, filename/reused flag; Cell reports | Nothing | Blocking gap: normal solve neither returns the full saved result artifact nor saves a local result. Full provenance/inventory/metric output stays behind a later retrieval call. |
| graph/cell.get_result; GET /projects/{id}/{planner}/result | Reads latest saved result, checks identity/planner/optional hash | Complete result document and retrieval report | Nothing | Useful recovery inspection, but merely receiving a Python dictionary is not a local save. |
| graph/cell.save_result | Same retrieval as get_result | Full document | result.json, or requested path, atomically | Positive building block, but manual second-call saving is not the agreed normal operation lifecycle. Local failure propagates and leaves server output intact. |
| export_placements; POST /projects/{id}/antenna_placements/export | Atomically writes outputs/antenna_placements.toml; protects synchronized input path | Actual TOML content plus path/identity/report | Nothing | Correct content is delivered by this export call, but export_placements itself does not save locally. It is not automatically invoked when tuning changes state. |
| save_placements | Same placement-export API | Actual TOML and report | Separate output TOML atomically | Meets local saving for this explicit output call. It does not close the tuning-call gap or encode removed interface definitions. |
| get_candidate_edges/get_metrics/get_solution | Inspects existing stage state/saved matching solution | Tables and stage reports | Nothing | Read-only inspection should not be confused with checkpoint production. These are not automatic persistence steps. |
| close; POST /projects/{id}/close | Closes feature pools and clears all worker scenario/planner/progress/cache/run dictionaries | closed=true | Nothing | Retains disk folder today and does not delete it. Critically, unsaved in-memory features and ongoing state are cleared with no delivery/completeness check. Behavior must change only after per-call guarantees exist. |

## Findings requiring implementation

### A1. Operation success is not artifact-delivery success

Project.build and planner methods simply return response data. _base._request
unwraps data; no project-bound artifact receiver runs before that return. The
existing HTTP transport also assumes a JSON envelope. There is no common artifact
inventory with content hashes/sizes, no local completion record and no close
guard for an unsaved artifact set. .session.json stores opening/recovery metadata,
not operation completion. HTTP success alone cannot prove a client disk write.

Keep the existing atomic local JSON/text writers. Add operation-coupled delivery
processing in one project-bound IO path rather than repeating it in every planner.
The destination must be the project handle that issued the call, not whichever
handle happens to be in PlanningClient.project. SDK file IO must not interpret
planner configuration or reconstruct missing artifacts.

Large compiled/cache artifacts need a bounded-memory response/stream protocol.
This audit does not choose an encoding or require giant inline JSON strings.
The protocol must return artifacts belonging to the operation; it must not become
a general reverse sync or a close-time sweep of the server directory.

### A2. Compiled artifact never reaches the client

build_scenario returns its report when called by the API. That report names the
compiled file but does not contain its bytes. Project.build returns that report
without saving anything. After server deletion the client cannot reuse the
compiled state; rebuilding would require a fresh server compilation.

Fix build's own response/save path, including build-report/input identity, before
claiming local compiled-project recovery.

### A3. Raw features have no v2 delivery or durable recovery path

CachedGeo inserts raw records into the worker FeatureCache; metrics/results
deliberately omit them. No v2 method saves/imports features.json. A failed metric
call may already have fetched reusable features before returning an issue/error.
Closing clears the cache even though the project folder is retained. Timeout or
worker loss likewise does not guarantee cache survival merely by preserving disk.

The legacy server FeatureCache.export_since cursor/generation implementation and
original SDK incremental cache tests are reusable evidence. Use the server-side
export logic to attach incremental updates to feature-producing operation/batch
responses, including defined failure checkpoints. Do not add a required second
cache-export call after every operation or a close-time download. Keep cache
validation/merging semantics server-owned; advance local delivery cursors only
after successful local persistence. Restoration from the saved artifact needs
an explicit supported path too.

### A4. Ongoing state is not represented by a final result or placements alone

Between compilation and solve, candidates/options, metric coverage/batch history,
tuning state and Mesh state live only in the worker. Cell pruning removes actual
interfaces. A placement snapshot cannot express that removal: an omitted placement
does not remove an interface when reapplied to original input. Current prune
reporting is recomputed after pruning and is not a complete edit log.

Define per-stage durable checkpoints or explicit deterministic-replay boundaries.
Preserve effective scenario changes, interface identities, pending work, execution
parameters and references to delivered compiled/cache artifacts. The contract must
state where continuation resumes; it cannot promise arbitrary instruction-level
resumption. Tuning preview, which does not mutate state, needs no new effective
checkpoint just because it produces a suggestion report.

Basic Mesh add_repeaters returns iterations/expanded state but does not save them.
Advanced finite-round expansion/checkpoint logic exists in the legacy Mesh API,
but its v2 request model rejects max_rounds/checkpoints and exposes no equivalent
restoration flow. Legacy capability is not v2 readiness.

### A5. Solve's full output requires an extra manual call

solve_execution writes the complete result, then returns solution_payload rather
than the full artifact. The SDK solve method does not save it. Explicit save_result
works and is atomic, but forgetting it loses the local final result. An interrupted
solve response can be retried/retrieved when server state survives; that is not an
automatic local handoff.

Return and save the full result through solve's own artifact path. Its effective
placements, parameter provenance and Cell reports must stay attached. Explicit
retrieval/save methods may remain useful for recovery or additional copies, but
must not be prerequisites that users have to remember after every normal solve.

### A6. Saving files is not enough to reopen a deleted server project

The input upload endpoint rejects compiled_scenario.json and result.json. That
protection is appropriate for source sync, but no separate generated-artifact
restoration operation exists. Activation loads only the server compiled artifact.
Compiled/result metadata includes the old temporary project ID and revision; the
loader rejects metadata not matching its current server identity. A fresh project
receives a new ID. Local resume metadata pointing to a missing server project raises
an error rather than reconstructing it from artifacts.

A supported server-side restoration path must validate local artifacts against
current inputs and restore/rebind provenance without making the SDK compile or
silently edit metadata. It must restore supported ongoing checkpoints/cache state,
not merely make a final result viewable. Do not route generated artifacts through
ordinary input sync or claim that copying an old compiled JSON into place solves
the identity problem.

### A7. Failure/retry and deletion gates are incomplete

Atomic writes and preservation of prior result files already work. Missing pieces
are per-operation delivery identity, consistent multi-artifact checkpoint commit,
lost-response recovery, and evidence of successful local persistence. No current
close guard checks those conditions. Exact receipt/acknowledgement mechanics still
need definition; the invariant is that a failed handoff cannot authorize deletion.

Close must do no downloads. First fix operation delivery/recovery; then implement
explicit deletion and local recovery-metadata transition. Retain server storage
on timeout/disconnection. Do not interpret retention of compiled/results as
durability of unsaved feature/ongoing runtime state.

## Disposable probe

Executed in the existing Python development container using TemporaryDirectory,
real API handlers/SDK methods through the test hub adapter, and deterministic
feature responses for one tiny Cell link. No source code, deployment settings,
notebooks or live features service were changed. This was not a full test-suite
run or a real spawned-worker test.

Observed after build -> tuning -> feature metrics -> solve, with no manual
save_result/save_placements:

- Server compiled file existed; build returned only its filename; local compiled
  file did not exist.
- Tuning returned no complete placement artifact; local generated placement file
  did not exist.
- Metrics completed with one raw record in the server cache; no cache update/cursor
  was returned and local features.json did not exist.
- Solve succeeded and server result.json existed; its full result was not in the
  solve response and local result.json did not exist.
- After close the folder remained, feature cache dictionaries were empty, and the
  local folder contained only .session.json, scenario.toml and the input placement
  file. No generated checkpoint was present.
- In a fresh server project, attempting to restore a locally copied compiled file
  via ordinary input sync returned HTTP 422. The previous server folder was not
  deleted by the probe.

## Recommended fix order and acceptance gates

1. Define one operation-artifact delivery contract and project-bound SDK receiver.
   Test local disk failure, truncation/hash mismatch, missing artifact members,
   multi-file partial save and response loss. SDK success requires durable local
   completion; the server copy remains recoverable on any failed handoff.
2. Fix build delivery and compiled-artifact restoration first. Test recreating a
   fresh server project from local inputs/artifact without client-side compilation.
   Validate changed inputs and old/new server identities explicitly.
3. Add incremental feature artifacts to metric batches and Mesh feature-producing
   calls, with atomic local persistence and supported server import. Test no-change,
   delta, generation reset, failed call/save and recovery after worker/storage loss.
4. Add ongoing stage checkpoints: candidates/replay parameters, partial metrics,
   effective Cell edits/pruning and Mesh preparation/finite-round state. Test actual
   continuation at each supported boundary, not just source-file reopening.
5. Fix solve and state-changing tuning/Mesh responses to return their complete
   generated artifacts for immediate SDK saving. Reuse existing per-file atomic IO.
6. Only then change explicit close to deletion, with no download/sync step. Test
   fresh-server reconstruction after close, while timeout/disconnection preserves
   the original server project. No deletion is implemented by this audit.

## Source evidence

- SDK [project methods](../../../client_sdk_v2/src/cisei_planning_sdk_v2/project.py),
  [execution methods](../../../client_sdk_v2/src/cisei_planning_sdk_v2/planners/_execution.py),
  [Mesh wrapper](../../../client_sdk_v2/src/cisei_planning_sdk_v2/planners/mesh.py),
  [atomic local IO](../../../client_sdk_v2/src/cisei_planning_sdk_v2/artifacts.py).
- Server [build/compiled publication](../../../cisei_lib/project_io/compiled_scenario.py),
  [compiled activation guards](../../../cisei_lib/project_io/project_operations.py),
  [input-only upload restrictions](../../../cisei_lib/project_io/project_sync.py).
- Server [network execution](../../../app/project_runtime/execution.py),
  [stage responses](../../../app/project_runtime/execution_routes.py),
  [Cell tuning](../../../app/project_runtime/cell.py),
  [Mesh preparation](../../../app/project_runtime/mesh.py),
  [runtime cache/cursor implementation](../../../app/feature_cache.py).
- [Placement output](../../../cisei_lib/project_io/placement_output.py),
  [result persistence](../../../cisei_lib/project_io/planning_results.py),
  [close](../../../app/routes_project_runtime.py),
  [legacy staged/cache/checkpoint APIs](../../../app/routes_planning.py).
- Existing tests reviewed: test_project_graph.py, test_project_cell.py,
  test_placement_output.py, test_project_runtime.py, test_project_storage.py,
  test_project_sync_http.py and test_mesh_network_planner.py. They validate useful
  pieces (manual saves, source/build behavior, result reuse, protection, worker
  reopening), but do not prove per-operation automatic artifact delivery.
