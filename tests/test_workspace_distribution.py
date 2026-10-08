import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes_examples
from cisei_planning_sdk import init_workspace, load_result, load_scenario_bundle, save_result
from cisei_planning_sdk import workspace as workspace_module


@pytest.fixture
def examples_client():
    app = FastAPI()
    app.include_router(routes_examples.router)
    with TestClient(app) as client:
        yield client


def test_distribution_round_trip(tmp_path, monkeypatch, examples_client):
    def get(url, **kwargs):
        return examples_client.get(url.removeprefix("http://service"))

    monkeypatch.setattr(workspace_module.requests, "get", get)
    root = init_workspace(tmp_path / "user", base_url="http://service")
    assert all((root / name).is_dir() for name in ("projects", "notebooks", "antennas", "metrics", "data"))
    assert not (root / "examples").exists()
    for scenario in (root / "projects").glob("*/scenario.toml"):
        payload = load_scenario_bundle(scenario)
        assert payload["sites"]
        assert "instances" not in payload
        json.dumps(payload, allow_nan=False)
    sample = root / "README.md"
    sample.write_text("user edits", encoding="utf-8")
    init_workspace(root, base_url="http://service")
    assert sample.read_text(encoding="utf-8") == "user edits"


def test_offline_and_result_round_trip(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Offline helpers must not contact the service")

    monkeypatch.setattr(workspace_module.requests, "get", unexpected)
    root = init_workspace(tmp_path)
    result = {"name": "São Paulo", "cost": float("inf"), "edges": [{"rank": 2}]}
    path = save_result(result, root / "results/nested/result.json")
    assert load_result(path) == {**result, "cost": None}
    assert result["cost"] == float("inf")
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_result(path)
    with pytest.raises(TypeError):
        save_result([], path)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\b", ".", ""])
def test_download_rejects_unsafe_manifest(tmp_path, monkeypatch, name):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"files": [name]}

    monkeypatch.setattr(workspace_module.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="Invalid example path"):
        init_workspace(tmp_path, base_url="http://service")


def test_public_files_are_confined(examples_client, tmp_path, monkeypatch):
    assert examples_client.get("/examples/scenarios/missing.toml").status_code == 404
    assert examples_client.get("/examples/%2e%2e/app/main.py").status_code == 404
    monkeypatch.setattr(routes_examples, "EXAMPLES_DIR", tmp_path)
    (tmp_path / ".secret").write_text("private")
    assert examples_client.get("/examples/.secret").status_code == 404
    assert examples_client.get("/examples/manifest.json").json() == {"files": []}


def test_notebook_samples_have_no_saved_outputs():
    for path in routes_examples.EXAMPLES_DIR.glob("notebooks/*.ipynb"):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                assert cell["outputs"] == []
                assert cell["execution_count"] is None
