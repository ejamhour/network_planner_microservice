"""Complete Graph SDK workflow, stage guards, durable results and retry reuse."""
import json
from pathlib import Path

import pytest
import requests

from test_project_storage import storage, sdk_for
from test_project_runtime import runtime_session, graph_project


def costs(candidates, value=1.0):
    return [{"src": edge["src"], "dst": edge["dst"], "metric": value}
            for edge in candidates["candidate_edges"]]


def test_graph_full_workflow_persists_and_retrieves_without_worker(runtime_session, tmp_path, monkeypatch):
    store, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "graph")
    assert project.graph.get_result()["issues"][0]["code"] == "result_missing"
    assert project.graph.compute_metrics(edge_metrics=[])["issues"][0]["code"] == "project_not_active"
    project.activate()
    candidates = project.graph.build_candidate_edges()
    assert project.graph.get_metrics()["issues"][0]["code"] == "metrics_missing"
    assert project.graph.solve()["issues"][0]["code"] == "metrics_incomplete"
    assert project.graph.compute_metrics(edge_metrics=costs(candidates))["complete"]
    assert project.graph.get_metrics()["complete"]
    solved = project.graph.solve()
    assert solved["valid"] and not solved["reused"], solved
    assert solved["planned_nodes"]
    assert project.graph.get_solution()["planned_edges"] == solved["planned_edges"]
    path = store.root / project.project_id / "workspace" / project.project_path / "result.json"
    saved = path.read_bytes()
    result = json.loads(saved)
    assert result["project_run"]["inputs"]["input_hash"] == solved["input_hash"]
    assert "edge_features" not in result or not result["edge_features"]
    planner = runtime.planning_planners[project.project_id]
    with monkeypatch.context() as patch:
        patch.setattr(planner, "run_rpl", lambda **kw: pytest.fail("Identical solve should reuse disk output"))
        retry = project.graph.solve()
    assert retry["reused"] and retry["parameter_hash"] == solved["parameter_hash"]
    changed = project.graph.solve(parent_set_size=2)
    assert not changed["reused"] and changed["parameter_hash"] != solved["parameter_hash"]
    assert project.graph.get_result(parameter_hash=solved["parameter_hash"])["issues"][0]["code"] == "result_parameters_mismatch"
    project.close()
    # Another SDK instance can retrieve output before registering any worker session.
    second = sdk_for(sdk.workspace, api)
    resumed = second.open_project(project.path, mode="resume")
    report = resumed.graph.save_result()
    assert report["valid"] and second.user_id is None
    assert json.loads(Path(report["local_path"]).read_text()) == json.loads(path.read_text())
    assert project.graph.get_result()["valid"]
    assert not runtime.geo_pools
    # A byte-identical sync preserves the result; changed inputs invalidate it.
    resumed.sync()
    assert resumed.graph.get_result()["valid"]
    resumed.scenario_path.write_text(resumed.scenario_path.read_text() + '\n# changed\n', encoding="utf-8")
    resumed.sync()
    assert resumed.graph.get_result()["issues"][0]["code"] == "result_outdated"
    local_bytes = Path(report["local_path"]).read_bytes()
    assert not resumed.graph.save_result()["valid"]
    assert Path(report["local_path"]).read_bytes() == local_bytes
    assert path.exists()


def test_metric_updates_invalidate_solution_and_reject_incomplete_or_wrong_planner(runtime_session, tmp_path):
    _, api, _ = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "graph")
    project.activate()
    candidates = project.graph.build_candidate_edges()
    supplied = costs(candidates)
    partial = project.graph.compute_metrics(edge_metrics=supplied[:1])
    assert partial["valid"] and not partial["complete"]
    assert project.graph.solve()["issues"][0]["code"] == "metrics_incomplete"
    assert project.graph.compute_metrics(edge_metrics=supplied)["complete"]
    assert project.graph.solve()["valid"]
    invalid = project.graph.compute_metrics(edge_metrics=[{"src": "unknown", "dst": "also-unknown", "metric": 1}])
    assert not invalid["valid"]
    assert project.graph.solve()["issues"][0]["code"] == "metrics_incomplete"
    assert project.graph.get_solution()["issues"][0]["code"] == "solution_missing"
    project.cell.build_candidate_edges(primary_tech="wisun")
    assert project.graph.compute_metrics(edge_metrics=[])["issues"][0]["code"] == "candidate_planner_mismatch"
    assert callable(project.cell.solve) and not hasattr(project.mesh, "compute_metrics")
    project.close()


def test_batched_feature_metrics_remain_on_server_and_block_early_solve(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "graph")
    # Use a deterministic metric so the real feature-backed path needs only distance.
    import tomlkit
    document = tomlkit.parse(project.scenario_path.read_text())
    document["metrics"] = {"wisun": {"spec": 'version = 1\nresult = "metric"\n[expressions]\nmetric = "features.dist_m"'}}
    project.scenario_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    project.sync()
    assert project.build()["valid"]
    project.activate()
    project.graph.build_candidate_edges()
    class Geo:
        async def get_features(self, *args, **kw):
            return {"dist_m": 1.0}
    async def pool(**kwargs):
        return Geo()
    runtime.get_geo_pool = pool
    offset = 0
    while True:
        report = project.graph.compute_metrics(geo_base_url="http://features.test", batch_offset=offset, batch_size=2)
        assert report["valid"], report
        assert "features" not in report
        if report["complete"]:
            break
        assert project.graph.solve()["issues"][0]["code"] == "metrics_incomplete"
        offset = report["batch"]["next_offset"]
    assert runtime.feature_caches[project.project_id].records
    assert project.graph.solve()["valid"]
    project.close()


def test_result_write_failure_preserves_last_success_and_reports_failure(runtime_session, tmp_path, monkeypatch):
    from cisei_lib.project_io import planning_results
    store, api, _ = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project = graph_project(sdk, "graph")
    project.activate()
    candidates = project.graph.build_candidate_edges()
    project.graph.compute_metrics(edge_metrics=costs(candidates))
    first = project.graph.solve()
    result = store.root / project.project_id / "workspace" / project.project_path / "result.json"
    original = result.read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("simulated result publish failure")
        patch.setattr(planning_results.os, "replace", fail)
        with pytest.raises(requests.HTTPError) as caught:
            project.graph.solve(parent_set_size=2)
        assert caught.value.response.status_code == 500
    assert result.read_bytes() == original
    assert project.graph.get_result(parameter_hash=first["parameter_hash"])["valid"]
    assert project.graph.solve(parent_set_size=2)["valid"]
    project.close()
