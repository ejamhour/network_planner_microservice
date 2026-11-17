# Saved compiled scenarios

Start with a staged directory containing `scenario.toml` and its companion
inputs. The builder currently supports node CSVs, antenna placements, and
pole and attached-equipment inventory validation.

```python
from cisei_lib.project_io import validate_inputs, build_scenario, read_compiled_scenario, load_compiled_scenario

report = validate_inputs(project_folder)
path = build_scenario(project_folder)
build_report = read_compiled_scenario(path)["build_report"]
data = read_compiled_scenario(path)
scenario = load_compiled_scenario(path)
```

## Build and reload

The builder parses scenario TOML, expands CSV-backed nodes using registered
profiles, applies per-interface placements, and validates the scenario.
It writes strict JSON to a temporary file and atomically replaces
`compiled_scenario.json` after a successful build. A failure preserves the previous
compiled scenario. Expected input failures raise `ProjectValidationError`, which also
inherits from `ValueError`. Its `issues` contain file, stable code, message,
and location fields where available. `error.to_dict()` produces a
JSON-serializable response with `valid: false`.

`validate_inputs()` is the separate non-instantiating syntax pass. It checks
file declarations, safe paths, file presence, TOML parsing and CSV header/row
shape. Node rows check required profile values, numeric coordinates/heights,
coordinate ranges, boolean flags and ranks (including infinity). Extra attribute
columns remain accepted. Placement checks cover supported fields, numeric
types/ranges, antenna ID string syntax and conflicting tilt aliases. Independent
field errors are collected across rows and placement entries. It returns `valid: true`, `stage: input_validation`, and an empty issues
list on success; failures raise `ProjectValidationError`. Pole rows share scalar checks with nodes and require pole/profile columns and
values; site/connectivity/candidate/rank columns are forbidden. Equipment requires
its three inventory columns and nonempty values, using the same value rules as
the shared equipment reader. Unknown profiles/devices and duplicate identities
remain construction checks. Missing inventory sources and unsupported formats
are syntax issues. Scenario TOML checks catalog/section shapes, required interface/device fields,
known scalar types, embedded site/device/interface tables, candidate-edge
endpoints and interface overrides. Extra metadata and supported numeric strings
are preserved. Profile/interface existence, CRS resolution, metric compilation,
antenna model contents and planner-specific connectivity/mesh semantics are
not fully covered by this syntax pass.

`build_scenario()` runs the same input pass first, then creates instances,
applies placements, resolves references and saves the compiled scenario. It
never relies on an earlier validation result for potentially changed files.
Failure reports identify `input_validation` or `scenario_build` in `stage`.
Independent parse failures are collected before construction; failed syntax
prevents dependent semantic checks. Once a scenario exists, independent pole
and equipment reference failures can be collected together. Each dependent
check may stop at its first failure.

Node errors include physical CSV record end lines and fields where available.
Placement errors identify the interface/field where available. Neither stage
opens a remote project or implements HTTP transfer. A failed check preserves
the previous compiled scenario.

Loading a compiled scenario restores the instances and settings saved in
`compiled_scenario.json`. It does not reread scenario.toml, re-expand node CSVs
or reapply placements. Changes to those source files take effect only after
`build_scenario(project_folder)` succeeds and the new artifact is loaded.
Rebuilding starts from source inputs, not the previous compiled scenario.
Separately referenced inventories such as poles remain separate files; this
rule does not imply that their contents are embedded in compiled_scenario.json.

The reader returns the saved dictionary. The loader constructs
`PlanningScenario` from it. The loader consumes the expanded sites without
reopening the node CSV or placement file. Mesh operations will need the
separate pole inventory referenced in the compiled scenario.

## Dictionary structure

The compiled scenario is an expanded scenario configuration, with these sections when
present:

| Key | Contents |
| --- | --- |
| `scenario` | Working CRS |
| `antenna_library` | Scenario-local antenna model definitions |
| `antennas` | Antenna profiles keyed by profile ID, including effective placement profiles |
| `interfaces` | Reusable interface profiles keyed by profile ID |
| `devices` | Reusable device profiles keyed by profile ID |
| `metrics` | Metric definitions by technology |
| `sites` | List of concrete sites with coordinates, device records, and interface records |
| `net.connectivity` | Rules for generating candidate edges among existing interfaces |
| `net.candidate_edges` | Explicit candidate edges and their attributes |
| `overrides` | Interface overrides retained in the canonical scenario |
| `mesh` | Mesh controls, including a project-relative `poles.source` reference when supplied |
| `attachments` | Project-relative source for the validated attached-equipment CSV |

Node CSV references are removed after expansion. Placements are incorporated
in the concrete interfaces and effective antenna profiles. Pole records stay
in their separate CSV. Feature caches and planning results are separate
artifacts. Nonfinite floating-point values serialize as JSON null.

## Current memory behavior

The complete network is materialized before serialization. Parsed TOML,
the `PlanningScenario` object, and the expanded output dictionary can coexist.
Placement application temporarily exports a dictionary and reconstructs the
scenario. JSON normalization also creates a copy. The final JSON is written
directly to the file without constructing an additional full JSON string.

Pole validation handles one row at a time and retains its ID for duplicate
detection. Future incremental network processing can be added behind the
public build, read, and load functions.

## Modules

- `compiled_scenario.py` owns staged construction, saving, and reloading.
- `equipment/inventory.py` reads and validates attached-equipment rows. The
  SDK wheel packages the same reader under its private `_equipment` module.
- `placements/installation.py` owns per-interface installation overrides.
  Its package initializer exposes the public functions. The SDK wheel
  packages this same source under its private `_placements` module.
- The earlier JSON schema prototype is under `cisei_lib.legacy.project_schemas`
  and remains available through its existing HTTP routes.

- `input_validation.py` owns the shared syntax pass and prepared file inputs.
  It does not import or construct `PlanningScenario`.
- `diagnostics.py` defines serializable issues and stage-specific failure reports.

- `scenario_syntax.py` checks known scenario table/field syntax without creating
  planning objects. It does not replace reference validation or metric compilation.

## Mesh controls and inline metrics

Mesh options in `[mesh]` and `[mesh.network]` use the same Pydantic option models
as the existing HTTP Mesh Link/Network request bodies. The syntax pass checks
known limits, finite thresholds/distances, heuristic choices and flag types
with the existing HTTP coercion behavior. It does not instantiate a planner.
Unknown/extra mesh keys and operation-specific precedence need further review.

Inline metric definitions reuse `parse_metric_spec()` from the metric compiler.
This checks TOML/JSON structure, declared expressions/result/parameters, and
expression grammar without compiling or evaluating expressions. Short catalog
names are not looked up. Expression references, feature availability and cycles
remain compiler concerns. A successful scenario build currently does not prove
that all metric expressions can be compiled or evaluated.

## Connectivity and interface capacity

Connectivity syntax uses the same checks as Graph candidate generation: rule
kind, RF technology presence, source/destination selectors, degree/distance
bounds, and internal metric bounds. It does not check whether technology or
endpoint references exist. Extra rule attributes remain accepted.

`max_links` uses the scenario constructor's shared positive-number/unlimited
conversion. This preserves existing fractional capacities and infinity aliases.
The common implementation is in `planners/shared/input_values.py`, alongside
blank-value handling and connectivity checks. Legacy fractional-degree integer
conversion and boolean numeric capacities require a separate compatibility
review before they are tightened.

## Antenna library contents and rule flags

An embedded `antenna_library` is checked without constructing `AntennaModel`
or `AntennaPattern`: library/model/pattern shapes, required IDs and numeric
pattern fields, frequency-range conversion and attenuation-table conversion.
The existing library loader calls the same checker. Empty pattern lists remain
valid for metadata-only entries; usable gain patterns are a later requirement.
Duplicate model IDs remain a construction failure.

Boolean parsing is shared by staged checks, PlanningScenario and GraphPlanner.
Rule flags `sector_aware` and `enabled_when_device_can_route` accept the existing
boolean forms. In particular, the string `"false"` no longer accidentally enables
sector filtering through Python string truthiness.

## Contract coverage audit (2026-11-17)

A read-only syntax sweep covered all 14 scenario.toml paths under
examples/projects: 12 project roots and two saved preparation backups. All
passed. This excludes recipe-only projects and proves neither build success
nor planning correctness. No notebooks or remote feature requests were run.

| Contract/input | Current staged coverage | Remaining boundary |
| --- | --- | --- |
| scenario TOML | Common tables, required profile fields, scalars, embedded instances, overrides | CRS validity, complete file-option checks, coordinated source/default alternatives |
| Node CSV | Header/row shape, profile column/value, numeric/boolean values, geographic bounds | Coordinate-pair policy and consistency; IDs/profile existence handled by build |
| Placement TOML | Shared field/type/range checks | Reference lookup in build; normal load never reapplies placements; legacy reimport remains separate |
| Pole CSV | Header/row shape, forbidden columns, required/scalar values | Coordinate alternatives and specialized workflow requirements; references/identity in build |
| Equipment CSV | Required columns/values, CSV shape | Device references and duplicate identity in build |
| Embedded antenna library | Shared model/pattern shape and conversion checks | Grid suitability, range ordering; duplicate models in build |
| Standalone antenna YAML/JSON | Existing configuration upload path only | No staged file role yet |
| Inline metric declaration | Shared parsing and expression grammar | Catalog/dependency/feature/cycle checks deferred |
| Standalone metric TOML | Existing configuration upload path only | No staged file role yet |
| Mesh controls | Known options share HTTP models | Unknown keys and specialized controls need review |
| Extension/replanning/baseline recipes | Not checked by this scenario entry point | Existing SDK workflows still interpret these recipes |
| Feature caches/measurements | Not checked by this input pass | Existing cache/configuration/baseline paths; upload lifecycle pending |
| Results/reconfiguration artifacts | Not checked by this input pass | Separate output/reuse/confirmation validation required |
| Proposed upload manifest | Not consumed | Transfer lifecycle pending |

Controlled probes used temporary copies of the annotated reference, leaving
examples unchanged:

| Probe | validate_inputs | build_scenario | Finding |
| --- | --- | --- | --- |
| Invalid working_crs | Fail after audit fix | Stops at input validation | Reports scenario.toml / scenario.working_crs with invalid_crs |
| instances.format = "parquet" | Fail after audit fix | Stops at input validation | Reports scenario.toml / instances.format with unsupported_format |
| Unknown mesh option | Pass | Pass | Unrecognized options are retained without effective validation |
| Unknown metric catalog name | Pass | Pass | Catalog lookup is deferred, as currently documented |
| Neither node source nor embedded sites | Pass | Failed after readiness fix | Build now reports no_interfaces; syntax can still pass |

Passing syntax checks means only that the implemented syntax checks passed.
It does not establish that every required project input is present or that a
compiled scenario is ready for a particular planning operation. The original
empty-scenario finding has since been fixed as described below.

## Build readiness and empty candidate graphs

Syntax validation can accept profiles without instances. `build_scenario()`
requires actual interface instances and reports `no_interfaces` when none were
created. It also reports `no_devices` for sites without devices,
`device_without_interfaces` for devices with no interfaces, and `unknown_device`
for an interface whose device does not exist at its site. These are
`scenario_build` issues. Failed builds preserve the previous compiled artifact.

Profiles alone do not count. Disabled interfaces still belong to their devices.
An isolated device or incompatible technologies do not make construction fail.
Candidate generation remains a separate operation, reporting an empty graph
rather than rejecting an otherwise valid compiled scenario.

The existing candidate build/inspection API returns an additive `diagnostics`
object in `data`, also returned by the existing SDK wrappers:

```json
{
  "stage": "candidate_generation",
  "issues": [{
    "code": "no_candidate_links",
    "severity": "warning",
    "message": "No candidate links are available after candidate generation. Review interface activation, compatibility, explicit links and connectivity rules."
  }]
}
```

Nonempty graphs return the same diagnostic stage with an empty issues list.
The normal API success envelope, counts and candidate-edge list are retained.
This change does not add HTTP file upload or change SDK project opening.

## CRS and node-file format validation

`validate_inputs()` checks a declared working_crs with pyproj.CRS.from_user_input
before any scenario instances are created. Invalid definitions report
`invalid_crs` against `scenario.toml`, field `scenario.working_crs`; they are no
longer attributed to the node CSV. This checks CRS validity, not whether a valid
CRS is appropriate for the project's distances or coordinate combinations.

`instances.format` uses the same `parse_instance_format()` normalization in
syntax checking and PlanningScenario construction. CSV is the supported format;
case and surrounding whitespace remain accepted. Other formats report
`unsupported_format` against `scenario.toml`, field `instances.format`, during
input validation. Format defaults to CSV when omitted. Independent CRS/format
issues are collected together; failed checks preserve the previous compiled
scenario and corrections allow rebuilding.

## Coordinate and placement precedence review

Current mounting-height behavior, verified by staged build/reload tests:

| Source | Effect |
| --- | --- |
| Device profile mount_height_m | Default when the node row has no height |
| Node row mount_height_m | Overrides profile default; zero is an explicit value |
| Row aliases ant_h, antenna_height_m | First nonblank value after mount_height_m, in that order |
| Embedded interface mount_height_m | Overrides the concrete device height for that interface |
| Per-interface placement mount_height_m | Overrides that interface only; other radios and device default remain unchanged |

Orientation/beamwidth/shadow come from the selected antenna profile and explicit
placement fields. Device/site/CSV metadata does not automatically become an
orientation override. Library patterns describe gain, not installation height.
The normal lifecycle compiles from source and loads the saved artifact unchanged;
it does not reapply placements to compiled state. Reimporting an exported
expanded TOML beside a placement file is an existing SDK compatibility case,
kept for later review without blocking the normal lifecycle.

Controlled temporary-project probes exposed coordinate inconsistencies:

| Input | Current staged build behavior |
| --- | --- |
| CSV row with only latitude and no complete projected pair | Now fails input validation with missing_coordinate_pair |
| Equivalent embedded site with only latitude | Now fails the same input check |
| Both complete pairs but geographically contradictory | Builds with coordinate_mismatch warnings above the configured threshold; preserves both pairs |

The staged builder now resolves CSV and embedded sites through the same
Site.resolve path using one working CRS. Geographic-only input can establish
the working CRS from the first site when none is declared; projected-only input
requires a declared CRS. The missing pair is calculated. Pair discrepancies are
reported separately as warnings; the existing strict Site.resolve(validate=True)
option is not enabled by the builder. Invalid CRS definitions are syntax errors.

## Errors, warnings and build reports

Every ProjectIssue has severity `error` (default) or `warning`. ProjectReport
returns valid=true when no error is present. Only blocking failures raise
ProjectValidationError; their report can include warnings already collected.
No partial scenario is published and no unconstructible device is discarded.

Successful builds save their report under `build_report` in
compiled_scenario.json, atomically with the compiled data. This avoids a report
that belongs to a different artifact. The default build_scenario return remains
a Path. To receive the report directly:

```python
report = build_scenario(project_folder, return_report=True)
assert report["valid"]  # May still contain warning-severity issues.
```

The report includes stage, issues, the project-relative compiled filename and
build options. It remains available through read_compiled_scenario(path).
Failed builds leave the previous artifact/report intact and return current
failure feedback through the exception. HTTP report delivery and SDK display
remain pending with the file-upload migration.

When both coordinate pairs are supplied, the builder transforms x/y to WGS84
and measures geodesic separation in metres. This also works for CRSs whose
coordinate units are not metres. Differences over the threshold produce a
coordinate_mismatch warning with site ID, CRS, distance, threshold and both
original coordinate pairs. Streamed pole warnings also include the CSV row.
Neither supplied pair is overwritten. Conversions that cannot produce finite
coordinates remain blocking errors.

```toml
[validation]
coordinate_warning_tolerance_m = 1.0
```

The default is 1 metre; the threshold is configurable, finite and nonnegative.
It is recorded in the saved report. Correct source files and rebuild to change
the compiled scenario; loading an existing artifact neither reevaluates warnings
nor reapplies placements. Complete coordinate pairs are required in CSV and
embedded site inputs before construction.
