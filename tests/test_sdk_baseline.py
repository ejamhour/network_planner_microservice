import json
import shutil
from pathlib import Path

import pytest

from cisei_planning_sdk import BaselineProject
from cisei_planning_sdk.rssi_model import ExpressionModel, is_number
from cisei_lib.planners.metric_compiler import compile_metric_spec


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("baseline")
    project = workspace / "projects/e08"
    project.mkdir(parents=True)
    shutil.copy(ROOT / "examples/projects/e08_baseline_copel/baseline.json", project)
    (workspace / "data/e08").mkdir(parents=True)
    shutil.copy(ROOT / "examples/data/e08/measurements.json", workspace / "data/e08")
    # The recipe points at the distributed project name, independently of this
    # test's project directory.
    recipe = json.loads((project / "baseline.json").read_text())
    recipe["data"] = "projects/e08/features.json"
    recipe["enriched"] = "projects/e08/enriched_features.json"
    (project / "baseline.json").write_text(json.dumps(recipe))
    shutil.copy(ROOT / "examples/data/e08/features.json", project / "features.json")
    baseline = BaselineProject(project)
    baseline.enrich_features()
    baseline.train()
    return baseline


def test_reproduces_recorded_notebook_without_server(trained):
    report = json.loads((trained.path / "training_result.json").read_text())
    assert report["feature_entries"] == 2468
    assert report["input_records"] == 2278
    assert [r["training"]["n_training_records"] for r in report["stages"]] == [243, 188, 521, 1890]
    assert report["stages"][-1]["evaluation"]["mae"] == pytest.approx(8.619868509515726)
    model = ExpressionModel.load(trained.model_path)
    assert model.learned_coeffs["veg_boundary"] == pytest.approx(3.8599562365669438)


def test_metric_compiler_and_model_agree_for_every_selected_link(trained):
    model = ExpressionModel.load(trained.model_path)
    metric = compile_metric_spec(trained.export_metric(required_rssi_dbm=-80).read_text())
    _, records = trained._records()
    for record in records:
        assert metric(record) == pytest.approx(max(1, 2 - (model.predict(record)+80)/10))


def test_classifies_all_four_conditions_and_retains_explanations(trained):
    report = trained.evaluate(rssi_threshold_dbm=-80)
    assert report["categories"] == {"investigate": 157, "satisfactory": 1463,
                                    "measured_satisfactory": 175, "review_design": 95}
    assert sum(report["categories"].values()) == report["summary"]["n"] == 1890
    for link in report["links"]:
        assert link["residual"] == pytest.approx(link["measured"]-link["predicted"])
        assert link["explanation"]["prediction"] == pytest.approx(link["predicted"])


def test_workspace_path_escape_and_output_collision_rejected(tmp_path):
    project = tmp_path / "projects/demo"
    project.mkdir(parents=True)
    recipe = {"version": 1, "data": "../outside.json", "model": "metrics/a.json", "metric": "metrics/a.toml"}
    (project / "baseline.json").write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="inside the workspace"):
        BaselineProject(project)
    recipe["data"] = recipe["model"]
    (project / "baseline.json").write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="distinct"):
        BaselineProject(project)


@pytest.mark.parametrize("threshold", [True, float("nan"), float("inf"), "-80"])
def test_invalid_threshold_rejected(trained, threshold):
    with pytest.raises(ValueError, match="finite"):
        trained.evaluate(rssi_threshold_dbm=threshold)


def test_no_finite_or_boolean_measurements_are_not_numbers():
    assert not any(is_number(v) for v in [True, False, float("nan"), float("inf")])


def test_notebook_uses_only_sdk_no_local_algorithms():
    import ast
    nb = json.loads((ROOT / "examples/notebooks/e08_baseline_tecnologia.ipynb").read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        assert not any(isinstance(n, (ast.For, ast.While, ast.FunctionDef, ast.ListComp)) for n in ast.walk(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in {"cisei_planning_sdk", "cisei_features_sdk.notebook"}


def test_divergent_inspector_reuses_features_views_without_eager_requests(trained):
    trained.evaluate(rssi_threshold_dbm=-80)
    class Geo:
        def __init__(self):
            self.calls = []
        def link(self, *args, **kwargs):
            self.calls.append((args, kwargs))
        def link_profile(self):
            return None
    geo = Geo()
    panel = trained.inspect_divergent_links(geo, freq_mhz=900, minimum_height_m=7,
                                           min_abs_error_db=20, limit=12)
    assert not geo.calls
    edges = panel.browser.edges
    assert len(edges) == 12
    assert [abs(e["residual"]) for e in edges] == sorted([abs(e["residual"]) for e in edges], reverse=True)
    assert len(panel._visible_indices) == 12
    panel.refresh.click()
    assert len(geo.calls) == 1
    args, kwargs = geo.calls[0]
    assert args == (tuple(edges[0]["record"]["tx"]["pos"]), tuple(edges[0]["record"]["rx"]["pos"]))
    assert kwargs == {"tx_ha": max(7,edges[0]["record"]["tx"]["ant_height"]),
                      "rx_ha": max(7,edges[0]["record"]["rx"]["ant_height"]), "freq_mhz":900}
    assert "previsto" in panel.status.value
    below = trained.inspect_divergent_links(geo, freq_mhz=900, direction="below_expected", limit=20)
    assert all(e["residual"] < 0 for e in below.browser.edges)
    empty = trained.inspect_divergent_links(geo, freq_mhz=900, min_abs_error_db=1000)
    assert not empty.browser.edges and empty.refresh.disabled


def test_inspector_rejects_stale_evaluation(trained):
    saved = trained.model_path.read_bytes()
    try:
        trained.model_path.write_bytes(saved+b"\n")
        with pytest.raises(ValueError, match="Model changed"):
            trained.inspect_divergent_links(object(), freq_mhz=900)
    finally:
        trained.model_path.write_bytes(saved)
