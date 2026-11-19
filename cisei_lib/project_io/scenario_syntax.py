"""Non-instantiating checks for scenario tables and their declared scalar fields.

Checks only known fields. Extra metadata is retained; references, CRS resolution,
metric compilation, and planner-specific rule semantics belong to later stages.
"""
from math import isfinite, isnan

from pydantic import ValidationError
from pyproj import CRS
from pyproj.exceptions import CRSError
from cisei_lib.planners.mesh.options import MeshNetworkOptions
from cisei_lib.planners.shared.metric_compiler import parse_metric_spec, MetricSpecError

from cisei_lib.planners.shared.input_values import parse_max_links, connectivity_rule_issues, parse_bool, parse_instance_format, coordinate_input_issues

from cisei_lib.planners.shared.antenna_planner import antenna_library_issues

from .diagnostics import ProjectIssue

INTERFACE_REQUIRED = ("tech", "freq_mhz", "tx_power_dbm", "antenna_id")
NUMERIC = {"freq_mhz", "tx_power_dbm", "gain_dbi", "azimuth_deg", "downtilt_deg",
           "beamwidth_deg", "shadow_azimuth_deg", "shadow_width_deg", "shadow_loss_db",
           "mount_height_m", "lat", "lon", "x", "y", "rank"}
STRINGS = {"site_id", "device_id", "interface_id", "tech", "antenna_id", "model_id",
           "kind", "medium", "working_crs", "src", "dst", "profile_column", "source"}
FLAGS = {"connected", "can_route", "can_relay", "auto_tune"}


def scenario_syntax_issues(config):
    """Return all independent shape/scalar issues without constructing objects."""
    issues = []

    def issue(path, message, code="invalid_value"):
        issues.append(ProjectIssue("scenario.toml", code, message, field=path))

    def table(value, path):
        if not isinstance(value, dict):
            issue(path, "Expected a table", "invalid_type")
            return False
        return True

    def fields(value, path, required=()):
        if not table(value, path):
            return False
        for key in required:
            if key not in value or value[key] is None or value[key] == "":
                issue(f"{path}.{key}", "Required field is missing", "missing_value")
        for key, item in value.items():
            field = f"{path}.{key}"
            if key in STRINGS:
                if not isinstance(item, str) or not item.strip():
                    issue(field, "Expected a nonempty string", "invalid_type")
            elif key == "max_links":
                try:
                    parse_max_links(item)
                except (ValueError, TypeError, OverflowError) as exc:
                    issue(field, str(exc))
            elif key == "enabled":
                if not isinstance(item, bool):
                    issue(field, "Expected a boolean", "invalid_type")
            elif key in FLAGS:
                try:
                    parse_bool(item)
                except (ValueError, TypeError, OverflowError) as exc:
                    issue(field, str(exc))
            elif key in NUMERIC and item is not None:
                # Existing loaders accept numeric strings and blank optional values.
                if isinstance(item, str) and not item.strip():
                    continue
                try:
                    number = float(item)
                    if isinstance(item, bool) or isnan(number) or (key != "rank" and not isfinite(number)):
                        raise ValueError
                except (ValueError, TypeError, OverflowError):
                    issue(field, "Expected numeric data (finite except for rank)")
        return True

    for name in ("scenario", "instances", "mesh", "files", "attachments", "antenna_placements", "validation"):
        if name in config:
            fields(config[name], name)
    validation = config.get("validation", {})
    if isinstance(validation, dict):
        for key in set(validation) - {"coordinate_warning_tolerance_m"}:
            issue("validation." + key, "Unsupported validation option", "unsupported_field")
        tolerance = validation.get("coordinate_warning_tolerance_m", 1.0)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not isfinite(tolerance) or tolerance < 0:
            issue("validation.coordinate_warning_tolerance_m", "Expected a finite nonnegative distance in metres")

    scenario_settings = config.get("scenario", {})
    if isinstance(scenario_settings, dict):
        crs = scenario_settings.get("working_crs")
        if isinstance(crs, str) and crs.strip():
            try:
                CRS.from_user_input(crs)
            except CRSError:
                issue("scenario.working_crs", f"Unrecognized coordinate reference system: {crs!r}", "invalid_crs")
    instances = config.get("instances", {})
    if isinstance(instances, dict) and "format" in instances:
        try:
            parse_instance_format(instances["format"])
        except ValueError as exc:
            issue("instances.format", str(exc), "unsupported_format")

    for name in ("antennas", "interfaces", "devices"):
        catalog = config.get(name, {})
        if not table(catalog, name):
            continue
        for identifier, entry in catalog.items():
            path = f"{name}.{identifier}"
            required = INTERFACE_REQUIRED if name == "interfaces" else ("interfaces",) if name == "devices" else ()
            if not fields(entry, path, required):
                continue
            if name == "devices" and "interfaces" in entry:
                refs = entry["interfaces"]
                if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                    issue(path + ".interfaces", "Expected a nonempty array of interface profile IDs", "invalid_type")

    metrics = config.get("metrics", {})
    if table(metrics, "metrics"):
        for technology, entry in metrics.items():
            path = f"metrics.{technology}"
            if isinstance(entry, str):
                continue
            if table(entry, path):
                for key in ("spec", "formula"):
                    if key in entry and not isinstance(entry[key], str):
                        issue(path + "." + key, "Expected a metric string", "invalid_type")

    net = config.get("net", {})
    if table(net, "net"):
        for name in ("connectivity", "candidate_edges"):
            entries = net.get(name, [])
            if not isinstance(entries, list):
                issue("net." + name, "Expected an array of tables", "invalid_type")
                continue
            for index, entry in enumerate(entries):
                fields(entry, f"net.{name}[{index}]", ("src", "dst") if name == "candidate_edges" else ())
                if name == "connectivity" and isinstance(entry, dict):
                    for field, message in connectivity_rule_issues(entry):
                        issue(f"net.{name}[{index}].{field}", message)

    sites = config.get("sites", [])
    if not isinstance(sites, list):
        issue("sites", "Expected an array of site tables", "invalid_type")
    else:
        for index, site in enumerate(sites):
            path = f"sites[{index}]"
            if not fields(site, path, ("site_id",)):
                continue
            settings = config.get("scenario", {})
            working_crs = settings.get("working_crs") if isinstance(settings, dict) else None
            for code, field, message in coordinate_input_issues(site, working_crs):
                issue(path + "." + field, message, code)
            for name, required in (("devices", ("device_id",)),
                                   ("interfaces", ("interface_id", "device_id") + INTERFACE_REQUIRED)):
                entries = site.get(name, [])
                if not isinstance(entries, list):
                    issue(path + "." + name, "Expected an array of tables", "invalid_type")
                    continue
                for item_index, entry in enumerate(entries):
                    fields(entry, f"{path}.{name}[{item_index}]", required)

    overrides = config.get("overrides", {})
    if table(overrides, "overrides"):
        for name in set(overrides) - {"interfaces"}:
            issue("overrides." + name, "Unsupported override section", "unsupported_field")
        entries = overrides.get("interfaces", {})
        if table(entries, "overrides.interfaces"):
            for iid, entry in entries.items():
                path = "overrides.interfaces." + iid
                if fields(entry, path):
                    for key in set(entry) - {"enabled"}:
                        issue(path + "." + key, "Only enabled is supported", "unsupported_field")
    if "antenna_library" in config:
        issues.extend(ProjectIssue("scenario.toml", "invalid_antenna_library", message,
                                   field="antenna_library." + field)
                      for field, message in antenna_library_issues(config["antenna_library"]))
    issues.extend(_mesh_and_metric_issues(config))
    return issues


def _mesh_and_metric_issues(config):
    issues = []
    mesh = config.get("mesh", {})
    if isinstance(mesh, dict):
        # Validate both declaration locations, even where network overrides a base value.
        for path, options in (("mesh", mesh), ("mesh.network", mesh.get("network", {}))):
            if not isinstance(options, dict):
                issues.append(ProjectIssue("scenario.toml", "invalid_type", "Expected a table", field=path))
                continue
            known = {key: value for key, value in options.items() if key in MeshNetworkOptions.model_fields}
            try:
                MeshNetworkOptions.model_validate(known)
            except ValidationError as exc:
                for error in exc.errors(include_url=False):
                    field = path + "." + ".".join(str(part) for part in error["loc"])
                    issues.append(ProjectIssue("scenario.toml", "invalid_value", error["msg"], field=field))
    metrics = config.get("metrics", {})
    if isinstance(metrics, dict):
        for tech, entry in metrics.items():
            key = "spec" if isinstance(entry, dict) and "spec" in entry else "formula"
            spec = entry.get(key) if isinstance(entry, dict) else entry
            path = f"metrics.{tech}" + ("." + key if isinstance(entry, dict) else "")
            if isinstance(entry, dict) and "spec" not in entry and "formula" not in entry:
                issues.append(ProjectIssue("scenario.toml", "missing_value", "Metric requires spec or formula", field=path))
            if not isinstance(spec, str):
                continue  # Basic shape/type diagnostics are produced by the section pass.
            if not spec.strip():
                issues.append(ProjectIssue("scenario.toml", "missing_value", "Metric specification must be nonempty", field=path))
            elif any(marker in spec for marker in ("\n", "=", "{")):
                try:
                    parse_metric_spec(spec)
                except MetricSpecError as exc:
                    issues.append(ProjectIssue("scenario.toml", "invalid_metric", str(exc), field=path))
    return issues
