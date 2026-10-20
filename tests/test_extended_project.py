import csv
import json
from pathlib import Path

import pytest
import tomlkit

from cisei_planning_sdk import ExtendedPlanningProject, PlanningProject, load_scenario_bundle


@pytest.fixture
def study(tmp_path):
    source = PlanningProject(tmp_path / "source")
    config = {
        "scenario": {"working_crs": "EPSG:31982"},
        "antennas": {"omni": {"kind": "omni", "height_m": 7}},
        "interfaces": {"radio": {"tech": "lte", "freq_mhz": 450, "tx_power_dbm": 20, "antenna_id": "omni"}},
        "devices": {"leaf": {"interfaces": ["radio"]}, "tower": {"interfaces": ["radio"], "connected": True, "can_route": True, "rank": 0}},
        "instances": {"source": "nodes.csv", "format": "csv", "profile_column": "device_profile"},
    }
    source.save_scenario(config)
    with (source.path / "nodes.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["site_id", "device_profile", "lat", "lon", "connected", "can_route", "rank", "region"])
        writer.writeheader()
        for site in ("a", "b", "c", "d", "root"):
            writer.writerow(dict(site_id=site, device_profile="tower" if site == "root" else "leaf", lat=-25, lon=-49,
                                 connected=site == "root", can_route=False, rank=0 if site == "root" else "", region="remote"))
    result = {"rpl_nodes": [{"node_id": f"{site}:d0:i0"} for site in ("a", "b", "c", "d", "root")],
              "planned_nodes": [{"site_id": site, "node_id": f"{site}:d0:i0", "rank": rank}
                                for site, rank in (("a", 1), ("b", 2), ("c", 2.1), ("d", None), ("root", 0))]}
    source.save_result(result)
    cache = {"format": "cisei-feature-cache", "version": 1, "entries": []}
    source.save_features(cache)
    extended = ExtendedPlanningProject(tmp_path / "derived", source=source)
    recipe = {"devices": {"hybrid": {"interfaces": ["radio"], "can_route": True},
                          "secondary": {"interfaces": ["radio"]}},
              "replacement": {"rules": [
                  {"name": "good", "profile": "leaf", "rank_lte": 2, "replace_with": "hybrid"},
                  {"name": "other", "profile": "leaf", "replace_with": "secondary"},
                  {"name": "no chaining", "profile": "hybrid", "replace_with": "secondary"},
              ]}}
    extended.extension_path.write_text(tomlkit.dumps(recipe), encoding="utf-8")
    return source, extended, result


def test_ordered_rules_defaults_cache_and_source_preservation(study):
    source, extended, _ = study
    before = {p.name: p.read_bytes() for p in source.path.iterdir()}
    report = extended.prepare()
    rows = {r["site_id"]: r for r in report["replacements"]}
    assert [rows[s]["new_profile"] for s in ("a", "b", "c", "d", "root")] == ["hybrid", "hybrid", "secondary", "secondary", "tower"]
    bundle = load_scenario_bundle(extended.scenario_path)
    assert bundle["sites"][0]["devices"][0]["can_route"] is True
    assert bundle["sites"][0]["devices"][0]["connected"] is False
    assert bundle["sites"][0]["devices"][0].get("rank") is None
    assert extended.load_features() == source.load_features()
    assert {p.name: p.read_bytes() for p in source.path.iterdir()} == before
    assert extended.prepare() == report


def test_invalid_profile_is_rejected_without_outputs(study):
    _, extended, _ = study
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"][0]["replace_with"] = "missing"
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    with pytest.raises(ValueError, match="Invalid replacement rule"):
        extended.prepare()
    assert not extended.scenario_path.exists()
    assert not (extended.path / "nodes.csv").exists()


def test_tags_and_broad_first_rule_do_not_require_result(study):
    source, extended, _ = study
    source.result_path.unlink()
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"] = [
        {"profile": "leaf", "tags": {"region": "remote"}, "replace_with": "secondary"},
        {"profile": "leaf", "replace_with": "hybrid"},
    ]
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    report = extended.prepare()
    assert all(r["new_profile"] == "secondary" for r in report["replacements"] if r["old_profile"] == "leaf")


def test_incompatible_source_result_is_rejected(study):
    _, extended, result = study
    result["rpl_nodes"].pop()
    with pytest.raises(ValueError, match="does not match"):
        extended.prepare(result=result)


def test_example3_recipe_builds_real_hybrid_graph(study):
    from cisei_lib.planners import PlanningScenario, CellPlanner, GraphPlanner
    source, extended, _ = study
    root = Path(__file__).resolve().parents[1]
    config = load_scenario_bundle(root / "examples/projects/example_2_LTE_simulation/scenario.toml")
    primary = CellPlanner(PlanningScenario.from_config_dict(config))
    primary.build_primary_candidates()
    primary_edges = {frozenset(edge) for edge in primary.graph.candidate_edges}
    cfg = PlanningScenario.from_config_dict(config).to_config_dict(include_sites=True)
    # Generic LTE rule must retain the original cell sector filter.
    cfg["net"] = {"connectivity": [{"kind": "rf", "tech": "lte", "source": "connected", "destination": "unconnected", "sector_aware": True}]}
    graph = GraphPlanner(PlanningScenario.from_config_dict(cfg))
    graph.build_candidate_edges_from_rules()
    assert {frozenset(edge) for edge in graph.candidate_edges} == primary_edges


def test_connected_copy_action_uses_result_rank_and_reports_initial_state(study):
    source, extended, _ = study
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"][0].update(connected=True, rank="copy")
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    report = extended.prepare()
    rows = {row["site_id"]: row for row in report["replacements"]}
    assert rows["a"]["initial_connected"] is True
    assert rows["a"]["initial_rank"] == 1
    assert rows["b"]["initial_rank"] == 2
    assert rows["a"]["rank_action"] == "copy"
    assert rows["c"]["initial_connected"] is False
    assert rows["c"]["initial_rank"] is None
    bundle = load_scenario_bundle(extended.scenario_path)
    assert bundle["sites"][0]["devices"][0]["connected"] is True
    assert bundle["sites"][0]["devices"][0]["rank"] == 1


def test_numeric_rank_action_overrides_result_and_profile(study):
    _, extended, _ = study
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"][0].update(connected=True, rank=0.5)
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    rows = extended.prepare()["replacements"]
    assert [row["initial_rank"] for row in rows[:2]] == [0.5, 0.5]
    assert [row["rank"] for row in rows[:2]] == [1, 2]


@pytest.mark.parametrize("actions", [
    {"connected": "true"}, {"rank": "reset"}, {"rank": True},
    {"rank": float("inf")}, {"rank": float("nan")},
    {"connected": False, "rank": 0.5}, {"connected": False, "rank": "copy"},
])
def test_invalid_actions_fail_before_writing(study, actions):
    _, extended, _ = study
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"][0].update(actions)
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    with pytest.raises(ValueError):
        extended.prepare()
    assert not extended.scenario_path.exists()
    assert not (extended.path / "nodes.csv").exists()


def test_copy_without_rank_predicate_requires_usable_source_rank(study):
    _, extended, _ = study
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"] = [{"profile": "leaf", "replace_with": "hybrid", "connected": True, "rank": "copy"}]
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    # a/b/c are finite, but d has no source route and cannot be copied.
    with pytest.raises(ValueError, match="Cannot copy.*d"):
        extended.prepare()
    assert not extended.scenario_path.exists()


def test_lower_initial_gateway_rank_is_more_attractive():
    from cisei_lib.planners.geo_rpl_agnostic import GeoRPL
    from cisei_lib.planners.planner_classes import RPLNode
    rpl = GeoRPL()
    rpl.set_nodes([RPLNode("a", connected=True, rpl_relay=True, rank=0.5),
                   RPLNode("b", connected=True, rpl_relay=True, rank=2.0),
                   RPLNode("client")])
    rpl.set_edge_metrics({("a", "client"): 1.0, ("b", "client"): 1.0})
    rpl.run_RPL()
    assert rpl.G.nodes["client"]["parent"] == "a"
    assert rpl.G.nodes["client"]["rank"] == 1.5


def test_colocated_devices_do_not_create_rf_candidates():
    from cisei_lib.planners import PlanningScenario, GraphPlanner, CellPlanner
    config = {
        "scenario": {"working_crs": "EPSG:31982"},
        "antennas": {"omni": {"kind": "omni"}},
        "interfaces": {"radio": {"tech": "lte", "freq_mhz": 450, "tx_power_dbm": 20, "antenna_id": "omni"}},
        "devices": {"root": {"interfaces": ["radio"], "connected": True, "can_route": True, "rank": 0},
                    "leaf": {"interfaces": ["radio"], "can_route": True}},
        "net": {"connectivity": [{"kind": "rf", "tech": "lte", "source": "relay", "destination": "any"}]},
    }
    scenario = PlanningScenario.from_config_dict(config)
    scenario.add_node_instances([
        {"site_id": "root", "device_profile": "root", "lat": -25, "lon": -49},
        {"site_id": "duplicate", "device_profile": "leaf", "lat": -25, "lon": -49},
        {"site_id": "neighbor", "device_profile": "leaf", "lat": -25.001, "lon": -49},
    ])
    graph = GraphPlanner(scenario)
    edges = graph.build_candidate_edges_from_rules()
    pairs = {frozenset((a.split(":")[0], b.split(":")[0])) for a, b in edges}
    assert frozenset(("root", "duplicate")) not in pairs
    assert frozenset(("root", "neighbor")) in pairs
    cell = CellPlanner(scenario)
    cell_edges = cell.build_primary_candidates()
    assert len(cell_edges) == 1


def test_overwrite_preserves_new_features_and_archives_stale_results(study):
    _, extended, _ = study
    extended.prepare()
    entry = {"source": "http://features", "request": {"tx": [1, 2], "rx": [3, 4], "tx_ha": 7, "rx_ha": 7, "freq_mhz": 915, "options": {}}, "features": {"fspl": 100}}
    extended.save_features({"format": "cisei-feature-cache", "version": 1, "entries": [entry]})
    extended.save_result({"old": "solution"})
    (extended.path / "solution_network.html").write_text("old view")
    recipe = tomlkit.parse(extended.extension_path.read_text()).unwrap()
    recipe["replacement"]["rules"][0].update(connected=True, rank=0.5)
    extended.extension_path.write_text(tomlkit.dumps(recipe))
    with pytest.raises(FileExistsError, match="overwrite=True"):
        extended.prepare()
    report = extended.prepare(overwrite=True)
    assert extended.load_features()["entries"] == [entry]
    assert not extended.result_path.exists()
    assert not (extended.path / "solution_network.html").exists()
    backup = Path(report["previous_study_backup"])
    assert json.loads((backup / "result.json").read_text()) == {"old": "solution"}
    assert report["replacements"][0]["initial_rank"] == 0.5
    assert extended.prepare() == report
