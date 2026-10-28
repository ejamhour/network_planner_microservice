"""Interface activation persistence, overrides and planning participation."""
import pytest

from cisei_lib.planners import PlanningScenario
from cisei_lib.planners.planner_classes import RadioInterface, RadioInterfacePattern
from cisei_planning_sdk.local_io import load_scenario_bundle, save_scenario


@pytest.fixture
def config():
    return {
        "scenario": {"working_crs": "EPSG:31982"},
        "antennas": {"omni": {"kind": "omni", "gain_dbi": 3.0}},
        "interfaces": {"mesh": {
            "tech": "wisun", "freq_mhz": 915.0,
            "tx_power_dbm": 23.0, "antenna_id": "omni",
        }},
        "devices": {"router": {"interfaces": ["mesh"]}},
        "instances": {"source": "nodes.csv"},
    }


def write_bundle(tmp_path, config):
    path = tmp_path / "scenario.toml"
    save_scenario(config, path)
    (tmp_path / "nodes.csv").write_text(
        "site_id,device_profile,x,y\nA,router,670000,7180000\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("enabled", [True, False, None])
def test_profile_expansion_and_embedded_round_trip(tmp_path, config, enabled):
    if enabled is not None:
        config["interfaces"]["mesh"]["enabled"] = enabled
    path = write_bundle(tmp_path, config)
    expected = True if enabled is None else enabled
    bundle = load_scenario_bundle(path)
    assert bundle["sites"][0]["interfaces"][0]["enabled"] is expected

    # SDK expansion and direct library CSV loading must agree.
    for scenario in (
        PlanningScenario.from_config_dict(bundle),
        PlanningScenario.from_toml(path),
    ):
        interface = scenario.site_nodes["A"].interfaces[0]
        assert interface.enabled is expected
        assert "enabled" not in interface.extra
        embedded = scenario.to_config_dict()
        assert embedded["sites"][0]["interfaces"][0]["enabled"] is expected
        exported = tmp_path / "embedded.toml"
        scenario.to_toml(exported)
        restored = PlanningScenario.from_toml(exported)
        assert restored.site_nodes["A"].interfaces[0].enabled is expected
        assert load_scenario_bundle(exported)["sites"][0]["interfaces"][0]["enabled"] is expected


def test_legacy_embedded_interface_defaults_to_enabled(tmp_path, config):
    bundle = load_scenario_bundle(write_bundle(tmp_path, config))
    del bundle["sites"][0]["interfaces"][0]["enabled"]
    scenario = PlanningScenario.from_config_dict(bundle)
    assert scenario.site_nodes["A"].interfaces[0].enabled is True


@pytest.mark.parametrize("invalid", ["false", 0, 1, None, [], {}])
def test_concrete_interface_rejects_non_boolean(invalid):
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        RadioInterface("i", "d", "s", "wisun", 915.0, 23.0, "omni", enabled=invalid)


@pytest.mark.parametrize("invalid", ["false", 0, 1])
def test_profile_rejects_non_boolean_in_both_loaders(tmp_path, config, invalid):
    config["interfaces"]["mesh"]["enabled"] = invalid
    path = write_bundle(tmp_path, config)
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        load_scenario_bundle(path)
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        PlanningScenario.from_toml(path)


def test_embedded_instance_overrides_profile_default(tmp_path, config):
    bundle = load_scenario_bundle(write_bundle(tmp_path, config))
    bundle["sites"][0]["interfaces"][0]["enabled"] = False
    scenario = PlanningScenario.from_config_dict(bundle)
    assert scenario.interface_profiles["mesh"].get("enabled", True) is True
    assert scenario.site_nodes["A"].interfaces[0].enabled is False


def test_pattern_preserves_disabled_state():
    interface = RadioInterfacePattern(enabled=False).instantiate("A", 0)
    assert interface.enabled is False
    assert interface.to_dict()["enabled"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_overrides_apply_after_csv_and_survive_round_trip(tmp_path, config, enabled):
    config["interfaces"]["mesh"]["enabled"] = not enabled
    config["overrides"] = {"interfaces": {"A:d0:i0": {"enabled": enabled}}}
    path = write_bundle(tmp_path, config)
    bundle = load_scenario_bundle(path)
    assert bundle["sites"][0]["interfaces"][0]["enabled"] is enabled
    for scenario in (PlanningScenario.from_toml(path),
                     PlanningScenario.from_config_dict(bundle)):
        assert scenario.site_nodes["A"].interfaces[0].enabled is enabled
        assert scenario.interface_profiles["mesh"]["enabled"] is not enabled
        assert scenario.to_config_dict(include_sites=False)["overrides"] == config["overrides"]
        embedded = tmp_path / "override_embedded.toml"
        scenario.to_toml(embedded)
        assert PlanningScenario.from_toml(embedded).site_nodes["A"].interfaces[0].enabled is enabled
        assert load_scenario_bundle(embedded)["sites"][0]["interfaces"][0]["enabled"] is enabled


def test_override_only_changes_selected_instance(tmp_path, config):
    config["overrides"] = {"interfaces": {"A:d0:i0": {"enabled": False}}}
    path = write_bundle(tmp_path, config)
    with (tmp_path / "nodes.csv").open("a", encoding="utf-8") as handle:
        handle.write("B,router,671000,7180000\n")
    for scenario in (PlanningScenario.from_toml(path),
                     PlanningScenario.from_config_dict(load_scenario_bundle(path))):
        assert scenario.site_nodes["A"].interfaces[0].enabled is False
        assert scenario.site_nodes["B"].interfaces[0].enabled is True


@pytest.mark.parametrize("overrides, message", [
    ([], "overrides must be a table"),
    ({"devices": {}}, "only interfaces"),
    ({"interfaces": []}, "overrides.interfaces must be a table"),
    ({"interfaces": {"": {"enabled": False}}}, "non-empty string"),
    ({"interfaces": {"A:d0:i0": False}}, "only enabled"),
    ({"interfaces": {"A:d0:i0": {"azimuth_deg": 90}}}, "only enabled"),
    ({"interfaces": {"A:d0:i0": {"enabled": "false"}}}, "enabled must be a boolean"),
    ({"interfaces": {"A:d0:i0": {"enabled": 0}}}, "enabled must be a boolean"),
    ({"interfaces": {"missing": {"enabled": False}}}, "unknown interface"),
])
def test_invalid_overrides_rejected_by_both_loaders(tmp_path, config, overrides, message):
    config["overrides"] = overrides
    path = write_bundle(tmp_path, config)
    with pytest.raises(ValueError, match=message):
        load_scenario_bundle(path)
    with pytest.raises(ValueError, match=message):
        PlanningScenario.from_toml(path)


def test_embedded_override_precedence_and_input_unchanged(tmp_path, config):
    from copy import deepcopy
    bundle = load_scenario_bundle(write_bundle(tmp_path, config))
    bundle["overrides"] = {"interfaces": {"A:d0:i0": {"enabled": False}}}
    original = deepcopy(bundle)
    scenario = PlanningScenario.from_config_dict(bundle)
    assert scenario.site_nodes["A"].interfaces[0].enabled is False
    assert bundle == original


def test_unknown_override_rejected_for_missing_csv(tmp_path, config):
    config["overrides"] = {"interfaces": {"A:d0:i0": {"enabled": False}}}
    path = tmp_path / "scenario.toml"
    save_scenario(config, path)
    with pytest.raises(ValueError, match="unknown interface"):
        PlanningScenario.from_toml(path)


def test_unexpanded_library_load_preserves_overrides(tmp_path, config):
    config["overrides"] = {"interfaces": {"A:d0:i0": {"enabled": False}}}
    scenario = PlanningScenario.from_toml(write_bundle(tmp_path, config), load_instances=False)
    assert not scenario.site_nodes
    assert scenario.to_config_dict()["overrides"] == config["overrides"]
    scenario.load_instance_source(tmp_path)
    assert scenario.site_nodes["A"].interfaces[0].enabled is False


@pytest.fixture
def active_scenario(tmp_path, config):
    config["interfaces"]["unused"] = {**config["interfaces"]["mesh"], "enabled": False}
    config["devices"]["router"].update(
        interfaces=["mesh", "unused"], can_route=True, mount_height_m=7.0,
    )
    config["net"] = {"connectivity": [
        {"kind": "rf", "tech": "wisun", "source": "relay", "destination": "any"},
        {"kind": "internal", "metric": 0.0},
    ]}
    path = write_bundle(tmp_path, config)
    (tmp_path / "nodes.csv").write_text(
        "site_id,device_profile,x,y,connected,can_route,rank\n"
        "A,router,670000,7180000,true,true,0\n"
        "B,router,671000,7180000,false,true,\n",
        encoding="utf-8",
    )
    return PlanningScenario.from_toml(path)


def test_disabled_interfaces_excluded_from_rf_internal_and_cell(active_scenario):
    from cisei_lib.planners import GraphPlanner
    from cisei_lib.planners.cell_planner import CellPlanner
    graph = GraphPlanner(active_scenario)
    assert {node.node_id for node in graph.rpl_nodes} == {"A:d0:i0", "B:d0:i0"}
    assert len(graph.build_candidate_edges_from_rules()) == 1
    assert all(attrs["kind"] == "rf" for attrs in graph.candidate_edge_attrs.values())
    cell = CellPlanner(graph, primary_tech="wisun")
    assert cell.build_primary_candidates(sector_aware=False) == [("A:d0:i0", "B:d0:i0")]
    assert set(graph.candidate_graph().nodes) == {"A:d0:i0", "B:d0:i0"}


def test_disabled_edges_rejected_in_all_entry_points(active_scenario):
    import asyncio
    from cisei_lib.planners import GraphPlanner
    graph = GraphPlanner(active_scenario)
    edge = ("A:d0:i0", "B:d0:i1")
    for operation in (
        lambda: graph.set_candidate_edges([edge]),
        lambda: graph.add_candidate_edges([edge]),
        lambda: graph.candidate_graph([edge]),
        lambda: graph.run_rpl({edge: 1.0}),
        lambda: asyncio.run(graph.compute_edge_metrics(object(), [edge])),
    ):
        with pytest.raises(ValueError, match="disabled interface"):
            operation()
    active_scenario.add_candidate_edge(*edge)
    with pytest.raises(ValueError, match="disabled interface"):
        GraphPlanner(active_scenario)


def test_only_enabled_links_request_features_and_enter_solution(active_scenario):
    import asyncio
    from cisei_lib.planners import GraphPlanner
    from cisei_lib.planners.planning_solution import PlanningSolution

    class Geo:
        calls = 0

        async def get_features(self, *args, **kwargs):
            self.calls += 1
            return {"dist_m": 1000.0}

    graph = GraphPlanner(active_scenario)
    graph.build_candidate_edges_from_rules()
    geo = Geo()
    metrics = asyncio.run(graph.compute_edge_metrics(
        geo, metric_specs_by_tech={"wisun": lambda record: 1.0},
    ))
    assert geo.calls == len(metrics) == 1
    graph.run_rpl()
    result = graph.to_result_dict()
    assert {r["node_id"] for r in result["planned_nodes"]} == {"A:d0:i0", "B:d0:i0"}
    assert len(result["interface_inventory"]) == 4
    assert sum(not row["enabled"] for row in result["interface_inventory"]) == 2
    quality = PlanningSolution.from_result(result).quality_summary(rank_threshold=2)
    assert quality["total_targets"] == quality["good_targets"] == 1
    assert quality["unserved_targets"] == 0


def test_enabled_internal_edges_still_work(active_scenario):
    from cisei_lib.planners import GraphPlanner
    active_scenario.site_nodes["B"].interfaces[1].enabled = True
    graph = GraphPlanner(active_scenario)
    graph.build_candidate_edges_from_rules()
    internal = [e for e, attrs in graph.candidate_edge_attrs.items() if attrs["kind"] == "internal"]
    assert len(internal) == 1
    assert set(internal[0]) == {"B:d0:i0", "B:d0:i1"}


def test_activation_change_invalidates_and_can_be_reenabled(active_scenario):
    from cisei_lib.planners import GraphPlanner
    graph = GraphPlanner(active_scenario)
    edges = graph.build_candidate_edges_from_rules()
    graph.run_rpl({edge: 1.0 for edge in edges})
    active_scenario.site_nodes["B"].interfaces[0].enabled = False
    result = graph.to_result_dict()
    assert graph.rpl is None and not graph.edge_metrics and not graph.edge_features
    assert not result["candidate_edges"] and not result["planned_nodes"]
    assert result["counts"]["rpl_nodes"] == 1
    active_scenario.site_nodes["B"].interfaces[0].enabled = True
    assert len(graph.build_candidate_edges_from_rules()) == 1


def test_root_only_and_all_disabled_do_not_need_features(active_scenario):
    import asyncio
    from cisei_lib.planners import GraphPlanner
    from cisei_lib.planners.planning_solution import PlanningSolution
    active_scenario.site_nodes["B"].interfaces[0].enabled = False
    graph = GraphPlanner(active_scenario)
    assert graph.build_candidate_edges_from_rules() == []
    # object() deliberately has no start/get_features/close methods.
    assert asyncio.run(graph.compute_edge_metrics(object(), manage_geo=True)) == {}
    graph.run_rpl()
    assert [row["node_id"] for row in graph.to_result_dict()["planned_nodes"]] == ["A:d0:i0"]
    active_scenario.site_nodes["A"].interfaces[0].enabled = False
    graph.build_candidate_edges_from_rules()
    graph.run_rpl()
    result = graph.to_result_dict()
    assert not result["planned_nodes"] and not result["rpl_nodes"]
    assert len(result["interface_inventory"]) == 4
    summary = PlanningSolution.from_result(result).quality_summary(rank_threshold=2)
    assert summary["total_targets"] == 0 and summary["success"] is False


def test_disconnected_active_target_remains_unserved(active_scenario):
    from cisei_lib.planners import GraphPlanner
    from cisei_lib.planners.planning_solution import PlanningSolution
    graph = GraphPlanner(active_scenario)
    graph.set_candidate_edges([])
    graph.run_rpl()
    summary = PlanningSolution.from_result(graph.to_result_dict()).quality_summary(rank_threshold=2)
    assert summary["unserved_targets"] == 1


def test_cell_refreshes_after_interface_activation_changes(active_scenario):
    from cisei_lib.planners import GraphPlanner
    from cisei_lib.planners.cell_planner import CellPlanner
    cell = CellPlanner(GraphPlanner(active_scenario), primary_tech="wisun")
    assert len(cell.build_primary_candidates(sector_aware=False)) == 1
    active_scenario.site_nodes["B"].interfaces[0].enabled = False
    assert cell.build_primary_candidates(sector_aware=False) == []
