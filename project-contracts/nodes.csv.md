# Node CSV reference

Use a node CSV when `scenario.toml` declares `[instances].source`. The
filename is relative to the TOML file. Each row describes one site and one
device. The device profile supplies the list of interfaces.

For two independent devices at the same physical location, use two rows with
distinct `site_id` values. The rows can share coordinates. For one device
with multiple interfaces, use one row and list the interfaces in its device
profile.

```csv
site_id,device_profile,lat,lon,mount_height_m
b2b_lte,lte_client,-25.43,-49.32,8
b2b_wisun,wisun_remote,-25.43,-49.32,12
```

These illustrative rows use profiles from the annotated TOML. If the devices
are wired back-to-back, declare the cable as an explicit internal edge between
their interface IDs. See the [B2B example in the TOML reference](scenario.reference.toml).

| Column | Requirement and meaning |
| --- | --- |
| `device_profile` | Required by default. Must match a key in `[devices]`. Set `[instances].profile_column` to use another header. |
| `site_id` | Recommended stable ID. The SDK also accepts `position_id`, `id`, or `name`, in that order after `site_id`. When all are blank it generates `site_<row number>`, counting data rows from 1. IDs must be unique. |
| `lat`, `lon` | Geographic coordinate pair in WGS 84 degrees. |
| `x`, `y` | Projected coordinate pair. Supply `[scenario].working_crs` when using this pair alone. Give each site at least one complete pair. |
| `kind` | Optional site category. Falls back to the device profile's category, then `field`. |
| `connected` | Optional initial connectivity. Use `true` for an existing access and `false` for a device that needs connectivity. If the column is omitted, the profile value is used when present. |
| `can_route` | Optional device routing capability. If the column is omitted, the profile value is used when present. Interface relay capability is defined separately in TOML. |
| `rank` | Optional initial rank. A blank rank defaults to zero for a connected device and is unset for a floating device. A finite value sets the initial rank explicitly. |
| `mount_height_m` | Optional mounting height in metres. Aliases are `ant_h` and `antenna_height_m`. Falls back to the device profile's height when present. |
| `candidate` | Optional site flag, false by default. Mesh uses it in specific workflows. Ordinary Graph sites can omit it. |
| Other columns | Nonblank values are carried as site attributes by the SDK. |

For a site named `gateway`, the SDK creates device `gateway:d0` and
interfaces `gateway:d0:i0`, `gateway:d0:i1`, and so on in profile order.
Explicit candidate edges and per-interface overrides refer to the interface
ID. Attached equipment refers to the device ID.

```csv
site_id,device_profile,lat,lon,connected,can_route
gateway,wisun_gateway,-25.43,-49.34,true,true
router,wisun_router,-25.43,-49.33,false,true
```

This excerpt assumes those two profiles are defined in the TOML. For a complete
table, see the [Wi-SUN example](../examples/projects/graph_planner_wisun/nodes.csv).

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
