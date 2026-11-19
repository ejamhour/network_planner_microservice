"""Profile policy, actual RF orientation and reusable effective placement output."""
from copy import deepcopy
from pathlib import Path

import pytest
import tomlkit

from cisei_lib.planners import PlanningScenario, CellPlanner
from cisei_lib.project_io.placements import apply_placement_document, effective_placements, save_placements
from test_project_storage import storage, sdk_for
from test_project_runtime import runtime_session


def setup_config():
    return {
        "scenario": {"working_crs": "EPSG:31982"},
        "antenna_library": {"antennas": [{"id": "same-model", "type": "directional",
            "patterns": [{"frequency_mhz": 900, "max_gain_dbi": 12,
                          "attenuation": {"0": {"0": 0}, "180": {"0": 20}}}]}]},
        "antennas": {
            "automatic": {"kind": "sector", "model_id": "same-model", "auto_tune": True,
                          "azimuth_deg": 0, "beamwidth_deg": 20},
            "fixed": {"kind": "sector", "model_id": "same-model", "auto_tune": False,
                      "azimuth_deg": 180, "beamwidth_deg": 20},
            "omni": {"kind": "omni", "gain_dbi": 3}},
        "interfaces": {"sector": {"tech": "lte", "freq_mhz": 900, "tx_power_dbm": 30, "antenna_id": "fixed"},
                       "client": {"tech": "lte", "freq_mhz": 900, "tx_power_dbm": 20, "antenna_id": "omni"}},
        "devices": {"tower": {"interfaces": ["sector", "sector"], "can_route": True, "mount_height_m": 7},
                    "terminal": {"interfaces": ["client"], "mount_height_m": 7}}}


def source_scenario():
    scenario = PlanningScenario.from_config_dict(setup_config())
    scenario.add_node_instances([
        {"site_id": "tower", "device_profile": "tower", "x": 500000, "y": 7200000, "connected": True},
        {"site_id": "client", "device_profile": "terminal", "x": 500100, "y": 7200000}])
    return scenario


def input_document():
    return {"placements": {"tower:d0:i0": {"antenna_id": "automatic", "azimuth_deg": 0,
                                              "downtilt_deg": 2, "mount_height_m": 12}}}


def test_profile_selection_preview_apply_rf_and_placement_roundtrip(tmp_path):
    original = source_scenario()
    config = original.to_config_dict()
    apply_placement_document(config, input_document())
    cell = CellPlanner(PlanningScenario.from_config_dict(config))
    cell.build_primary_candidates()
    before = effective_placements(cell.graph.scenario)
    assert before["placements"]["tower:d0:i0"]["antenna_id"] == "automatic"
    old_graph = cell.graph
    nodes = {n.node_id: n for n in old_graph.rpl_nodes}
    old_gain = old_graph._link_antenna_gain(nodes["tower:d0:i0"], nodes["client:d0:i0"],
        old_graph.antenna_catalog[nodes["tower:d0:i0"].extra["antenna_id"]])
    preview = cell.tune_sectors(apply_rotation=False)
    assert len(preview["rotation"]) == 1
    assert effective_placements(cell.graph.scenario) == before
    cell.tune_sectors(apply_rotation=True)
    after = effective_placements(cell.graph.scenario)
    tuned = after["placements"]["tower:d0:i0"]
    assert tuned["azimuth_deg"] != 0 and tuned["downtilt_deg"] == 2
    assert tuned["mount_height_m"] == 12
    assert after["placements"]["tower:d0:i1"] == before["placements"]["tower:d0:i1"]
    nodes = {n.node_id: n for n in cell.graph.rpl_nodes}
    antenna = cell.graph.antenna_catalog[nodes["tower:d0:i0"].extra["antenna_id"]]
    assert cell.graph._link_antenna_gain(nodes["tower:d0:i0"], nodes["client:d0:i0"], antenna) > old_gain + 5
    path = save_placements(cell.graph.scenario, tmp_path / "output.toml")
    exported = tomlkit.parse(path.read_text()).unwrap()
    assert len(exported["placements"]) == 3
    assert all(not fields["antenna_id"].startswith("__placement_") for fields in exported["placements"].values())
    restored = original.to_config_dict()
    apply_placement_document(restored, exported)
    assert effective_placements(PlanningScenario.from_config_dict(restored)) == after
    assert original.antenna_profiles["fixed"].azimuth_deg == 180
    assert original.antenna_profiles["automatic"].azimuth_deg == 0
    cell.tune_sectors(apply_rotation=True)
    assert effective_placements(cell.graph.scenario) == after  # No doubled rotation.


def test_missing_auto_tune_stays_fixed_and_invalid_policy_is_rejected():
    scenario = source_scenario()
    cell = CellPlanner(scenario)
    before = effective_placements(scenario)
    assert len(cell.tune_sectors(apply_rotation=True)["rotation"]) == 0
    assert effective_placements(cell.graph.scenario) == before
    config = setup_config()
    config["antennas"]["fixed"].pop("auto_tune")
    assert PlanningScenario.from_config_dict(config).antenna_profiles["fixed"].auto_tune is False
    config["antennas"]["fixed"]["auto_tune"] = "unknown"
    with pytest.raises(ValueError):
        PlanningScenario.from_config_dict(config)


def make_project(sdk, placement_file="antenna_placements.toml", *, metric_spec=None):
    project = sdk.open_project(sdk.workspace / "projects/tuning")
    document = tomlkit.parse(source_scenario().to_toml())
    document["files"] = {"antenna_placements": placement_file}
    if metric_spec is not None:
        document["metrics"] = {"lte": {"spec": metric_spec}}
    project.scenario_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    source = project.path / placement_file
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(tomlkit.dumps(input_document()), encoding="utf-8")
    project.sync()
    assert project.validate()["valid"]
    assert project.build()["valid"]
    project.activate()
    project.cell.build_candidate_edges()
    return project, source


def test_sdk_cell_policy_and_separate_output_do_not_rewrite_inputs(runtime_session, tmp_path):
    store, api, _ = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project, source = make_project(sdk)
    original = source.read_bytes()
    preview = project.cell.tune_sectors(apply_rotation=False)
    assert preview["valid"], preview
    before = project.save_placements()
    assert tomlkit.parse(before["content"])["placements"]["tower:d0:i0"]["azimuth_deg"] == 0
    assert project.cell.tune_sectors()["valid"]
    saved = project.save_placements()
    output = tomlkit.parse(Path(saved["local_path"]).read_text()).unwrap()
    assert output["placements"]["tower:d0:i0"]["azimuth_deg"] != 0
    assert output["placements"]["tower:d0:i1"]["azimuth_deg"] == 180
    project.cell.build_candidate_edges()
    assert tomlkit.parse(project.export_placements()["content"]).unwrap() == output
    assert source.read_bytes() == original
    server = store.root / project.project_id / "workspace" / project.project_path
    assert (server / "antenna_placements.toml").read_bytes() == original
    assert (server / saved["file"]).read_text() == saved["content"]
    assert project.sync()["inputs_changed"] is False  # Output is not implicitly uploaded/promoted.
    project.close()


def test_generated_output_refuses_to_overwrite_synced_input(runtime_session, tmp_path):
    _, api, _ = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project, source = make_project(sdk, "outputs/antenna_placements.toml")
    original = source.read_bytes()
    report = project.save_placements()
    assert not report["valid"] and report["issues"][0]["code"] == "placement_output_is_input"
    assert source.read_bytes() == original
    project.close()
