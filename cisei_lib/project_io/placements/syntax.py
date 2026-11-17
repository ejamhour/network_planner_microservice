"""Placement field rules shared by syntax checking and installation application."""
from math import isfinite

ANTENNA_FIELDS = frozenset({"azimuth_deg", "downtilt_deg", "beamwidth_deg",
                            "shadow_azimuth_deg", "shadow_width_deg", "shadow_loss_db"})


def placement_syntax_issues(document):
    """Return (field path, message) pairs without resolving any references."""
    issues = []
    if not isinstance(document, dict) or set(document) - {"placements"} or not isinstance(document.get("placements", {}), dict):
        return [("placements", "Antenna placement file accepts only a placements table")]
    for iid, fields in document.get("placements", {}).items():
        prefix = f"placements.{iid}"
        if not isinstance(fields, dict):
            issues.append((prefix, f"Antenna placement {iid}: expected a table"))
            continue
        allowed = ANTENNA_FIELDS | {"antenna_id", "mount_height_m", "tilt_deg"}
        for key in sorted(set(fields) - allowed):
            issues.append((f"{prefix}.{key}", f"Antenna placement {iid}: unsupported placement fields: {key}"))
        if "tilt_deg" in fields and "downtilt_deg" in fields:
            issues.append((prefix + ".tilt_deg", f"Antenna placement {iid}: use downtilt_deg or tilt_deg, not both"))
        for key, value in fields.items():
            field = f"{prefix}.{key}"
            if key not in allowed:
                continue
            if key == "antenna_id":
                if not isinstance(value, str) or not value.strip():
                    issues.append((field, f"Antenna placement {iid}: antenna_id must be a nonempty string"))
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                issues.append((field, f"Antenna placement {iid}: {key} must be finite numeric data"))
            elif key in {"mount_height_m", "shadow_loss_db"} and value < 0:
                issues.append((field, f"Antenna placement {iid}: {key} must be nonnegative"))
            elif key in {"beamwidth_deg", "shadow_width_deg"} and not 0 < value <= 360:
                issues.append((field, f"Antenna placement {iid}: {key} must be in (0, 360]"))
    return issues
