"""Prepare a new study by applying ordered device replacement rules locally."""
import copy
import csv
import hashlib
import json
import shutil
from datetime import datetime
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import tomlkit

from .local_io import load_scenario_bundle, _bool_value, _rank_value
from .project import PlanningProject


def _merge(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ExtendedPlanningProject(PlanningProject):
    """An independent project prepared from a source and an extension.toml recipe.

    Construction creates the destination folder, as PlanningProject does.
    prepare() validates and generates scenario.toml, nodes.csv, features.json
    and replacement_report.json. Matching prepared studies are reused;
    overwrite=True explicitly regenerates them while retaining cached features.
    Source/destination paths are supplied in Python, never in the recipe.
    """

    def __init__(self, path, *, source, extension_file="extension.toml"):
        super().__init__(path)
        self.source = source if isinstance(source, PlanningProject) else PlanningProject(source)
        if self.path == self.source.path or self.path.is_relative_to(self.source.path) or self.source.path.is_relative_to(self.path):
            raise ValueError("Source and destination must be separate project directories")
        self.extension_path = (self.path / extension_file).resolve()
        if not self.extension_path.is_relative_to(self.path):
            raise ValueError("Extension recipe must remain inside its project")
        if self.extension_path.name in {"scenario.toml", "nodes.csv", "features.json", "replacement_report.json"}:
            raise ValueError("Extension recipe must not use a generated output filename")

    def prepare(self, *, result=None, overwrite=False):
        """Apply the first matching rule to each original CSV row, exactly once.

        Rules support profile, rank_lte (<=), and exact CSV tags. All predicates
        are ANDed. rank_lte is an operator, not the LTE radio technology.
        Actions may override connected and set rank to "copy" or a finite number;
        omitted actions use replacement-profile defaults.
        Rank rules require an unambiguous device rank; differing interface ranks
        raise an error instead of silently introducing an aggregation policy.
        """
        outputs = [self.scenario_path, self.path / "nodes.csv", self.features_path,
                   self.path / "replacement_report.json"]
        existing = any(p.exists() for p in outputs)
        source = tomlkit.parse(self.source.scenario_path.read_text(encoding="utf-8")).unwrap()
        extension = tomlkit.parse(self.extension_path.read_text(encoding="utf-8")).unwrap()
        topology = extension.pop("topology", {})
        if not isinstance(topology, dict) or set(topology) - {"mode"}:
            raise ValueError("topology accepts only mode = 'installed'")
        if topology and topology.get("mode") != "installed":
            raise ValueError("topology.mode must be 'installed'")
        installed = topology.get("mode") == "installed"
        rules = extension.pop("replacement", {}).get("rules", [])
        if not isinstance(rules, list) or not rules:
            raise ValueError("Extension requires ordered [[replacement.rules]]")
        if "sites" in source or "sites" in extension or "instances" in extension:
            raise ValueError("Derived preparation requires a source CSV; the recipe cannot replace sites/instances")
        instances = source.get("instances", {})
        if instances.get("format", "csv") != "csv" or not instances.get("source"):
            raise ValueError("Source requires CSV instances")
        source_csv = self.source.path / instances["source"]
        with source_csv.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        profile_column = instances.get("profile_column", "device_profile")
        if not fields or "site_id" not in fields or profile_column not in fields:
            raise ValueError("CSV requires site_id and the configured profile column")
        config = _merge(source, extension)
        profiles = config.get("devices", {})
        for rule in rules:
            unknown = set(rule) - {"name", "profile", "rank_lte", "tags", "replace_with", "connected", "rank"}
            if unknown or not rule.get("profile") or rule.get("replace_with") not in profiles:
                raise ValueError(f"Invalid replacement rule: {rule.get('name', rule)}; unknown fields: {sorted(unknown)}")
            if rule["profile"] not in profiles:
                raise ValueError(f"Unknown source profile: {rule['profile']}")
            if "rank_lte" in rule and (isinstance(rule["rank_lte"], bool) or not isinstance(rule["rank_lte"], (float, int)) or not isfinite(rule["rank_lte"])):
                raise ValueError("rank_lte must be a finite numeric threshold")
            if not isinstance(rule.get("tags", {}), dict) or set(rule.get("tags", {})) - set(fields):
                raise ValueError("Rule tags must reference existing CSV columns")
            if "connected" in rule and not isinstance(rule["connected"], bool):
                raise ValueError("connected action must be a boolean")
            if "rank" in rule:
                value = rule["rank"]
                if value != "copy" and (isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value)):
                    raise ValueError('rank action must be "copy" or a finite number')
        needs_rank = any("rank_lte" in rule or rule.get("rank") == "copy" for rule in rules)
        result = result if result is not None else self.source.load_result() if needs_rank or installed else None
        ranks = {}
        if needs_rank or installed:
            if not isinstance(result, dict) or not result.get("planned_nodes"):
                raise ValueError("Rank conditions/copy actions require a solved source result")
            bundle = load_scenario_bundle(self.source.scenario_path)
            expected = {i["interface_id"] for s in bundle["sites"] for i in s["interfaces"] if i.get("enabled", True)}
            if {n["node_id"] for n in result.get("rpl_nodes", [])} != expected:
                raise ValueError("Source result does not match source interface IDs")
            for node in result["planned_nodes"]:
                ranks.setdefault(node["site_id"], []).append(node.get("rank"))
        identity = {
            "source_project": str(self.source.path),
            "source_scenario_sha256": hashlib.sha256(self.source.scenario_path.read_bytes()).hexdigest(),
            "source_csv_sha256": hashlib.sha256(source_csv.read_bytes()).hexdigest(),
            "source_result_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() if result is not None else None,
            "extension_sha256": hashlib.sha256(self.extension_path.read_bytes()).hexdigest(),
        }
        if existing and not overwrite:
            report_path = self.path / "replacement_report.json"
            if self.scenario_path.exists() and (self.path / "nodes.csv").exists() and report_path.exists():
                previous = json.loads(report_path.read_text(encoding="utf-8"))
                if all(previous.get(key) == value for key, value in identity.items()):
                    return previous
            raise FileExistsError("Prepared inputs changed or the study is incomplete. Call prepare(overwrite=True) to regenerate in this project; its feature cache will be retained.")
        report = []
        for row in rows:
            old = row[profile_column]
            matched = None
            values = ranks.get(row["site_id"], [])
            rank = values[0] if len(values) == 1 else None
            for index, rule in enumerate(rules):
                if old != rule["profile"]:
                    continue
                if any(str(row[key]).strip().lower() != str(value).strip().lower() for key, value in rule.get("tags", {}).items()):
                    continue
                if "rank_lte" in rule:
                    if len(values) > 1:
                        raise ValueError(f"Ambiguous rank for {row['site_id']}; use profile/tag rules for multi-interface devices")
                    if rank is None or not isfinite(float(rank)) or float(rank) > rule["rank_lte"]:
                        continue
                matched = (index, rule)
                break
            if matched:
                index, rule = matched
                new = rule["replace_with"]
                profile = profiles[new]
                row[profile_column] = new
                # Materialize profile defaults: blank CSV values override profiles
                # in the current importer, so merely blanking these is insufficient.
                for key, default in (("connected", False), ("can_route", False), ("kind", "field")):
                    if key not in fields:
                        fields.append(key)
                    row[key] = profile.get(key, default)
                if "rank" not in fields:
                    fields.append("rank")
                connected = rule.get("connected", _bool_value(profile.get("connected"), False))
                row["connected"] = connected
                if rule.get("rank") == "copy":
                    if len(values) != 1 or rank is None or not isfinite(float(rank)):
                        raise ValueError(f"Cannot copy a finite unambiguous source rank for {row['site_id']}")
                    initial_rank = float(rank)
                elif "rank" in rule:
                    initial_rank = float(rule["rank"])
                else:
                    initial_rank = _rank_value(profile.get("rank"), connected)
                if connected and (initial_rank is None or not isfinite(initial_rank)):
                    raise ValueError(f"Connected node {row['site_id']} requires a finite initial rank")
                if not connected and initial_rank is not None:
                    raise ValueError(f"Unconnected node {row['site_id']} must start with infinite rank; omit its rank action")
                row["rank"] = initial_rank if initial_rank is not None else ""
            report.append({"site_id": row["site_id"], "old_profile": old,
                           "new_profile": row[profile_column], "rank": float(rank) if rank is not None and isfinite(float(rank)) else None,
                           "initial_connected": _bool_value(row.get("connected"), False),
                           "initial_rank": _rank_value(row.get("rank"), _bool_value(row.get("connected"), False)),
                           "rank_action": matched[1].get("rank", "profile_default") if matched else None,
                           "rule_index": matched[0] if matched else None,
                           "rule": matched[1].get("name", str(matched[0])) if matched else None})
        config["instances"] = {**instances, "source": "nodes.csv"}
        if installed:
            # The source solution is the installed network. Never regenerate
            # geometric candidates, even after disabling an upstream interface.
            config["net"] = {"connectivity": [], "candidate_edges": []}
        for name, profile in profiles.items():
            for interface in profile.get("interfaces", []):
                if interface not in config.get("interfaces", {}):
                    raise ValueError(f"Profile {name}: unknown interface {interface}")
                antenna = config["interfaces"][interface].get("antenna_id")
                if antenna not in config.get("antennas", {}):
                    raise ValueError(f"Interface {interface}: unknown antenna {antenna}")
        cache = self.source.load_features() if self.source.features_path.exists() else None
        if cache is not None and (cache.get("format") != "cisei-feature-cache" or cache.get("version") != 1 or not isinstance(cache.get("entries"), list)):
            raise ValueError("Unsupported source feature cache")
        if overwrite and self.features_path.exists():
            retained = self.load_features()
            if retained.get("format") != "cisei-feature-cache" or retained.get("version") != 1 or not isinstance(retained.get("entries"), list):
                raise ValueError("Unsupported destination feature cache; refusing to overwrite it")
            # Keep destination-only extractions; its entries win on shared keys.
            records = {}
            for entry in (cache or {"entries": []})["entries"] + retained["entries"]:
                key = json.dumps([entry["source"].rstrip("/"), entry["request"]], sort_keys=True)
                records[key] = entry
            cache = {"format": "cisei-feature-cache", "version": 1, "entries": list(records.values())}
        provenance = {**identity, "rules": rules, "replacements": report}
        with TemporaryDirectory(dir=self.path, prefix=".prepare-") as temporary:
            stage = Path(temporary)
            (stage / "scenario.toml").write_text(tomlkit.dumps(config), encoding="utf-8")
            with (stage / "nodes.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            resolved = load_scenario_bundle(stage / "scenario.toml")
            if installed:
                from .topology import installed_edges
                edges, excluded = installed_edges(result, resolved, bundle)
                config["net"]["candidate_edges"] = edges
                provenance["topology"] = {
                    "mode": "installed", "retained_edges": len(edges),
                    "excluded_edges": excluded,
                }
                (stage / "scenario.toml").write_text(tomlkit.dumps(config), encoding="utf-8")
                load_scenario_bundle(stage / "scenario.toml")
            if cache is not None:
                (stage / "features.json").write_text(json.dumps(cache, allow_nan=False), encoding="utf-8")
            if overwrite and existing:
                backup = self.path / "preparation_backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup.mkdir(parents=True)
                stale = [self.result_path, self.path / "candidate_network.html", self.path / "solution_network.html", self.path / "evaluation.html", self.path / "metrics.html"]
                for old in [self.scenario_path, self.path / "nodes.csv", self.path / "replacement_report.json", *stale]:
                    if old.exists():
                        shutil.copy2(old, backup / old.name)
                provenance["previous_study_backup"] = str(backup)
            (stage / "replacement_report.json").write_text(json.dumps(provenance, indent=2, allow_nan=False), encoding="utf-8")
            for file in stage.iterdir():
                file.replace(self.path / file.name)
            if overwrite and existing:
                for old in stale:
                    old.unlink(missing_ok=True)
        return provenance
