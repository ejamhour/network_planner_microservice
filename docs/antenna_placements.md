# Antenna placements per interface

SDK 0.1.63 supports optional installation data alongside the normal device and
antenna profiles. Each placement addresses one **interface ID**, allowing two
radios on one device to have different antennas, heights and orientations.

## Project files

Place `antenna_placements.toml` beside `scenario.toml`. The SDK discovers that
filename automatically. Alternatively, select another project-local file:

```toml
# In scenario.toml
[antenna_placements]
source = "installations/antennas.toml"
```

```toml
# In antenna_placements.toml, or the referenced file
[placements."AGU-S-GE-003:d0:i0"]
antenna_id = "ge_radio_2"
mount_height_m = 9.0
azimuth_deg = 125.0
downtilt_deg = 2.0

[placements."AGU-S-GE-015:d0:i0"]
mount_height_m = 8.0
```

`antenna_id` references an entry in the scenario's antenna catalog, including
any library-backed pattern/model already configured there. It does not create
a new antenna model. A table can contain any subset of placement fields.

The first entry replaces the selected antenna and overrides its orientation
and mounting height for that interface only. The second keeps its original
antenna and changes only its installation height. Other interfaces, including
other interfaces of the same device, retain their existing values.

## Fields and fallback

- `antenna_id`: optional catalog reference; omission retains the interface's
  existing antenna. Antenna characteristics then default to this selected model.
- `mount_height_m`: optional nonnegative per-interface mounting height. Omission
  retains existing interface/device/CSV behavior; other radios are unaffected.
- `azimuth_deg`: boresight in degrees, clockwise from north. Zero is a real,
  explicit north-facing direction, not a marker for unknown orientation.
- `downtilt_deg`: installation tilt in degrees, positive downward. `tilt_deg`
  is an accepted alias; specifying both is rejected.
- `beamwidth_deg`: optional sector width, greater than zero and at most 360.
- `shadow_azimuth_deg`, `shadow_width_deg`, `shadow_loss_db`: installation
  modifiers supported by the existing antenna evaluator. Width is in (0, 360];
  loss is nonnegative dB.

Every omitted field retains its current default. With no reference and no
conventional file, loading follows the previous path unchanged. An empty
`[placements]` table is also valid. A declared file that is missing, a file
outside the project, an unknown interface or antenna, unsupported fields,
nonfinite values and malformed entries raise errors. There is no silent
fallback for invalid data. Disabled interfaces remain eligible for installation
metadata and remain disabled.

For a fixed-gain antenna without a library pattern, gain evaluation remains
constant: setting azimuth alone does not invent a directional radiation
pattern. Existing sector filters continue to use the resolved antenna's
azimuth and beamwidth. Missing orientation is not populated by this extension;
existing model behavior remains unchanged. Record measured installation data
when it becomes available, particularly for AGU's legacy YAGI antennas.

## Resolution and persistence

The SDK applies placements after CSV/profile instantiation and enabled-state
overrides. It sends the resolved canonical scenario to the existing service
API. A shared antenna profile is never edited: orientation overrides produce
isolated effective catalog entries associated with their interface. Their
metadata records the interface and base antenna ID. These entries and
per-interface heights survive canonical scenario export and reload without
the external file. Construction snapshots, expanded scenarios and final
solutions therefore remain portable.

Derived CSV-based projects copy the source placement file into their own
folder and track its hash. If a replacement removes a referenced interface,
preparation fails until the installation mapping is reviewed. This feature
does not choose antennas, orient them automatically or replace equipment.

AGU's new project includes an empty placement table: its initial RF inputs
remain unchanged. Installation data and automatic selection will be added
separately when available.
