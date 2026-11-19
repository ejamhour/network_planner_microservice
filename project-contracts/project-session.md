# Temporary project storage and recovery

Implemented scope: creating/recovering remote project storage and explicit
[raw input sync](upload-manifest.md). Opening/resuming never uploads files,
compiles inputs, restores an in-memory planner or runs planning. Those remain
separate operations.

## Local identity

The SDK stores `.session.json` beside the local `scenario.toml`. A project can
be opened before scenario.toml or any input exists. The file belongs to SDK
connection management, not the planning inputs: omit it from `[files]`, uploads,
and version control. Do not put worker session IDs/tokens in it as project IDs.

| Field | Meaning |
| --- | --- |
| `version` | Metadata format version, currently 1 |
| `server_url` | Planning hub base URL |
| `project_id` | Server-issued project identifier, independent of worker lifetime |
| `project_path` | Project directory relative to the server workspace subtree |
| `recovery_token` | Credential required to recover this project |
| `created_at` | Server creation timestamp in UTC |
| `previous_projects` | Optional previous recovery records, preserved when explicitly starting another remote project |

The SDK replaces this file atomically. Recovery credentials are excluded from
the returned project's display representation and from normal exception messages.
Malformed local metadata stops opening; it never triggers silent replacement.

## Server directories

`PLANNING_PROJECT_ROOT` selects the storage root. Its default is `temp/` relative
to the service working directory. Temporary storage is excluded from Git.

```text
temp/
  <project_id>/
    .project.json
    workspace/
      projects/
        <project_name>/
```

The workspace preserves relative paths. The project directory is initially
empty. Shared workspace directories such as `antennas/` and `metrics/` can be
populated by explicit sync; they are not copies of service-global catalogs.
Private `.project.json` metadata sits outside the synchronized workspace subtree.
It stores a hash of the recovery credential, not the credential itself.

Project storage belongs to the hub/service filesystem, not to a worker's
in-memory registry. Worker timeout, reset and session replacement do not remove
it. Recovery also works after service restart if the same storage root is
preserved. Durable storage across container replacement requires preserving that
directory; this change does not modify deployment or Docker configuration.

## Implemented HTTP operations

These routes are relative to the hub base URL, without a `/<session_id>` prefix.
They are also available on the standalone worker application for direct use.
No existing worker session is needed for these storage operations.

| Operation | Request | Result |
| --- | --- | --- |
| Create | `POST /projects` with `project_path`, e.g. `projects/example` | HTTP 201; new project ID, path, creation time and recovery credential returned on creation |
| Resume | `POST /projects/{project_id}/resume`, header `X-Project-Token` | HTTP 200; existing identity/path/time; no new project or replacement credential |
| Sync | `POST /projects/{project_id}/sync`, same recovery header, application/zip body | HTTP 200; uploaded-file hashes/actions and persistent input revision/hash |

Responses use the existing `status: OK`, `kind: json`, `data` envelope. Absolute
server paths and credential hashes are not returned. Invalid project paths are
rejected; missing projects return 404, incorrect recovery credentials return 403,
and unavailable/corrupt storage returns a storage error. No failure automatically
creates a substitute project.

Creation builds the directory privately and publishes it only after its metadata
is written. Input paths must be workspace-relative POSIX paths; absolute paths,
Windows drives and parent traversal are rejected. This does not yet change the
current compiler's companion-file boundary: workspace-wide file staging and
reference resolution will be handled by the sync implementation.

## SDK use

```python
from cisei_planning_sdk_v2 import PlanningClient

client = PlanningClient(
    "http://planning-hub:8080",
    workspace="/path/to/workspace",
)

# The local folder may be empty. The server creates a new identity.
project = client.open_project(
    client.workspace / "projects/example", mode="new"
)

# A later client can resume using only this folder's saved metadata.
project = client.open_project(
    client.workspace / "projects/example", mode="resume"
)
```

`Project` exposes its local folder as path, project_id, server_url, project_path,
created_at and a resumed flag. It is a storage handle, not a ScenarioSession.

Without saved metadata, opening with no mode creates a project. With saved
metadata, omitting mode raises `ProjectChoiceRequired`, carrying project_id,
server_url and the `resume`/`new` choices. A notebook/UI or agent can ask the
user; scripts can supply the mode. The SDK never calls `input()` or implicitly
chooses to replace a project. Elicitation/MCP interaction is not implemented here.

`mode="resume"` requires the local file and matching server URL. It authenticates
the stored project and never uploads, recompiles or recomputes results. A missing
remote project is reported without replacing local metadata. `mode="new"` creates
another project, retains previous recovery records in `.session.json`, and does
not delete the earlier server directory. A saved identity copied with a folder
can therefore be resumed deliberately or replaced by an explicit new identity.

If the server responds successfully but local metadata cannot be saved, the SDK
raises `ProjectMetadataSaveError`. Its `recovery_metadata` attribute contains the
new identity/credential for explicit recovery; its message does not disclose
them. The previous session file remains intact. Recovery from a lost initial
create response still needs an idempotent-create/retry mechanism.

These storage/sync methods belong to the independent SDK v2 in client_sdk_v2/.
The preserved cisei_planning_sdk package in client_sdk/ retains its original
open_project() and local compilation behavior. The two packages can coexist;
no remote-opening alias or conditional legacy mode is added to SDK v2.

## Explicit sync and next operations

`project.sync()` now copies new/updated files selected by `[files]`, without
deletions or automatic validation/compilation. See the [sync contract](upload-manifest.md).
Result retrieval remains separate.

### Syntax validation

`POST /projects/{project_id}/validate` requires `X-Project-Token` and no body.
SDK v2 exposes it as `project.validate()`. It checks the currently synchronized
`scenario.toml` and companion inputs using the shared server validator. It does
not sync local edits, construct planning instances or write a compiled artifact.

Completed checks return HTTP 200 with the usual `status: OK`, `kind: json`,
`data` envelope. `data` contains `valid`, `stage: input_validation`, `issues`,
`project_id`, `input_revision` and `input_hash`. Blocking input issues produce
`valid: false`, not an HTTP transport failure. Check `valid` before continuing.
Issues retain file, code, severity, message and available row/field/location.
Authentication/missing identity/storage failures remain HTTP errors. Unexpected
server failures are not converted into successful validation reports.

Before any sync, validation reports a missing scenario with revision 0 and a
null hash. Validation serializes with sync commits, so its revision/hash identify
the inputs checked. Repeating validation does not advance the revision; later
syncs can make a previous report stale. No validation status is persisted yet.
The current validator requires scenario.toml at the project root; alternate
scenario locations accepted by the raw upload layer report unsupported_scenario_path.

### Remote compilation

`POST /projects/{project_id}/build` requires X-Project-Token and no body.
SDK v2 exposes it as `project.build()`. It runs the existing server compiler
against uploaded sources, including syntax checks. No previous validation call
is required. Reports follow validation's HTTP 200 envelope and valid/issues
semantics. Syntax errors have stage input_validation; construction feedback
has stage scenario_build. Authentication/storage failures remain HTTP errors.

Successful reports include compiled_scenario (relative filename), options,
issues and inputs (project_id, input_revision, input_hash). Input identity is
also returned at the top level. The compiled JSON embeds build_report with
this identity atomically. Compilation holds the sync commit lock through
publication; errors preserve the previous artifact, warnings allow a complete
artifact. Build recompiles each time without syncing local edits or planning.
It does not transfer the compiled contents to the SDK or invalidate saved results.

Successful compilation replaces the active compiled scenario; saved results
will be matched against input identities and operation parameters. Operation
result persistence, explicit deletion and garbage collection remain subsequent work.

## One active project per user worker

This decision supersedes the earlier meaning of close as deletion. Saved storage
and active resources are separate. A user session may have only one active project;
opening saved storage alone does not instantiate a planner or features workers.

The hub's existing POST /register issues a user session ID and token. SDK v2
registers lazily on activate (or explicitly through client.connect()) and never
resets an existing session implicitly. The temporary registration ID represents
the user session, not a permanent login. Separate registrations remain separate
user sessions. Project recovery credentials remain independent of these sessions.

Worker lifecycle calls use the existing authenticated hub proxy:

- GET /{user_id}/projects/active, with X-Token, returns active_project or null.
- POST /{user_id}/projects/{project_id}/activate, with X-Token and
  X-Project-Token, loads a current compiled scenario and returns scenario_load
  feedback. Missing/outdated/corrupt compilation returns valid=false. It never
  rebuilds, rereads source inputs, or starts candidate generation. Successful
  activation stores the scenario under project_id in the existing runtime;
  existing planning routes create the selected planner later. Repeat activation
  reuses state when the input identity is unchanged.
- POST /{user_id}/projects/{project_id}/close, with both tokens, closes features
  pools and clears scenario/planner/progress/feature-cache memory. Saved folders
  and compiled artifacts remain. Repeat close with no active project succeeds.

Another active project causes HTTP 409 detail code active_project_exists and
active_project_id. SDK v2 raises ActiveProjectExists with those fields. Interfaces
and agents ask the user to close the current project; no automatic replacement
occurs. A connected client's open_project also checks the active slot before
creating/resuming another local association. Root storage routes remain storage
operations, not activation; they do not allocate planning/features resources.

Close serializes with ordinary planning HTTP calls. A running background mesh
job returns project_busy rather than silently cancelling work. Features cleanup
failure returns HTTP 503 project_cleanup_failed, retains active ownership and
blocks reuse/planning until close succeeds. Planning calls reject active state
whose input revision/hash has changed (active_project_outdated). Close, rebuild
if needed, and activate again; saved files are never pruned on this path.

Worker timeout/reset uses its existing graceful shutdown to close features pools.
The disk project survives and can be activated in a replacement worker. Abrupt
process/container termination cannot guarantee remote resource cleanup; durable
cleanup/reconciliation and permanent user identity are not implemented here.
Legacy sessions retain their older API behavior until migrated; a worker holding
legacy scenarios cannot activate a saved project without closing/resetting it.

## Candidate generation and inspection

These worker calls require X-Token (user session) and X-Project-Token:

- POST /{user_id}/projects/{project_id}/candidate_edges/build accepts the existing
  CandidateEdgeBuildBody: planner (graph/cell/mesh, default graph),
  preserve_existing (default true), primary_tech (default lte), sector_aware
  (default true), limit_m and degree (optional). SDK v2 exposes
  project.graph/cell/mesh.build_candidate_edges(), selecting the matching planner
  automatically; each interface accepts only its applicable options.
- GET /{user_id}/projects/{project_id}/candidate_edges inspects the current graph
  without regenerating it. SDK v2 exposes project.graph/cell/mesh.get_candidate_edges(). These pass the
  optional planner query, returning candidate_planner_mismatch if current
  candidates belong to another planner. The saved build
  options are retained in worker memory so Cell inspection also returns sectors.

Both use the existing candidate generators and reporting code. Graph follows
scenario rules; Cell builds primary-technology cell/client candidates. Mesh uses
the repeater engine already prepared in the same worker; missing expansion
returns mesh_repeaters_required. SDK v2 project.mesh.add_repeaters() now exposes the basic
repeater stage using server project files. Planner selection remains explicit
per candidate call; no pole files are interpreted locally.

Success uses status OK / kind json / data with valid=true,
stage=candidate_generation, project/input identity, counts, candidate_edges and
diagnostics. Cell adds sectors. No candidate links is a successful empty graph
with diagnostics.issues code no_candidate_links and severity warning.

Expected prerequisites return valid=false with issues containing code, message
and severity: project_not_active, candidate_graph_missing, active_project_outdated,
project_busy, or mesh_repeaters_required. Expected generation ValueErrors use
candidate_generation_failed. Other-project ownership remains HTTP 409
active_project_exists, cleanup failures HTTP 503, and request-model errors HTTP
422. Unexpected exceptions remain server failures. Failed prerequisites do not
replace a previously prepared candidate graph.

Calls serialize with activation/close and check active input identity. They never
sync, compile, fetch features, persist output files or run the solver. Candidate
state/options are worker-local and are cleared on close or worker loss.

## Basic Mesh repeater preparation

POST /{user_id}/projects/{project_id}/mesh/add_repeaters requires both session
and project tokens. SDK v2 exposes project.mesh.add_repeaters(geo_base_url=None,
**options). The body accepts MeshNetworkOptions controls plus optional
geo_base_url. Raw poles, max_rounds/checkpoints and unknown fields are rejected
with HTTP 422. Omitted features URL uses the existing server environment lookup.

The server reads mesh controls and pole-source metadata from the current compiled
artifact, not source TOML. Defaults merge as mesh -> mesh.network -> supplied
controls, validated with the existing MeshNetworkOptions model. Pole rows are
read from the uploaded companion CSV and expanded with the same streaming reader
used during compilation. They remain separate from the fixed scenario sites and
are not embedded in compiled_scenario.json. No base-height prefetch is added.

The adapter calls the existing shared Mesh repeater operation and stores the
engine/expanded graph in the active worker. It uses existing feature caches/pools
and search semantics; adding repeaters performs feature requests. This is not a
new planning implementation or an implicit final solve. Candidate generation,
metrics and final solving follow separately. The response wraps existing result
data under result alongside valid=true, stage=mesh_preparation, issues and input
identity. selected_repeaters belongs to preparation; used_repeaters remains null
until the final solving stage can determine it.

Active ownership/freshness/cleanup/busy guards are shared with candidates.
Expected input/preparation errors return valid=false and issues; request-model,
transport and unexpected service failures remain HTTP errors. Close waits for
this synchronous operation to finish and then releases its feature resources.
Result persistence, finite-round expansion, checkpoint restoration and progress
polling are not migrated by this basic call.

## Graph metric, solution and result stages

The core Graph path now continues through project.graph.compute_metrics(),
get_metrics(), solve(), get_solution(), get_result() and save_result(). See
[Graph execution](graph-execution.md) for the exact endpoints, stage reports,
batching, saved-result metadata and retry semantics. These methods do not live
on Project or MeshPlanning. Cell has its own execution namespace and follows the
[Cell execution contract](cell-execution.md), rather than invoking Graph's API. Saved Graph output is retrievable
without a worker; raw feature-cache persistence remains separate work.
