"""Validate parsed mesh-link bundles without opening user files or calling RF services."""

from copy import deepcopy
from pathlib import PurePosixPath, PureWindowsPath

from jsonschema import Draft202012Validator
from pyproj.exceptions import ProjError

from cisei_lib.planners.mesh.mesh_link_planner import MeshLinkPlanner
from cisei_lib.planners.shared.planning_scenario import PlanningScenario
from cisei_lib.legacy.project_schemas import load_document


def _issue(file, message, *, row=None, column=None, path=None):
    return {"file": file, "row": row, "column": column, "path": path, "message": message}


def _read_table(tables, source, required, issues, *, forbidden=()):
    path = PurePosixPath(source.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(source).drive or ".." in path.parts:
        issues.append(_issue(source, "Use a project-relative filename."))
        return []
    if source not in tables:
        issues.append(_issue(source, "Referenced CSV table is missing."))
        return []
    table = tables[source]
    headers = set(table["headers"])
    for column in sorted(set(required) - headers):
        issues.append(_issue(source, "Required CSV column is missing.", row=1, column=column))
    for column in sorted(headers.intersection(forbidden)):
        issues.append(_issue(source, "Column is not permitted in this table.", row=1, column=column))
    rows = []
    for line, row in enumerate(table["rows"], start=2):
        if row.keys() - headers:
            issues.append(_issue(source, "Row contains fields outside the CSV header.", row=line))
        for column in required:
            if column in headers and not (row.get(column) or "").strip():
                issues.append(_issue(source, "Required value is empty.", row=line, column=column))
        rows.append(row)
    return rows


def _check_profiles(rows, config, source, column, issues):
    for line, row in enumerate(rows, start=2):
        value = (row.get(column) or "").strip()
        if value and value not in config.get("devices", {}):
            issues.append(_issue(source, f"Unknown device profile {value!r}.", row=line, column=column))


def _add_rows(scenario, rows, source, profile, issues):
    for line, row in enumerate(rows, start=2):
        record = dict(row)
        if scenario._record_site_id(record) is None:
            record["site_id"] = f"site_{line - 1}"
        try:
            scenario.add_node_instances([record], profile_column=profile)
        except (ValueError, TypeError, KeyError, ProjError) as exc:
            issues.append(_issue(source, str(exc), row=line))


def _validate_attachments(config, scenario, tables, issues):
    settings = config.get("attachments")
    if settings is None:
        return
    source = settings["source"]
    required = ("equipment_id", "equipment_type", "device_id")
    rows = _read_table(tables, source, required, issues)
    devices = {
        device["device_id"]
        for site in scenario.to_config_dict()["sites"]
        for device in site["devices"]
    }
    seen = set()
    for line, row in enumerate(rows, start=2):
        equipment, kind, device = ((row.get(key) or "").strip() for key in required)
        if device and device not in devices:
            issues.append(_issue(source, f"Unknown attachment device {device!r}.", row=line, column="device_id"))
        if equipment and kind:
            key = (kind, equipment)
            if key in seen:
                issues.append(_issue(source, "Duplicate attached equipment.", row=line, column="equipment_id"))
            seen.add(key)


def validate_mesh_link(bundle):
    validator = Draft202012Validator(load_document("mesh-link.schema.json"))
    issues = []
    for error in validator.iter_errors(bundle):
        path = list(error.absolute_path)
        file = "scenario.toml" if path and path[0] == "scenario" else None
        if len(path) > 1 and path[0] == "tables":
            file = path[1]
        issues.append(_issue(file, error.message, path=path))
    if issues:
        return issues

    config = deepcopy(bundle["scenario"])
    tables = bundle.get("tables", {})
    if "antenna_placements" in config:
        return [_issue("scenario.toml", "Antenna placement files are outside this schema version.",
                       path=["scenario", "antenna_placements"])]

    instances = config.get("instances")
    mesh = config.get("mesh", {})
    poles = mesh.get("poles")
    node_rows = pole_rows = []
    if instances and not config.get("sites"):
        source = instances["source"]
        profile = instances.get("profile_column", "device_profile")
        node_rows = _read_table(tables, source, (profile,), issues)
        _check_profiles(node_rows, config, source, profile, issues)
    if poles:
        source = poles["source"]
        profile = poles.get("profile_column", "device_profile")
        pole_rows = _read_table(tables, source, ("pole_id", profile), issues,
                                forbidden=("site_id", "connected", "candidate", "rank"))
        _check_profiles(pole_rows, config, source, profile, issues)
    if issues:
        return issues

    try:
        scenario = PlanningScenario.from_config_dict(config)
        if node_rows:
            _add_rows(scenario, node_rows, instances["source"],
                      instances.get("profile_column", "device_profile"), issues)
            if issues:
                return issues
            scenario = PlanningScenario.from_config_dict(scenario.to_config_dict())
        issues.extend(_issue("scenario.toml", error) for error in scenario.validate())
        _validate_attachments(config, scenario, tables, issues)
        if issues:
            return issues
    except (ValueError, TypeError, KeyError, ProjError) as exc:
        return [_issue("scenario.toml", str(exc))]

    pole_sites = []
    if pole_rows:
        pole_scenario = PlanningScenario.from_config_dict({
            key: config[key] for key in ("scenario", "antennas", "antenna_library", "interfaces", "devices")
            if key in config
        })
        candidates = [
            dict(row, site_id=row["pole_id"], connected=False, candidate=True, rank=None)
            for row in pole_rows
        ]
        _add_rows(pole_scenario, candidates, poles["source"], poles.get("profile_column", "device_profile"), issues)
        if issues:
            return issues
        pole_sites = pole_scenario.to_config_dict()["sites"]
        for line, site in enumerate(pole_sites, start=2):
            if site["site_id"] in scenario.site_nodes:
                issues.append(_issue(poles["source"], "Pole ID matches a network site.", row=line, column="pole_id"))
        if issues:
            return issues

    operation = {key: value for key, value in mesh.items() if key not in {"poles", "network"}}
    operation.update(bundle["operation"])
    operation_schema = validator.schema["properties"]["operation"]
    for error in Draft202012Validator(operation_schema).iter_errors(operation):
        issues.append(_issue(None, error.message, path=["operation", *error.absolute_path]))
    if issues:
        return issues
    operation.pop("geo_base_url", None)
    try:
        MeshLinkPlanner(scenario, geo=None, poles=pole_sites or None, **operation)
    except (ValueError, TypeError, KeyError, ProjError) as exc:
        issues.append(_issue(None, str(exc), path=["operation"]))
    return issues
