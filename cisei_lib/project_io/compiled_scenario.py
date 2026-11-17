"""Build and reload a compiled scenario in a staged project folder."""

import csv
import json
from pathlib import Path
from math import isfinite
from tempfile import NamedTemporaryFile

from pyproj.exceptions import ProjError
from tomlkit.exceptions import TOMLKitError

from cisei_lib.planners.shared.planning_scenario import PlanningScenario
from cisei_lib.planners.shared.instance_validation import instance_issues
from cisei_lib.planners.shared.position_validation import coordinate_discrepancy_m
from .placements import apply_placement_document
from .equipment import read_equipment_rows
from .diagnostics import ProjectIssue, ProjectReport, ProjectValidationError, input_issue
from .input_validation import prepare_inputs, _companion_path


def build_scenario(folder: str | Path, *, return_report: bool = False) -> Path | dict:
    """Publish complete output; errors block, warnings accompany successful builds.

    Return the artifact Path by default for compatibility. With return_report=True,
    return the saved report (including the project-relative artifact filename).
    Reports are also embedded in compiled_scenario.json under build_report.
    """
    try:
        return _build_scenario(folder, return_report=return_report)
    except ProjectValidationError:
        raise
    except (TOMLKitError, ValueError, TypeError, KeyError, ProjError) as exc:
        raise ProjectValidationError([input_issue("scenario.toml", exc)]) from exc


def _build_scenario(folder: str | Path, *, return_report: bool) -> Path | dict:
    prepared = prepare_inputs(folder)
    root, config, files = prepared.root, prepared.config, prepared.files
    attachments, mesh, poles = prepared.attachments, prepared.mesh, prepared.poles
    placement, instances = prepared.placement, prepared.instances
    placement_document = prepared.placement_document
    compiled = root / "compiled_scenario.json"
    options = config.pop("validation", {})
    tolerance = options.get("coordinate_warning_tolerance_m", 1.0)
    build_warnings = []

    location = "scenario.toml"
    try:
        scenario = PlanningScenario.from_config_dict(config)
        if "nodes" in files or (scenario.instance_source and not scenario.site_nodes):
            name = instances.get("source")
            _companion_path(root, name)
            location = name
            if scenario.site_nodes:
                raise ValueError("omit [files].nodes when sites are embedded")
            scenario.load_instance_source(root, missing_ok=False, resolve=False)
        # Resolve CSV and embedded sites together using one project CRS.
        position_issues = []
        for index, site in enumerate(scenario.site_nodes.values()):
            try:
                supplied_both_pairs = all(value is not None for value in (site.site.lat, site.site.lon, site.site.x, site.site.y))
                scenario.working_crs = site.resolve_position(scenario.working_crs)
                if not all(isfinite(value) for value in (site.site.lat, site.site.lon, site.site.x, site.site.y)):
                    raise ValueError(f"Position conversion for site {site.site_id!r} did not produce finite coordinates")
                if supplied_both_pairs:
                    warning = _coordinate_warning(site.site, scenario.working_crs, tolerance, location,
                                                  field=f"sites[{index}].position")
                    if warning:
                        build_warnings.append(warning)
            except (ValueError, TypeError, ProjError) as exc:
                position_issues.append(ProjectIssue(location, "invalid_position", str(exc), field=f"sites[{index}].position"))
        if position_issues:
            raise ProjectValidationError(position_issues)
        location = "scenario.toml"
        readiness = instance_issues(scenario.site_nodes)
        if readiness:
            raise ProjectValidationError([
                ProjectIssue("scenario.toml", code, message, field=field)
                for code, field, message in readiness
            ])
        if "source" in placement:
            path = _companion_path(root, placement["source"])
            location = placement["source"]
            expanded = scenario.to_config_dict()
            apply_placement_document(expanded, placement_document)
            scenario = PlanningScenario.from_config_dict(expanded)
            location = "scenario.toml"
        issues = scenario.validate()
        if issues:
            raise ProjectValidationError([
                ProjectIssue("scenario.toml", "invalid_reference", message) for message in issues
            ])
    except ProjectValidationError as exc:
        if build_warnings:
            raise ProjectValidationError([*exc.issues, *build_warnings], stage=exc.stage) from exc
        raise
    except (TOMLKitError, ValueError, TypeError, KeyError, OSError, ProjError) as exc:
        raise ProjectValidationError([input_issue(location, exc), *build_warnings]) from exc

    expanded = scenario.to_json_dict()
    expanded.pop("instances", None)
    companion_issues = []
    for settings, role in ((attachments, "attachments"), (poles, "poles")):
        if settings is None:
            continue
        try:
            path = _companion_path(root, settings.get("source"))
            if role == "attachments":
                _validate_attachments(path, settings, scenario)
                expanded["attachments"] = settings
            else:
                build_warnings.extend(_validate_poles(path, settings, config, scenario, tolerance))
                mesh["poles"] = settings
        except ProjectValidationError as exc:
            companion_issues.extend(exc.issues)
    if companion_issues:
        raise ProjectValidationError([*companion_issues, *build_warnings])
    if mesh:
        expanded["mesh"] = mesh
    report = ProjectReport("scenario_build", tuple(build_warnings)).to_dict()
    report["compiled_scenario"] = compiled.name
    report["options"] = {"coordinate_warning_tolerance_m": tolerance}
    expanded["build_report"] = report
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(expanded, stream, allow_nan=False)
        temporary.replace(compiled)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return report if return_report else compiled


def _coordinate_warning(site, working_crs, tolerance, filename, *, field, row=None):
    distance = coordinate_discrepancy_m(site, working_crs)
    if distance <= tolerance:
        return None
    return ProjectIssue(
        filename, "coordinate_mismatch",
        f"Site {site.site_id!r}: geographic and projected positions differ by {distance:.3f} m "
        f"(warning threshold {tolerance:g} m). Both supplied pairs were preserved.",
        row=row, field=field, severity="warning",
        details={"site_id": site.site_id, "working_crs": working_crs,
                 "distance_m": distance, "tolerance_m": tolerance,
                 "lat": site.lat, "lon": site.lon, "x": site.x, "y": site.y},
    )


def _validate_poles(path, settings, config, scenario, tolerance):
    """Validate pole rows without retaining expanded candidate records."""
    if str(settings.get("format", "csv")).lower() != "csv":
        raise ValueError(f"{settings['source']}: pole inventory must use CSV format")
    profile_column = settings.get("profile_column", "device_profile")
    if not isinstance(profile_column, str) or not profile_column.strip():
        raise ValueError(f"{settings['source']}: profile_column must be a nonempty string")
    required = {"pole_id", profile_column}
    forbidden = {"site_id", "connected", "candidate", "rank"}
    inventory = PlanningScenario.from_config_dict(
        {key: config[key] for key in ("antennas", "interfaces", "devices") if key in config},
        working_crs=scenario.working_crs,
    )
    seen = set()
    warnings = []
    row_number = None
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames or []
            if len(set(headers)) != len(headers):
                raise ValueError("duplicate column names")
            if missing := required - set(headers):
                raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
            if invalid := forbidden & set(headers):
                raise ValueError(f"unsupported columns: {', '.join(sorted(invalid))}")
            for row in reader:
                row_number = reader.line_num
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("row does not match the CSV header")
                pole_id = row.pop("pole_id").strip()
                if not pole_id:
                    raise ValueError("pole_id is required")
                if pole_id in scenario.site_nodes or pole_id in seen:
                    raise ValueError(f"duplicate or conflicting pole ID: {pole_id}")
                row.update(site_id=pole_id, pole_id=pole_id, connected=False, candidate=True, rank=None)
                nodes = inventory.add_node_instances([row], profile_column=profile_column)
                if all(row.get(key, "").strip() for key in ("lat", "lon", "x", "y")):
                    warning = _coordinate_warning(nodes[0].site, scenario.working_crs, tolerance, settings["source"],
                                                  field="position", row=row_number)
                    if warning:
                        warnings.append(warning)
                inventory.site_nodes.clear()
                seen.add(pole_id)
    except (ValueError, TypeError, KeyError, OSError, csv.Error, ProjError) as exc:
        raise ProjectValidationError([input_issue(settings["source"], exc, row=row_number), *warnings]) from exc
    return warnings


def _validate_attachments(path, settings, scenario):
    """Check the staged inventory against concrete device IDs, row by row."""
    if str(settings.get("format", "csv")).lower() != "csv":
        raise ValueError(f"{settings['source']}: attached equipment must use CSV format")
    devices = {device.device_id for site in scenario.site_nodes.values() for device in site.devices}
    try:
        for _ in read_equipment_rows(path, devices):
            pass
    except (ValueError, OSError, csv.Error) as exc:
        raise ProjectValidationError([input_issue(settings["source"], exc)]) from exc


def read_compiled_scenario(path: str | Path) -> dict:
    """Read the expanded scenario and saved companion file references."""
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def load_compiled_scenario(path: str | Path) -> PlanningScenario:
    """Load saved instances/settings without rereading or applying source inputs.

    Editing scenario.toml, nodes or placements does not change this artifact.
    Call build_scenario(project_folder) to compile updated inputs, then reload.
    Separately referenced inventories (such as poles) are not embedded here.
    """
    return PlanningScenario.from_config_dict(read_compiled_scenario(path))
