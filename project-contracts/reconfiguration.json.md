# Saved network selections

## Confirmed reconfiguration

The installed-network review writes `reconfiguration.json`. Generate it
by reviewing the installed result and confirming the selected removals.
The SDK checks the saved decisions against the current source topology when
it loads the file.

| Field | Meaning in the saved version 2 document |
| --- | --- |
| `format` | `cisei-network-reconfiguration` |
| `version` | `2`. The loader also supports confirmed version 1 documents. |
| `confirmed` | Must be true for a saved selection. |
| `source` | Source project identifiers and fingerprints used to detect changes. |
| `requests` | Selected installed links and removal modes. |
| `remove_links`, `remove_devices` | Removals calculated from the reviewed requests. |

`LinkReplanningProject` consumes confirmed link-only selections. If the
source changes or the saved removals disagree with the recalculated impact,
review and confirm the selection again.

## Redundancy selection

`redundancy_selection.json` records a selected site and interface,
the active sites, and the source result's SHA-256. It is produced by
`PlanningProject.save_redundancy_selection()` and accompanies the E07
dependency review. See the [saved E07 selection](../examples/projects/e07_ge_mesh_original/redundancy_selection.json).
