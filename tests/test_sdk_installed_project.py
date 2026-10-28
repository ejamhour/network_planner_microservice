import json
import shutil
from pathlib import Path

import pytest
import tomlkit

from cisei_planning_sdk import InstalledNetworkProject

SOURCE = Path(__file__).resolve().parents[1] / "examples/projects/e09_diagnostico_bro"


@pytest.fixture
def project(tmp_path):
    shutil.copytree(SOURCE, tmp_path / "bro")
    return InstalledNetworkProject(tmp_path / "bro")


def test_import_preserves_bro_paths_and_does_not_invent_rank(project):
    before = project.scenario_path.read_bytes()
    assert project.import_topology() == {"interfaces": 25, "links": 24, "backbone_accesses": 1}
    path = project.path_to_backbone("BRO-S-GE-006:d0:i0")
    assert path == [f"{name}:d0:i0" for name in ["BRO-S-GE-006", "BRO-S-GE-012", "BRO-S-GE-010", "BRO-S-GE-007", "BRO-S-GE-024", "BRO-S-GE-025", "BRO-S-GE-026", "BRO-S-P7-001"]]
    result = project.load_result()
    assert all(n["rank"] is None for n in result["planned_nodes"])
    assert project.scenario_path.read_bytes() == before
    assert project.path_to_backbone("BRO-S-P7-001:d0:i0") == ["BRO-S-P7-001:d0:i0"]
    with pytest.raises(ValueError, match="Unknown interface"):
        project.path_to_backbone("missing")


@pytest.mark.parametrize("change,match", [("cycle", "cycle"), ("unknown", "unknown"), ("disconnected", "exactly one"), ("automatic", "explicit")])
def test_invalid_topology_does_not_overwrite_result(project, change, match):
    project.import_topology()
    before = project.result_path.read_bytes()
    config = tomlkit.parse(project.scenario_path.read_text()).unwrap()
    edges = config["net"]["candidate_edges"]
    if change == "cycle":
        edges.append({"src": "BRO-S-GE-006:d0:i0", "dst": "BRO-S-P7-001:d0:i0"})
    elif change == "unknown":
        edges[0]["src"] = "missing"
    elif change == "disconnected":
        edges.pop()
    else:
        config["net"]["connectivity"] = [{"kind": "rf"}]
    project.scenario_path.write_text(tomlkit.dumps(config))
    with pytest.raises(ValueError, match=match):
        project.import_topology()
    assert project.result_path.read_bytes() == before


def test_notebook_executes_offline_and_saves_both_views(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    shutil.copytree(SOURCE, workspace / "projects/e09_diagnostico_bro")
    (workspace / "notebooks").mkdir()
    monkeypatch.chdir(workspace / "notebooks")
    notebook = json.loads((SOURCE.parents[1] / "notebooks/e09_diagnostico_bro.ipynb").read_text(encoding="utf-8"))
    namespace = {}
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code" and "requires-features-service" not in cell.get("metadata", {}).get("tags", []):
            exec("".join(cell["source"]), namespace)
    project = namespace["project"]
    for layout in ("logical", "geographic"):
        text = (project.path / f"topology_{layout}.html").read_text(encoding="utf-8")
        assert '"installedLayout": "' + layout + '"' in text
        assert "upstreamSites" in text
    assert not project.features_path.exists()


def test_inspector_is_lazy_and_uses_original_radio_direction(project):
    project.import_topology()
    requests = []
    class Geo:
        def link(self, *args, **kwargs):
            requests.append((args, kwargs))
        def link_profile(self):
            pass
    panel = project.inspect_links(Geo())
    assert len(panel.browser.edges) == 22
    assert not requests
    assert panel.browser.edges[0]["src"] == "BRO-S-P7-001:d0:i0"
    assert panel.browser.edges[0]["dst"] == "BRO-S-GE-003:d0:i0"
    panel.refresh.click()
    assert len(requests) == 1
    assert requests[0][1] == {"tx_ha": 7.0, "rx_ha": 7.0, "freq_mhz": 900.0}
    assert requests[0][0][0] == (-26.24628645, -53.63843458)


def test_e08_measurements_no_longer_require_feature_service_address():
    from cisei_planning_sdk.baseline_features import enrich_cache
    request = {"tx": [-26, -53], "rx": [-26.1, -53.1], "tx_ha": 7, "rx_ha": 7, "freq_mhz": 900}
    cache = {"format": "cisei-feature-cache", "version": 1,
             "entries": [{"source": "http://actual-service", "request": request, "features": {}}]}
    measures = {"format": "cisei-link-measurements", "version": 1, "records": [
        {"id": "a", "request": request, "tx": {"name": "A", "pw": 30, "ant_gain": 8},
         "rx": {"name": "B", "ant_gain": 8}, "measures": {"rssi": -70}}]}
    enriched, report = enrich_cache(cache, measures)
    assert report["added"] == 1
    assert "source" not in enriched["entries"][0]["observations"][0]
    cache["entries"].append({**cache["entries"][0], "source": "http://another-service"})
    with pytest.raises(ValueError, match="ambiguous"):
        enrich_cache(cache, measures)


@pytest.fixture
def enrichment_project(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "projects/e09_diagnostico_bro"
    shutil.copytree(SOURCE, target)
    data = workspace / "data/e09"
    shutil.copytree(SOURCE.parents[1] / "data/e09", data)
    project = InstalledNetworkProject(target)
    project.import_topology()
    measurements = json.loads((data / "hop_metrics.json").read_text())
    edges = {e["link_id"]: e for e in project.export_scenario()["net"]["candidate_edges"]}
    cache = {"format": "cisei-feature-cache", "version": 1, "entries": [
        {"source": "http://test-features", "request": row["request"], "features": {"fspl": 100.0}}
        for row in measurements["records"] if edges[row["link_id"]]["kind"] != "internal"]}
    project.save_features(cache)
    return project, measurements, cache


def test_installed_enrichment_reuses_e08_and_skips_only_internal_links(enrichment_project):
    project, measurements, original = enrichment_project
    topology = project.result_path.read_bytes()
    raw = project.features_path.read_bytes()
    report = project.enrich_features()
    assert report["input_records"] == 24
    assert report["added"] == 22
    assert report["internal_skipped"] == 2
    assert set(report["internal_observation_ids"]) == {"BRO-S-GE-026__BRO-S-GE-025", "BRO-S-GE-001__BRO-S-GE-028"}
    cache = json.loads(project.enriched_features_path.read_text())
    for before, after in zip(original["entries"], cache["entries"]):
        assert all(before[key] == after[key] for key in ("source", "request", "features"))
        assert len(after["observations"]) == 1
        assert after["observations"][0]["measures"]["rssi"] < 0
    saved = project.enriched_features_path.read_bytes()
    assert project.enrich_features()["unchanged"] == 22
    assert project.enriched_features_path.read_bytes() == saved
    measurements["records"][0]["measures"]["rssi"] = -90
    with pytest.raises(ValueError, match="replace=True"):
        project.enrich_features(measurements=measurements)
    assert project.enrich_features(measurements=measurements, replace=True)["updated"] == 1
    assert project.result_path.read_bytes() == topology
    assert project.features_path.read_bytes() == raw


@pytest.mark.parametrize("change", ["link_id", "direction", "request", "duplicate_internal"])
def test_invalid_hop_metrics_do_not_change_cache(enrichment_project, change):
    project, measurements, _ = enrichment_project
    before = project.features_path.read_bytes()
    row = measurements["records"][-1]
    if change == "link_id":
        row["link_id"] = "unknown"
    elif change == "direction":
        row["tx"], row["rx"] = row["rx"], row["tx"]
    elif change == "request":
        row["request"]["freq_mhz"] = 915
    else:
        measurements["records"].append(next(r for r in measurements["records"] if r["link_id"] == "BRO-S-GE-026__BRO-S-GE-025"))
    with pytest.raises(ValueError):
        project.enrich_features(measurements=measurements)
    assert project.features_path.read_bytes() == before


def test_stale_extraction_cache_save_cannot_erase_enriched_observations(enrichment_project):
    project, _, raw = enrichment_project
    project.enrich_features()
    saved = project.enriched_features_path.read_bytes()
    project.save_features(raw)  # Simulate a worker opened before enrichment.
    assert project.enriched_features_path.read_bytes() == saved
    assert all(not e.get("observations") for e in project.load_features()["entries"])
    raw["entries"][0]["features"]["fspl"] = 105.0
    project.save_features(raw)
    report = project.enrich_features()
    enriched = json.loads(project.enriched_features_path.read_text())
    assert report["unchanged"] == 22
    assert enriched["entries"][0]["features"]["fspl"] == 105.0
    assert sum(len(e["observations"]) for e in enriched["entries"]) == 22


def test_enrichment_output_cannot_overwrite_raw_cache(enrichment_project):
    from cisei_planning_sdk import BaselineProject
    project, _, _ = enrichment_project
    recipe_path = project.path / "baseline.json"
    recipe = json.loads(recipe_path.read_text())
    recipe["enriched"] = recipe["data"]
    recipe_path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="distinct"):
        BaselineProject(project.path)


@pytest.fixture
def diagnosed_project(enrichment_project):
    from cisei_planning_sdk import BaselineProject
    from cisei_planning_sdk.rssi_model import ExpressionModel
    project, measurements, cache = enrichment_project
    for entry, loss in zip(cache["entries"], [70, 70, 90, 90]):
        entry["features"]["fspl"] = loss
    for row, measured in zip(measurements["records"], [-70, -90, -70, -90]):
        row["measures"]["rssi"] = measured
    project.save_features(cache)
    project.enrich_features(measurements=measurements)
    baseline = BaselineProject(project.path)
    baseline.model_path.parent.mkdir(parents=True)
    model = ExpressionModel(fixed_terms={"loss": "-features.fspl"}, fixed_coeffs={"loss": 1},
                            learned_terms={}, learned_coeffs={}, intercept=0, y_expr="measures.rssi")
    model.save(baseline.model_path)
    return project


def test_classifies_four_e08_conditions_and_diagnoses_upstream_path(diagnosed_project):
    project = diagnosed_project
    before = {p: p.read_bytes() for p in (project.features_path, project.enriched_features_path, project.result_path)}
    report = project.evaluate_links(rssi_threshold_dbm=-80)
    assert [r["category"] for r in report["links"][:4]] == ["satisfactory", "investigate", "measured_satisfactory", "review_design"]
    assert report["installed_categories"]["internal"] == 2
    assert len(report["installed_links"]) == 24
    path = project.diagnose_path("BRO-S-GE-006:d0:i0")
    assert path["hop_count"] == 7
    assert path["categories"]["internal"] == 1
    assert sum(path["categories"].values()) == 7
    assert path["backbone"] == "BRO-S-P7-001:d0:i0"
    for p, content in before.items():
        assert p.read_bytes() == content
    view = project.show_topology(layout="logical", diagnosis=True)
    assert '"path_diagnoses"' in view.to_html()


def test_interactive_reconfiguration_preserves_diagnosis_and_input_files(diagnosed_project):
    project = diagnosed_project
    project.evaluate_links(rssi_threshold_dbm=-80)
    before = {p: p.read_bytes() for p in project.path.iterdir() if p.is_file()}
    for layout in ('logical', 'geographic'):
        view = project.review_reconfiguration(layout=layout)
        data = view.result['reconfiguration_review']
        assert len(data['choices']) == 24
        assert data['base']['requests'] == []
        assert not data['base']['confirmed']
        assert 'review-confirm' in view.to_html()
        assert 'allow-downloads' in view._repr_html_()
        assert 'allow-downloads' not in project.show_topology(layout=layout)._repr_html_()
    assert before == {p: p.read_bytes() for p in before}


def test_unmeasured_links_use_prediction_without_inventing_defects(diagnosed_project):
    project = diagnosed_project
    data = json.loads(project.enriched_features_path.read_text())
    for entry in data["entries"]:
        entry.pop("observations", None)
    project.enriched_features_path.write_text(json.dumps(data))
    report = project.evaluate_links(rssi_threshold_dbm=-80)
    assert report["categories"]["investigate"] == 0
    assert report["installed_links"][0]["category"] == "predicted_satisfactory"
    assert report["installed_links"][2]["category"] == "review_design"
    assert all(r["measured"] is None for r in report["installed_links"])
    data["entries"].pop(0)
    project.enriched_features_path.write_text(json.dumps(data))
    report = project.evaluate_links(rssi_threshold_dbm=-80)
    assert report["installed_links"][0]["category"] == "unassessed"


@pytest.mark.parametrize("file", ["enriched", "model", "topology"])
def test_stale_diagnosis_is_rejected(diagnosed_project, file):
    from cisei_planning_sdk import BaselineProject
    project = diagnosed_project
    project.evaluate_links(rssi_threshold_dbm=-80)
    path = {"enriched": project.enriched_features_path, "model": BaselineProject(project.path).model_path,
            "topology": project.result_path}[file]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed"):
        project.show_topology(diagnosis=True)


def test_multiple_samples_require_an_explicit_choice_without_overwriting_diagnosis(diagnosed_project):
    from copy import deepcopy
    project = diagnosed_project
    project.evaluate_links(rssi_threshold_dbm=-80)
    before = (project.path / "evaluation.json").read_bytes()
    data = json.loads(project.enriched_features_path.read_text())
    another = deepcopy(data["entries"][0]["observations"][0])
    another["id"] = "second-measurement"
    data["entries"][0]["observations"].append(another)
    project.enriched_features_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="one observation"):
        project.evaluate_links(rssi_threshold_dbm=-80)
    assert (project.path / "evaluation.json").read_bytes() == before
