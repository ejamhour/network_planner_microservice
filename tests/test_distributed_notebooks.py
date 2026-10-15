"""Execute the actual distributed notebooks against FastAPI with controlled geo data.

HTTP is adapted in-process; hub worker lifecycle and external geo deployment
must additionally be verified with the notebooks against a running service.
"""
import json
import shutil
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes_examples import router as examples_router
from app.routes_planning import router as planning_router
from app.session import UserRuntime

NOTEBOOKS = Path(__file__).resolve().parents[1] / "examples/notebooks"
LTE_NOTEBOOKS = {"example1_LTE_planning_basic", "example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation"}


@pytest.mark.parametrize("notebook", sorted(NOTEBOOKS.glob("*.ipynb")), ids=lambda p: p.stem)
def test_distributed_notebook(notebook, tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    plt.close("all")
    app = FastAPI()
    app.include_router(examples_router)
    app.include_router(planning_router)
    runtime = UserRuntime()
    app.state.runtime = runtime
    geo_calls = []

    class Geo:
        async def get_features(self, *args, **kwargs):
            geo_calls.append((args, kwargs))
            return {"dist_m": 1000.0, "fspl": 100.0, "delta_diffra": 0.0, "terrain_peak": {"vv_0": 0.0}, "buildings": {"core": 0.0, "fresnel": 0.0}}

    async def geo_pool(**kwargs):
        return Geo()

    runtime.get_geo_pool = geo_pool
    monkeypatch.setenv("PLANNING_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PLANNING_BASE_URL", "http://testserver")
    monkeypatch.setenv("PLANNING_USE_GEO", "0")
    calls = []
    result_dir = tmp_path / "workspace/projects"
    (tmp_path / "cisei_workspace/notebooks").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "cisei_workspace/notebooks")
    shutil.copytree(NOTEBOOKS.parent / "projects", tmp_path / "cisei_workspace/projects")
    for directory in ("antennas", "metrics", "data"):
        shutil.copytree(NOTEBOOKS.parent / directory, tmp_path / "cisei_workspace" / directory)
    if notebook.stem == "custom_configuration":
        (tmp_path / "cisei_workspace/projects/graph_planner_antenna_function_test/features.json").unlink()
    result_dir = tmp_path / "cisei_workspace/projects"
    if notebook.stem in LTE_NOTEBOOKS:
        result_dir /= notebook.stem
    inspection_calls = []
    if notebook.stem in {"example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation"}:
        class FeaturesNotebook:
            def __init__(self, *args, **kwargs):
                self.client = self
            def link(self, tx, rx, *, tx_ha, rx_ha, freq_mhz):
                assert len(tx) == len(rx) == 2
                assert tx_ha is not None and rx_ha is not None and freq_mhz > 0
                inspection_calls.append((tx, rx, tx_ha, rx_ha, freq_mhz))
            def __getattr__(self, name):
                assert name in {"link_profile", "link_area", "bldg_prepare", "bldg_profile",
                                "bldg_filter", "bldg_invasions", "bldg_browser", "close_worker",
                                "lulc_fresnel", "dem_tiles", "dem_profiles", "dem_surface",
                                "bldg_fresnel", "bldg_next_invasion", "link_features"}
                return lambda *args, **kwargs: None
        package = types.ModuleType("cisei_features_sdk")
        module = types.ModuleType("cisei_features_sdk.notebook")
        module.FeaturesNotebook = FeaturesNotebook
        monkeypatch.setitem(sys.modules, "cisei_features_sdk", package)
        monkeypatch.setitem(sys.modules, "cisei_features_sdk.notebook", module)
    with TestClient(app) as api:
        def request(_session, method, url, **kwargs):
            path = urlsplit(url).path
            calls.append((method.lower(), path))
            if path == "/register":
                response = requests.Response()
                response.status_code = 200
                response._content = json.dumps({"user_id": kwargs["params"]["user_id"], "token": "test-token"}).encode()
                return response
            if "/planning/" in path:
                assert kwargs["headers"]["X-Token"] == "test-token"
                path = "/planning/" + path.split("/planning/", 1)[1]
            kwargs.pop("timeout", None)
            kwargs.pop("allow_redirects", None)
            return api.request(method, path, **kwargs)

        monkeypatch.setattr(requests.Session, "request", request)
        namespace = {"__name__": "__main__"}
        document = json.loads(notebook.read_text(encoding="utf-8"))
        for index, cell in enumerate(document["cells"]):
            if cell["cell_type"] == "code":
                assert cell["outputs"] == [] and cell["execution_count"] is None
                source = "".join(cell["source"])
                assert "from cisei_lib" not in source
                exec(compile(source, f"{notebook.name}:cell-{index}", "exec"), namespace)
        assert list(result_dir.rglob("*result.json"))
        if notebook.stem in LTE_NOTEBOOKS:
            assert (result_dir / "candidate_network.html").exists()
            assert (result_dir / "solution_network.html").exists()
            import matplotlib.pyplot as plt
            assert (result_dir / "result.json").exists()
            plt.close("all")
        if notebook.stem in LTE_NOTEBOOKS | {"custom_configuration"}:
            assert geo_calls, "The custom example must actually evaluate uploaded models and metrics"
        else:
            assert not geo_calls, "Synthetic smoke cases must not require geo data"
        if notebook.stem == "advanced_api":
            assert (result_dir / "graph_planner_wisun_extended/scenario.toml").exists()
            assert not any("/solution/solve" in path for _, path in calls)
        else:
            assert any("/solution/solve" in path for _, path in calls)
        if notebook.stem in {"example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation"}:
            assert len(inspection_calls) == 2
            assert namespace["candidate_panel"].current is not None
            assert namespace["solution_panel"].current is not None
