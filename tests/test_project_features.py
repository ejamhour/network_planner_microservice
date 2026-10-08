import asyncio
import json
from pathlib import Path
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.feature_cache import FeatureCache, CachedGeo
from app.routes_planning import router
from app.session import UserRuntime
from cisei_planning_sdk import PlanningClient, PlanningProject, init_workspace


def test_project_reuses_persisted_features_after_worker_restart(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples/projects/graph_planner_antenna_function_test"
    init_workspace(tmp_path)
    shutil.copytree(source, tmp_path / "projects/project")
    project = PlanningProject(tmp_path / "projects/project")
    project.features_path.unlink(missing_ok=True)
    extracted = []

    def run(runtime, fail_on_geo=False):
        class Geo:
            async def get_features(self, *args, **kwargs):
                extracted.append(kwargs)
                return {"dist_m": 1000.0, "terrain_peak": {"vv_0": 0.0}}

        async def get_pool(**kwargs):
            if fail_on_geo:
                pytest.fail("A fully imported cache must not start the geo pool")
            return Geo()

        runtime.get_geo_pool = get_pool
        app = FastAPI()
        app.state.runtime = runtime
        app.include_router(router)
        with TestClient(app) as api:
            sdk = PlanningClient(
                "http://test", workspace=tmp_path, auto_register=False,
            )
            def request(method, path, **kwargs):
                payload = api.request(method, path, **kwargs).json()
                if payload["status"] != "OK":
                    raise RuntimeError(payload["data"])
                return payload
            sdk._request = request
            scenario = sdk.open_project(project)
            scenario.upload_configuration(metrics={"antenna_test": {"spec":
                'version=1\nresult="metric"\n[expressions]\nmetric="2.0 + features.dist_m / 1000.0"'}})
            scenario.build_candidate_edges()
            metrics = scenario.compute_metrics()
            assert metrics["metrics"][0]["metric"] == 3.0
            assert project.features_path.exists()
            assert scenario.export_feature_cache()["entries"]
            scenario.solve()
            assert scenario.save_result() == project.result_path
            assert scenario.load_result()["planned_nodes"]
            assert json.loads(project.features_path.read_text())["entries"][0]["features"] == {
                "dist_m": 1000.0, "terrain_peak": {"vv_0": 0.0}}
    run(UserRuntime())
    count = len(extracted)
    assert count > 0
    run(UserRuntime(), fail_on_geo=True)
    assert len(extracted) == count


def test_feature_cache_keys_and_atomic_validation():
    async def exercise():
        calls = []
        class Geo:
            async def get_features(self, *args, **kwargs):
                calls.append((args, kwargs))
                return {"dist_m": 123.0}
        class Runtime:
            async def get_geo_pool(self, **kwargs):
                return Geo()
        cache = FeatureCache()
        geo = CachedGeo(Runtime(), cache, base_url="http://geo", user_prefix="test", pool_size=1, timeout=10)
        args = {"tx": (1, 2), "rx": (3, 4), "tx_ha": 10, "rx_ha": 5, "freq_mhz": 915}
        await geo.get_features(**args)
        restored = FeatureCache.from_document(json.loads(json.dumps(cache.export())))
        geo.cache = restored
        await geo.get_features(**args)
        assert len(calls) == 1
        for change in ({"freq_mhz": 916}, {"tx_ha": 11}, {"rx_ha": 6},
                       {"tx": (2, 2)}, {"tx": (3, 4), "rx": (1, 2)}, {"resolution": 5}):
            await geo.get_features(**(args | change))
        assert len(calls) == 7
        geo.pool_options["base_url"] = "http://other-geo"
        await geo.get_features(**args)
        assert len(calls) == 8
        bad = restored.export()
        bad["entries"][0]["request"]["freq_mhz"] = float("nan")
        with pytest.raises(ValueError):
            FeatureCache.from_document(bad)
        assert len(restored.records) == 8
    asyncio.run(exercise())


def test_project_alternate_files_and_atomic_write(tmp_path):
    project = PlanningProject(tmp_path, scenario_file="alternate.toml", result_file="alternate.json")
    assert project.scenario_path == tmp_path / "alternate.toml"
    project.save_features({"version": 1})
    with pytest.raises(ValueError):
        project.save_features({"invalid": float("nan")})
    assert project.load_features() == {"version": 1}
    with pytest.raises(ValueError):
        PlanningProject(tmp_path, features_file="../outside.json")


def test_explicit_workspace_and_project_preserve_existing_files(tmp_path):
    init_workspace(tmp_path)
    project = PlanningProject(tmp_path / "projects/my_network")
    marker = project.path / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    client = PlanningClient("http://test", workspace=tmp_path, auto_register=False)
    assert client.workspace == tmp_path.resolve()
    assert client.project is None
    second = PlanningProject(project.path)
    assert second.path == project.path
    assert marker.read_text(encoding="utf-8") == "existing"
    assert client.antenna_path("custom.yaml") == (tmp_path / "antennas/custom.yaml").resolve()
    assert client.metric_path("custom.toml") == (tmp_path / "metrics/custom.toml").resolve()


def test_client_finds_workspace_from_notebooks_directory(tmp_path, monkeypatch):
    workspace = init_workspace(tmp_path / "cisei_workspace")
    monkeypatch.chdir(workspace / "notebooks")
    client = PlanningClient("http://test", auto_register=False)
    assert client.workspace == workspace.resolve()
    assert client.project is None


def test_client_rejects_project_outside_workspace(tmp_path):
    workspace = init_workspace(tmp_path / "workspace")
    client = PlanningClient("http://test", workspace=workspace, auto_register=False)
    project = PlanningProject(tmp_path / "outside")
    with pytest.raises(ValueError, match="projects directory"):
        client.open_project(project)
