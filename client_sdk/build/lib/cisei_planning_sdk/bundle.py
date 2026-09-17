from __future__ import annotations

import csv
from math import isfinite
from pathlib import Path
from typing import Any

import tomlkit


def load_scenario_bundle(path: str | Path) -> dict[str, Any]:
    """
    Load a local TOML scenario bundle and return server-ready JSON.

    The SDK owns local file access. If the TOML declares ``[instances]`` with a
    CSV ``source``, this function reads that CSV relative to the TOML directory,
    instantiates concrete ``sites`` from the configured device/interface
    profiles, and removes the file-based ``instances`` section from the payload.
    The server receives a complete scenario dictionary with no local path
    dependency.
    """
    path = Path(path)
    config = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
    config = _plain(config)

    instances = dict(config.get("instances") or {})
    if instances and not config.get("sites"):
        rows = _read_instances(path.parent, instances)
        config["sites"] = _instantiate_sites(config, rows, instances)

    config.pop("instances", None)
    return _json_value(config)


def _read_instances(
    base_dir: Path,
    instances: dict[str, Any],
) -> list[dict[str, Any]]:
    source = instances.get("source")
    if not source:
        raise ValueError("[instances].source is required")

    format_name = str(instances.get("format", "csv")).lower()
    if format_name != "csv":
        raise ValueError("Only CSV instance files are supported by the SDK")

    path = base_dir / str(source)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _instantiate_sites(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    instances: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_column = str(instances.get("profile_column", "device_profile"))
    device_profiles = config.get("devices") or {}
    interface_profiles = config.get("interfaces") or {}

    sites = []
    used_ids = set()
    for index, row in enumerate(rows, start=1):
        site_id = _first_present(row, "site_id", "position_id", "id", "name")
        if not site_id:
            site_id = f"site_{index}"
        site_id = _clean_id(site_id)
        if site_id in used_ids:
            raise ValueError(f"Duplicated site id: {site_id}")
        used_ids.add(site_id)

        profile_id = _clean_id(row.get(profile_column))
        if profile_id not in device_profiles:
            raise ValueError(f"Unknown device profile {profile_id!r} for site {site_id!r}")

        device_profile = device_profiles[profile_id]
        connected = _bool_value(row.get("connected", device_profile.get("connected")), False)
        can_route = _bool_value(row.get("can_route", device_profile.get("can_route")), False)
        rank = _rank_value(row.get("rank", device_profile.get("rank")), connected)
        mount_height = _optional_float(
            _first_present(row, "mount_height_m", "ant_h", "antenna_height_m")
        )
        if mount_height is None:
            mount_height = _optional_float(device_profile.get("mount_height_m"))

        site = {
            "site_id": site_id,
            "kind": str(row.get("kind") or device_profile.get("kind") or "field"),
            "lat": _optional_float(row.get("lat")),
            "lon": _optional_float(row.get("lon")),
            "x": _optional_float(row.get("x")),
            "y": _optional_float(row.get("y")),
        }

        known_site_keys = {
            "site_id",
            "position_id",
            "id",
            "name",
            "lat",
            "lon",
            "x",
            "y",
            "kind",
            "mount_height_m",
            "ant_h",
            "antenna_height_m",
            "connected",
            "can_route",
            "rank",
            profile_column,
        }
        for key, value in row.items():
            if key not in known_site_keys and not _is_blank(value):
                site[key] = _parsed_scalar(value)

        device_id = f"{site_id}:d0"
        site["devices"] = [
            {
                "device_id": device_id,
                "site_id": site_id,
                "connected": connected,
                "can_route": can_route,
                "rank": rank,
                "mount_height_m": mount_height,
                "device_profile": profile_id,
            }
        ]
        site["interfaces"] = []

        for interface_index, interface_profile_id in enumerate(device_profile["interfaces"]):
            interface_profile_id = _clean_id(interface_profile_id)
            if interface_profile_id not in interface_profiles:
                raise ValueError(
                    f"Device profile {profile_id!r} references unknown "
                    f"interface profile {interface_profile_id!r}"
                )
            interface_profile = interface_profiles[interface_profile_id]
            site["interfaces"].append(
                {
                    "interface_id": f"{site_id}:d0:i{interface_index}",
                    "device_id": device_id,
                    "site_id": site_id,
                    "tech": str(interface_profile["tech"]),
                    "freq_mhz": float(interface_profile["freq_mhz"]),
                    "tx_power_dbm": float(interface_profile["tx_power_dbm"]),
                    "antenna_id": str(interface_profile["antenna_id"]),
                    "can_relay": _bool_value(interface_profile.get("can_relay"), False),
                    "medium": str(interface_profile.get("medium", "rf")),
                    "max_links": _max_links_value(interface_profile.get("max_links")),
                    "interface_profile": interface_profile_id,
                }
            )

        sites.append(_drop_none(site))

    return sites


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if not _is_blank(value):
            return value
    return None


def _clean_id(value: Any) -> str:
    if _is_blank(value):
        raise ValueError("ID cannot be blank")
    return str(value).strip()


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value != value:
            return True
    except TypeError:
        pass
    return isinstance(value, str) and not value.strip()


def _optional_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    value = float(value)
    if not isfinite(value):
        return None
    return value


def _rank_value(value: Any, connected: bool) -> float | None:
    if _is_blank(value):
        return 0.0 if connected else None
    value = float(value)
    return value if isfinite(value) else None


def _bool_value(value: Any, default: bool = False) -> bool:
    if _is_blank(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _max_links_value(value: Any) -> float | None:
    if _is_blank(value):
        return None
    value = float(value)
    return value if isfinite(value) else None


def _parsed_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        number = float(text)
    except ValueError:
        return text
    if not isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    return value
