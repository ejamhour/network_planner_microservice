# Project upload manifest v1

This proposed contract describes the files supplied for a project request.
The same manifest can be used to test with a staged server directory or to
send the files over HTTP. In a staged directory, save it as `manifest.json`
at the root. For HTTP, send the manifest with the original file bytes.
The manifest itself is separate from the files listed in `uploaded_files`.

## Example

```json
{
  "version": 1,
  "action": "open",
  "scenario_file": "scenario.toml",
  "uploaded_files": [
    {"path": "scenario.toml", "role": "input"},
    {"path": "nodes.csv", "role": "input"},
    {"path": "installations/antennas.toml", "role": "input"},
    {"path": "features.json", "role": "feature_cache"}
  ]
}
```

In this example, `scenario.toml` declares `nodes.csv` and
`installations/antennas.toml` in its `[files]` table. The feature cache is
declared only in the manifest.

## Manifest fields

| Field | Requirement |
| --- | --- |
| `version` | Required integer. Use `1` for this format. |
| `action` | Required. Use `validate` to check the files, `open` to create a project copy, or `sync` to replace the inputs of an open copy. |
| `scenario_file` | Required project-relative path to the scenario TOML. Its path must be listed once in `uploaded_files` with role `input`. |
| `uploaded_files` | Required complete inventory of the files supplied with the request. Each entry contains a unique `path` and a `role`. File order has no meaning. |
| `project_id`, `base_revision` | Required for `sync` only. Use the project ID and revision returned by the service. Sync supplies a complete replacement input set and the service checks the base revision before accepting it. |

Paths are relative to the staged project root and use `/` between
components. The service rejects absolute paths, parent-directory traversal,
and paths that resolve outside the project. For an HTTP request, the file
parts must match the inventory. For a staged-folder test, every listed file
must exist and the loader consumes only listed files.

## File roles

| Role | When to use it |
| --- | --- |
| `input` | The scenario TOML and its declared companion inputs. `[files]` describes each companion's purpose, such as nodes, poles, antenna placements, or attached equipment. Each declared companion must be supplied. |
| `feature_cache` | Optional reusable RF features. The service checks the cache separately from the scenario inputs and can compute features if the cache cannot be used. |

Keep ordinary outputs such as `result.json` and HTML reports in the user
workspace. An operation that explicitly consumes a saved result or
checkpoint can define a role for that artifact when resume or derived
projects are added to this contract.

## Validation and project state

The manifest records the requested action and what the caller supplied.
The service determines the project state. A `validate` request checks the
files without creating a planning session. An `open` request creates a
project copy after the inputs pass validation. A failed `sync` leaves the
last valid revision active. The planner and its operation arguments are
selected separately when the caller runs the project.
