# Antenna placements per interface

Installation data accompanies device and antenna profiles. Each placement addresses one **interface ID**, allowing two
radios on one device to have different antennas, heights and orientations.

## Project files

Declare the input file explicitly in scenario.toml:

```toml
[files]
antenna_placements = "antenna_placements.toml"
```

Legacy adjacent-file discovery remains supported by the builder/old SDK. The
earlier source declaration is also supported; do not declare both forms:

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
a new antenna model. See the [antenna library reference](antenna-library.yaml.md)
for model definitions. A table can contain any subset of placement fields.

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

During compilation, omitted orientation/shadow fields use the selected antenna
profile, and omitted height retains the interface/device value. Loading a saved
`compiled_scenario.json` restores its saved values and does not read or reapply
the placement file. After changing placement inputs, run `build_scenario()`
again and load the newly compiled scenario. Each build starts from the source
files, not the previous compiled artifact.

The existing SDK can also reimport an expanded TOML beside a placement file.
That compatibility path is distinct from loading compiled_scenario.json and
remains for later review.

With no reference and no
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

The original SDK loader and server scenario builder apply placements after
CSV/profile instantiation and enabled-state overrides. SDK v2 uploads raw files;
the server compiles them into compiled_scenario.json. A shared antenna profile is never edited: orientation overrides produce
isolated effective catalog entries associated with their interface. Their
metadata records the interface and base antenna ID. These entries and
per-interface heights survive canonical scenario export and reload without
the external file. Construction snapshots, expanded scenarios and final
solutions therefore remain portable.

Derived CSV-based projects copy the source placement file into their own
folder and track its hash. If a replacement removes a referenced interface,
preparation fails until the installation mapping is reviewed. This feature
does not run a tuning search or replace equipment merely by applying a file.

AGU's new project includes an empty placement table: its initial RF inputs
remain unchanged. Installation data and automatic selection will be added
separately when available.

## Profile-controlled auto-tuning

auto_tune is an optional antenna-profile boolean in scenario.toml, default false.
It belongs neither to the radiation-model library nor to placement entries.
Two profiles may select the same model with different policies:

```toml
[antennas.sector_fixed]
model_id = "commscope_rv_65s_fvb"
kind = "sector"
azimuth_deg = 90.0
beamwidth_deg = 65.0
auto_tune = false

[antennas.sector_auto]
model_id = "commscope_rv_65s_fvb"
kind = "sector"
azimuth_deg = 90.0
beamwidth_deg = 65.0
auto_tune = true
```

The device/interface selects antenna_id normally. If a placement supplies
antenna_id, that profile controls tuning. Placement fields override defaults
field by field and become the starting configuration. Explicit values do not
implicitly lock a tunable profile. Fixed profiles remain fixed during tuning.
The software obeys this policy without asking for repeated confirmation.

Current Cell tuning searches one common azimuth delta for eligible interfaces
per site; fixed interfaces contribute to coverage but do not rotate. It remains
a geometric coverage/load-balancing heuristic, not RF optimization. The flag
does not invent automatic tilt/height/beamwidth/shadow tuning or trigger tuning
during compilation. Current support is Cell azimuth tuning only. Explicit
prune_empty is separate equipment removal, not enabled by auto_tune; its default
remains false. Users can still edit fixed placements explicitly through inputs.

SDK v2 project.cell.tune_sectors() follows Cell candidate generation and applies
permitted changes by default. apply_rotation=false requests a preview. Preview
suggestions appear in the tuning report only; they do not change exported
placements. Applied settings become isolated per-interface profiles used by
both candidate filtering and directional RF gain. Shared models/profiles and
the original compiled/input files remain unchanged.

## Generated placement output

Output uses the same [placements."<interface_id>"] syntax as input. It is a
complete effective snapshot of every antenna-bearing interface, including
unchanged/disabled interfaces, with orientation/shadow/beamwidth and mounting
height where known. Unknown values are omitted; explicit zero is preserved.
antenna_id references the original selected scenario profile, not a generated
__placement_* ID. Model/default/policy definitions remain in the scenario.

The library exposes effective_placements(scenario) and
save_placements(scenario, output_path) in cisei_lib.project_io.placements.
The latter atomically writes TOML to an explicit destination. Pass a planner's
graph.scenario for its effective configuration. Export contains applied settings,
not every search candidate or unapplied suggestion.

POST /{user_id}/projects/{project_id}/antenna_placements/export requires session
and project tokens and current active inputs. It writes the separate server file
outputs/antenna_placements.toml and returns valid, stage=placement_export, issues,
input identity, file and TOML content. A synchronized input at that output path
is protected: the operation returns placement_output_is_input instead of
overwriting it. Storage failures remain HTTP errors.

SDK v2 project.export_placements() requests server output.
project.save_placements(path=None) also saves it locally, defaulting to the same
outputs/antenna_placements.toml path, and adds local_path to the report. This is
output IO on Project; Cell tuning remains on project.cell. Input placement files
and scenario.toml are never implicitly rewritten or promoted.

For another run, explicitly copy/promote the output to an input path, reference
it in [files].antenna_placements, sync and recompile. The receiving scenario must
provide the same selected profiles and interface IDs. For Mesh-created equipment,
carry the generated node/device definitions too: placement export does not add
missing nodes. Output is not auto-discovered from the outputs directory. Profiles
that retain auto_tune=true may be adjusted again in later tuning; select a fixed
profile when the installation must remain fixed. There is no hidden lock.
