"""Validate staged file syntax without constructing planning instances."""

import csv
from dataclasses import dataclass
from math import isfinite, isnan
from pathlib import Path, PureWindowsPath

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .scenario_syntax import scenario_syntax_issues
from cisei_lib.planners.shared.input_values import coordinate_input_issues
from .diagnostics import ProjectIssue, ProjectReport, ProjectValidationError, input_issue
from .placements.syntax import placement_syntax_issues
from .equipment.inventory import REQUIRED_COLUMNS, equipment_value_issues


@dataclass
class PreparedInputs:
    root: Path
    config: dict
    files: dict
    attachments: dict | None
    mesh: dict
    poles: dict | None
    placement: dict
    instances: dict
    placement_document: dict | None


def validate_inputs(folder: str | Path) -> dict:
    """Return syntax success or raise input_validation issues; never save a scenario.

    This initial pass checks declarations, paths, file presence, TOML parsing,
    and CSV header/row shape. Domain field/value coverage remains incomplete.
    Reference resolution and instance consistency belong to build_scenario().
    """
    prepare_inputs(folder)
    return ProjectReport("input_validation").to_dict()


def prepare_inputs(folder: str | Path) -> PreparedInputs:
    """Internal shared preparation used by validation and compilation."""
    try:
        return _read_inputs(folder)
    except ProjectValidationError as exc:
        exc.stage = "input_validation"
        raise
    except (TOMLKitError, ValueError, TypeError, KeyError) as exc:
        raise ProjectValidationError([input_issue("scenario.toml", exc)], stage="input_validation") from exc


def _read_inputs(folder):
    root = Path(folder).resolve()
    source = root / "scenario.toml"
    if not source.resolve().is_relative_to(root):
        raise ValueError("scenario.toml must remain inside the project folder")
    try:
        config = tomlkit.parse(source.read_text(encoding="utf-8")).unwrap()
    except (TOMLKitError, ValueError, OSError) as exc:
        raise ProjectValidationError([input_issue("scenario.toml", exc)]) from exc

    scenario_issues = scenario_syntax_issues(config)
    if scenario_issues:
        raise ProjectValidationError(scenario_issues)

    files = config.pop("files", {})
    if not isinstance(files, dict):
        raise ValueError("scenario.toml: [files] must be a table")
    if set(files) - {"nodes", "antenna_placements", "poles", "attached_equipment"}:
        raise ValueError("scenario.toml: unsupported role in [files]")
    attachments = config.pop("attachments", None)
    if attachments is not None and not isinstance(attachments, dict):
        raise ValueError("scenario.toml: [attachments] must be a table")
    if "attached_equipment" in files:
        if attachments is not None and "source" in attachments:
            raise ValueError("scenario.toml: declare attached equipment in [files] or [attachments]")
        attachments = {**(attachments or {}), "source": files["attached_equipment"]}
    mesh = config.pop("mesh", {})
    if not isinstance(mesh, dict):
        raise ValueError("scenario.toml: [mesh] must be a table")
    poles = mesh.pop("poles", None)
    if poles is not None and not isinstance(poles, dict):
        raise ValueError("scenario.toml: [mesh.poles] must be a table")
    if "poles" in files:
        if poles is not None and "source" in poles:
            raise ValueError("scenario.toml: declare the pole CSV in [files].poles or [mesh.poles].source")
        poles = {**(poles or {}), "source": files["poles"]}
    placement = config.pop("antenna_placements", None)
    if placement is not None and (not isinstance(placement, dict) or set(placement) != {"source"}):
        raise ValueError("scenario.toml: [antenna_placements] requires a source filename")
    placement = placement or {}
    if "antenna_placements" in files:
        if "source" in placement:
            raise ValueError("scenario.toml: declare placements in [files] or [antenna_placements]")
        placement["source"] = files["antenna_placements"]
    if "source" not in placement and (root / "antenna_placements.toml").exists():
        placement["source"] = "antenna_placements.toml"
    instances = config.get("instances", {})
    if not isinstance(instances, dict):
        raise ValueError("scenario.toml: [instances] must be a table")
    if "nodes" in files:
        if "source" in instances:
            raise ValueError("scenario.toml: declare the node CSV in [files].nodes or [instances].source")
        instances["source"] = files["nodes"]
        config["instances"] = instances

    # Parse independent inputs before scenario construction. Semantic checks below
    # still require successfully expanded nodes and registered profiles.
    parse_issues = []
    placement_document = None
    companions = [(instances.get("source"), "nodes"), (placement.get("source"), "placements")]
    companions.extend((settings.get("source"), role) for settings, role in
                      ((attachments, "equipment"), (poles, "poles")) if settings is not None)
    for settings, section in ((attachments, "attachments"), (poles, "mesh.poles")):
        if settings is not None:
            if not isinstance(settings.get("source"), str) or not settings["source"].strip():
                parse_issues.append(ProjectIssue("scenario.toml", "missing_value", "A source filename is required", field=section + ".source"))
            if str(settings.get("format", "csv")).lower() != "csv":
                parse_issues.append(ProjectIssue("scenario.toml", "invalid_value", "Only CSV format is supported", field=section + ".format"))
    for name, kind in companions:
        if name is None:
            continue
        row_number = None
        try:
            path = _companion_path(root, name)
            if kind == "placements":
                placement_document = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
                parse_issues.extend(ProjectIssue(name, "invalid_placement", message, field=field)
                                    for field, message in placement_syntax_issues(placement_document))
            else:
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream, strict=True)
                    headers = reader.fieldnames or []
                    row_number = 1
                    if not headers or any(not header.strip() for header in headers) or len(set(headers)) != len(headers):
                        raise ValueError("CSV requires nonempty, unique column names")
                    settings = poles if kind == "poles" else instances
                    profile_column = (settings or {}).get("profile_column", "device_profile")
                    if kind in {"nodes", "poles"} and (not isinstance(profile_column, str) or not profile_column.strip()):
                        raise ValueError("profile_column must be a nonempty string")
                    required = ({profile_column, "pole_id"} if kind == "poles" else
                                {profile_column} if kind == "nodes" else REQUIRED_COLUMNS)
                    for field in sorted(required - set(headers)):
                        parse_issues.append(ProjectIssue(name, "missing_column", "Required column is missing", row=1, field=field))
                    if kind == "poles":
                        for field in sorted({"site_id", "connected", "candidate", "rank"} & set(headers)):
                            parse_issues.append(ProjectIssue(name, "unsupported_column", "Pole inventories cannot contain this column", row=1, field=field))
                    for row in reader:
                        row_number = reader.line_num
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("row does not match the CSV header")
                        if kind in {"nodes", "poles"}:
                            parse_issues.extend(_node_row_issues(name, row_number, row, profile_column))
                            parse_issues.extend(ProjectIssue(name, code, message, row=row_number, field=field)
                                                for code, field, message in coordinate_input_issues(row, config.get("scenario", {}).get("working_crs")))
                            if kind == "poles" and "pole_id" in row and not row["pole_id"].strip():
                                parse_issues.append(ProjectIssue(name, "missing_value", "Pole ID is required", row=row_number, field="pole_id"))
                        elif kind == "equipment":
                            parse_issues.extend(input_issue(name, issue) for issue in equipment_value_issues(row, row_number))
        except (TOMLKitError, ValueError, TypeError, OSError, csv.Error) as exc:
            parse_issues.append(input_issue(name, exc, row=row_number))
    if parse_issues:
        raise ProjectValidationError(parse_issues)

    return PreparedInputs(root, config, files, attachments, mesh, poles,
                          placement, instances, placement_document)


def _companion_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("companion file path must be a nonempty string")
    relative = Path(name)
    windows_path = PureWindowsPath(name)
    path = (root / relative).resolve()
    if (
        relative.is_absolute() or windows_path.anchor or ".." in windows_path.parts
        or not path.is_relative_to(root)
        or path in {(root / "scenario.toml").resolve(), (root / "compiled_scenario.json").resolve()}
    ):
        raise ValueError("path must refer to a companion file inside the project")
    return path



def _node_row_issues(filename, line, row, profile_column):
    """Check scalar CSV syntax; leave profile lookup and ID collisions to build."""
    issues = []
    for field, value in row.items():
        if value is None or not value.strip():
            continue
        message = None
        if field in {"lat", "lon", "x", "y", "rank", "mount_height_m", "ant_h", "antenna_height_m"}:
            try:
                number = float(value)
                if isnan(number) or (field != "rank" and not isfinite(number)):
                    message = "must be finite numeric data" if field != "rank" else "rank cannot be NaN"
                elif field == "lat" and not -90 <= number <= 90:
                    message = "latitude must be between -90 and 90"
                elif field == "lon" and not -180 <= number <= 180:
                    message = "longitude must be between -180 and 180"
            except ValueError:
                message = "must be numeric data"
        elif field in {"connected", "can_route", "candidate"}:
            if value.strip().lower() not in {"1", "true", "t", "yes", "y", "0", "false", "f", "no", "n"}:
                message = "must be a boolean (true/false, yes/no, t/f, y/n, or 1/0)"
        if message:
            issues.append(ProjectIssue(filename, "invalid_value", message, row=line, field=field))
    if profile_column in row and not row[profile_column].strip():
        issues.append(ProjectIssue(filename, "missing_value", "Device profile is required", row=line, field=profile_column))
    return issues
