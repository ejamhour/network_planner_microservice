# Candidate pole inventory

For Mesh Link or Mesh Network planning, reference the inventory from
`scenario.toml` with `[mesh.poles]` and `source = "poles.csv"`. The path is
relative to the project and must stay within it. SDK calls that accept
`poles=` can receive candidate records directly.

| Column | Requirement |
| --- | --- |
| `pole_id` | Required. Unique within the inventory and distinct from existing site IDs. |
| `device_profile` | Required. References a device profile in the scenario. Use `[mesh.poles].profile_column` to name a different column. |
| `lat`, `lon` | Geographic coordinates in WGS 84 degrees. Supply this pair or projected coordinates. |
| `x`, `y` | Projected coordinates. Supply `[scenario].working_crs` when using projected coordinates alone. |
| `mount_height_m` | Optional row override in metres. The device profile can supply a default. Mesh link search needs an effective height for each radio it uses. |

The SDK assigns candidate status and initial connectivity. Keep
`site_id`, `connected`, `candidate`, and `rank` out of this CSV. The planner
uses the profile to determine which radio could be installed on each pole.
Mesh Link considers compatibility with the endpoint technology, frequency,
and routing capabilities. A direct link study can run with an empty inventory.

The [Mesh Link example](../examples/projects/exemple_4_Mesh_basic/poles.csv)
uses this format. Installed link replanning reads its inventory through
[replanning.toml](replanning.toml.md) and requires both coordinate pairs.

## Coordinate construction feedback

Supply at least one complete coordinate pair. With only projected x/y, declare
scenario.working_crs. During compilation the service calculates the missing
pair. Embedded scenario sites follow the same resolution as CSV sites.

If both pairs are supplied, both are preserved. Separation above the configured
`[validation].coordinate_warning_tolerance_m` (default 1 metre) produces a
`coordinate_mismatch` warning in the build report and does not block compilation.
The report includes the site/pole ID, CRS, measured separation and original
values. Correct the source files and rebuild if the difference indicates an
input error. A position that cannot be constructed remains a blocking error.
