"""Training observations attached to the existing portable raw-feature cache.

The format/version, source, request and features are unchanged. Each entry can
carry observations (radio configuration and measurements), one per sample.
This module never requests geographic data or converts legacy file formats.
"""
from copy import deepcopy
import json
import math


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")
    return float(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected a nonempty string")
    return value


def request_key(source, request):
    """Match the server's directed extraction identity without importing it."""
    source = _text(source, "source").rstrip("/")
    if not isinstance(request, dict):
        raise ValueError("request: expected an object")
    allowed = {"tx", "rx", "tx_ha", "rx_ha", "freq_mhz", "options", "tag"}
    if set(request) - allowed:
        raise ValueError("request: unknown extraction fields")
    positions = []
    for side in ("tx", "rx"):
        pos = request.get(side)
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ValueError(f"request.{side}: expected [latitude, longitude]")
        lat, lon = (_number(v, f"request.{side}") for v in pos)
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"request.{side}: invalid coordinates")
        positions.append((lat, lon))
    heights = [_number(request.get(k), f"request.{k}") for k in ("tx_ha", "rx_ha")]
    if min(heights) < 0:
        raise ValueError("request: antenna heights must be nonnegative")
    freq = _number(request.get("freq_mhz"), "request.freq_mhz")
    if freq <= 0:
        raise ValueError("request.freq_mhz: must be positive")
    options = request.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("request.options: expected an object")
    # Normalize numeric option values as the server's tuple equality does.
    def freeze(value):
        if isinstance(value, dict):
            return tuple(sorted((k, freeze(v)) for k, v in value.items()))
        if isinstance(value, list):
            return tuple(freeze(v) for v in value)
        return value
    json.dumps(options, allow_nan=False)
    return (source, *positions, *heights, freq, freeze(options))


def observation(value, request):
    if not isinstance(value, dict):
        raise ValueError("observation: expected an object")
    _text(value.get("id"), "observation.id")
    for side in ("tx", "rx"):
        radio = value.get(side)
        if not isinstance(radio, dict):
            raise ValueError(f"observation.{side}: expected an object")
        _text(radio.get("name"), f"observation.{side}.name")
        _number(radio.get("ant_gain"), f"observation.{side}.ant_gain")
        if side == "tx":
            _number(radio.get("pw"), "observation.tx.pw")
        for field, expected in (("pos", request[side]), ("ant_height", request[f"{side}_ha"])):
            if field in radio:
                value = radio[field]
                if field == "ant_height":
                    value = _number(value, f"observation.{side}.{field}")
                else:
                    if not isinstance(value, (list, tuple)) or len(value) != 2:
                        raise ValueError(f"observation.{side}.pos: expected latitude and longitude")
                    value = [_number(v, f"observation.{side}.pos") for v in value]
                if value != expected:
                    raise ValueError(f"observation.{side}.{field}: conflicts with extraction request")
    measures = value.get("measures")
    if not isinstance(measures, dict):
        raise ValueError("observation.measures: expected an object")
    _number(measures.get("rssi"), "observation.measures.rssi")
    if any(k in value for k in ("features", "request", "source")):
        raise ValueError("observation: extraction fields belong to the cache entry")
    json.dumps(value, allow_nan=False)
    return deepcopy(value)


def validate_cache(document):
    if (not isinstance(document, dict) or document.get("format") != "cisei-feature-cache"
            or type(document.get("version")) is not int or document["version"] != 1
            or not isinstance(document.get("entries"), list)):
        raise ValueError("Expected features.json in cisei-feature-cache version 1 format")
    json.dumps(document, allow_nan=False)
    keys, identifiers = {}, {}
    for index, entry in enumerate(document["entries"]):
        try:
            key = request_key(entry.get("source"), entry.get("request"))
            if key in keys:
                raise ValueError("duplicate extraction request")
            keys[key] = index
            if not isinstance(entry.get("features"), dict):
                raise ValueError("features: expected an object")
            samples = entry.get("observations", [])
            if not isinstance(samples, list):
                raise ValueError("observations: expected a list")
            for sample in samples:
                observation(sample, entry["request"])
                if sample["id"] in identifiers:
                    raise ValueError(f"duplicate observation id {sample['id']!r}")
                identifiers[sample["id"]] = (index, sample)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"features.entries[{index}]: {exc}") from exc
    return keys, identifiers


def enrich_cache(document, measurements, *, replace=False):
    """Validate everything before returning a changed copy; never mutate inputs."""
    if not isinstance(replace, bool):
        raise ValueError("replace must be a boolean")
    keys, existing = validate_cache(document)
    if (not isinstance(measurements, dict) or measurements.get("format") != "cisei-link-measurements"
            or type(measurements.get("version")) is not int or measurements["version"] != 1
            or not isinstance(measurements.get("records"), list)):
        raise ValueError("Expected cisei-link-measurements version 1 with a records list")
    result = deepcopy(document)
    by_request = {}
    for key in keys:
        by_request.setdefault(key[1:], []).append(key)
    seen, added, updated = set(), 0, 0
    for index, row in enumerate(measurements["records"]):
        try:
            if "source" in row:
                key = request_key(row["source"], row.get("request"))
            else:
                identity = request_key("local", row.get("request"))[1:]
                matches = by_request.get(identity, [])
                if len(matches) != 1:
                    raise ValueError("expected one feature entry for measurement request; missing or ambiguous match")
                key = matches[0]
            if key not in keys:
                raise ValueError("no matching feature entry (source, direction, coordinates, heights, frequency or options)")
            entry_index = keys[key]
            sample = observation({k: v for k, v in row.items() if k not in {"source", "request"}},
                                 document["entries"][entry_index]["request"])
            identifier = sample["id"]
            if identifier in seen:
                raise ValueError(f"duplicate measurement id {identifier!r}")
            seen.add(identifier)
            samples = result["entries"][entry_index].setdefault("observations", [])
            if identifier in existing:
                previous_index, previous = existing[identifier]
                if previous_index != entry_index:
                    raise ValueError(f"measurement {identifier!r} already belongs to another extraction request")
                if previous != sample:
                    if not replace:
                        raise ValueError(f"measurement {identifier!r} changed; use replace=True to update it")
                    samples[samples.index(previous)] = sample
                    updated += 1
            else:
                samples.append(sample)
                added += 1
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"measurements.records[{index}]: {exc}") from exc
    return result, {"measurements": len(seen), "added": added, "updated": updated,
                    "unchanged": len(seen)-added-updated, "feature_entries": len(keys)}


def training_records(document):
    validate_cache(document)
    records = []
    for entry in document["entries"]:
        request = entry["request"]
        for sample in entry.get("observations", []):
            record = deepcopy(sample)
            for side in ("tx", "rx"):
                record[side]["pos"] = list(request[side])
                record[side]["ant_height"] = request[f"{side}_ha"]
            record["freq_mhz"] = request["freq_mhz"]
            record["features"] = deepcopy(entry["features"])
            records.append(record)
    if not records:
        raise ValueError("features.json has no training observations; call enrich_features() first")
    return records
