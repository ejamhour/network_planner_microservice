# Project contracts v1

Start with the requirements for the planner or operation. Then consult the
shared file references for field meanings, defaults, units, and relationships.
The annotated TOML uses a hypothetical hybrid Graph deployment. Use it with
its [companion node CSV](scenario.reference.nodes.csv). Additional sections
are still needed to cover all planners.

| Reference | Purpose |
| --- | --- |
| [Project storage and recovery](project-session.md) | Temporary project directories, `.session.json`, creation and authenticated recovery |
| [Input sync manifest](upload-manifest.md) | Implemented raw-file sync, workspace paths, hashes and update-only behavior |
| [scenario.reference.toml](scenario.reference.toml) | Annotated scenario sections and field meanings |
| [nodes.csv](nodes.csv.md) | Node inventory and profile references |
| [antenna_placements.toml](antenna-placements.toml.md) | Per-interface installation overrides |
| [Antenna library](antenna-library.yaml.md) | Global and workspace-local model definitions and gain patterns |
| [Metric definition](metric.toml.md) | Edge, path, and network metric files and selection |
| [result.json](result.json.md) | Graph, Mesh, and installed topology results and reuse |
| [features.json and measurements](features.json.md) | Raw cache identity, measurement association, and enriched observations |
| [baseline.json](baseline.json.md) | RSSI training recipe and generated artifacts |
| [Mesh Network](mesh-network.md) | Existing Mesh Network configuration and SDK workflow |
| [poles.csv](poles.csv.md) | Candidate infrastructure for Mesh Link and Mesh Network |
| [attached_equipment.csv](attached-equipment.csv.md) | Equipment served by installed devices |
| [extension.toml](extension.toml.md) | Recipe for deriving a project from an existing scenario |
| [replanning.toml](replanning.toml.md) | Recipe and prerequisites for installed link replanning |
| [Saved network selections](reconfiguration.json.md) | Confirmed reconfiguration and redundancy review artifacts |

For each new planner or operation, document its purpose, required and
conditional inputs, outputs, and SDK and HTTP call sequences. Link to shared
file references and to a runnable project under `examples/projects/`.

The [TOML reference](scenario.reference.toml) includes the proposed top-level
`[files]` table for project uploads. The table maps input roles to
project-relative paths. Each declared file must exist, and the service
determines which roles the selected operation requires. The runnable example
still uses the current `source` declarations until upload support is available.

## SDK generations

The original client remains under [client_sdk](../client_sdk/) with the
cisei_planning_sdk import and existing notebook workflows. The independent
[SDK v2](../client_sdk_v2/README.md), imported as cisei_planning_sdk_v2, implements
server-managed open_project(), recovery and explicit input sync. Validation,
compilation and planning wrappers are still being connected there. The Graph
SDK example below describes the preserved original SDK.

## Graph planner

Graph plans a static deployment. It builds or accepts a candidate graph over
the sites already declared in the scenario and selects links to connect those
sites. Connectivity rules can add candidate edges between existing interfaces.
Graph does not create sites or place repeaters.

Use the [Wi-SUN Graph project](../examples/projects/graph_planner_wisun/)
as a worked example. Its scenario defines equipment profiles and a
connectivity rule. Its node table declares gateways, routers, and leaves.

### Inputs

| File | When to supply it |
| --- | --- |
| [scenario.toml](scenario.reference.toml) | Required for an SDK project. Defines the scenario and either embeds the sites or names a node CSV. |
| [nodes.csv](nodes.csv.md) | Required when the scenario uses `[instances].source` and has no embedded sites. Use the filename declared in TOML. |
| `antenna_placements.toml` | Optional per-interface installation settings. See the antenna placement notes in the TOML reference. |
| `features.json` | Optional reusable RF feature cache from a previous run. The SDK imports it when present. Feature extraction runs for requests without a matching cache entry. |

For explicit links, declare `[[net.candidate_edges]]` in the scenario. The
candidate graph can combine them with edges generated from
`[[net.connectivity]]`. Existing candidates are preserved by default when
building candidates. Whether a link is fixed depends on the scenario and
workflow. Adding a candidate alone offers the link to the solver.

An extension or replacement recipe prepares a derived project before Graph
runs. For example, [the E07 replacement recipe](../examples/projects/e07_ge_mesh_redundancia/extension_fixed_links.toml)
is read by `ExtendedPlanningProject`. Graph consumes the resulting scenario.

### Outputs

| File | What it contains |
| --- | --- |
| [result.json](result.json.md) | Candidate and scored edges, planned nodes and selected edges, and evaluation data when requested. |
| `features.json` | The SDK can persist the worker's reusable feature cache after metric computation. |
| HTML exports | Optional views of planning results generated by the notebook tools. |

### SDK call sequence

Use a workspace containing a `projects/` directory. Place the example
project inside it. Supply the URL of the planning hub and a features URL
reachable **from the planning worker**.

```python
from cisei_planning_sdk import PlanningClient, PlanningProject

client = PlanningClient(
    "http://network-service:8080",
    workspace="/path/to/workspace",
)
project = PlanningProject(client.workspace / "projects" / "graph_planner_wisun")
session = client.open_project(project, planner="graph")
if session.validation_errors:
    raise ValueError(session.validation_errors)

session.build_candidate_edges()
session.compute_metrics(geo_base_url="http://features:8080")
session.solve()
result_path = session.save_result()
```

### HTTP call sequence

When using the hub, register with `POST /register?user_id=<user_id>`.
Prefix the planning paths below with `/<user_id>` and send the returned
token in the `X-Token` header. Keep the same worker and scenario ID throughout
the sequence.

| Step | Method and path | Request and result |
| --- | --- | --- |
| Define | `POST /planning/scenarios/define` | Send `{"planner":"graph","scenario":{...}}` with the expanded scenario, including concrete sites. Use `data.scenario_id` and check `data.validation_errors` before continuing. |
| Build candidates | `POST /planning/scenarios/{scenario_id}/candidate_edges/build` | Send `{"planner":"graph","preserve_existing":true}`. |
| Score candidates | `POST /planning/scenarios/{scenario_id}/metrics/compute` | Send `{"geo_base_url":"http://features:8080"}`. |
| Solve | `POST /planning/scenarios/{scenario_id}/solution/solve` | Send `{}` to use the default solver controls. |
| Export | `GET /planning/scenarios/{scenario_id}/export/result` | Save the response's `data` object as `result.json`. |

The HTTP define call accepts structured scenario data. The SDK reads
`scenario.toml`, expands CSV-backed sites, and applies local installation
settings when opening a project. A direct HTTP client can use the SDK's
`load_scenario_bundle()` helper to prepare that same request body. Check
the API response status at each step as well as the HTTP status.
