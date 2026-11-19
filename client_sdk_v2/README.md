# Planning SDK v2

An independent thin SDK for server-managed projects. The original SDK remains
under `client_sdk/`, using the unchanged `cisei_planning_sdk` import and legacy
notebook workflows. This package has a separate distribution/import name so
both can be installed in the same environment.

```text
client_sdk/       cisei-planning-sdk       import cisei_planning_sdk
client_sdk_v2/    cisei-planning-sdk-v2    import cisei_planning_sdk_v2
```

Install the new source package for development:

```sh
pip install -e ./client_sdk_v2
```

## Implemented workflow

```python
from cisei_planning_sdk_v2 import PlanningClient

client = PlanningClient("http://planning-hub:8080", workspace="/path/to/workspace")
project = client.open_project(client.workspace / "projects/example", mode="new")
report = project.sync()
validation = project.validate()
if not validation["valid"]:
    print(validation["issues"])

# Later, including after worker/session timeout:
project = client.open_project(client.workspace / "projects/example", mode="resume")
```

The workspace directory must exist; its project subdirectory can be empty and
is created when opened. `Project.path` is the local folder. Server credentials
are saved privately in `.session.json`; without an explicit resume/new choice,
existing metadata raises `ProjectChoiceRequired` for a caller/UI/agent to handle.

Opening does not upload files or compile. Explicit sync transfers raw inputs
selected by scenario.toml's `[files]` list, preserving relative paths. It does
not delete server files or invoke validation/planning. See the
[storage/recovery contract](../project-contracts/project-session.md) and
[sync contract](../project-contracts/upload-manifest.md).

## Boundary

Dependencies are requests and tomlkit only. There are no imports from the old
SDK or cisei_lib, and no local profile expansion, scenario compilation, metric
evaluation or planner implementation. Future viewers should reuse presentation
source deliberately; this package does not copy the old viewer/compiler tree.

Validation checks uploaded files on the server without syncing or compiling.
Its report contains valid, stage, issues, project_id, input_revision and input_hash.
Input issues are returned as data; authentication/transport failures raise exceptions.

`project.build()` now compiles uploaded sources on the server and returns a
structured report. Check valid and issues. Syntax errors have stage
input_validation; construction feedback has stage scenario_build. Warnings
allow publication, while errors preserve the previous compiled artifact.
The saved build report includes the input revision/hash. Build never uploads
local changes implicitly or starts a planner.

After a successful build, call `project.activate()` to load the saved artifact
without recompiling. This lazily registers a user session and starts its worker.
One user worker holds one active project. Repeated activation of the same current
project reuses its in-memory state. Another project's activation raises
`ActiveProjectExists`; the caller/UI/agent must prompt for explicit close.
Opening another folder through that connected client also raises this condition.
The SDK never prompts or closes the active project automatically.

Call `project.close()` to release planner objects, feature caches and features
worker pools. It preserves all project files and local recovery metadata.
Cleanup failure keeps the project active for retry and blocks further planning;
a running mesh background job must finish before close. Changed synchronized
inputs require close, build and activation again. The same worker session is
reused after close; worker expiration requires reactivation from saved storage.

After activation, generate and inspect candidates explicitly:

```python
report = project.graph.build_candidate_edges()
if report["valid"]:
    candidates = project.graph.get_candidate_edges()
else:
    print(report["issues"])
```

Project owns only local files and lifecycle: sync, validate, build, activate and
close. Planning operations live in separate thin interfaces:

- project.graph: static Graph candidate generation/inspection.
- project.cell: Cell candidate generation/inspection, including primary_tech,
  sector_aware, limit_m and degree, plus profile-controlled sector tuning.
- project.mesh: Mesh network repeater addition and candidate generation/inspection.
  The separate Mesh link call has not been migrated yet; it belongs here too.

The interfaces select the planner automatically; no planner argument is accepted
by their candidate methods. Graph and Mesh accept preserve_existing; Cell also
accepts its specific options. Existing server implementations are reused. Graph
is an independent planner, not a preparation stage for Cell or Mesh. Accessing
an interface performs no network request or worker allocation.

Only one candidate graph lives in the active worker. Generating with another
planner replaces that graph; inspecting through the wrong interface returns
candidate_planner_mismatch rather than returning a different planner's results.
These interfaces do not create three independent in-memory planning sessions.

Mesh requires repeater preparation before Mesh candidates:

```python
preparation = project.mesh.add_repeaters(geo_base_url="http://features-service:8080")
if preparation["valid"]:
    candidates = project.mesh.build_candidate_edges()
```

The server reads saved mesh/network controls and the referenced pole CSV; SDK v2
does not read TOML or poles for this call. Explicit control arguments override
mesh.network, which overrides mesh. The features URL may be omitted when the
server environment supplies it. This synchronous call returns valid/stage/issues,
input identity and result. It performs the existing feature-backed repeater search;
final candidates, metric evaluation and solving remain explicit. Advanced finite
round/checkpoint restoration, progress polling and result-file saving are pending.
For long searches, configure the client's timeout to suit the deployment.

Successful candidate reports contain valid, stage, input identity, counts,
candidate_edges and diagnostics. An empty graph is valid with the existing
no_candidate_links warning in diagnostics. Inspection never rebuilds candidates.
Missing activation/graph, stale active inputs, and busy background work return
valid=false with structured issues. Invalid HTTP option shapes remain HTTP 422;
another active project raises ActiveProjectExists. No features are fetched,
scenario compiled, result file written or solver started by these methods.

## Graph metrics, solve and results

After activation and Graph candidate generation, the remaining core Graph calls
belong only to project.graph:

```python
metrics = project.graph.compute_metrics(geo_base_url="http://features-service:8080")
if metrics["valid"] and metrics["complete"]:
    solution = project.graph.solve(parent_set_size=1)
    if solution["valid"]:
        saved = project.graph.save_result()  # Writes local result.json.
```

compute_metrics can instead accept edge_metrics=[{src, dst, metric}, ...] to
provide explicit costs without contacting features. Supplied costs must refer
to unique generated candidates and be finite/nonnegative. get_metrics() inspects
current costs; get_solution() inspects the current successful solution.

With batch_size, compute_metrics processes one batch. Pass batch.next_offset as
the next batch_offset until complete is true. The SDK performs no metric
evaluation. Incomplete or failed metric computation blocks solve. Generating
candidates again clears Graph execution state; changing metrics clears current
solution readiness. Graph and Cell are sibling interfaces using a common
NetworkExecution HTTP/IO base; Cell does not inherit GraphPlanning or invoke
Graph endpoints. Mesh has not yet opted into these execution stages.

solve persists result.json atomically on the server. The result retains the
existing Graph output structure and adds project_run metadata: input identity,
execution parameters and parameter_hash. An identical solve reuses the saved
execution and returns reused=true; changed costs/candidates/options do not.
Only the latest successful execution is kept, not a history of every parameter
combination. Failed publication preserves the previous saved artifact.

get_result(parameter_hash=None) retrieves the latest successful server output
without activating or even registering a worker. An optional parameter_hash
requires an exact execution match. Input changes make the prior artifact stale
without deleting it. Closing a project or replacing its worker preserves saved
results. Retrieval does not restore candidates, metrics or solver objects.

save_result(path=None, parameter_hash=None) retrieves that output and atomically
writes it locally (default: project folder/result.json). It returns the retrieval
report with local_path added on success. Invalid retrieval reports do not write
a file. Raw feature caches remain on the server and are not included in these
results. Durable feature-cache saving/restoration remains separate work.

Graph/Cell evaluation and viewers, remaining Mesh stages, advanced Mesh operations
and explicit project deletion remain pending.

## Cell metrics, solve and results

Cell now provides its complete core execution path in its own namespace:

```python
project.cell.build_candidate_edges(primary_tech="lte")
tuning = project.cell.tune_sectors()  # Optional; honors antenna-profile policy.
metrics = project.cell.compute_metrics(geo_base_url="http://features-service:8080")
if metrics["valid"] and metrics["complete"]:
    solved = project.cell.solve()
    if solved["valid"]:
        saved = project.cell.save_result()
        placements = project.save_placements()
```

Cell supports the same metric batching/supplied-cost and solver options as Graph,
with get_metrics(), get_solution(), get_result() and save_result(). These inherited
HTTP/IO methods are defined once in planners/_execution.py and dispatch through
the interface's bound kind. Cell candidate/tuning methods remain in planners/cell.py;
Graph candidate methods remain in planners/graph.py. Nothing moves onto Project.

The server uses the current Cell graph, including applied tuning. Results include
antenna_placements (the effective snapshot), plus Cell sectors, client service and
cell load reports. Effective placements and planner kind participate in result
reuse identity. get_result/save_result work without a worker after close/reset.
The latest successful result.json is shared by the project's successive runs;
requesting another planner's result returns result_planner_mismatch. Applied
tuning clears metrics/solution readiness; a pure preview preserves it.
See the [Cell execution contract](../project-contracts/cell-execution.md).

## Profile policy and placement output

Set auto_tune=true on an antenna profile in scenario.toml to permit automatic
tuning; omission defaults to false. Two profiles may use the same model_id with
different policies. Device/interface or placement antenna_id selects the profile.
Placement values are starting values, not an implicit lock. Compilation never
runs a search automatically. Current Cell tuning adjusts azimuth only.

```python
project.cell.build_candidate_edges()
tuning = project.cell.tune_sectors()  # Applies only permitted profile changes.
if tuning["valid"]:
    placement_output = project.save_placements()
```

tune_sectors(apply_rotation=False) previews suggestions without changing state.
Fixed profiles do not rotate; pruning remains a separate explicit option.
The applied per-interface orientation is shared by candidates, RF gain and
export. Rebuilding Cell candidates retains current applied orientations.

project.export_placements() produces complete effective placement TOML on the
server. project.save_placements(path=None) also saves it locally, by default to
outputs/antenna_placements.toml, without changing input files or scenario.toml.
Both return structured reports; successful local saving adds local_path.
The server protects synchronized inputs at the output path from overwrite.
Promoting output to a later run is explicit: copy/select it as an input, keep
matching profile/interface IDs, sync and recompile. Policies remain profile-owned;
choose a fixed profile if subsequent tuning must not change the installation.
See the [placement contract](../project-contracts/antenna-placements.toml.md).
The old SDK and `/sdk/latest` distribution
route continue to serve the old workflows; this change does not publish or
silently replace either installed package.
