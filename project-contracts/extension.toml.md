# Project extension recipe

`ExtendedPlanningProject.prepare()` reads a source `scenario.toml` and its
node CSV, then writes a derived project. The source CSV must have `site_id`
and the configured device-profile column. The recipe can add or update
scenario settings. Tables merge by key and lists replace the earlier list.
The recipe cannot supply `[instances]` or embedded sites.

Use ordered `[[replacement.rules]]`. For each original row, the first rule
whose predicates all match applies once.

| Field | Meaning |
| --- | --- |
| `profile`, `replace_with` | Required source and destination device profiles. Both must exist after merging the scenario and recipe. |
| `name` | Optional descriptive label. |
| `tags` | Optional exact matches on existing CSV columns. |
| `rank_lte` | Optional inclusive upper bound for the solved source rank. Here `lte` means less than or equal to. |
| `connected` | Optional boolean override. Otherwise use the replacement profile's default. |
| `rank` | Optional finite initial rank or `"copy"` to reuse the solved source rank. Floating nodes must start without a finite rank. |

Rank matching and copying require a solved source result and an unambiguous
rank for the affected device. `[topology]` with `mode = "installed"` also
requires the solved source result. It carries installed topology forward
into the derived scenario. `extension_fixed_links.toml` is an example of
this same recipe format.

The SDK writes the derived scenario, node CSV, and `replacement_report.json`.
It can carry over compatible feature records and copies the placement file
when used. Run planning on the derived project to produce its solution.

See the [hybrid recipe](../examples/projects/example_3_LTE_hybrid/extension.toml)
and the [installed topology recipe](../examples/projects/e07_ge_mesh_redundancia/extension_fixed_links.toml).
