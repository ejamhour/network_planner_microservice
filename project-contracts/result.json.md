# Planning result reference

`result.json` contains the `data` object from the result export endpoint.
It can be loaded by a notebook viewer or read directly by another tool.

| Field | Meaning |
| --- | --- |
| `working_crs` | The coordinate reference system for projected `x` and `y`, when present. |
| `counts` | Counts of sites, routing nodes, candidate edges, metric edges, and planned nodes and edges. |
| `rpl_nodes` | Interfaces considered by the planner. `node_id` identifies the interface. `site_id` and `device_id` identify its containing site and device. |
| `interface_inventory` | Interface records with an `enabled` flag, including disabled interfaces. Older saved results may lack this inventory. |
| `antennas` | Antenna profiles keyed by antenna ID. Older saved results may omit them. |
| `candidate_edges` | Candidate links offered to the planning stages. `src` and `dst` refer to interface IDs. `distance_m`, where present, is distance in metres. |
| `metric_edges` | Scored edges with a `metric` value. Its interpretation depends on the metric configured for the technology. |
| `planned_nodes` | Routing nodes in the exported solution. `parent` identifies the next interface toward an access when assigned. `rank` describes the node's accumulated path rank. `connected` reflects the device's initial connectivity. |
| `planned_edges` | Links selected in the solution, with `src`, `dst`, and the edge `metric`. |
| `edge_features` | Optional raw feature records included when requested at export. |
| `evaluation` | Optional evaluation included when a rank threshold is supplied at export. |

Join by identifier when building a viewer. Several interfaces can share a
device or site. Use `interface_inventory` or `rpl_nodes` together with the
planned topology when showing unserved sites.

See the [saved Wi-SUN result](../examples/projects/graph_planner_wisun/result.json)
for a populated example.

## Mesh Link proposal

`mesh_search` identifies the source and destination interfaces, selected
`node_path`, proposed `repeaters`, `path_cost`, and whether every segment
meets the threshold. `mesh_candidates` contains the explored graph for
inspection. Use `planned_nodes` and `planned_edges` for the proposed path.
A proposal can report that its segments failed the threshold.

## Mesh Network and resume

`mesh_network` describes the expansion phase, fixed and required
interfaces, repeater selection and use, and the study history. For a solved
network, `unserved_interfaces` identifies required interfaces missing from
the solution. During a paused expansion, the report contains status,
fingerprints, pending interfaces, and the latest iteration needed to
continue the study.

The SDK may save older iterations and detailed studies under
`mesh_iterations/`, leaving relative `artifact` references in the main
result. Keep the project result and referenced files together for offline
inspection. When present, `mesh_expanded/` and `mesh_solution/` contain
derived scenario and result snapshots. The scenario embeds the sites. A
companion node CSV in a snapshot serves as an inventory.

## Installed topology and derived projects

An installed project writes `result.json` from the declared topology.
Its planned nodes and edges are read by path diagnosis, reconfiguration,
and installed link replanning. The installed scenario remains a companion
input. Attached equipment and diagnosis data may be added for a view, so
read the corresponding artifacts when a workflow needs them.

An extension that selects devices by solved rank or preserves installed
topology also reads the source result. Keep that result with the source
scenario and node inventory. See the [extension recipe](extension.toml.md)
and [replanning recipe](replanning.toml.md).
