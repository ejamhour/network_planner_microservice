# Input sync manifest v1

Implemented by `POST /projects/{project_id}/sync`. This replaces the earlier
unimplemented proposal that combined open/validate/sync and replacement/deletion.
Creation, validation, compilation and planning are separate operations.

## Wire format

Send `Content-Type: application/zip` and the recovery credential in
`X-Project-Token`. The request body is a ZIP_STORED archive of original file
bytes plus exactly one `.sync-manifest.json` member. ZIP64 is supported. The
archive and extracted staging files are processed on disk, without base64 or
loading input contents into JSON. Compressed members, directories and symlink
members are rejected; parent directories are created from the member paths.

```json
{
  "version": 1,
  "project_path": "projects/example",
  "scenario_file": "projects/example/scenario.toml",
  "files": [
    {
      "path": "projects/example/scenario.toml",
      "size": 1234,
      "sha256": "<64 lowercase hexadecimal characters>"
    },
    {
      "path": "metrics/custom.toml",
      "size": 567,
      "sha256": "<64 lowercase hexadecimal characters>"
    }
  ]
}
```

The example sizes/hashes are placeholders, not sample file contents.

| Field | Requirement |
| --- | --- |
| version | Required protocol version 1 |
| project_path | Must match the directory recorded when this remote project was created |
| scenario_file | Workspace-relative scenario path inside project_path; must be uploaded in this batch |
| files | Exact inventory of archive file members, excluding the manifest itself |
| files[].path | Unique canonical workspace-relative POSIX path; no absolute path, drive, backslash or parent traversal |
| files[].size | Nonnegative integer, equal to the archived file byte count |
| files[].sha256 | SHA-256 digest of original bytes, 64 lowercase hexadecimal characters |

Archive members must match the inventory exactly. Private session/project/Git
metadata cannot be uploaded. Input sync cannot overwrite the active project's
compiled_scenario.json or result.json. A saved result belonging to a different
source project can be selected as an input dependency under that source path.

The server authenticates before consuming the upload, verifies all entries,
paths, sizes and hashes in private staging, then adds/updates workspace files.
There is no input syntax or domain validation during this operation.

## Selection in SDK v2

```python
project = client.open_project(folder, mode="resume")
report = project.sync()
```

The separate cisei_planning_sdk_v2 package implements this workflow.
The preserved legacy SDK is unchanged.

The SDK always includes its local scenario file (scenario.toml by default), and
reads `[files]` only to select companion paths. Entries may be strings, arrays
of paths, or tables of named paths. For example:

```toml
[files]
nodes = "inputs/nodes.csv"
antenna_library = "../../antennas/custom.yaml"

[files.metrics]
lte = "../../metrics/custom.toml"
```

References resolve relative to scenario.toml and must remain inside the local
workspace. The SDK maps them to canonical workspace-relative archive paths;
original TOML bytes and relative references are not rewritten. The service's
global antenna/metric catalogs are unaffected. Legacy scattered source
references are not inferred: complete `[files]` when adopting this sync path.
Nested role names are transfer metadata; support for their contents in the
compiler must be added separately where missing.

Only selected inputs are sent: unlisted results, HTML, cache files and other
workspace contents are not cloned. Session credentials are rejected even when
listed. Local symlink aliases are rejected to avoid copying a target under a
name different from the name referenced by TOML.

Default selection needs a parseable TOML file list. To transfer malformed or
partial TOML for server-side validation, explicitly supply the companion list:

```python
report = project.sync(files=["inputs/nodes.csv"])
```

The scenario bytes are still included, but the SDK does not parse them in this
mode. Neither mode expands instances or interprets antenna/metric/planner data.
A missing local file stops selection before any upload. A project moved to a
different workspace-relative location must be restored to its recorded path or
opened as a new remote project before syncing; relocation is not automatic.

## Update-only behavior and receipt

Unmentioned files remain on the server. Removing a local file or reference does
not delete its server copy. Future compilation must honor explicit references
and avoid implicitly reviving obsolete files. Files with identical bytes are
not rewritten. This first implementation transfers all selected bytes and
compares them server-side; bandwidth delta/preflight selection is not yet added.

The usual `status: OK`, `kind: json`, `data` envelope contains:

| Receipt field | Meaning |
| --- | --- |
| project_id | Authenticated project identity |
| input_revision | Monotonic revision, incremented when registered inputs/content change |
| input_hash | SHA-256 identity of the scenario entry path and sorted registered file hashes/sizes |
| inputs_changed | Whether this request changed input identity or actual file bytes |
| files | Uploaded paths with original sizes, hashes and action: added, updated or unchanged |

Input metadata is retained in private `.sync.sqlite`, outside workspace/. It
tracks previously uploaded files as well as this batch; omitted files are not
removed from that registry. Identical retries preserve the hash and revision,
including after project recovery. File order does not affect the identity.
Changed-input receipts provide the identity for future result invalidation;
existing planning/result APIs are not yet bound to this remote-project state.

SQLite serializes sync commits. Detected write/commit failures restore replaced
files and roll back metadata; malformed archives and hash failures publish no
files. Hard process termination mid-commit still needs transaction recovery;
this is not a claim of cross-file crash atomicity. Interrupted body transfer
before staging completes does not modify inputs. Retrying a completed upload
is safe if its response was lost.

Sync does not validate, compile, restore a planner, delete files or rerun
planning. It preserves any existing compiled scenario and saved outputs.
Result retrieval remains separate. Explicit close releases active planning and
features resources while preserving project storage. Project deletion and
garbage collection remain separate subsequent work.
