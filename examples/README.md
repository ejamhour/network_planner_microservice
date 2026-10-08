# Planning workspace and projects

Start the numbered series with
`notebooks/example_1_LTE_planning_and_link_inspection.ipynb` (SDK 0.1.20+).
Its project is `projects/example_1_LTE_planning_and_link_inspection`.
It includes direct interface inspection and candidate/solution link browsing.
The previous LTE notebook and project remain available for existing workspaces.

The workspace contains reusable notebooks and independent planning projects.
The service publishes this source collection at `/examples/manifest.json`;
SDK 0.1.18+ downloads its contents directly into the workspace.

```text
cisei_workspace/
├── notebooks/
│   └── example1_LTE_planning_basic.ipynb
├── projects/
│   └── example1_LTE_planning_basic/
│       ├── scenario.toml
│       ├── nodes.csv
│       ├── features.json   # created after metric computation
│       └── result.json     # created when you save a result
├── antennas/               # shared antenna-model library files
├── metrics/                # shared metric specifications (one metric per file)
├── data/
├── prepare_workspace.py
└── run_examples.py
```

Each project is a directory, not a notebook. `init_workspace` creates the
standard workspace directories. `PlanningProject` creates a selected project
directory when needed and reuses its contents when it already exists. A client
connects to the workspace, while `open_project` selects the active project.
The same client can open another project later. Copy a project directory to
make an independent planning case.
No project manifest or run directories are required.

## Preparation

Download the script first, from the directory that will contain the workspace:

```sh
curl -fSLo prepare_workspace.py http://network-service:8080/examples/prepare_workspace.py
python prepare_workspace.py --workspace ./cisei_workspace
```

The script installs network SDK 0.1.19+ and features SDK 0.1.1 (compatible 0.1.x)
with notebook extras using the Python that
launches it. Restart an existing notebook kernel after upgrading. Override the
service address with `--base-url`. From inside a workspace, pass `--workspace .`
to avoid creating another workspace. Existing local files are preserved.

To receive updated example notebooks in an existing workspace, add
`--refresh-notebooks`. Previous notebook copies are backed up under
`notebook_backups`; projects and results are preserved. Close notebooks before
refreshing and reopen them afterward.

Features defaults to `http://features:8080`; override it with
`--features-base-url`. Each SDK wheel is downloaded exclusively from its own
service and both local wheels are installed together. Third-party dependencies
use the normal pip index. Both services must be reachable during preparation;
installation does not create a features runtime session or download its examples.

New initialization creates project folders rather than separate scenario/result
folders. Older downloaded workspaces are not deleted or reorganized by the SDK.
Back up edited notebooks and replace their downloaded copies to receive updates;
keep existing scenario files and results when adopting projects.

## Project workflow

### Inspect planning links (SDK 0.1.20+)

Create a features object explicitly; its optional user label and session are
independent of the network session. Select interfaces, not site/device IDs:

```python
from cisei_features_sdk.notebook import FeaturesNotebook

geo = FeaturesNotebook("http://features:8080", user=None)
scenario.link(geo, src_interface, dst_interface)
geo.link_profile()
geo.link_area("COVER")

browser = scenario.browse_links(geo, candidates["candidate_edges"])
# Alternatively: scenario.browse_links(geo, solution["planned_edges"])
browser.next()  # prepares the first link; no automatic visualization
geo.link_profile()
browser.current  # selected edge dictionary
# browser.select(2), browser.next(), browser.previous()
geo.close_worker()  # caller controls session cleanup
```

Each selection uses current server configuration and rejects mismatched technology
or frequency. Internal edges cannot be inspected geographically. List indices are
zero-based; next/previous raise StopIteration at the boundaries. A failed request
does not advance the browser. Inspection does not modify saved metrics or solutions.

```python
from cisei_planning_sdk import PlanningClient, PlanningProject

client = PlanningClient(
    "http://network-service:8080",
    workspace="..",
)
project = PlanningProject("../projects/example1_LTE_planning_basic")
scenario = client.open_project(project, planner="cell")
```

Opening a project reads `scenario.toml`, loads its referenced node CSV and imports
`features.json` if present. Malformed or unsupported caches raise an error rather
than being silently ignored. Each open creates an independent server scenario id
unless a name is explicitly supplied.

After building candidates, `scenario.compute_metrics()` reuses matching raw
features and fetches missing ones. Successful computation atomically saves the
updated cache to the project's `features.json`, independently of result export
or the `include_features` response setting. After a failed computation, any
completed entries remain in server memory and can be explicitly persisted with
`scenario.save_feature_cache()` before retrying or closing the session.

Save and reload the result with `scenario.save_result(...)` and
`scenario.load_result()`. Both default to the active project's `result.json`.
Candidate and final plots read that result locally.
The project name identifies the planning case; files inside it have conventional
names. To test alternatives in the same project, supply `scenario_file`,
`features_file` or `result_file` to `PlanningProject`. Alternatives can share
`features.json` while keeping distinct scenario/result filenames. Use one active
writer per local cache file; concurrent file writes are not merged.

## Feature cache behavior

The portable cache is JSON with format `cisei-feature-cache`, version 1, and an
`entries` list. Each entry stores the source geo URL, the extraction request and
raw features. It contains no computed edge metric or antenna gain.

Cache matching uses the existing pool key: ordered endpoint coordinates, both
mounting heights, frequency and extraction options. Source service identity is
also checked. Antenna pattern/gain or metric formula changes can reuse these
features. Import does not start geo workers; a fully cached computation can run
without reaching the geo service. Misses start the pool as needed.

Caches belong to individual server scenarios and their local project files.
Configuration changes preserve their raw features. Closing/restarting a worker
loses in-memory entries, but reopening a project imports its saved file again.
The network service does not write files into a user's project: SDK file I/O
performs that persistence.

Geographic dataset revisions are not automatically detected. If the same geo
URL starts using changed data or extraction algorithms, discard the old cache
and start a fresh planning worker to clear the pool's own in-memory cache too.
Files are versioned for serialization compatibility, not dataset provenance.

FastAPI provides scenario-scoped GET/POST
`/planning/scenarios/{scenario_id}/features/cache` for export/import. Imports
replace the scenario's raw cache after validating the entire document. Flat
SDK calls remain supported for callers who supply explicit scenario/result paths;
they can import/export a cache manually without adopting project conventions.

## Demonstrations

| Notebook | Project / purpose | Geo service |
| --- | --- | --- |
| `example1_LTE_planning_basic.ipynb` | Basic cell planning, live reports, project cache, local saved-result graphs | Cache misses only |
| `graph_planning.ipynb` | Single-radio Wi-SUN mesh with gateways, routers and leaves | Cache misses only |
| `custom_configuration.ipynb` | Upload a shared custom antenna model and metric | Cache misses only |
| `advanced_api.ipynb` | Retrieve, modify and save a scenario with embedded sites | Not used |

Open notebooks with the kernel working in `notebooks/`. The default project
paths are relative to that folder. Example 1 is the first simplified end-user
notebook; the others will be simplified and numbered as reviewed. Custom antenna
and metric files are shared at the workspace level. Projects refer to those
libraries when their configuration is uploaded to an in-memory scenario.

From a workspace root, run `python run_examples.py --notebook all --workspace .`
for sequential execution. Use `--base-url` when the planning service is not
available at its container-network address, and `--geo-url` when the features
service uses another address. The runner waits for every code cell and does not
write cell outputs into the notebook files.
