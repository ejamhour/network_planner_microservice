"""Stream pole CSV rows into hypothetical site instances, separate from nodes.

Shared by compilation checks and server-side Mesh preparation. Each yielded
tuple contains CSV row number, resolved SiteNode and whether both coordinate
pairs were supplied. No terrain/base-height request is made here.
"""
import csv

from pyproj.exceptions import ProjError
from cisei_lib.planners.shared.planning_scenario import PlanningScenario
from .diagnostics import ProjectValidationError, input_issue


def iter_pole_instances(path, settings, config, scenario):
    row_number = None
    try:
        if str(settings.get("format", "csv")).lower() != "csv":
            raise ValueError("pole inventory must use CSV format")
        profile = settings.get("profile_column", "device_profile")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("profile_column must be a nonempty string")
        inventory = PlanningScenario.from_config_dict(
            {key: config[key] for key in ("antennas", "antenna_library", "interfaces", "devices") if key in config},
            working_crs=scenario.working_crs)
        seen = set()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames or []
            if len(set(headers)) != len(headers):
                raise ValueError("duplicate column names")
            if missing := {"pole_id", profile} - set(headers):
                raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
            if invalid := {"site_id", "connected", "candidate", "rank"} & set(headers):
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
                both = all(row.get(key, "").strip() for key in ("lat", "lon", "x", "y"))
                row.update(site_id=pole_id, pole_id=pole_id, connected=False, candidate=True, rank=None)
                node = inventory.add_node_instances([row], profile_column=profile)[0]
                inventory.site_nodes.clear()
                seen.add(pole_id)
                yield row_number, node, both
    except (ValueError, TypeError, KeyError, OSError, csv.Error, ProjError) as exc:
        raise ProjectValidationError([input_issue(settings["source"], exc, row=row_number)]) from exc
