# Workspace and planning SDK

## Workspace layout

The standard workspace is:

```text
cisei_workspace/
├── antennas/                 shared additional antenna libraries
├── metrics/                  shared custom metric specifications
├── data/                     user-owned raw input data
├── notebooks/                reusable examples or user notebooks
└── projects/
    └── project_name/
        ├── scenario.toml     project configuration
        ├── nodes.csv         optional external site instances
        ├── features.json     portable raw-feature cache
        └── result.json       exported planning result
```

`init_workspace` creates the five top-level directories. With a service URL it
downloads the example manifest directly into the workspace root. Existing files
are preserved, so initialization is not an example-upgrade mechanism.

## Project rules

`PlanningProject(path)` creates the project directory if it is absent. Its
default filenames are `scenario.toml`, `features.json`, and `result.json`.
Alternative filenames can be supplied, but all must remain inside the project
directory and must be distinct.

`PlanningClient` must be associated with an existing workspace containing a
`projects` directory. A project is not a constructor argument. The client starts
with no active project, and `open_project` makes one project active. The same
client can open another project later.

`open_project` enforces that the project is inside `<workspace>/projects`,
requires `scenario.toml`, defines the scenario on the server, and imports
`features.json` if it exists.

## Scenario lifecycle

The normal staged lifecycle is:

1. `client.open_project(project, planner=...)`
2. `session.build_candidate_edges(...)`
3. `session.compute_metrics(...)`
4. `session.solve()`
5. `session.evaluate(...)`
6. `session.save_result(...)`

`compute_metrics` saves the portable feature cache to the active project after
a successful server computation. `save_result` exports the live server result
and writes the project's `result.json`. Loading `result.json` is local and does
not reconstruct the live server scenario.

An exported saved result can be evaluated later through
`PlanningClient.evaluate_result`; the server evaluates the serialized artifact
without restoring the original worker state.

## Shared configuration libraries

`antennas/` and `metrics/` are workspace-level libraries. They are not copied
automatically into a project.

`ScenarioSession.upload_configuration` reads local library files through the
SDK and uploads their contents into the active in-memory scenario:

- an antenna library is YAML or JSON with an `antennas` list;
- antenna-profile overrides map scenario antenna profile IDs to settings;
- metric overrides map technology IDs to TOML files or inline declarations.

The service merges these values into the scenario only. It does not modify its
built-in resource files or persist the upload globally. A successful update
invalidates candidate edges, metrics, solution, evaluation, and result state;
the planning stages must be run again.

## Feature cache

`features.json` uses format `cisei-feature-cache`, version 1. Each entry records:

- source features-service URL;
- ordered transmitter and receiver coordinates;
- transmitter and receiver mounting heights;
- frequency;
- extraction options;
- returned raw features.

The cache does not contain an antenna gain calculation, metric formula, or RPL
solution. A change to antenna gain or metric formula can reuse raw features when
the cache key remains valid. Coordinate, height, frequency, options, or source
changes produce a different key.

## Revision policy for the assistant SDK

**Decision:** each accepted revision becomes a new project directory. The
assistant SDK copies or regenerates the required scenario and nodes artifacts,
then allows the normal planning SDK to create the new cache and result. It must
not overwrite the last accepted project during AI correction.

The naming and metadata convention for linking revisions is still a design gap.

