import json
import shutil
from pathlib import Path

import pytest
import tomlkit

from cisei_lib.planners import GraphPlanner, PlanningScenario
from cisei_planning_sdk import ExtendedPlanningProject, PlanningProject, load_scenario_bundle
from cisei_planning_sdk.dependencies import network_dependencies

PROJECTS = Path(__file__).resolve().parents[1] / "examples/projects"


@pytest.fixture
def installed_study(tmp_path):
    source = PlanningProject(tmp_path / "source")
    for name in ("scenario.toml", "nodes.csv"):
        shutil.copy2(PROJECTS / "e07_ge_mesh_original" / name, source.path / name)
    graph = GraphPlanner(PlanningScenario.from_config_dict(load_scenario_bundle(source.scenario_path)))
    edges = graph.build_candidate_edges_from_rules()
    graph.run_rpl({edge: 1.0 for edge in edges})
    source.save_result(graph.to_result_dict())
    target = ExtendedPlanningProject(tmp_path / "target", source=source, extension_file="extension_fixed_links.toml")
    shutil.copy2(PROJECTS / "e07_ge_mesh_redundancia/extension_fixed_links.toml", target.extension_path)
    return source, target


def test_installed_links_dual_profile_and_disabled_upstream(installed_study):
    source, target = installed_study
    before = {p.name: p.read_bytes() for p in source.path.iterdir()}
    report = target.prepare()
    bundle = load_scenario_bundle(target.scenario_path)
    sites = {s["site_id"]: s for s in bundle["sites"]}
    assert [i["tech"] for i in sites["barigui"]["interfaces"]] == ["ge_mesh", "cellular_public"]
    assert all(i["enabled"] for i in sites["barigui"]["interfaces"])
    assert sites["barigui"]["devices"][0]["connected"] is True
    assert sites["barigui"]["devices"][0]["rank"] == 1.0
    assert sites["torre"]["interfaces"][0]["enabled"] is False
    assert bundle["net"]["connectivity"] == []
    original = source.load_result()
    expected = {frozenset((e["src"], e["dst"])) for e in original["planned_edges"]
                if "torre:d0:i0" not in (e["src"], e["dst"])}
    graph = GraphPlanner(PlanningScenario.from_config_dict(bundle))
    edges = graph.build_candidate_edges_from_rules()
    assert {frozenset(e) for e in edges} == expected
    assert report["topology"]["excluded_edges"]
    graph.run_rpl({edge: 1.0 for edge in edges})
    result = graph.to_result_dict()
    planned = {r["node_id"]: r for r in result["planned_nodes"]}
    descendants = network_dependencies(original)["interfaces"]["barigui:d0:i0"]["descendants"]
    parents = {r["node_id"]: r.get("parent") for r in original["planned_nodes"]}
    for iid in descendants:
        assert planned[iid]["parent"] == parents[iid]
    assert planned["barigui:d0:i1"]["connected"] is True
    assert not planned["barigui:d0:i1"].get("parent")
    assert "torre:d0:i0" not in planned
    assert "tenis:d0:i0" not in planned  # Cannot invent another GE parent.
    assert {p.name: p.read_bytes() for p in source.path.iterdir()} == before
    assert target.prepare() == report


def test_reordered_profile_cannot_change_installed_interface_identity(installed_study):
    source, target = installed_study
    config = tomlkit.parse(target.extension_path.read_text()).unwrap()
    config["devices"]["ge_mesh_cellular_gateway"]["interfaces"].reverse()
    target.extension_path.write_text(tomlkit.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="replacement changes tech"):
        target.prepare()
    assert not target.scenario_path.exists()
    assert not (target.path / "nodes.csv").exists()


def test_source_links_and_parent_assignments_must_agree(installed_study):
    source, target = installed_study
    result = source.load_result()
    result["planned_edges"].pop()
    source.save_result(result)
    with pytest.raises(ValueError, match="do not match"):
        target.prepare()
    assert not target.scenario_path.exists()


def test_manual_only_graph_does_not_generate_radio_candidates(installed_study):
    _, target = installed_study
    target.prepare()
    graph = GraphPlanner(PlanningScenario.from_config_dict(load_scenario_bundle(target.scenario_path)))
    initial = graph.candidate_edges[:]
    assert graph.build_candidate_edges_from_rules() == initial
    assert graph.build_candidate_edges_from_rules(preserve_existing=False) == []
    assert graph.candidate_graph().number_of_edges() == 0
