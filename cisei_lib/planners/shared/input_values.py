"""Shared scalar conversions and connectivity input checks, without instances."""
from math import inf, isfinite

RULE_SELECTORS = frozenset({"any", "connected", "unconnected", "relay", "non_relay"})


def is_blank(value):
    if value is None:
        return True
    try:
        if value != value:
            return True
    except TypeError:
        pass
    return isinstance(value, str) and not value.strip()


def parse_max_links(value):
    """Retain the existing positive-number/unlimited capacity convention."""
    if is_blank(value):
        return inf
    if isinstance(value, str) and value.strip().lower() in {"inf", "+inf", "infinity", "+infinity", "unlimited"}:
        return inf
    if isinstance(value, (int, float)) and not isfinite(float(value)):
        if float(value) > 0:
            return inf
        raise ValueError("max_links must be positive or unlimited")
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError("max_links must be positive or unlimited")
    return number


def connectivity_rule_issues(rule):
    """Return independent (field, message) issues using current planner conventions.

    Extra attributes remain allowed. No technology/profile/instance lookup occurs.
    Degree keeps the planner's existing int conversion; do not silently tighten it.
    """
    issues = []
    kind = str(rule.get("kind", "rf")).lower()
    if kind not in {"rf", "internal"}:
        return [("kind", f"Unknown connectivity rule kind: {kind}")]
    flag = "enabled_when_device_can_route" if kind == "internal" else "sector_aware"
    if flag in rule:
        try:
            parse_bool(rule[flag])
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append((flag, str(exc)))
    if kind == "internal":
        try:
            metric = float(rule.get("metric", 0.0))
            if metric < 0 or not isfinite(metric):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            issues.append(("metric", "Internal metric must be finite and >= 0"))
        return issues
    if is_blank(rule.get("tech")):
        issues.append(("tech", "RF rule requires a technology"))
    for field in ("source", "destination"):
        if str(rule.get(field, "any")).lower() not in RULE_SELECTORS:
            issues.append((field, "Selector must be any, connected, unconnected, relay or non_relay"))
    for field in ("degree", "limit"):
        value = rule.get(field)
        if is_blank(value):
            continue
        try:
            number = int(value) if field == "degree" else float(value)
            if number < 0 or not isfinite(number):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            issues.append((field, f"{field} must be finite and >= 0"))
    return issues


def parse_bool(value, default=False):
    if is_blank(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not isfinite(float(value)):
            raise ValueError("Boolean numeric fields must be finite")
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y"}:
            return True
        if normalized in {"0", "false", "f", "no", "n"}:
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")



def parse_instance_format(value="csv"):
    """Normalize the supported node-table format for syntax and construction."""
    table_format = str(value).strip().lower()
    if table_format != "csv":
        raise ValueError("Only CSV instance sources are supported")
    return table_format


def coordinate_input_issues(record, working_crs=None):
    """Check coordinate alternatives without transforming or comparing pairs."""
    has_geo = all(not is_blank(record.get(key)) for key in ("lat", "lon"))
    has_xy = all(not is_blank(record.get(key)) for key in ("x", "y"))
    if not has_geo and not has_xy:
        return [("missing_coordinate_pair", "position", "Provide a complete lat/lon or x/y coordinate pair.")]
    if has_xy and not has_geo and is_blank(working_crs):
        return [("missing_crs", "position", "Projected-only positions require scenario.working_crs.")]
    return []
