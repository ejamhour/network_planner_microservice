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

## LTE 450 simulation

`notebooks/example_2_LTE_simulation.ipynb` uses the corrected COPEL dataset
(86 clients and 5 cells) in `projects/example_2_LTE_simulation`. It adapts
example 1 to 450 MHz with assumed fixed antenna gains and three sectors per
cell. See the project README for radio assumptions and execution instructions.

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

## Features pool ownership

Planning creates worker IDs using `<user_prefix>-<runtime_id>-pool-<number>-worker-<index>`.
The runtime ID is unique to each planning worker lifetime; pool numbers increase
within that runtime, including when connection parameters change. Concurrent
requests with matching connection parameters reuse the same pool. No notebook
change is required; `geo_user_prefix` remains a label.

`GET /planning/status` includes each pool's runtime ID, pool number, owned worker
IDs, and cleanup errors. Authentication tokens are not returned. Cleanup uses
the credentials retained by each pool, attempts every owned worker, and retains
failed workers for inspection and retry. Successful worker closures are not
retried. Partial startup failures also clean up already registered workers.
This cleanup still waits for active extractions; active cancellation is a
separate planned change.

## Batched metrics (SDK 0.1.25+)

Use `scenario.compute_metrics(batch_size=20)` to compute bounded subsets of the
full candidate graph. Each batch is a separate HTTP request. The SDK atomically
saves the accumulated raw-feature cache before submitting another batch. A
request or save failure stops the loop; earlier saved batches remain available.
An optional `on_batch` callback receives `offset`, `next_offset`, `total_links`,
and `complete` after the save. The API accepts `batch_size` and `batch_offset`
on the metrics computation endpoint. Finish all batches before solving.

Omitting `batch_size` retains the existing single-request workflow. Batching
requires an open project and an updated network service as well as SDK 0.1.25+.
Reopening the project imports saved features; calling compute again starts at
offset zero and reuses those features, recalculating metrics without extracting
cached links again. Recovery of an incomplete batch and active cancellation
are separate changes. Keep one writer and an unchanged candidate graph during
a batch sequence. Example 2 uses batches of 20 with checkpoint progress output.

## Interactive network views (SDK 0.1.26+)

Existing `plot_network(result, kind="candidate")` and `kind="final"` calls now
show an offline HTML/SVG view embedded in Jupyter. Search or click an endpoint
to filter connected links, click a link for saved metric/node details, and zoom
or pan. Colocated sites share a marker listing all IDs. Candidate sector guides
use antenna azimuth/beamwidth from updated server exports; their radius is a
visual aid, not a coverage prediction. Older saved results remain usable, but
cannot show sector orientation if antenna metadata is absent. Solution views
retain unserved sites and expose saved routing, metrics, and evaluation data;
no new feature extraction occurs from map interaction.

```python
view = plot_network(saved_result, kind="final")
view.save_html(project.path / "network.html")
```

Use `display=False` to generate HTML without displaying it. Each notebook view
runs in its own iframe; notebook outputs may need to be trusted to run scripts.
The saved HTML can also be opened directly in a browser. Passing an explicit
Matplotlib `ax` retains the previous static renderer; the default return type is
now a `NetworkView`, rather than Matplotlib Axes. These views use projected x/y
coordinates with north up; they do not load basemap tiles or external libraries.

SDK 0.1.28 highlights solution sites with saved `quality="poor"` in magenta
with an outer ring and a visible label. Unserved sites remain red. A site with
multiple interfaces is flagged if any evaluated interface is poor. Colocated
sites retain an orange marker outline. Use `scenario.save_result(rank_threshold=2)`
(or another study threshold) before loading the saved solution for plotting;
without an embedded evaluation the view reports that quality is unavailable,
rather than guessing from individual link metrics.

## Endpoint selection in link inspection (SDK 0.1.30+)

`scenario.show_link_browser(geo, links)` now starts with links for the first
source site. Type a site ID or choose one from the Endpoint dropdown; incoming
and outgoing interface links are included. Commit a typed ID with Enter or by
leaving the field. Clear the field to show all links. Previous/Next navigation
stays inside the filtered list, with a visible filtered/total count. Changing
an endpoint only filters the list; select a link or press Show to inspect it.
No call-site change is required for candidate or solution browsers.

## Example 3: ordered replacement into a new hybrid project (SDK 0.1.32+)

Run `notebooks/example_3_LTE_hybrid.ipynb` after saving the intended example 2
baseline. It prepares `projects/example_3_LTE_hybrid` from a small extension.toml
recipe. Paths remain in Python. `ExtendedPlanningProject(..., source=source)`
merges tables recursively and replaces lists, applies the first matching rule
once to each original CSV row, copies raw features, and reports replacements.
`rank_lte` means <=, and conditions can also match a profile and exact CSV tags.
Rules with no rank predicate do not require a solved result. Rank rules reject
ambiguous multi-interface device ranks rather than silently aggregating them.

Changed rows inherit the replacement profile's initial connectivity, routing,
kind and rank. Coordinates, heights, IDs and other columns remain intact.
No promoted device is automatically fixed or marked connected. The generated
scenario is independent from its source and can be opened through the ordinary
SDK. Existing generated files are never overwritten by prepare(). The recipe
and resolved snapshot are separate. Source/result provenance is recorded.

This example adds graph connectivity rules for LTE sectors, bounded Wi-SUN
neighbors and same-device forwarding. `sector_aware=true` is supported for RF
graph rules to preserve LTE source-sector filtering. New Wi-SUN features must
be extracted; the inherited LTE cache does not cover a different frequency.
Read the project README for radio assumptions and rank-threshold limitations.

### Explicit replacement connectivity and rank (SDK 0.1.33+)

Rules may add `connected = true/false` and `rank = "copy"` or a finite numeric
rank. Copy uses the source result, not the original CSV. Numeric ranks bias
RPL gateway preference; lower initial rank is more attractive. Omitted actions
use the replacement profile defaults. Connected devices require a finite rank;
unconnected devices must start at infinity (leave rank omitted). Missing or
ambiguous copied ranks fail before output writes. Each replacement report
includes initial_connected, initial_rank, rank_action and the original rank.

Example 3's first rule now explicitly makes good LTE nodes connected gateways
and copies their ranks. The Wi-SUN candidate rule targets unconnected devices,
removing gateway-to-gateway links while preserving multihop secondary links.
Its second rule leaves mesh-only clients unconnected. Existing generated
projects are not rewritten; prepare a new destination using the updated recipe.

SDK 0.1.34 makes derived preparation repeatable: unchanged prepared studies are
reused, while `prepare(overwrite=True)` regenerates changed inputs in place,
retains extracted features, and archives obsolete result/view files. The active
result must then be recalculated; old solutions are available in the backup.

## HTML evaluation reports (SDK 0.1.35+)

`scenario.show_evaluation(rank_threshold=...)` displays one offline HTML browser
instead of dumping DataFrames. It automatically writes `evaluation.html` in the
active project directory. Targets include every technology, independently of
the API service table's primary-tech filter. Separate sections show preset
interfaces and selected links. Search, quality/technology filtering, rank/cost
sorting, page size and record selection operate entirely locally. Internal
forwarding edges are hidden by default. Threshold is report context, not a
repeated field. Summary counts refer to interfaces, not distinct sites.

Preset interfaces are initial conditions, not validated upstream connections.
Their direct-dependent count considers selected cross-site children only; it
is not a transitive traffic or unused-radio classification. Detailed IDs remain
available on selection. No geographic extraction or solve is triggered by the
view. `scenario.evaluation` retains the original API data for programmatic use.
Call `show_evaluation(..., save_path=...)` to choose another report location.
Existing calls and notebooks require no code changes. Trust notebook outputs
if its iframe scripts are blocked, or open evaluation.html in a browser.

## HTML metric browsing (SDK 0.1.36+)

`scenario.show_metrics()` now displays one HTML metric browser and automatically
saves `metrics.html` beside the active project's results. Search sites or
interfaces, filter technology, sort by cost, select records, and browse pages.
Internal forwarding edges are hidden unless requested. Metric-specific costs
are not automatically labeled good/poor: evaluation uses accumulated route rank
and its explicit threshold. These are computed candidates, not only the links
chosen by the solver. Viewing reloads saved metrics via GET but never computes
features or metrics. The original response remains in `scenario.metrics`.
An optional `save_path` chooses another HTML destination. Existing notebook
calls require no edits; trust outputs if notebook security blocks iframe scripts.
