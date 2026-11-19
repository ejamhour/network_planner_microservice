"""Cell execution owns its stages and preserves effective tuned placements."""
import json
from pathlib import Path

import pytest
import tomlkit

from test_project_storage import storage, sdk_for
from test_project_runtime import runtime_session
from test_placement_output import make_project
from test_project_graph import costs


def test_complete_cell_workflow_tuned_result_and_worker_independent_retrieval(runtime_session, tmp_path, monkeypatch):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    project, source = make_project(sdk)
    original = source.read_bytes()
    assert project.cell.solve()["issues"][0]["code"] == "metrics_incomplete"
    assert project.cell.tune_sectors()["valid"]
    candidates = project.cell.get_candidate_edges()
    assert candidates["candidate_edges"]
    assert project.cell.compute_metrics(edge_metrics=costs(candidates))["complete"]
    assert project.cell.get_metrics()["stage"] == "cell_metrics"
    first = project.cell.solve(parent_set_size=1)
    assert first["valid"] and first["stage"] == "cell_solve", first
    assert first["cell"]["client_service"][0]["served"]
    assert first["cell"]["cell_load"] and first["cell"]["sectors"]
    placements = first["antenna_placements"]["placements"]
    assert placements["tower:d0:i0"]["azimuth_deg"] != 0
    assert placements["tower:d0:i1"]["azimuth_deg"] == 180
    assert project.cell.get_solution()["antenna_placements"] == first["antenna_placements"]
    engine = runtime.cell_planners[project.project_id]
    with monkeypatch.context() as patch:
        patch.setattr(engine, "run_rpl", lambda **kw: pytest.fail("Exact Cell solve should reuse saved output"))
        assert project.cell.solve()["reused"]
    # Change only physical orientation: the same edges/costs must not reuse old output.
    current = engine.sector_rotation_by_site["tower"]
    engine.apply_sector_rotation({"tower": current + 1})
    project.cell.build_candidate_edges()
    changed_candidates = project.cell.get_candidate_edges()
    assert [(e["src"], e["dst"]) for e in changed_candidates["candidate_edges"]] == [(e["src"], e["dst"]) for e in candidates["candidate_edges"]]
    project.cell.compute_metrics(edge_metrics=costs(changed_candidates))
    second = project.cell.solve()
    assert second["valid"] and not second["reused"]
    assert second["parameter_hash"] != first["parameter_hash"]
    assert project.graph.get_result()["issues"][0]["code"] == "result_planner_mismatch"
    assert project.cell.get_result(parameter_hash=first["parameter_hash"])["issues"][0]["code"] == "result_parameters_mismatch"
    exported = project.save_placements()
    assert tomlkit.parse(exported["content"]).unwrap() == second["antenna_placements"]
    assert source.read_bytes() == original
    project.close()
    resumed_sdk = sdk_for(sdk.workspace, api)
    resumed = resumed_sdk.open_project(project.path, mode="resume")
    saved = resumed.cell.save_result()
    assert saved["valid"] and resumed_sdk.user_id is None
    data = json.loads(Path(saved["local_path"]).read_text())
    assert data["project_run"]["planner"] == "cell"
    assert data["antenna_placements"] == second["antenna_placements"]
    assert data["cell"]["primary_tech"] == "lte"


def test_cell_feature_metrics_use_tuned_gain_and_preview_does_not_invalidate_solution(runtime_session, tmp_path):
    _, api, runtime = runtime_session
    sdk = sdk_for(tmp_path / "client", api)
    spec = 'version = 1\nresult = "metric"\n[expressions]\nmetric = "tx.ant_gain"'
    project, _ = make_project(sdk, metric_spec=spec)
    project.cell.tune_sectors()
    assert not project.cell.compute_metrics(edge_metrics=[])["complete"]
    assert project.cell.solve()["issues"][0]["code"] == "metrics_incomplete"
    class Geo:
        async def get_features(self, *args, **kw):
            return {"dist_m": 100.0}
    async def pool(**kwargs):
        return Geo()
    runtime.get_geo_pool = pool
    metric = project.cell.compute_metrics(geo_base_url="http://features.test", batch_size=1)
    assert metric["valid"] and metric["complete"], metric
    assert metric["metrics"][0]["metric"] > 5  # Tuned boresight reaches the actual RF metric.
    assert project.cell.solve()["valid"]
    preview = project.cell.tune_sectors(apply_rotation=False)
    assert preview["valid"]
    assert project.cell.get_solution()["valid"]
    project.cell.tune_sectors()
    assert project.cell.get_solution()["issues"][0]["code"] == "solution_missing"
    assert project.cell.solve()["issues"][0]["code"] == "metrics_incomplete"
    project.scenario_path.write_text(project.scenario_path.read_text() + '\n# changed\n', encoding="utf-8")
    project.sync()
    assert project.cell.compute_metrics(edge_metrics=[])["issues"][0]["code"] == "active_project_outdated"
    assert project.cell.get_result()["issues"][0]["code"] == "result_outdated"
    project.close()
