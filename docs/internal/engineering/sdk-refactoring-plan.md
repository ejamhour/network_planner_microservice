# SDK responsibility review and migration plan

Inventory snapshot: 2026-11-17. Execution order updated 2026-11-19. Current source
is authoritative; project-contracts/project-session.md describes the implemented
storage, sync, compilation, active-project and candidate APIs.

Latest lifecycle clarification and priority: the user requires operation-returned
artifacts to be saved locally by SDK v2 before the corresponding call completes.
No general reverse sync and no close-time downloads. Explicit close should release
resources and delete server storage once those guarantees exist; timeout or
disconnection preserves server storage. Current close still retains files and
clears volatile caches, and the operation artifact lifecycle is incomplete.
The [artifact-delivery audit](sdk-v2-artifact-delivery-audit.md) records the concrete
gaps, evidence and fix order. Address those gates before enabling deletion or
continuing planner migration. Older retention descriptions below are historical
implementation notes, not the latest requested policy.

## Current SDK ownership and migration sequence (2026-11-19)

Project owns files and lifecycle: sync, validate, build, activate and close.
GraphPlanning, CellPlanning and MeshPlanning live in separate modules under
client_sdk_v2/src/cisei_planning_sdk_v2/planners, exposed through project.graph,
project.cell and project.mesh. Repeater addition belongs only to MeshPlanning.
Planning methods were removed from Project rather than retained as confusing
forwarding aliases. The original SDK remains unchanged.

These are thin API interfaces, not new planning algorithms. They reuse existing
server implementations. Graph is a standalone static planner, not a general
preparation stage for Cell or Mesh. Reuse of server operations does not imply
that every planner has the same workflow. Interfaces share one active project
and one current candidate graph; they are not separate resource-owning sessions.

| Milestone | Scope | Status |
| --- | --- | --- |
| 1 | Project lifecycle and one active project per user worker | Implemented |
| 2 | Separate lifecycle from planner interfaces | Implemented; Graph/Cell/Mesh calls live under their corresponding interfaces |
| 3 | Core Graph workflow: candidates, metrics, solve, retrieve/save results | Implemented and tested with a real hub/worker; evaluation/viewers remain separate |
| 4 | Complete Cell workflow, including its antenna tuning and options | Core candidates, tuning, metrics, solve and results connected; evaluation/viewers remain separate |
| 5 | Complete Mesh network workflow: repeater addition, candidates, metrics, solve, final pruning | Basic repeaters/candidates already connected; checkpoints/progress and remaining stages pending |
| 6 | Mesh link planning under project.mesh | Dedicated call/result handling pending; not implemented by the network add_repeaters wrapper |
| 7 | Remaining specialized workflows and compatible viewers | Audit/reuse old SDK presentation; extension, diagnosis, reconfiguration, link replanning and baseline remain tracked below |

Earlier work connected some Cell/Mesh calls before completing Graph. The core
Graph path is now connected; Cell core execution is now connected using the same ownership rules; remaining Mesh network stages are next.
Run focused tests per change and complete notebook acceptance
checks as each workflow becomes ready. e06 will be tested from source through
Mesh planning later; its existing results do not imply stage-skipping support.
Shared HTTP plumbing may live in an internal base, but planner-specific methods
must stay with their planner. File expansion/validation belongs to project_io on
the server. Do not introduce SDK compiler or planner implementations.

### Code placement for the core Graph path

- SDK planners/{graph,cell}.py own their specific operations. _base.py owns
  project-bound transport/candidate inspection; _execution.py supplies reusable
  metric/solve/result HTTP methods to sibling GraphPlanning and CellPlanning. Mesh
  has not opted into network execution yet; no Cell-to-Graph API delegation exists.
- SDK artifacts.py owns atomic local JSON saving. Project owns lifecycle only.
- app/routes_projects.py owns storage/sync/validation/build; routes_project_runtime.py
  owns activate/close. routes_project_results.py retrieves saved output without
  needing a worker and is registered in hub and worker.
- app/project_runtime/candidates.py and mesh.py own their respective HTTP adapters;
  graph.py owns Graph metric/solution routes. guards.py holds active-project checks.
- app/project_runtime/execution_routes.py registers namespace-bound stage routes;
  execution.py orchestrates existing metric and Graph/Cell solver implementations
  with ExecutionState readiness metadata. graph.py and cell.py own their namespaces;
  Cell keeps its candidate/tuning behavior and adds existing Cell solution reports.
  Neither module implements a new planning algorithm.
- cisei_lib/project_io/planning_results.py owns atomic saved result IO and input/
  parameter matching. Existing solver and metric implementations remain unchanged.

Saved Graph results extend the existing output format with project_run metadata.
The latest result survives close/reset; exact retries can reuse it. Reuse does
not rehydrate solver objects: current solution inspection reads the matched saved
result. Future evaluation can use the existing serialized-result evaluation API.
Raw feature-cache disk persistence, result history and checkpoint restoration
remain explicit follow-up work. Do not imply those are implemented by retrieval.

## SDK generation boundary (2026-11-18)

User decision: preserve the original SDK source and develop an independent second
SDK rather than mixing old/new opening behavior. client_sdk/ continues to provide
cisei-planning-sdk / cisei_planning_sdk. client_sdk_v2/ provides
cisei-planning-sdk-v2 / cisei_planning_sdk_v2, initially version 0.1.0.

The new client uses open_project() for storage/recovery and Project.sync() for
explicit transfers. New storage/sync source was moved out of the legacy package;
legacy client.py and exports were restored to their pre-migration versions. The
new package has requests/tomlkit dependencies only, with no legacy SDK/backend
imports or inherited compiler. Its version metadata comes from version.py.

The module/method inventory below remains a legacy SDK review snapshot. It guides
which service capabilities and presentation code to reuse; it is no longer a
plan to replace the old SDK in place. Existing notebook imports and /sdk/latest
remain on the preserved SDK. New server-managed operations will be added to v2.

## Agreed terminology and lifecycle clarification

Use **compiled scenario** for expanded, resolved project inputs, and
`build_scenario()` for the server operation that creates them. Its artifact is
`compiled_scenario.json`. Input validation is a separate lifecycle operation,
`validate_inputs()`, checking files without instantiating the project. The
server now exposes a separate `validate_inputs()` operation for declarations,
paths, file presence, TOML parsing and CSV shape. Full domain field/type/value
coverage remains incomplete. Building reruns this shared pass before construction.

Opening creates/attaches a project even when its input files are incomplete.
The intended sequence is open/create → sync → validate inputs → build scenario
→ plan. Each validation/construction stage returns its own structured feedback.
Failed builds preserve the previous successful compiled scenario. Loading a compiled scenario restores its saved instances/settings without
rereading source inputs or reapplying placements. Source edits require a fresh
build from source files and reload; legacy expanded-TOML reimport is a separate
compatibility concern.

Legacy load_scenario_bundle/load_poles_bundle and local expansion remain in the
preserved SDK. They are not copied into v2; project interpretation belongs to
the service. Viewer reuse remains a separate presentation task.

Successful server-local builds now retain a build_report with error/warning
severity in the compiled artifact; callers may request it using return_report=True.
Warnings permit complete construction, while errors block publication and preserve
the previous successful artifact. The future SDK/API must present warnings even
on successful requests. Coordinate mismatch warnings do not change supplied data.

## Findings and boundary

The 2026-11-17 SDK snapshot had 32 Python source modules including its package initializer. The storage foundation adds remote_project.py and PlanningClient.open_remote_project(). Its public surface is broader than the package-root exports: notebooks also import module functions and classes directly. The method inventory below includes those candidates for compatibility review, not a promise to preserve every helper forever.

Project compilation remains active in the SDK. `PlanningClient.open_project()` calls `define_scenario()` → `_scenario_json()` → `local_io.load_scenario_bundle()`. That loader expands node CSV rows, applies overrides and placements, and sends an expanded dictionary to `POST /planning/scenarios/define`. Opening also parses and imports the complete local feature cache. The staged server builder exists independently; the SDK does not yet use it.

Most Graph/Cell planning stages already execute through `ScenarioSession` API wrappers. The larger remaining migrations are extension preparation, installed topology construction and diagnosis, reconfiguration impact, link-replanning preparation, and baseline model training/evaluation. Moving the initial loader alone will not make this a thin SDK.

Keep local path resolution, file selection, byte transfer, atomic local saves, authentication, polling, progress callbacks, and rendering in the SDK. Keep project interpretation, validation, transformations, derived network facts, and planning on the service. Local filesystem safety and checks that an artifact can be rendered remain legitimate SDK checks; they do not duplicate project validation.

## Module ownership map

| Current modules | Current responsibility | Target and prerequisite |
| --- | --- | --- |
| `client.py` | Registration, workspace selection, expanded scenario definition, project opening, HTTP error handling | Retain facade and transport; replace local compiler calls after staged-project lifecycle exists. Preserve dictionary-based definition separately for compatibility. |
| `session.py` | Stage wrappers, configuration parsing, mesh input preparation, cache merge/export, viewer entry points, result saving | Retain public facade; delegate file interpretation and mesh/checkpoint decisions to service. Download artifacts and render locally. |
| `project.py` | Local paths and persistence; solution-to-project transformation; dependency-based selection | Keep local file operations. Move solution promotion and semantic selection validation to service; return input artifacts for the next project. |
| `workspace.py` | Download and extract sample workspace | Keep. Root `examples/` remains the distribution source. |
| `local_io.py` | JSON/TOML persistence, antenna-library loading, metric parsing, CSV expansion, pole expansion, overrides | Keep artifact readers/writers and generic serialization. Move project, library and metric interpretation to server. Public bundle helper needs a deliberate compatibility decision because it currently works offline without a client. |
| `antenna_placements.py`, `attachments.py` | Shared placement adapter; local equipment loading and validation | Remove from active SDK opening after server migration. Retire packaged `_placements`/`_equipment` only after all callers migrate. |
| `extended_project.py` | Merge extension configuration, apply replacement rules/ranks, preserve source identities and feature data, write a derived project | Service transformation with downloaded inputs/report. Retain `ExtendedPlanningProject.prepare` facade if a connection can be supplied compatibly. |
| `installed_project.py` | Construct installed topology, extract/enrich features, inspect links, diagnose and reconfigure | Split local presentation/persistence from service construction, enrichment and analysis. |
| `installed_diagnosis.py` | Evaluate installed links using baseline model; diagnose selected paths; configuration identity | Service domain processing. Download diagnosis reports for offline viewers. |
| `topology.py`, `dependencies.py` | Derive installed edges and descendant relationships | Service result enrichment. Viewers consume saved relationships; do not silently lose offline dependency viewing. |
| `reconfiguration.py` | Validate saved selections, calculate removal impact, reject stale/tampered confirmation; discover Jupyter destinations | Move semantic preview/confirmation to service. Keep Jupyter destination discovery local. |
| `link_replanning.py` | Read recipes, remove links/devices, select poles spatially, orchestrate searches, compose final topology and artifacts | Service workflow under mesh/replanning responsibilities, using existing mesh planner rather than a new algorithm. SDK retains calls, progress and viewers. |
| `baseline.py`, `baseline_features.py`, `rssi_model.py` | Associate measurements, filter observations, fit/evaluate expression models, generate metrics | Service baseline/model workflow. Destination in active library architecture needs review; a legacy RF implementation is not automatically a migration target. |
| `baseline_inspection.py`, `link_browser.py` | Adapt link endpoints/configuration for direct features calls, select links, manage browser state | Keep navigation. Move semantic endpoint/height/technology resolution to planning service; preserve features viewer integration explicitly. |
| `mesh_progress.py` | Submit work and poll progress/expansion operations | Keep transport/progress; server owns durable run/checkpoint identity. |
| `mesh_artifacts.py` | Split/compact mesh reports and read/write referenced study artifacts | Keep file access. Service should define artifact layout and checkpoint references; SDK should not reconstruct executable state from rendered reports. |
| `baseline_view.py`, `evaluation_view.py`, `link_panel.py`, `mesh_iterations_view.py`, `mesh_path_panel.py`, `mesh_studies_view.py`, `metrics_view.py`, `network_view.py`, `reconfiguration_view.py`, `visualization.py` | HTML/widgets/plots and interactive actions | Keep presentation. Audit imported dependency/diagnosis/feature computations and replace them with server-produced fields. Live actions call SDK operations; saved viewers read artifacts. |
| `__init__.py` | Public exports and version | Preserve exports initially. Resolve version mismatch with package metadata during packaging work, independently of migration. |

## Dependency map

Current major chains:

- `PlanningClient` → `PlanningProject`, `ScenarioSession`, `workspace`, `local_io` → local CSV/profile expansion and shared placement code.
- `ScenarioSession` → `PlanningClient._request` → existing planning API. It also calls `local_io` for libraries/metrics/poles, `mesh_progress` for polling, `mesh_artifacts` and project persistence for saving, and viewer modules for rendering.
- `ExtendedPlanningProject` → `PlanningProject` + `local_io` + replacement/rank processing.
- `InstalledNetworkProject` → `local_io`, `topology`, `baseline_features`, `installed_diagnosis`, `reconfiguration` and viewers.
- `LinkReplanningProject` → source project + reconfiguration + local pole selection → scenario sessions/mesh link API → local final-result composition.
- `BaselineProject` → `baseline_features` → `rssi_model` (NumPy for fitting) → model/metric/reports → baseline viewers.
- Viewers → artifacts, plus some domain helpers and direct features integration. This is the boundary to untangle before moving viewer files mechanically.

Confirmed overlap: SDK profile expansion/overrides and server `PlanningScenario`; staged builder and SDK file interpretation; shared placement/equipment source packaged in both distributions. Similar model code exists under `cisei_lib/legacy/core/rf/rssi_core.py`; equivalence is unverified. Installed/dependency/reconfiguration methods were not found as corresponding active service operations in this review. Treat these as missing service capabilities, not already migrated code.

## Public workflow migration map

| Existing entry points | Keep in SDK | Processing to move / server prerequisite |
| --- | --- | --- |
| `PlanningClient.connect`, `init_workspace`, `antenna_path`, `metric_path` | Authentication, workspace setup and local path selection | None beyond existing endpoints. |
| `open_project`, path form of `define_scenario` | File access, transfer, returned handle | Stage original inputs, validate, save bundle, activate scenario from saved bundle. |
| Dictionary form of `define_scenario` | JSON request adapter | Keep existing programmatic compatibility path until policy is reviewed. |
| `ScenarioSession.upload_configuration` | Select local files and send requested updates | Parse antenna libraries/metrics and validate references server-side. Determine revision/invalidation behavior. |
| `build_candidate_edges`, `compute_metrics`, `set_edge_metrics`, `solve`, `evaluate`, `evaluate_paths`, `network_metric`, `optimize_parents`, getters/exports | Existing API wrappers and returned state | Already service-backed; preserve stage ordering and output semantics. |
| `add_repeaters`, `plan_mesh_network`, `plan_mesh_link`, `load_mesh_expansion`, `remove_unused_repeaters` | API calls, progress and artifact saves | Read staged poles and mesh settings; server-owned checkpoints. Preserve explicit in-memory poles as compatibility input until reviewed. |
| `import_feature_cache`, `export_feature_cache`, `save_feature_cache` | Transfer/save cache artifacts, acknowledge cursor after successful local write | Keep cache parsing/merging semantics server-owned eventually. Define reopen/export policy and cursor lifetime. |
| `PlanningProject.import_solution`, `save_redundancy_selection` | Copy/download files and persist user choices | Produce next-stage inputs and validate selected topology/identity server-side. |
| `ExtendedPlanningProject.prepare` | Facade, destination selection, artifact download | Extension transformation and validation service operation. |
| Installed `import_topology`, `enrich_features`, `path_to_backbone`, `evaluate_links`, `diagnose_path`, preview/load reconfiguration | Facades, downloaded results and local selections | Installed topology/measurement/diagnosis/reconfiguration operations. |
| `LinkReplanningProject.prepare`, `replan_links` | Facades and progress callbacks | Service orchestration, pole selection, final topology composition. |
| `BaselineProject.enrich_features`, `train`, `evaluate`, `export_metric` | Facades and artifact saves | Baseline service workflow; current signatures have no client, so connection binding needs agreement. |
| `show_*`, `review_reconfiguration`, browser selection | HTML/widgets/navigation and local saves | Consume saved reports; live preview/inspection uses service calls. |
| `load_result`, `save_result`, `save_scenario`, project path properties | Generic artifact IO | Retain. Saving a supplied dictionary is distinct from compiling a project. |
| `load_scenario_bundle`, `load_poles_bundle`, direct `ExpressionModel` APIs | Compatibility decision required | Their offline return values cannot be preserved by silently replacing them with HTTP calls without a connection. |

## Agreed lifecycle (2026-11-18)

1. Open/create associates a local project folder with isolated server storage, even when inputs are absent. The server issues a project ID and recovery credential, saved locally in .session.json beside scenario.toml. Project identity survives worker/session timeout. Creation/resume are implemented through SDK v2 open_project(); the original SDK open_project remains preserved.
2. Preserve workspace-relative folder structure. [files] centralizes the transfer list; completing antenna/metric dependency coverage is implementation work, not a new dependency-discovery protocol.
3. Sync is implemented as SDK v2 Project.sync(): explicit add/update of raw files without deleting server files. It does not trigger validation, compilation or planning. Remove obsolete file references from inputs; prevent implicit discovery from reviving stale files.
4. validate_inputs checks synchronized files without changing compiled state. build_scenario constructs/replaces the compiled scenario on success. There is no separate activation operation. Loading compiled state never reapplies source files.
5. Results continue through existing API retrieval and SDK local saving, outside input sync. Persist artifacts server-side and identify input bytes plus effective operation parameters so retries can retrieve an existing result without replanning. Changed planning input bytes invalidate related results; identical sync/validation do not.
6. Updated 2026-11-19: explicit close releases active project/planner/features resources and preserves storage. One active project per user worker; another requires explicit close. Timeout/disconnection does not delete files. Saved-scenario activation and close are implemented in SDK v2; deletion, result persistence and garbage collection remain separate work.
7. Saved .session.json metadata requires an explicit resume/new choice. The SDK raises ProjectChoiceRequired rather than calling input(); a UI/agent may ask the user, while scripts supply mode. New identities preserve previous recovery records rather than deleting old storage. Failed recovery never silently creates a new project.

Remaining implementation details include manifest/file-role completion, workspace-bound path resolution, transfer retry/idempotency, result invalidation/download semantics, explicit close and migration of legacy wrappers. The directory itself associates the local project and its metadata; no additional user-account binding is required for this temporary-project model.

## Execution batches and gates

| Batch | Work | Evidence required before removing old processing |
| --- | --- | --- |
| 0 | Review this inventory/lifecycle and capture existing workflow outputs | Pin service source, SDK wheel, examples and controlled features. Distinguish preexisting failures. |
| 1 | Project file staging/build/activation; redirect opening only | Compare legacy loader and server builder on representative graph/cell/mesh inputs; structured failures, previous valid revision, placements, attached equipment and separate poles. |
| 2 | Configuration updates and mesh file/checkpoint loading | Stage-order/invalidation tests, mesh link and network resume, same poles/paths/metrics; avoid complete cache export per link. |
| 3 | Installed topology, dependencies, reconfiguration and promotion | Stable IDs/edges, protected equipment, unchanged source files, stale/tampered preview rejection, offline viewer artifacts. |
| 4 | Extension and link-replanning transformations | Replacement rules/ranks, corridor/pole choices, final topology and provenance match controlled fixtures. |
| 5 | Baseline measurement association, training and diagnosis | Same observation matching, fitted coefficients within tolerance, metric outputs, diagnosis categories and source fingerprints. Review module placement first. |
| 6 | Package cleanup and viewer organization | Remove unreachable compiler helpers/shared wheel mappings and unused dependencies only after callers migrate. Build/install wheel in isolation and run distributed notebooks. |

Existing tests to reuse: `test_project_io.py`, `test_scenario_configuration.py`, `test_antenna_placements.py`, `test_project_features.py`, `test_mesh_link_planner.py`, `test_mesh_network_planner.py`, `test_mesh_progress.py`, `test_sdk_installed_project.py`, `test_sdk_dependencies.py`, `test_sdk_reconfiguration.py`, `test_sdk_link_replanning.py`, `test_extended_project.py`, `test_sdk_baseline.py`, `test_sdk_baseline_features.py`, SDK viewer tests, `test_distributed_notebooks.py`, and workspace distribution tests.

Missing gates include raw-file transfer failures, interrupted sync/retry, last-valid-revision activation, save/download recovery, session/project isolation, expiry/close cleanup, errors presented without raw tracebacks, and original-input preservation. Use controlled features for deterministic regression and a separate deployed-service smoke test. Existing tests do not yet prove the proposed upload lifecycle.

Packaging findings: `pyproject.toml` declares 0.1.84 while package `__version__` is 0.1.68. Mandatory dependencies currently include requests, tomlkit, PyYAML and cisei-features-sdk. NumPy is optional for baseline and notebook use. Do not remove parsers/numerical dependencies until migrated callers and remaining serialization/viewer needs have been audited.

## Method and dependency appendix

Generated by Python AST from the current SDK source. Includes public functions, explicit public methods/properties, and constructors; inherited methods are listed at their defining class. Direct private helpers/imports are recorded as supporting evidence. Calls are syntactic, not a complete runtime call graph. Notebook hits are exact method/function-name tokens in code cells under `examples/notebooks`; a hit does not establish receiver type, and no hit does not establish that an API is unused. HTTP expressions are direct `_request` calls, not a proposed endpoint list. File IO can be indirect through helpers and project properties.

### __init__.py

SDK/library imports: `cisei_planning_sdk.baseline`, `cisei_planning_sdk.client`, `cisei_planning_sdk.extended_project`, `cisei_planning_sdk.installed_project`, `cisei_planning_sdk.link_replanning`, `cisei_planning_sdk.local_io`, `cisei_planning_sdk.project`, `cisei_planning_sdk.session`, `cisei_planning_sdk.visualization`, `cisei_planning_sdk.workspace`.

No explicitly declared public functions/methods; inspect reexports and module imports.

### antenna_placements.py

SDK/library imports: `_placements`, `cisei_lib.project_io.placements`.

No explicitly declared public functions/methods; inspect reexports and module imports.

### attachments.py

SDK/library imports: `_equipment`, `cisei_lib.project_io.equipment`, `local_io`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `load_attached_equipment` (16) | — | `load_scenario_bundle`, `scenario_path.read_text` | — |

### baseline.py

SDK/library imports: `baseline_features`, `baseline_inspection`, `baseline_view`, `rssi_model`.

Private supporting functions/methods: `_save`, `BaselineProject._file`, `BaselineProject._records`, `BaselineProject._check_result`, `BaselineProject._show`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `BaselineProject.__init__` (39) | — | `(self.path / 'baseline.json').read_text`, `json.loads`, `self._file`, `self.path.is_relative_to`, `self.recipe.get` | — |
| `BaselineProject.enrich_features` (80) | — | `json.loads`, `self._file`, `self._file(measurements).read_text`, `self.enriched_features_path.exists`, `self.enriched_features_path.read_text`, `self.features_path.read_text`, `self.measurements_path.read_text` | e08_baseline_tecnologia.ipynb, e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `BaselineProject.train` (114) | — | `evaluate_model`, `fit_expression_model`, `json.loads`, `self._records`, `self.data_path.read_bytes`, `self.enriched_features_path.read_text`, `self.model_path.read_bytes`, `self.recipe.get` | e08_baseline_tecnologia.ipynb |
| `BaselineProject.evaluate` (155) | — | `ExpressionModel.load`, `evaluate_model`, `self._records`, `self.data_path.read_bytes`, `self.model_path.read_bytes` | custom_configuration.ipynb, e08_baseline_tecnologia.ipynb, example1_LTE_planning_basic.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `BaselineProject.export_metric` (185) | — | `ExpressionModel.load`, `self.metric_path.parent.mkdir`, `self.metric_path.write_text`, `tomlkit.dumps` | e08_baseline_tecnologia.ipynb |
| `BaselineProject.show_training` (216) | — | `self._show` | e08_baseline_tecnologia.ipynb |
| `BaselineProject.show_evaluation` (220) | — | `self._show` | e08_baseline_tecnologia.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb |
| `BaselineProject.inspect_divergent_links` (224) | — | `(self.path / 'evaluation.json').read_text`, `json.loads`, `self._check_result`, `self._records` | e08_baseline_tecnologia.ipynb |

### baseline_features.py

Private supporting functions/methods: `_number`, `_text`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `request_key` (24) | — | `json.dumps` | — |
| `observation` (61) | — | `json.dumps` | — |
| `validate_cache` (94) | — | `json.dumps` | — |
| `enrich_cache` (122) | — | — | — |
| `training_records` (174) | — | — | — |

### baseline_inspection.py

SDK/library imports: `link_panel`.

Private supporting functions/methods: `BaselineInspectionPanel._label`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `finite` (8) | — | — | — |
| `MeasuredLinkScenario.__init__` (16) | — | — | — |
| `MeasuredLinkScenario.link` (21) | — | — | — |
| `BaselineInspectionPanel.__init__` (48) | — | — | — |

### baseline_view.py

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `training_view` (7) | — | — | — |
| `evaluation_view` (25) | — | `json.dumps` | — |

### client.py

SDK/library imports: `cisei_planning_sdk.local_io`, `cisei_planning_sdk.project`, `cisei_planning_sdk.session`, `cisei_planning_sdk.workspace`.

Private supporting functions/methods: `PlanningClient._library_path`, `PlanningClient._scenario_json`, `PlanningClient._request`, `PlanningClient._url`, `_json_value`, `_find_workspace`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `PlanningClient.__init__` (24) | — | `self.connect` | — |
| `PlanningClient.init_workspace` (45) | — | — | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `PlanningClient.connect` (55) | — | `self.session.post` | — |
| `PlanningClient.define_scenario` (85) | `'post', '/planning/scenarios/define'` | `self._request`, `self._scenario_json` | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `PlanningClient.open_project` (132) | — | `self.define_scenario` | advanced_api.ipynb, custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, exemple_4_Mesh_basic.ipynb, graph_planning.ipynb |
| `PlanningClient.antenna_path` (176) | — | `self._library_path` | — |
| `PlanningClient.metric_path` (180) | — | `self._library_path` | — |
| `PlanningClient.evaluate_result` (194) | `'post', '/planning/results/evaluate'` | `self._request` | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |

### dependencies.py

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `network_dependencies` (5) | — | — | — |

### evaluation_view.py

SDK/library imports: `local_io`.

Private supporting functions/methods: `EvaluationView._repr_html_`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `EvaluationView.__init__` (9) | — | — | — |
| `EvaluationView.to_html` (12) | — | `json.dumps` | — |
| `EvaluationView.save_html` (23) | — | `path.write_text`, `self.to_html` | — |

### extended_project.py

SDK/library imports: `antenna_placements`, `local_io`, `project`, `topology`.

Private supporting functions/methods: `_merge`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `ExtendedPlanningProject.__init__` (38) | — | `self.extension_path.is_relative_to`, `self.path.is_relative_to`, `self.source.path.is_relative_to` | — |
| `ExtendedPlanningProject.prepare` (49) | — | `(stage / 'features.json').write_text`, `(stage / 'nodes.csv').open`, `(stage / 'replacement_report.json').write_text`, `(stage / 'scenario.toml').write_text`, `json.dumps`, `json.loads`, `load_scenario_bundle`, `placements.read_bytes`, `previous_report.read_text`, `report_path.read_text`, `self.extension_path.read_bytes`, `self.extension_path.read_text`, `self.features_path.exists`, `self.load_features`, `self.scenario_path.exists`, `self.source.features_path.exists`, `self.source.load_features`, `self.source.load_result`, `self.source.scenario_path.read_bytes`, `self.source.scenario_path.read_text`, `source_csv.open`, `source_csv.read_bytes`, `tomlkit.dumps` | e07_02_projeto_redundancia.ipynb, e13_replanejamento.ipynb, example_3_LTE_hybrid.ipynb |

### installed_diagnosis.py

SDK/library imports: `baseline`, `baseline_features`, `rssi_model`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `configuration_hash` (13) | — | `json.dumps` | — |
| `evaluate_links` (18) | — | `ExpressionModel.load`, `baseline.data_path.read_bytes`, `baseline.enriched_features_path.read_text`, `baseline.model_path.read_bytes`, `evaluate_model`, `json.loads`, `project.result_path.read_bytes` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `load_diagnosis` (100) | — | `(project.path / 'evaluation.json').read_text`, `json.loads`, `project.result_path.read_bytes` | — |
| `path_diagnosis` (110) | — | — | — |

### installed_project.py

SDK/library imports: `attachments`, `baseline`, `installed_diagnosis`, `link_browser`, `link_panel`, `local_io`, `network_view`, `project`, `reconfiguration`.

Private supporting functions/methods: `InstalledNetworkProject._installed_links`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `InstalledNetworkProject.export_scenario` (17) | — | `load_scenario_bundle` | advanced_api.ipynb, custom_configuration-checkpoint.ipynb |
| `InstalledNetworkProject.enriched_features_path` (22) | — | — | — |
| `InstalledNetworkProject.extract_features` (40) | — | `self._installed_links`, `self.load_features` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `InstalledNetworkProject.link` (60) | — | — | — |
| `InstalledNetworkProject.inspect_links` (65) | — | `self._installed_links` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `InstalledNetworkProject.enrich_features` (73) | — | `baseline._file(measurements).read_text`, `baseline.measurements_path.read_text`, `json.loads`, `self._installed_links`, `self.export_scenario` | e08_baseline_tecnologia.ipynb, e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `InstalledNetworkProject.import_topology` (131) | — | `load_scenario_bundle`, `self.save_result` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `InstalledNetworkProject.path_to_backbone` (201) | — | `self.load_result` | — |
| `InstalledNetworkProject.evaluate_links` (218) | — | `evaluate_links` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |
| `InstalledNetworkProject.diagnose_path` (222) | — | `load_diagnosis` | — |
| `InstalledNetworkProject.attached_equipment` (226) | — | `load_attached_equipment` | — |
| `InstalledNetworkProject.preview_reconfiguration` (231) | — | `preview_reconfiguration` | — |
| `InstalledNetworkProject.load_reconfiguration` (236) | — | `load_reconfiguration` | e13_diagnostico.ipynb, e13_replanejamento.ipynb |
| `InstalledNetworkProject.review_reconfiguration` (241) | — | `self.show_topology` | e13_diagnostico.ipynb |
| `InstalledNetworkProject.show_topology` (252) | — | `load_attached_equipment`, `load_diagnosis`, `self.load_result` | e09_diagnostico_bro.ipynb, e13_diagnostico.ipynb |

### link_browser.py

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `prepare_link` (6) | — | — | — |
| `LinkBrowser.__init__` (55) | — | — | — |
| `LinkBrowser.current` (65) | — | — | — |
| `LinkBrowser.select` (68) | — | `self.scenario.link` | — |
| `LinkBrowser.next` (80) | — | `self.select` | — |
| `LinkBrowser.previous` (85) | — | `self.select` | — |

### link_panel.py

SDK/library imports: `link_browser`.

Private supporting functions/methods: `LinkInspectionPanel._endpoints`, `LinkInspectionPanel._endpoint_list_changed`, `LinkInspectionPanel._endpoint_changed`, `LinkInspectionPanel._label`, `LinkInspectionPanel._update_controls`, `LinkInspectionPanel._selection_changed`, `LinkInspectionPanel._move`, `LinkInspectionPanel._inspect`, `LinkInspectionPanel._render`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `LinkInspectionPanel.__init__` (26) | — | `self._endpoints`, `self._label`, `self._move`, `self._render`, `self._update_controls`, `self.endpoint.observe`, `self.endpoint_list.observe`, `self.links.observe`, `self.next.on_click`, `self.previous.on_click`, `self.refresh.on_click`, `self.view.observe` | — |
| `LinkInspectionPanel.current` (124) | — | — | — |
| `LinkInspectionPanel.show` (128) | — | — | — |

### link_replanning.py

SDK/library imports: `installed_project`, `local_io`, `project`, `session`, `visualization`.

Private supporting functions/methods: `_read`, `_key`, `LinkReplanningProject._settings`, `LinkReplanningProject._session`, `LinkReplanningProject._poles`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `LinkReplanningProject.__init__` (32) | — | `self.path.is_relative_to`, `self.source.path.is_relative_to` | — |
| `LinkReplanningProject.prepare` (65) | — | `(self.path / 'attached_equipment.csv').open`, `(self.path / 'nodes.csv').open`, `json.dumps`, `poles_path.read_bytes`, `save_result`, `save_scenario`, `self._session`, `self._settings`, `self.result_path.exists`, `self.save_features`, `self.save_result`, `self.scenario_path.exists`, `self.source._installed_links`, `self.source.attached_equipment`, `self.source.export_scenario`, `self.source.features_path.exists`, `self.source.load_features`, `self.source.load_reconfiguration`, `self.source.load_result`, `self.source.path_to_backbone`, `self.source.scenario_path.read_text`, `source_csv.open` | e07_02_projeto_redundancia.ipynb, e13_replanejamento.ipynb, example_3_LTE_hybrid.ipynb |
| `LinkReplanningProject.replan_links` (188) | — | `save_result`, `save_scenario`, `self._poles`, `self._session`, `self._settings`, `self.save_features`, `self.save_result` | e13_replanejamento.ipynb |
| `LinkReplanningProject.show_prepared_network` (262) | — | — | e13_replanejamento.ipynb |
| `LinkReplanningProject.show_network` (268) | — | `self.load_result` | e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, e13_replanejamento.ipynb, example_5_Mesh_network.ipynb |
| `LinkReplanningProject.show_link_candidates` (279) | — | — | e13_replanejamento.ipynb |
| `LinkReplanningProject.show_link_solution` (284) | — | — | e13_replanejamento.ipynb |

### local_io.py

SDK/library imports: `antenna_placements`.

Private supporting functions/methods: `_apply_interface_overrides`, `_read_instances`, `_instantiate_sites`, `_plain`, `_toml_value`, `_first_present`, `_clean_id`, `_is_blank`, `_optional_float`, `_rank_value`, `_bool_value`, `_max_links_value`, `_parsed_scalar`, `_drop_none`, `_json_value`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `load_antenna_library` (13) | — | `Path(path).expanduser().read_text` | — |
| `load_metric_spec` (21) | — | `Path(path).expanduser().read_text` | — |
| `save_result` (28) | — | `json.dumps`, `path.write_text` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `load_result` (43) | — | `Path(path).expanduser().read_text`, `json.loads` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `save_scenario` (51) | — | `path.write_text`, `tomlkit.dumps` | advanced_api.ipynb |
| `load_scenario_bundle` (62) | — | `apply_antenna_placements`, `path.read_text` | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `load_poles_bundle` (91) | — | `load_scenario_bundle`, `path.read_text` | — |

### mesh_artifacts.py

SDK/library imports: `cisei_planning_sdk.local_io`.

Private supporting functions/methods: `_snapshot`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `load_mesh_artifact` (23) | — | `load_result` | — |
| `compact_mesh_result` (27) | — | `save_result` | — |
| `save_mesh_artifacts` (70) | — | — | — |

### mesh_iterations_view.py

SDK/library imports: `network_view`.

Private supporting functions/methods: `MeshIterationsView._repr_html_`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `MeshIterationsView.__init__` (10) | — | `self.snapshots.append` | — |
| `MeshIterationsView.payload` (29) | — | — | — |
| `MeshIterationsView.to_html` (35) | — | `json.dumps`, `self.payload` | — |
| `MeshSolutionView.__init__` (51) | — | — | — |

### mesh_path_panel.py

SDK/library imports: `mesh_artifacts`, `mesh_progress`, `mesh_studies_view`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `mesh_path_panel` (3) | `'post', path + '/path_options'`; `'post', f"{path}/expansions/{proposal['run_id']}/apply_path"` | `load_parents`, `save_mesh_artifacts` | — |

### mesh_progress.py

Private supporting functions/methods: `_box`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `run_with_progress` (45) | `'post', path`; `'post', path`; `'get', path + '/progress'` | — | — |
| `run_expansion` (93) | `'post', path`; `'get', url` | — | — |

### mesh_studies_view.py

SDK/library imports: `mesh_iterations_view`, `network_view`.

Private supporting functions/methods: `MeshStudiesView._repr_html_`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `proposal_view` (9) | — | — | — |
| `MeshStudiesView.__init__` (21) | — | `self.steps.append` | — |
| `MeshStudiesView.to_html` (48) | — | `json.dumps` | — |

### metrics_view.py

SDK/library imports: `evaluation_view`.

Private supporting functions/methods: `MetricsView._repr_html_`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `MetricsView.__init__` (7) | — | — | — |

### network_view.py

SDK/library imports: `cisei_planning_sdk.dependencies`, `reconfiguration_view`.

Private supporting functions/methods: `NetworkView._repr_html_`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `NetworkView.__init__` (10) | — | `network_dependencies` | — |
| `NetworkView.to_html` (31) | — | `json.dumps`, `self.result.get` | — |
| `NetworkView.save_html` (49) | — | `path.write_text`, `self.to_html` | — |

### project.py

SDK/library imports: `attachments`, `cisei_planning_sdk.dependencies`, `cisei_planning_sdk.local_io`, `cisei_planning_sdk.network_view`, `cisei_planning_sdk.visualization`, `local_io`.

Private supporting functions/methods: `PlanningProject.__post_init__`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `PlanningProject.scenario_path` (27) | — | — | advanced_api.ipynb |
| `PlanningProject.name` (31) | — | — | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `PlanningProject.features_path` (35) | — | — | — |
| `PlanningProject.result_path` (39) | — | — | — |
| `PlanningProject.load_features` (42) | — | `json.loads`, `self.features_path.read_text` | — |
| `PlanningProject.save_features` (45) | — | `json.dumps`, `self.features_path.parent.mkdir` | — |
| `PlanningProject.save_result` (61) | — | `save_result` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `PlanningProject.load_result` (64) | — | `load_result` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `PlanningProject.save_scenario` (67) | — | `save_scenario` | advanced_api.ipynb |
| `PlanningProject.show_network` (70) | — | `self.load_result` | e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, e13_replanejamento.ipynb, example_5_Mesh_network.ipynb |
| `PlanningProject.import_solution` (75) | — | `(self.path / filename).open`, `load_attached_equipment`, `load_scenario_bundle`, `self.load_result`, `self.load_result().get`, `self.result_path.exists`, `self.save_result`, `self.save_scenario`, `self.scenario_path.exists`, `self.scenario_path.is_file` | e06_02_agu_selecao_de_pais.ipynb |
| `PlanningProject.show_dependencies` (119) | — | `self.load_result` | e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb |
| `PlanningProject.save_redundancy_selection` (128) | — | `json.dumps`, `json.loads`, `network_dependencies`, `path.write_text`, `self.result_path.read_bytes` | e07_01_projeto_original.ipynb |

### reconfiguration.py

SDK/library imports: `installed_diagnosis`.

Private supporting functions/methods: `_digest`, `_impact`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `jupyter_destinations` (10) | — | — | — |
| `review_context` (32) | — | `project.result_path.read_bytes` | — |
| `preview_reconfiguration` (100) | — | — | — |
| `load_reconfiguration` (137) | — | `Path(path).expanduser().read_text`, `json.loads`, `preview_reconfiguration` | e13_diagnostico.ipynb, e13_replanejamento.ipynb |
| `compact_reconfiguration` (158) | — | — | — |
| `review_data` (166) | — | `preview_reconfiguration` | — |

### reconfiguration_view.py

No explicitly declared public functions/methods; inspect reexports and module imports.

### rssi_model.py

Private supporting functions/methods: `_to_ns`, `_build_matrix`, `_project_bounds`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `classify_rssi` (17) | — | — | — |
| `load_json` (32) | — | `json.load`, `open` | — |
| `load_jsonl` (40) | — | `json.loads`, `open` | — |
| `get_nested` (50) | — | — | — |
| `is_number` (59) | — | — | — |
| `filter_records_by_feature` (69) | — | — | — |
| `filter_records_by_many` (104) | — | — | — |
| `eq` (130) | — | — | — |
| `ne` (134) | — | — | — |
| `gt` (138) | — | — | — |
| `ge` (142) | — | — | — |
| `lt` (146) | — | — | — |
| `le` (150) | — | — | — |
| `eval_expr` (154) | — | — | — |
| `ExpressionModel.to_dict` (202) | — | `self.fixed_coeffs.items`, `self.learned_coeffs.items` | — |
| `ExpressionModel.from_dict` (214) | — | — | — |
| `ExpressionModel.save` (238) | — | `json.dump`, `path.open`, `self.to_dict` | — |
| `ExpressionModel.load` (244) | — | `json.load`, `path.open` | planning_sdk-checkpoint.ipynb |
| `ExpressionModel.predict` (252) | — | `self.fixed_coeffs.get`, `self.fixed_terms.items`, `self.learned_coeffs.get`, `self.learned_terms.items` | — |
| `ExpressionModel.explain` (269) | — | `self.fixed_coeffs.get`, `self.fixed_terms.items`, `self.learned_coeffs.get`, `self.learned_terms.items` | — |
| `ExpressionModel.residual` (303) | — | `self.predict` | — |
| `fit_expression_model` (377) | — | — | — |
| `residuals` (491) | — | — | — |
| `evaluate_model` (500) | — | — | — |
| `describe_residuals` (535) | — | — | — |
| `add_model_field` (545) | — | — | — |

### session.py

SDK/library imports: `attachments`, `cisei_planning_sdk.link_browser`, `cisei_planning_sdk.link_panel`, `cisei_planning_sdk.local_io`, `cisei_planning_sdk.mesh_artifacts`, `cisei_planning_sdk.mesh_progress`, `cisei_planning_sdk.visualization`, `mesh_artifacts`, `mesh_iterations_view`, `mesh_path_panel`, `mesh_studies_view`.

Private supporting functions/methods: `ScenarioSession._mesh_network_body`, `ScenarioSession._feature_file_state`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `ScenarioSession.add_repeaters` (70) | `'post', path + '/add_repeaters'` | `save_mesh_artifacts`, `self._mesh_network_body`, `self.client._request`, `self.project.load_result`, `self.project.result_path.exists`, `self.project.save_result`, `self.save_feature_cache` | e06_01_agu_projeto_completo.ipynb, example_5_Mesh_network.ipynb |
| `ScenarioSession.load_mesh_expansion` (125) | `'post', f'/planning/scenarios/{self.scenario_id}/mesh/load_expansion'` | `self.client._request`, `self.project.load_result`, `self.project.save_result` | e06_01_agu_projeto_completo.ipynb |
| `ScenarioSession.remove_unused_repeaters` (144) | `'post', f'/planning/scenarios/{self.scenario_id}/mesh/remove_unused_repeaters'` | `self.client._request` | e06_01_agu_projeto_completo.ipynb, example_5_Mesh_network.ipynb |
| `ScenarioSession.plan_mesh_network` (153) | `'post', f'/planning/scenarios/{self.scenario_id}/mesh/plan_network'` | `save_mesh_artifacts`, `self._mesh_network_body`, `self.client._request`, `self.save_feature_cache` | — |
| `ScenarioSession.show_mesh_network` (170) | — | — | e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb, example_5_Mesh_network.ipynb |
| `ScenarioSession.show_mesh_iterations` (187) | — | `load_mesh_artifact` | e06_01_agu_projeto_completo.ipynb, example_5_Mesh_network.ipynb |
| `ScenarioSession.show_mesh_study` (206) | — | `load_mesh_artifact`, `self.project.load_result`, `self.project.result_path.exists` | e06_01_agu_projeto_completo.ipynb |
| `ScenarioSession.plan_mesh_link` (255) | `'post', f'/planning/scenarios/{self.scenario_id}/mesh/plan_link'` | `load_poles_bundle`, `self.client._request`, `self.project.save_result`, `self.project.scenario_path.read_text`, `self.save_feature_cache` | exemple_4_Mesh_basic.ipynb |
| `ScenarioSession.show_mesh_link` (293) | — | `self.result.get` | exemple_4_Mesh_basic.ipynb |
| `ScenarioSession.show_mesh_candidates` (329) | — | `self.show_mesh_link` | exemple_4_Mesh_basic.ipynb |
| `ScenarioSession.show_network` (333) | — | `self.export_result` | e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, e13_replanejamento.ipynb, example_5_Mesh_network.ipynb |
| `ScenarioSession.link` (346) | — | — | — |
| `ScenarioSession.browse_links` (356) | — | — | — |
| `ScenarioSession.show_link_browser` (361) | — | — | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb |
| `ScenarioSession.get_candidate_links` (374) | — | `self.get_candidate_edges` | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb |
| `ScenarioSession.get_solution_links` (383) | — | `self.get_candidate_edges`, `self.get_solution` | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb |
| `ScenarioSession.import_feature_cache` (396) | `'post', f'/planning/scenarios/{self.scenario_id}/features/cache'` | `self.client._request` | — |
| `ScenarioSession.export_feature_cache` (401) | `'get', f'/planning/scenarios/{self.scenario_id}/features/cache'` | `self.client._request` | — |
| `ScenarioSession.save_feature_cache` (405) | `'get', endpoint`; `'get', endpoint` | `self._feature_file_state`, `self.client._request`, `self.project.load_features`, `self.project.save_features` | — |
| `ScenarioSession.ok` (436) | — | — | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.upload_configuration` (440) | `'post', f'/planning/scenarios/{self.scenario_id}/configuration'` | `load_antenna_library`, `load_metric_spec`, `self.client._request`, `self.client.antenna_path`, `self.client.metric_path` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, custom_configuration-checkpoint.ipynb |
| `ScenarioSession.refresh` (472) | `'get', f'/planning/scenarios/{self.scenario_id}'` | `self.client._request` | graph_planning-checkpoint.ipynb |
| `ScenarioSession.build_candidate_edges` (489) | `'post', f'/planning/scenarios/{self.scenario_id}/candidate_edges/build'` | `self.client._request` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.get_candidate_edges` (531) | `'get', f'/planning/scenarios/{self.scenario_id}/candidate_edges'` | `self.client._request` | — |
| `ScenarioSession.compute_metrics` (546) | `'post', f'/planning/scenarios/{self.scenario_id}/metrics/compute'` | `self.client._request`, `self.metrics.get`, `self.save_feature_cache` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.get_metrics` (614) | `'get', f'/planning/scenarios/{self.scenario_id}/metrics'` | `self.client._request` | graph_planning-checkpoint.ipynb |
| `ScenarioSession.show_metrics` (634) | — | `self.get_metrics` | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb |
| `ScenarioSession.solve` (646) | `'post', f'/planning/scenarios/{self.scenario_id}/solution/solve'` | `self.client._request`, `self.result.items` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.get_solution` (679) | `'get', f'/planning/scenarios/{self.scenario_id}/solution'` | `self.client._request` | custom_configuration-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.set_edge_metrics` (693) | `'post', f'/planning/scenarios/{self.scenario_id}/metrics/compute'` | `self.client._request` | graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.show_evaluation` (721) | — | `self.evaluate` | e08_baseline_tecnologia.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb |
| `ScenarioSession.evaluate` (743) | `'get', f'/planning/scenarios/{self.scenario_id}/evaluation'` | `self.client._request` | custom_configuration.ipynb, e08_baseline_tecnologia.ipynb, example1_LTE_planning_basic.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.export_result` (776) | `'get', f'/planning/scenarios/{self.scenario_id}/export/result'` | `self.client._request` | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.evaluate_paths` (820) | `'post', f'/planning/scenarios/{self.scenario_id}/paths/score'` | `load_attached_equipment`, `self.client._request`, `self.client.metric_path`, `self.client.metric_path(metric).read_text`, `self.export_result` | e06_01_agu_projeto_completo.ipynb |
| `ScenarioSession.network_metric` (835) | `'post', '/planning/results/network-metric'` | `load_attached_equipment`, `self.client._request`, `self.client.metric_path`, `self.client.metric_path(metric).read_text` | e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb |
| `ScenarioSession.optimize_parents` (860) | `'post', '/planning/results/optimize-parents'` | `load_attached_equipment`, `network_metric.endswith`, `self.client._request`, `self.client.metric_path`, `self.client.metric_path(network_metric).read_text`, `self.client.metric_path(path_metric).read_text` | e06_02_agu_selecao_de_pais.ipynb |
| `ScenarioSession.save_result` (880) | — | `save_mesh_artifacts`, `self.evaluate_paths`, `self.export_result`, `self.project.save_result` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e07_01_projeto_original.ipynb, e07_02_projeto_redundancia.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.load_result` (906) | — | `self.project.load_result` | custom_configuration.ipynb, e06_01_agu_projeto_completo.ipynb, e06_02_agu_selecao_de_pais.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, example_5_Mesh_network.ipynb, graph_planning.ipynb, custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `ScenarioSession.export_scenario` (913) | `'get', f'/planning/scenarios/{self.scenario_id}/export/scenario'` | `self.client._request` | advanced_api.ipynb, custom_configuration-checkpoint.ipynb |

### topology.py

SDK/library imports: `dependencies`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `installed_edges` (5) | — | `network_dependencies` | — |

### visualization.py

SDK/library imports: `cisei_planning_sdk.evaluation_view`, `cisei_planning_sdk.metrics_view`, `cisei_planning_sdk.network_view`.

Private supporting functions/methods: `_plot_network_matplotlib`.

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `show_metrics` (6) | — | — | example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb |
| `show_evaluation` (18) | — | — | e08_baseline_tecnologia.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb |
| `plot_network` (129) | — | — | custom_configuration.ipynb, example1_LTE_planning_basic.ipynb, example_1_LTE_planning_and_link_inspection.ipynb, example_2_LTE_simulation.ipynb, example_3_LTE_hybrid.ipynb, graph_planning.ipynb |

### workspace.py

| Definition (source line) | Direct SDK requests | File IO / helpers called | Example notebook name hits |
| --- | --- | --- | --- |
| `init_workspace` (8) | — | `target.open` | custom_configuration-checkpoint.ipynb, graph_planning-checkpoint.ipynb, planning_sdk-checkpoint.ipynb |
| `locate_workspace` (53) | — | — | — |
