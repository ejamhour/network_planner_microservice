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
E07_NOTEBOOKS = {"e07_01_projeto_original", "e07_02_projeto_redundancia"}


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
            return {"dist_m": 1000.0, "fspl": 100.0, "delta_diffra": 0.0, "terrain_peak": {"vv_0": 0.0}, "terrain_peaks_vv": [], "buildings": {"core": 0.0, "fresnel": 0.0, "boundary": 0.0}, "vegetation": {"core": 0.0, "fresnel": 0.0, "boundary": 0.0}}

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
    # Installed-network examples do not use the large E08 training datasets.
    # Copy only their own inputs instead of duplicating unrelated projects.
    repair = notebook.stem == 'e13_replanejamento'
    installed = notebook.stem in {"e09_diagnostico_bro", "e13_diagnostico"} or repair
    if installed:
        project_name = "e10_diagnostico" if notebook.stem == "e13_diagnostico" or repair else notebook.stem
        shutil.copytree(NOTEBOOKS.parent / "projects" / project_name,
                        tmp_path / "cisei_workspace/projects" / project_name)
        if repair:
            shutil.copytree(NOTEBOOKS.parent / 'projects/e13_replanejamento',
                            tmp_path / 'cisei_workspace/projects/e13_replanejamento')
            from cisei_planning_sdk import InstalledNetworkProject
            from cisei_planning_sdk.reconfiguration import compact_reconfiguration
            source = InstalledNetworkProject(tmp_path / 'cisei_workspace/projects/e10_diagnostico')
            source.import_topology()
            ids = ['AGU-S-GE-014__AGU-S-GE-058', 'AGU-S-GE-015__AGU-S-GE-066',
                   'AGU-S-GE-022__AGU-S-GE-031', 'AGU-S-GE-024__AGU-S-GE-025']
            selection = compact_reconfiguration(source.preview_reconfiguration(
                [{'link_id': key, 'mode': 'link'} for key in ids]))
            (source.path / 'reconfiguration.json').write_text(json.dumps(selection))
            # A smoke test needs only a few physical positions, not the full inventory.
            poles = tmp_path / 'cisei_workspace/projects/e13_replanejamento/poles.csv'
            poles.write_text('\n'.join(poles.read_text().splitlines()[:6]) + '\n')
    else:
        shutil.copytree(NOTEBOOKS.parent / "projects", tmp_path / "cisei_workspace/projects")
    for directory in ("antennas", "metrics", "data"):
        source = NOTEBOOKS.parent / directory
        destination = tmp_path / "cisei_workspace" / directory
        if installed and directory == "data":
            source /= project_name[:3]
            destination /= project_name[:3]
        shutil.copytree(source, destination)
    if notebook.stem == "custom_configuration":
        (tmp_path / "cisei_workspace/projects/graph_planner_antenna_function_test/features.json").unlink()
    if notebook.stem == "e08_baseline_tecnologia":
        project_dir = tmp_path / "cisei_workspace/projects/e08_baseline_copel"
        document = json.loads((project_dir / "features.json").read_text())
        for entry in document["entries"]:
            entry.pop("observations", None)
        (project_dir / "features.json").write_text(json.dumps(document))
        for name in ("enriched_features.json", "training_result.json", "evaluation.json", "training.html", "evaluation.html"):
            (project_dir / name).unlink(missing_ok=True)
        for name in ("e08_copel_rssi.json", "e08_copel.toml"):
            (tmp_path / "cisei_workspace/metrics" / name).unlink(missing_ok=True)
    result_dir = tmp_path / "cisei_workspace/projects"
    if notebook.stem in E07_NOTEBOOKS:
        for name in ("e07_ge_mesh_original", "e07_ge_mesh_redundancia"):
            (result_dir / name / "features.json").unlink(missing_ok=True)
    if notebook.stem in LTE_NOTEBOOKS:
        result_dir /= notebook.stem
    inspection_calls = []
    if notebook.stem in {"example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation", "e08_baseline_tecnologia", "e09_diagnostico_bro", "e13_diagnostico"}:
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
        if notebook.stem == "e07_02_projeto_redundancia":
            # Each parameterized notebook test starts in an independent workspace.
            seed_document = json.loads((NOTEBOOKS / "e07_01_projeto_original.ipynb").read_text(encoding="utf-8"))
            seed_namespace = {"__name__": "__main__"}
            for cell in seed_document["cells"]:
                if cell["cell_type"] == "code":
                    exec("".join(cell["source"]), seed_namespace)
        if notebook.stem == "example_3_LTE_hybrid":
            from cisei_planning_sdk import PlanningClient, PlanningProject
            seed_client = PlanningClient("http://testserver", workspace="..")
            seed_project = PlanningProject("../projects/example_2_LTE_simulation")
            seed = seed_client.open_project(seed_project, planner="cell")
            seed.build_candidate_edges()
            seed.compute_metrics(batch_size=20, geo_base_url="http://features:8080")
            seed.solve()
            seed.save_result(rank_threshold=2)
            baseline = seed_project.load_result()
            next(n for n in baseline["planned_nodes"] if not n["connected"])["rank"] = 3.0
            seed_project.save_result(baseline)
            baseline_extractions = len(geo_calls)
        namespace = {"__name__": "__main__"}
        document = json.loads(notebook.read_text(encoding="utf-8"))
        for index, cell in enumerate(document["cells"]):
            if cell["cell_type"] == "code":
                if 'requires-user-confirmation' in cell.get('metadata', {}).get('tags', []):
                    # This cell reads a file produced by a human's UI confirmation.
                    continue
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
        if notebook.stem == "example_3_LTE_hybrid":
            new_extractions = geo_calls[baseline_extractions:]
            assert new_extractions and all(kwargs["freq_mhz"] == 915.0 for _, kwargs in new_extractions)
            derived = tmp_path / "cisei_workspace/projects/example_3_LTE_hybrid"
            assert (derived / "replacement_report.json").exists()
            assert (derived / "solution_network.html").exists()
            assert (derived / "evaluation.html").exists()
            assert (derived / "metrics.html").exists()
        if notebook.stem in LTE_NOTEBOOKS | E07_NOTEBOOKS | {"custom_configuration", "example_3_LTE_hybrid", "e09_diagnostico_bro", "e13_diagnostico", "e13_replanejamento", "exemple_4_Mesh_basic"}:
            assert geo_calls, "The custom example must actually evaluate uploaded models and metrics"
        else:
            assert not geo_calls, "Synthetic smoke cases must not require geo data"
        if notebook.stem == "advanced_api":
            assert (result_dir / "graph_planner_wisun_extended/scenario.toml").exists()
            assert not any("/solution/solve" in path for _, path in calls)
        elif notebook.stem == "e08_baseline_tecnologia":
            project_dir = result_dir / "e08_baseline_copel"
            cache = json.loads((project_dir / "enriched_features.json").read_text())
            assert sum(len(e.get("observations", [])) for e in cache["entries"]) == 2278
            raw = json.loads((project_dir / "features.json").read_text())
            assert all(not e.get("observations") for e in raw["entries"])
            evaluation = json.loads((project_dir / "evaluation.json").read_text())
            assert evaluation["summary"]["n"] == 1890
            assert evaluation["summary"]["mae"] == pytest.approx(8.619868509515726)
            assert (tmp_path / "cisei_workspace/metrics/e08_copel.toml").exists()
            assert not calls, "Baseline enrichment/training must not call planning or features"
            assert not inspection_calls, "Inspector construction must not prepare a geographic link"
            namespace["inspector"].refresh.click()
            assert len(inspection_calls) == 1
            assert namespace["inspector"].current is not None
        elif notebook.stem in {"e09_diagnostico_bro", "e13_diagnostico"}:
            agu = notebook.stem == "e13_diagnostico"
            project = namespace["project"]
            result = project.load_result()
            assert len(result["planned_nodes"]) == (65 if agu else 25)
            assert all(n["rank"] is None for n in result["planned_nodes"])
            assert not any("/solution/solve" in path for _, path in calls)
            assert len(project.load_features()["entries"]) == (59 if agu else 22)
            assert all(not e.get("observations") for e in project.load_features()["entries"])
            enriched = json.loads(project.enriched_features_path.read_text())
            assert sum(len(e.get("observations", [])) for e in enriched["entries"]) == (57 if agu else 22)
            evaluation = json.loads((project.path / "evaluation.json").read_text())
            assert len(evaluation["installed_links"]) == (64 if agu else 24)
            assert evaluation["installed_categories"]["internal"] == (5 if agu else 2)
            assert sum(evaluation["categories"].values()) == (57 if agu else 22)
            assert (project.path / "topology_logical_diagnosis.html").exists()
            assert (project.path / "topology_geographic_diagnosis.html").exists()
            assert not inspection_calls
            namespace["inspector"].refresh.click()
            assert len(inspection_calls) == 1
            assert inspection_calls[0][-1] == 900.0
        elif notebook.stem == "exemple_4_Mesh_basic":
            assert sum("/mesh/plan_link" in path for _, path in calls) == 2
            assert len(list((result_dir / "exemple_4_Mesh_basic").glob("mesh_*.html"))) == 4
            assert namespace["proposal_guaira"]["mesh_search"]["dst_site"] == "guaira"
            assert namespace["proposal_italia"]["mesh_search"]["dst_site"] == "italia"
            assert len(runtime.planning_scenarios[namespace["scenario"].scenario_id].site_nodes) == 3
            assert (result_dir / "exemple_4_Mesh_basic/poles.csv").exists()
        elif repair:
            assert namespace['replanning']['accepted_links'] == 4
            assert namespace['preparation']['installed_radios'] == 65
            assert namespace['preparation']['preserved_links'] == 60
            derived = result_dir / 'e13_replanejamento'
            assert (derived / 'solution_scenario.toml').exists()
            assert len(list((derived / 'link_studies').glob('*/result.json'))) == 4
        else:
            assert any("/solution/solve" in path for _, path in calls)
        if notebook.stem in {"example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation"}:
            assert len(inspection_calls) == 2
            assert namespace["candidate_panel"].current is not None
            assert namespace["solution_panel"].current is not None
