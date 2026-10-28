"""Apply E08 to installed connections; no path selection or network requests."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math

from .baseline import BaselineProject, _save
from .baseline_features import request_key, validate_cache
from .rssi_model import ExpressionModel, classify_rssi, evaluate_model


def configuration_hash(project):
    text = json.dumps(project.export_scenario(), sort_keys=True, allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def evaluate_links(project, threshold):
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
        raise ValueError("RSSI threshold must be finite")
    baseline = BaselineProject(project.path)
    document = json.loads(baseline.enriched_features_path.read_text(encoding="utf-8"))
    validate_cache(document)
    edges = project._installed_links()
    by_id = {e.get("link_id"): e for e in edges}
    if len(by_id) != len(edges) or None in by_id:
        raise ValueError("Installed links require unique link_id values")
    config = project.export_scenario()
    endpoints = {i["interface_id"]: (site, i, next(d for d in site["devices"] if d["device_id"] == i["device_id"]))
                 for site in config["sites"] for i in site["interfaces"]}
    observed = {}
    for entry in document["entries"]:
        for sample in entry.get("observations", []):
            link_id = sample.get("link_id")
            if link_id not in by_id or by_id[link_id].get("kind") == "internal":
                raise ValueError("Observation references an unknown or internal installed link")
            if link_id in observed:
                raise ValueError("Select one observation per installed link before path diagnosis")
            for side, iid in (("tx", by_id[link_id]["src"]), ("rx", by_id[link_id]["dst"])):
                site, interface, device = endpoints[iid]
                if (sample[side]["name"] != site["site_id"]
                        or sample[side].get("interface_id", iid) != iid
                        or entry["request"][side] != [site["lat"], site["lon"]]
                        or entry["request"][f"{side}_ha"] != device["mount_height_m"]
                        or entry["request"]["freq_mhz"] != interface["freq_mhz"]):
                    raise ValueError("Enriched observation conflicts with installed link direction or configuration")
            observed[link_id] = sample
    model = ExpressionModel.load(baseline.model_path)
    # Keep the existing E08 evaluation/report and its four measured categories.
    if observed:
        report = baseline.evaluate(rssi_threshold_dbm=threshold, save=False)
    else:
        report = {"threshold_dbm": threshold, "links": [],
                  "categories": dict.fromkeys(("investigate", "satisfactory", "measured_satisfactory", "review_design"), 0),
                  "summary": evaluate_model(model, []), "selected_records": 0, "excluded_predictions": 0,
                  "data_sha256": hashlib.sha256(baseline.data_path.read_bytes()).hexdigest(),
                  "model_sha256": hashlib.sha256(baseline.model_path.read_bytes()).hexdigest()}
    evaluated = {row["link_id"]: row for row in report["links"]}
    entries = {}
    for entry in document["entries"]:
        identity = request_key(entry["source"], entry["request"])[1:]
        entries.setdefault(identity, []).append(entry)
    installed = []
    for edge in edges:
        row = {**deepcopy(edge), "predicted": None, "measured": None, "residual": None}
        if edge.get("kind") == "internal":
            row["category"] = "internal"
        elif edge["link_id"] in evaluated:
            row.update(evaluated[edge["link_id"]])
        elif edge["link_id"] in observed:
            row["category"] = "unassessed"
            row["measured"] = observed[edge["link_id"]]["measures"]["rssi"]
        else:
            record, request = {}, {"options": {}}
            for side, iid in (("tx", edge["src"]), ("rx", edge["dst"])):
                site, interface, device = endpoints[iid]
                antenna = config["antennas"][interface["antenna_id"]]
                request[side] = [site["lat"], site["lon"]]
                request[f"{side}_ha"] = device["mount_height_m"]
                request["freq_mhz"] = interface["freq_mhz"]
                record[side] = {"name": site["site_id"], "pw": interface["tx_power_dbm"],
                                "ant_gain": antenna["gain_dbi"], "pos": request[side],
                                "ant_height": request[f"{side}_ha"]}
            matches = entries.get(request_key("local", request)[1:], [])
            if len(matches) > 1:
                raise ValueError("Ambiguous feature entries for installed link")
            if matches:
                record["features"] = matches[0]["features"]
                record["freq_mhz"] = request["freq_mhz"]
                row["predicted"] = model.predict(record)
            row["category"] = classify_rssi(row["predicted"], None, threshold)
        installed.append(row)
    report.update(installed_links=installed, installed_categories=dict(Counter(r["category"] for r in installed)),
                  topology_sha256=hashlib.sha256(project.result_path.read_bytes()).hexdigest(),
                  scenario_sha256=configuration_hash(project))
    _save(project.path / "evaluation.json", report)
    return report


def load_diagnosis(project):
    baseline = BaselineProject(project.path)
    report = json.loads((project.path / "evaluation.json").read_text(encoding="utf-8"))
    baseline._check_result(report)
    if (report.get("topology_sha256") != hashlib.sha256(project.result_path.read_bytes()).hexdigest()
            or report.get("scenario_sha256") != configuration_hash(project)):
        raise ValueError("Installed topology changed; rerun evaluate_links()")
    return report


def path_diagnosis(project, interface_id, report):
    path = project.path_to_backbone(interface_id)
    edges = {frozenset((e["src"], e["dst"])): e for e in report["installed_links"]}
    links = [deepcopy(edges[frozenset((src, dst))]) for src, dst in zip(path, path[1:])]
    return {"path": path, "backbone": path[-1], "hop_count": len(links),
            "categories": dict(Counter(e["category"] for e in links)), "links": links}
