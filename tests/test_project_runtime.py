"""Saved projects share a user worker, but only one may consume its resources."""
import shutil
from pathlib import Path

import httpx
import pytest
import requests
import tomlkit
from fastapi.testclient import TestClient

from app import main_hub as hub
from app.main import app as worker
from app.routes_projects import get_project_store
from cisei_planning_sdk_v2 import ActiveProjectExists
from test_project_storage import storage, sdk_for


@pytest.fixture
def runtime_session(storage, monkeypatch):
    store, api = storage
    monkeypatch.setitem(worker.dependency_overrides, get_project_store, lambda: store)
    original = httpx.AsyncClient

    async def start(user_id):
        hub.user_registry[user_id]["port"] = 15555

    monkeypatch.setattr(hub, "start_worker_instance", start)
    monkeypatch.setattr(hub.httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.ASGITransport(app=worker), **kw))
    with TestClient(worker):
        yield store, api, worker.state.runtime


def graph_project(sdk, name, *, example_name="graph_planner_wisun"):
    project = sdk.open_project(sdk.workspace / "projects" / name)
    example = Path(__file__).resolve().parents[1] / "examples/projects" / example_name
    document = tomlkit.parse((example / "scenario.toml").read_text(encoding="utf-8"))
    filename = document["instances"].pop("source")
    document["files"] = {"nodes": filename}
    project.scenario_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    shutil.copyfile(example / filename, project.path / filename)
    project.sync()
    assert project.build()["valid"]
    return project


def test_activate_conflict_close_and_switch_preserve_saved_projects(runtime_session, tmp_path):
    store, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    first, second = graph_project(sdk, "first"), graph_project(sdk, "second")
    assert first.activate()["valid"]
    scenario = runtime.planning_scenarios[first.project_id]
    assert len(scenario.site_nodes) == 13
    assert first.activate()["reused"]
    assert runtime.planning_scenarios[first.project_id] is scenario
    with pytest.raises(ActiveProjectExists) as caught:
        second.activate()
    assert caught.value.active_project_id == first.project_id
    with pytest.raises(ActiveProjectExists):
        sdk.open_project(sdk.workspace / "projects/third")
    assert not (sdk.workspace / "projects/third").exists()

    class Pool:
        closed = False
        async def close(self):
            self.closed = True
    pool = Pool()
    runtime.geo_pools["test"] = pool
    runtime.feature_caches[first.project_id] = object()
    assert first.close()["closed"]
    assert pool.closed and not runtime.geo_pools and not runtime.feature_caches
    assert not runtime.planning_scenarios and runtime.active_project is None
    assert (store.root / first.project_id / "workspace" / first.project_path / "compiled_scenario.json").exists()
    assert first.close()["closed"]  # Idempotent after a lost successful reply.
    assert second.activate()["valid"]
    assert list(runtime.planning_scenarios) == [second.project_id]
    assert second.close()["closed"]


def test_cleanup_failure_keeps_active_project_for_retry(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "first")
    project.activate()
    class Pool:
        fail = True
        async def close(self):
            if self.fail:
                raise RuntimeError("simulated remote cleanup failure")
    pool = Pool()
    runtime.geo_pools["test"] = pool
    with pytest.raises(requests.HTTPError) as caught:
        project.close()
    assert caught.value.response.status_code == 503
    assert runtime.active_project["project_id"] == project.project_id
    assert project.project_id in runtime.planning_scenarios and runtime.geo_pools
    with pytest.raises(requests.HTTPError) as reuse:
        project.activate()
    assert reuse.value.response.status_code == 503
    pool.fail = False
    assert project.close()["closed"]


def test_stale_active_project_cannot_generate_candidates(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "first")
    project.activate()
    path = f"/planning/scenarios/{project.project_id}/candidate_edges/build"
    result = sdk._worker_request("post", path, json={"planner": "graph"})
    assert result["status"] == "OK"
    project.scenario_path.write_text(project.scenario_path.read_text() + '\n# changed\n', encoding="utf-8")
    project.sync()
    assert not project.activate()["valid"]
    assert project.graph.build_candidate_edges()["issues"][0]["code"] == "active_project_outdated"
    assert project.graph.get_candidate_edges()["issues"][0]["code"] == "active_project_outdated"
    with pytest.raises(requests.HTTPError) as caught:
        sdk._worker_request("post", path, json={"planner": "graph"})
    assert caught.value.response.status_code == 409
    assert project.close()["closed"]
    stale = project.activate()
    assert not stale["valid"] and runtime.active_project is None
    assert project.build()["valid"]
    assert project.activate()["valid"]
    project.close()


def test_activation_requires_compiled_artifact_and_project_token(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/empty")
    report = project.activate()
    assert not report["valid"] and report["issues"][0]["code"] == "compiled_scenario_missing"
    assert runtime.active_project is None
    with pytest.raises(requests.HTTPError) as caught:
        sdk._worker_request("post", f"/projects/{project.project_id}/activate",
                            headers={"X-Project-Token": "wrong"})
    assert caught.value.response.status_code == 403


def test_busy_project_cannot_close_or_be_replaced(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "first")
    project.activate()
    runtime.mesh_progress[project.project_id] = {"status": "running"}
    with pytest.raises(requests.HTTPError) as caught:
        project.close()
    assert caught.value.response.json()["detail"]["code"] == "project_busy"
    assert runtime.active_project is not None
    runtime.mesh_progress[project.project_id]["status"] = "completed"
    project.close()


def test_candidate_wrappers_require_activation_and_inspection_never_builds(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "graph")
    assert project.graph.build_candidate_edges()["issues"][0]["code"] == "project_not_active"
    project.activate()
    assert project.graph.get_candidate_edges()["issues"][0]["code"] == "candidate_graph_missing"
    assert not runtime.planning_planners
    report = project.graph.build_candidate_edges()
    assert report["valid"] and report["candidate_edges"]
    assert report["stage"] == "candidate_generation"
    saved = runtime.planning_planners[project.project_id]
    assert project.graph.get_candidate_edges() == report
    assert runtime.planning_planners[project.project_id] is saved
    mismatch = project.cell.get_candidate_edges()
    assert not mismatch["valid"] and mismatch["issues"][0]["code"] == "candidate_planner_mismatch"
    assert runtime.planning_planners[project.project_id] is saved
    assert not runtime.geo_pools  # Candidate generation never contacts features.
    mesh = project.mesh.build_candidate_edges()
    assert not mesh["valid"] and mesh["issues"][0]["code"] == "mesh_repeaters_required"
    assert runtime.planning_planners[project.project_id] is saved
    with pytest.raises(requests.HTTPError) as caught:
        sdk._worker_request("post", f"/projects/{project.project_id}/candidate_edges/build",
                            headers={"X-Project-Token": project._recovery_token}, json={"planner": "unknown"})
    assert caught.value.response.status_code == 422
    project.close()
    assert not runtime.candidate_options


def test_planner_interfaces_keep_lifecycle_and_mesh_operations_separate(storage, tmp_path):
    from cisei_planning_sdk_v2.planners import GraphPlanning, CellPlanning, MeshPlanning
    _, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/interfaces")
    assert isinstance(project.graph, GraphPlanning)
    assert isinstance(project.cell, CellPlanning)
    assert isinstance(project.mesh, MeshPlanning)
    assert project.graph is project.graph and project.mesh is project.mesh
    assert sdk.user_id is None  # Obtaining planner interfaces allocates no worker.
    for owner in (project, project.graph, project.cell):
        assert not hasattr(owner, "add_repeaters")
    assert not hasattr(project, "build_candidate_edges")
    assert not hasattr(project, "get_candidate_edges")
    with pytest.raises(TypeError):
        project.graph.build_candidate_edges(primary_tech="lte")
    with pytest.raises(TypeError):
        project.mesh.build_candidate_edges(planner="graph")


def test_cell_candidates_and_empty_graph_feedback(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "cell", example_name="example_2_LTE_simulation")
    project.activate()
    report = project.cell.build_candidate_edges(primary_tech="lte", sector_aware=True)
    assert report["valid"] and report["candidate_edges"]
    assert "sectors" in report
    assert project.cell.get_candidate_edges() == report
    # The project remains valid even when filters produce no candidate links.
    empty = project.cell.build_candidate_edges(primary_tech="nonexistent", preserve_existing=False)
    assert empty["valid"] and not empty["candidate_edges"]
    assert empty["diagnostics"]["issues"][0]["code"] == "no_candidate_links"
    assert empty["diagnostics"]["issues"][0]["severity"] == "warning"
    assert not runtime.geo_pools
    project.close()


def test_mesh_repeater_wrapper_uses_server_poles_and_saved_controls(runtime_session, tmp_path):
    from test_mesh_network_planner import inputs, Geo
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/mesh")
    scenario, poles = inputs()
    document = tomlkit.parse(scenario.to_toml())
    document["files"] = {"poles": "poles.csv"}
    document["mesh"] = {"max_repeaters": 2, "parent_candidates": 2,
                        "network": {"parent_candidates": 1, "corridor_m": 10, "final_degree": 3},
                        "poles": {"profile_column": "device_profile", "format": "csv"}}
    project.scenario_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    (project.path / "poles.csv").write_text(
        "pole_id,device_profile,x,y,mount_height_m\np,relay,500100,7200000,7\nfar,relay,503000,7200000,7\n", encoding="utf-8")
    project.sync()
    assert project.build()["valid"]
    assert project.mesh.add_repeaters()["issues"][0]["code"] == "project_not_active"
    assert project.activate()["valid"]
    geo = Geo(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1), (('p', 'b'), 1)])
    async def pool(**kwargs):
        return geo
    runtime.get_geo_pool = pool
    # The SDK must not read either local source file for this operation.
    project.scenario_path.rename(project.path / "scenario.hidden")
    (project.path / "poles.csv").rename(project.path / "poles.hidden")
    report = project.mesh.add_repeaters(geo_base_url="http://features.test", max_repeaters=1, final_degree=2)
    assert report["valid"], report
    assert report["stage"] == "mesh_preparation"
    assert report["result"]["mesh_network"]["selected_repeaters"] == ['p']
    assert report["result"]["mesh_network"]["used_repeaters"] is None  # Final solving is a later stage.
    engine = runtime.mesh_planners[project.project_id]
    assert set(engine.poles) == {"p", "far"}
    assert engine.parent_candidates == 1 and engine.corridor_m == 10
    assert engine.final_degree == 2 and engine.link_options["max_repeaters"] == 1
    assert set(runtime.planning_scenarios[project.project_id].site_nodes) == {"root", "a", "b"}
    assert geo.calls and runtime.feature_caches[project.project_id].records
    assert project.mesh.get_candidate_edges()["issues"][0]["code"] == "candidate_graph_missing"
    candidates = project.mesh.build_candidate_edges()
    assert candidates["valid"] and candidates["candidate_edges"]
    assert project.mesh.get_candidate_edges() == candidates
    with pytest.raises(requests.HTTPError) as error:
        project.mesh.add_repeaters(geo_base_url="http://features.test", poles=[])
    assert error.value.response.status_code == 422
    project.close()
