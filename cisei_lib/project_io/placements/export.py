"""Export effective installations as reusable placement input, not search trials.

Input is an instantiated PlanningScenario (for a planner, pass graph.scenario).
Output contains every antenna-bearing interface, original profile selection,
effective installation values and resolved height. Profile policy/model data
remain in scenario.toml and are not copied into placement entries.
"""
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import tomlkit
from .syntax import ANTENNA_FIELDS, placement_syntax_issues


def effective_placements(scenario):
    config = scenario.to_config_dict()
    antennas = config.get("antennas", {})
    placements = {}
    for site in config.get("sites", []):
        devices = {device["device_id"]: device for device in site.get("devices", [])}
        for interface in site.get("interfaces", []):
            iid = interface["interface_id"]
            selected = interface.get("antenna_id")
            if selected is None:
                continue
            antenna = antennas[selected]
            base, seen = selected, set()
            while antennas[base].get("placement_base_antenna_id"):
                if base in seen:
                    raise ValueError(f"Cyclic placement antenna reference for {iid}")
                seen.add(base)
                base = antennas[base]["placement_base_antenna_id"]
            fields = {"antenna_id": base}
            fields.update({key: antenna[key] for key in sorted(ANTENNA_FIELDS)
                           if antenna.get(key) is not None})
            height = interface.get("mount_height_m")
            if height is None:
                height = devices.get(interface.get("device_id"), {}).get("mount_height_m")
            if height is not None:
                fields["mount_height_m"] = height
            placements[iid] = fields
    document = {"placements": dict(sorted(placements.items()))}
    if issues := placement_syntax_issues(document):
        raise ValueError(f"Cannot export placements: {issues[0][1]}")
    return document


def save_placements(scenario, path):
    """Atomically write complete effective placements to an explicit output path.

    The caller chooses an output destination; source input discovery/promotion
    is not performed here. Return the path written. Unknown values stay omitted.
    """
    path = Path(path)
    text = "# Effective antenna placements; applied settings, not tuning proposals.\n"
    text += tomlkit.dumps(effective_placements(scenario))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
