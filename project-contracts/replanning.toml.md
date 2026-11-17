# Installed link replanning recipe

`LinkReplanningProject` uses a separate destination project. It reads the
source installed project's scenario, node CSV, and saved result, plus a
confirmed [reconfiguration selection](reconfiguration.json.md). The
current workflow handles link cuts without radio removals and expects one
device and interface per installed site.

| Setting | Meaning |
| --- | --- |
| `[poles].source` | Project-local pole CSV. Defaults to `poles.csv`. |
| `[poles].corridor_m` | Positive distance from the endpoint segment, in metres. Defaults to 1500. |
| `[poles].device_profile` | Default equipment profile for rows without `device_profile`. Required when such rows are used. |
| `[poles].mount_height_m` | Default mounting height for rows without `mount_height_m`. Defaults to 7 metres. |
| `[search]` | Optional controls forwarded to the mesh link search, such as `max_repeaters`, `max_candidates`, `segment_threshold`, and `search_midpoint`. |

The replanning CSV needs `pole_id`, `lat`, `lon`, `x`, and `y`. The
projected pair must use the source scenario CRS. The corridor filter uses
`x` and `y`. Candidate positions use the geographic pair.

Preparation writes a derived scenario and records the original and prepared
networks. The replanning run studies the affected links, builds the revised
graph, and writes its solution to `result.json`. Keep the preparation and
run artifacts with the project for review.

See the [E13 recipe](../examples/projects/e13_replanejamento/replanning.toml)
and [pole inventory](../examples/projects/e13_replanejamento/poles.csv).
