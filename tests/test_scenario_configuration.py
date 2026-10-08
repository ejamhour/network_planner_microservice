from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes_planning import router
from cisei_lib.planners import GraphPlanner, PlanningScenario
from cisei_planning_sdk import PlanningClient, PlanningProject, ScenarioSession, init_workspace, load_scenario_bundle

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def setup(tmp_path):
    app = FastAPI()
    app.include_router(router)
    runtime = SimpleNamespace(planning_scenarios={}, planning_planners={})
    app.state.runtime = runtime
    init_workspace(tmp_path)
    with TestClient(app) as api:
        sdk = PlanningClient(
            "http://testserver", workspace=tmp_path, auto_register=False,
        )

        def request(method, path, **kwargs):
            payload = api.request(method, path, **kwargs).json()
            if payload["status"] != "OK":
                raise RuntimeError(payload["data"])
            return payload

        sdk._request = request
        config = load_scenario_bundle(EXAMPLES / "projects/example1_LTE_planning_basic/scenario.toml")
        project = PlanningProject(tmp_path / "projects/custom")
        project.save_scenario(config)
        session = sdk.open_project(project, planner="cell")
        assert session.project is sdk.project
        sdk.define_scenario(name="other", scenario=config, planner="cell")
        yield api, runtime, session


def test_upload_local_files_applies_gain_and_metric_and_invalidates(setup):
    api, runtime, session = setup
    root = session.client.workspace
    antenna = root / "antennas/mine.yaml"
    metric = root / "metrics/mine.toml"
    antenna.write_bytes((EXAMPLES / "antennas/custom_omni.yaml").read_bytes())
    metric.write_bytes((EXAMPLES / "metrics/custom_metric.toml").read_bytes())
    session.build_candidate_edges()
    session.result = {"old": True}
    session.upload_configuration(
        antenna_library="mine.yaml",
        antennas={"lte_client_omni": {"model_id": "custom_omni"}},
        metrics={"lte": "mine.toml"},
    )
    assert "custom" not in runtime.planning_planners
    assert session.candidate_edges is None and session.result is None
    session.build_candidate_edges()
    planner = runtime.planning_planners["custom"]
    assert planner.antenna_planner.model("custom_omni").pattern_for_frequency(915).gain_dbi() == 6
    assert planner.metric_functions_by_tech["lte"]({"features": {"dist_m": 1000}}) == 2
    assert planner.antenna_catalog["lte_client_omni"].model_id == "custom_omni"
    other = GraphPlanner(runtime.planning_scenarios["other"])
    assert "custom_omni" not in other.antenna_planner.models
    # Export/reload must preserve the custom library, rather than silently use defaults.
    restored = GraphPlanner(PlanningScenario.from_config_dict(session.export_scenario()))
    assert "custom_omni" in restored.antenna_planner.models


@pytest.mark.parametrize("changes", [
    {"metrics": {"lte": {"spec": "invalid metric"}}},
    {"antenna_library": {"antennas": [{"id": "bad", "patterns": [{"frequency_mhz": "bad"}]}]}},
    {"antennas": {"lte_client_omni": {"model_id": "missing_model"}}},
])
def test_invalid_upload_preserves_live_state(setup, changes):
    _, runtime, session = setup
    session.build_candidate_edges()
    original = runtime.planning_scenarios["custom"]
    planner = runtime.planning_planners["custom"]
    cached = session.candidate_edges
    with pytest.raises(RuntimeError):
        session.upload_configuration(**changes)
    assert runtime.planning_scenarios["custom"] is original
    assert runtime.planning_planners["custom"] is planner
    assert session.candidate_edges is cached
