"""Exercise actual streamed requests, without an in-process HTTP adapter."""
from hashlib import sha256
import socket
from threading import Thread
from time import monotonic, sleep

from fastapi import FastAPI
import uvicorn

from app.routes_projects import get_project_store, router
from cisei_lib.project_io.project_store import ProjectStore
from cisei_planning_sdk_v2 import PlanningClient


def test_live_http_sync_streams_binary_inputs_and_recovers_identity(tmp_path):
    store = ProjectStore(tmp_path / "server-temp")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_project_store] = lambda: store
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(.01)
        assert server.started
        workspace = tmp_path / "client"
        (workspace / "projects").mkdir(parents=True)
        client = PlanningClient(f"http://127.0.0.1:{port}", workspace=workspace,
                                timeout=10)
        local = workspace / "projects/live"
        remote = client.open_project(local)
        (local / "scenario.toml").write_text('[files]\nnodes = "data.bin"\n', encoding="utf-8")
        content = bytes(range(256)) * 8193  # Cross multiple upload/copy chunks.
        (local / "data.bin").write_bytes(content)
        first = remote.sync()
        uploaded = store.root / remote.project_id / "workspace/projects/live/data.bin"
        assert uploaded.read_bytes() == content
        data = next(row for row in first["files"] if row["path"].endswith("/data.bin"))
        assert data["sha256"] == sha256(content).hexdigest()
        resumed_client = PlanningClient(client.base_url, workspace=workspace, timeout=10)
        resumed = resumed_client.open_project(local, mode="resume")
        repeated = resumed.sync()
        assert not repeated["inputs_changed"]
        assert repeated["input_revision"] == first["input_revision"]
        client.session.close()
        resumed_client.session.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive()


def test_live_hub_worker_activation_close_and_recovery(tmp_path, monkeypatch):
    """Real hub proxy and spawned worker restore the saved artifact after reset."""
    from app import main_hub as hub
    from test_project_runtime import graph_project
    monkeypatch.setenv("PLANNING_PROJECT_ROOT", str(tmp_path / "server-temp"))
    monkeypatch.setattr(hub, "user_registry", {})
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(hub.hub_app, log_level="error"))
    port = listener.getsockname()[1]
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    sdk = None
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(.01)
        assert server.started
        workspace = tmp_path / "client"
        (workspace / "projects").mkdir(parents=True)
        sdk = PlanningClient(f"http://127.0.0.1:{port}", workspace=workspace, timeout=30)
        project = graph_project(sdk, "graph")
        assert project.activate()["valid"]
        result = project.graph.build_candidate_edges()
        assert result["valid"] and result["candidate_edges"]
        assert project.graph.get_candidate_edges() == result
        supplied = [{"src": row["src"], "dst": row["dst"], "metric": 1.0} for row in result["candidate_edges"]]
        assert project.graph.compute_metrics(edge_metrics=supplied)["complete"]
        solved = project.graph.solve()
        assert solved["valid"] and not solved["reused"]
        assert project.graph.save_result()["valid"]
        # Graceful worker shutdown retains the project/artifact on disk.
        response = sdk.session.request("post", f"{sdk.base_url}/{sdk.user_id}/session/reset",
                                       headers={"X-Token": sdk.token}, timeout=30)
        response.raise_for_status()
        artifact = tmp_path / "server-temp" / project.project_id / "workspace" / project.project_path / "compiled_scenario.json"
        saved = artifact.read_bytes()
        assert hub.user_registry[sdk.user_id]["port"] is None
        assert project.graph.get_result(parameter_hash=solved["parameter_hash"])["valid"]
        assert hub.user_registry[sdk.user_id]["port"] is None  # Retrieval does not start a worker.
        assert project.activate()["valid"]
        project.graph.build_candidate_edges()
        project.graph.compute_metrics(edge_metrics=supplied)
        assert project.graph.solve()["reused"]
        assert artifact.read_bytes() == saved
        assert project.close()["closed"]
        assert artifact.read_bytes() == saved
        # Reuse the same user worker for a complete Cell project after explicit close.
        from test_placement_output import make_project
        cell_project, _ = make_project(sdk)
        assert cell_project.cell.tune_sectors()["valid"]
        cell_candidates = cell_project.cell.get_candidate_edges()
        cell_costs = [{"src": row["src"], "dst": row["dst"], "metric": 1.0}
                      for row in cell_candidates["candidate_edges"]]
        assert cell_project.cell.compute_metrics(edge_metrics=cell_costs)["complete"]
        cell_solution = cell_project.cell.solve()
        assert cell_solution["valid"] and cell_solution["cell"]["client_service"][0]["served"]
        assert cell_project.cell.save_result()["valid"]
        assert cell_project.save_placements()["valid"]
        assert cell_project.cell.get_solution()["antenna_placements"] == cell_solution["antenna_placements"]
        cell_project.close()
        assert cell_project.cell.get_result()["valid"]
        assert project.graph.get_result()["valid"]  # Other saved projects remain intact.
    finally:
        if sdk is not None:
            sdk.session.close()
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()
    assert not thread.is_alive()
