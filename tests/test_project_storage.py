"""Temporary project identity survives session loss; recovery requires its secret."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import requests

from app import main_hub as hub
from app.routes_projects import get_project_store
from cisei_lib.project_io.project_store import ProjectStore
from cisei_planning_sdk_v2 import PlanningClient, ProjectChoiceRequired, ProjectMetadataSaveError


class HubTransport:
    """Use real hub routes while preserving requests.Response SDK behavior."""
    def __init__(self, api):
        self.api = api

    def request(self, method, url, **kwargs):
        kwargs.pop("timeout", None)
        kwargs["follow_redirects"] = kwargs.pop("allow_redirects", True)
        if "data" in kwargs:
            kwargs["content"] = kwargs.pop("data")
        result = self.api.request(method, url, **kwargs)
        response = requests.Response()
        response.status_code, response._content = result.status_code, result.content
        response.headers, response.url = dict(result.headers), str(result.url)
        return response


@pytest.fixture
def storage(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "server-temp")
    monkeypatch.setattr(hub, "user_registry", {})
    monkeypatch.setitem(hub.hub_app.dependency_overrides, get_project_store, lambda: store)
    with TestClient(hub.hub_app) as api:
        yield store, api


def sdk_for(workspace, api):
    (workspace / "projects").mkdir(parents=True, exist_ok=True)
    sdk = PlanningClient("http://testserver", workspace=workspace)
    sdk.session = HubTransport(api)
    return sdk


def test_creation_is_empty_private_and_does_not_require_worker(storage):
    store, api = storage
    response = api.post("/projects", json={"project_path": "projects/example"})
    assert response.status_code == 201
    data = response.json()["data"]
    directory = store.root / data["project_id"]
    assert (directory / "workspace/projects/example").is_dir()
    assert not list((directory / "workspace/projects/example").iterdir())
    assert not hub.user_registry
    metadata = (directory / ".project.json").read_text(encoding="utf-8")
    assert data["recovery_token"] not in metadata
    assert json.loads(metadata)["credential_sha256"]
    assert "credential_sha256" not in data
    assert str(store.root) not in response.text


def test_recovery_survives_worker_and_registry_loss(storage, monkeypatch):
    store, api = storage
    data = api.post("/projects", json={"project_path": "projects/example"}).json()["data"]
    directory = store.root / data["project_id"]
    artifact = directory / "workspace/projects/example/result.json"
    artifact.write_text('{"saved":true}', encoding="utf-8")
    # Timeout resets worker/session state; the disk-backed project is independent.
    monkeypatch.setattr(hub, "user_registry", {})
    monkeypatch.setitem(hub.hub_app.dependency_overrides, get_project_store,
                        lambda: ProjectStore(store.root))
    response = api.post(f"/projects/{data['project_id']}/resume",
                        headers={"X-Project-Token": data["recovery_token"]})
    assert response.status_code == 200
    assert response.json()["data"]["project_id"] == data["project_id"]
    assert "recovery_token" not in response.json()["data"]
    assert artifact.read_text(encoding="utf-8") == '{"saved":true}'


def test_credential_is_per_project_and_required(storage):
    store, api = storage
    first = api.post("/projects", json={"project_path": "projects/a"}).json()["data"]
    second = api.post("/projects", json={"project_path": "projects/a"}).json()["data"]
    assert first["project_id"] != second["project_id"]
    path = f"/projects/{first['project_id']}/resume"
    assert api.post(path).status_code == 422
    assert api.post(path, headers={"X-Project-Token": second["recovery_token"]}).status_code == 403
    assert len(list(store.root.iterdir())) == 2


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "C:relative", "projects/../../escape", "projects\\example", ""])
def test_invalid_workspace_paths_create_nothing(storage, path):
    store, api = storage
    assert api.post("/projects", json={"project_path": path}).status_code == 422
    assert not store.root.exists()


def test_missing_project_is_reported_without_replacement(storage):
    store, api = storage
    response = api.post("/projects/" + "a" * 32 + "/resume", headers={"X-Project-Token": "secret"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"
    assert not store.root.exists()


def test_sdk_creation_and_new_client_recovery_use_local_folder(storage, tmp_path):
    store, api = storage
    workspace = tmp_path / "client"
    sdk = sdk_for(workspace, api)
    local = workspace / "projects/example"
    opened = sdk.open_project(local)
    session = json.loads((local / ".session.json").read_text(encoding="utf-8"))
    assert session["project_id"] == opened.project_id
    assert session["project_path"] == "projects/example"
    assert not (local / "scenario.toml").exists()
    assert not opened.resumed
    assert session["recovery_token"] not in repr(opened)
    other_client = sdk_for(workspace, api)
    resumed = other_client.open_project(local, mode="resume")
    assert resumed.project_id == opened.project_id and resumed.resumed
    assert len(list(store.root.iterdir())) == 1
    assert other_client.project is resumed


def test_sdk_requires_choice_and_keeps_old_recovery_records(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    first = sdk.open_project(local)
    with pytest.raises(ProjectChoiceRequired) as caught:
        sdk.open_project(local)
    assert caught.value.project_id == first.project_id
    assert caught.value.choices == ("resume", "new")
    assert len(list(store.root.iterdir())) == 1
    second = sdk.open_project(local, mode="new")
    assert second.project_id != first.project_id
    metadata = json.loads((local / ".session.json").read_text(encoding="utf-8"))
    previous, = metadata["previous_projects"]
    assert previous["project_id"] == first.project_id
    assert store.resume(first.project_id, previous["recovery_token"])["project_id"] == first.project_id


def test_sdk_failed_resume_never_replaces_metadata_or_creates(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    sdk.open_project(local)
    path = local / ".session.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["project_id"] = "a" * 32
    path.write_text(json.dumps(metadata), encoding="utf-8")
    previous = path.read_bytes()
    with pytest.raises(FileNotFoundError, match="No replacement was created"):
        sdk.open_project(local, mode="resume")
    assert path.read_bytes() == previous
    assert len(list(store.root.iterdir())) == 1


def test_sdk_rejects_other_server_before_sending_secret(storage, tmp_path):
    _, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    sdk.open_project(local)
    sdk.base_url = "http://different-server"
    with pytest.raises(ValueError, match="credential was not sent"):
        sdk.open_project(local, mode="resume")


def test_failed_local_save_preserves_file_and_exposes_recovery_explicitly(storage, tmp_path, monkeypatch):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    first = sdk.open_project(local)
    previous = (local / ".session.json").read_bytes()
    import cisei_planning_sdk_v2.project as module
    def fail(*args, **kwargs):
        raise PermissionError("cannot write")
    monkeypatch.setattr(module, "_save_session", fail)
    with pytest.raises(ProjectMetadataSaveError) as caught:
        sdk.open_project(local, mode="new")
    assert (local / ".session.json").read_bytes() == previous
    assert sdk.project is first
    recovery = caught.value.recovery_metadata
    assert recovery["recovery_token"] not in str(caught.value)
    assert store.resume(recovery["project_id"], recovery["recovery_token"])


def test_corrupted_server_metadata_is_not_treated_as_missing(storage):
    store, api = storage
    data = api.post("/projects", json={"project_path": "projects/example"}).json()["data"]
    directory = store.root / data["project_id"]
    (directory / ".project.json").write_text("[]", encoding="utf-8")
    response = api.post(f"/projects/{data['project_id']}/resume",
                        headers={"X-Project-Token": data["recovery_token"]})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "project_storage_error"
    assert directory.exists()
    assert len(list(store.root.iterdir())) == 1


def test_corrupted_local_metadata_stops_without_creating_another_project(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    sdk.open_project(local)
    (local / ".session.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid .session.json"):
        sdk.open_project(local)
    assert len(list(store.root.iterdir())) == 1


def test_atomic_session_write_failure_preserves_previous_credentials(storage, tmp_path, monkeypatch):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    sdk.open_project(local)
    previous = (local / ".session.json").read_bytes()
    original = Path.replace
    def fail_session_replace(path, target):
        if Path(target).name == ".session.json":
            raise OSError("replacement failed")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", fail_session_replace)
    with pytest.raises(ProjectMetadataSaveError):
        sdk.open_project(local, mode="new")
    assert (local / ".session.json").read_bytes() == previous
    assert not list(local.glob(".session-*.tmp"))


def test_project_staging_failure_does_not_publish_half_created_directory(tmp_path, monkeypatch):
    root = tmp_path / "server-temp"
    store = ProjectStore(root)
    original = Path.open
    def fail_metadata(path, *args, **kwargs):
        if path.name == ".project.json":
            raise OSError("metadata write failed")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_metadata)
    with pytest.raises(OSError, match="metadata write failed"):
        store.create("projects/example")
    assert not list(root.iterdir())


def test_sdk_rejects_folder_outside_workspace_before_creating_it(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    outside = tmp_path / "outside"
    with pytest.raises(ValueError, match="workspace projects directory"):
        sdk.open_project(outside)
    assert not outside.exists()
    assert not store.root.exists()


@pytest.fixture
def sync_project(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/example"
    remote = sdk.open_project(local)
    (local / "inputs").mkdir()
    (sdk.workspace / "antennas").mkdir()
    (sdk.workspace / "metrics").mkdir()
    (local / "scenario.toml").write_text(
        '[files]\nnodes = "inputs/nodes.csv"\n'
        'antenna_library = "../../antennas/custom.yaml"\n'
        '[files.metrics]\nlte = "../../metrics/custom.toml"\n', encoding="utf-8"
    )
    (local / "inputs/nodes.csv").write_bytes(b"original node bytes\r\n")
    (sdk.workspace / "antennas/custom.yaml").write_bytes(b"opaque library\x00\xff")
    (sdk.workspace / "metrics/custom.toml").write_bytes(b"opaque metric\r\n")
    return store, api, sdk, remote


def test_sync_preserves_relative_structure_and_original_bytes(sync_project, monkeypatch):
    from hashlib import sha256
    from cisei_lib.planners import PlanningScenario
    store, _, sdk, remote = sync_project
    def forbidden(*args, **kwargs):
        raise AssertionError("sync must not construct planning objects")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    (remote.path / "result.json").write_text("local output", encoding="utf-8")
    report = remote.sync()
    workspace = store.root / remote.project_id / "workspace"
    expected = {"projects/example/scenario.toml", "projects/example/inputs/nodes.csv",
                "antennas/custom.yaml", "metrics/custom.toml"}
    assert {row["path"] for row in report["files"]} == expected
    for row in report["files"]:
        original = (sdk.workspace / row["path"]).read_bytes()
        assert (workspace / row["path"]).read_bytes() == original
        assert row["sha256"] == sha256(original).hexdigest()
        assert row["size"] == len(original) and row["action"] == "added"
    assert report["inputs_changed"] and report["input_revision"] == 1
    assert not (workspace / "projects/example/.session.json").exists()
    assert not (workspace / "projects/example/result.json").exists()
    assert not (workspace / "projects/example/compiled_scenario.json").exists()
    assert remote.sync_report == report


def test_sync_updates_only_changed_files_and_identical_retry_keeps_revision(sync_project):
    store, _, _, remote = sync_project
    first = remote.sync()
    workspace = store.root / remote.project_id / "workspace"
    times = {row["path"]: (workspace / row["path"]).stat().st_mtime_ns for row in first["files"]}
    identical = remote.sync()
    assert not identical["inputs_changed"]
    assert identical["input_hash"] == first["input_hash"] and identical["input_revision"] == 1
    assert all(row["action"] == "unchanged" for row in identical["files"])
    assert all((workspace / path).stat().st_mtime_ns == value for path, value in times.items())
    (remote.path / "inputs/nodes.csv").write_bytes(b"changed nodes")
    changed = remote.sync()
    assert changed["inputs_changed"] and changed["input_revision"] == 2
    assert changed["input_hash"] != first["input_hash"]
    assert {row["path"] for row in changed["files"] if row["action"] == "updated"} == {"projects/example/inputs/nodes.csv"}


def test_sync_retains_removed_local_files_and_server_outputs(sync_project):
    store, _, _, remote = sync_project
    remote.sync()
    local = remote.path
    server = store.root / remote.project_id / "workspace/projects/example"
    (server / "result.json").write_text("saved result", encoding="utf-8")
    (server / "compiled_scenario.json").write_text("saved compiled state", encoding="utf-8")
    (local / "inputs/nodes.csv").unlink()
    (local / "scenario.toml").write_text("[files]\n", encoding="utf-8")
    report = remote.sync()
    assert report["inputs_changed"]
    assert (server / "inputs/nodes.csv").read_bytes() == b"original node bytes\r\n"
    assert (server / "result.json").read_text(encoding="utf-8") == "saved result"
    assert (server / "compiled_scenario.json").read_text(encoding="utf-8") == "saved compiled state"


def test_sync_after_recovery_uses_persisted_hashes(sync_project):
    _, api, sdk, remote = sync_project
    first = remote.sync()
    resumed_client = sdk_for(sdk.workspace, api)
    resumed = resumed_client.open_project(remote.path, mode="resume")
    report = resumed.sync()
    assert not report["inputs_changed"]
    assert (report["input_hash"], report["input_revision"]) == (first["input_hash"], first["input_revision"])


def test_missing_input_stops_before_any_upload(sync_project):
    store, _, _, remote = sync_project
    (remote.path / "inputs/nodes.csv").unlink()
    with pytest.raises(FileNotFoundError, match="Missing sync input"):
        remote.sync()
    server = store.root / remote.project_id
    assert not (server / ".sync.sqlite").exists()
    assert not (server / "workspace/projects/example/scenario.toml").exists()


def test_explicit_file_list_can_sync_malformed_toml_for_server_validation(sync_project):
    store, _, _, remote = sync_project
    (remote.path / "scenario.toml").write_bytes(b"[broken")
    report = remote.sync(files=["inputs/nodes.csv"])
    assert len(report["files"]) == 2
    assert (store.root / remote.project_id / "workspace/projects/example/scenario.toml").read_bytes() == b"[broken"


def upload_archive(api, remote, files, *, wrong_digest=False):
    from hashlib import sha256
    from io import BytesIO
    from zipfile import ZipFile, ZIP_STORED
    manifest = {"version": 1, "project_path": remote.project_path,
                "scenario_file": remote.project_path + "/scenario.toml",
                "files": [{"path": name, "size": len(data), "sha256": sha256(data).hexdigest()}
                          for name, data in files.items()]}
    if wrong_digest:
        manifest["files"][-1]["sha256"] = "0" * 64
    payload = BytesIO()
    with ZipFile(payload, "w", compression=ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(".sync-manifest.json", json.dumps(manifest))
    return api.post(f"/projects/{remote.project_id}/sync", content=payload.getvalue(),
                    headers={"X-Project-Token": remote._recovery_token, "Content-Type": "application/zip"})


@pytest.mark.parametrize("bad_path", ["../escape", "/absolute", "C:/absolute", "projects/example/../escape",
                                     "projects/example/.session.json", "projects/example/compiled_scenario.json",
                                     "projects/example/result.json"])
def test_server_rejects_unsafe_upload_without_updating_other_files(sync_project, bad_path):
    store, api, _, remote = sync_project
    remote.sync()
    server = store.root / remote.project_id / "workspace/projects/example"
    previous = (server / "scenario.toml").read_bytes()
    response = upload_archive(api, remote, {"projects/example/scenario.toml": b"changed", bad_path: b"forbidden"})
    assert response.status_code == 422
    assert (server / "scenario.toml").read_bytes() == previous


def test_hash_mismatch_preserves_previous_batch(sync_project):
    store, api, _, remote = sync_project
    first = remote.sync()
    server = store.root / remote.project_id / "workspace/projects/example"
    previous = (server / "scenario.toml").read_bytes()
    response = upload_archive(api, remote, {"projects/example/scenario.toml": b"changed",
                                           "projects/example/data.bin": b"bytes"}, wrong_digest=True)
    assert response.status_code == 422
    assert (server / "scenario.toml").read_bytes() == previous
    assert not (server / "data.bin").exists()
    assert remote.sync()["input_hash"] == first["input_hash"]


def test_detected_commit_failure_rolls_back_prior_file_updates(sync_project, monkeypatch):
    import cisei_lib.project_io.project_sync as module
    store, api, _, remote = sync_project
    first = remote.sync()
    server = store.root / remote.project_id / "workspace/projects/example"
    previous = (server / "scenario.toml").read_bytes()
    original = module.os.replace
    def fail_second(source, target):
        if Path(target).name == "second.bin":
            raise OSError("simulated disk failure")
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", fail_second)
        response = upload_archive(api, remote, {"projects/example/scenario.toml": b"changed",
                                               "projects/example/second.bin": b"new"})
    assert response.status_code == 500
    assert (server / "scenario.toml").read_bytes() == previous
    assert not (server / "second.bin").exists()
    report = remote.sync()
    assert (report["input_hash"], report["input_revision"]) == (first["input_hash"], first["input_revision"])


def test_sync_requires_project_credential(sync_project):
    store, api, _, remote = sync_project
    response = api.post(f"/projects/{remote.project_id}/sync", content=b"not an archive",
                        headers={"X-Project-Token": "wrong", "Content-Type": "application/zip"})
    assert response.status_code == 403
    assert not (store.root / remote.project_id / ".sync.sqlite").exists()


def test_sync_real_graph_example_then_explicit_server_build(storage, tmp_path):
    import shutil
    import tomlkit
    from cisei_lib.project_io import load_compiled_scenario
    from cisei_lib.project_io.project_operations import load_project_scenario
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    local = sdk.workspace / "projects/graph"
    remote = sdk.open_project(local)
    example = Path(__file__).resolve().parents[1] / "examples/projects/graph_planner_wisun"
    document = tomlkit.parse((example / "scenario.toml").read_text(encoding="utf-8"))
    filename = document["instances"].pop("source")
    document["files"] = {"nodes": filename}
    (local / "scenario.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    shutil.copyfile(example / filename, local / filename)
    report = remote.sync()
    server = store.root / remote.project_id / "workspace/projects/graph"
    assert (server / filename).read_bytes() == (example / filename).read_bytes()
    assert not (server / "compiled_scenario.json").exists()
    identity = store.resume(remote.project_id, remote._recovery_token)
    missing, scenario = load_project_scenario(store, identity)
    assert scenario is None and missing["issues"][0]["code"] == "compiled_scenario_missing"
    built = remote.build()
    assert built["valid"], built
    assert built["stage"] == "scenario_build"
    assert built["input_hash"] == report["input_hash"]
    compiled = server / built["compiled_scenario"]
    assert len(load_compiled_scenario(compiled).site_nodes) == 13
    assert json.loads(compiled.read_text())["build_report"]["inputs"]["input_hash"] == report["input_hash"]
    previous = compiled.read_bytes()
    # Loading requires only the artifact, not scenario.toml or its CSV inputs.
    (server / "scenario.toml").rename(server / "scenario.hidden")
    (server / filename).rename(server / "nodes.hidden")
    try:
        loaded, scenario = load_project_scenario(store, identity)
        assert loaded["valid"] and len(scenario.site_nodes) == 13
        assert loaded["input_hash"] == report["input_hash"]
        assert compiled.read_bytes() == previous
    finally:
        (server / "scenario.hidden").rename(server / "scenario.toml")
        (server / "nodes.hidden").rename(server / filename)
    # Unsynced edits are ignored; failed later compilation preserves the artifact.
    (local / "scenario.toml").write_text('[broken', encoding="utf-8")
    assert remote.build()["valid"]
    remote.sync(files=[])
    failed = remote.build()
    assert not failed["valid"] and failed["stage"] == "input_validation"
    assert compiled.read_bytes() == previous
    stale, scenario = load_project_scenario(store, identity)
    assert scenario is None and stale["issues"][0]["code"] == "compiled_scenario_outdated"
    (local / "scenario.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    assert remote.sync()["input_hash"] == report["input_hash"]
    resumed = sdk.open_project(local, mode="resume")
    assert resumed.build()["valid"]
    resumed.sync()  # Identical retry leaves the compiled scenario current.
    assert load_project_scenario(store, identity)[0]["valid"]
    import csv
    with (local / filename).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        columns, rows = reader.fieldnames, list(reader)
    rows[0]["x"] = str(float(rows[0]["x"]) + 10)
    with (local / filename).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    resumed.sync()
    warning_report = resumed.build()
    assert warning_report["valid"]
    assert any(issue["code"] == "coordinate_mismatch" and issue["severity"] == "warning"
               for issue in warning_report["issues"])
    assert json.loads(compiled.read_text())["build_report"]["issues"] == warning_report["issues"]


def test_sdk_never_uploads_session_credentials_even_when_listed(sync_project):
    store, _, _, remote = sync_project
    (remote.path / "scenario.toml").write_text('[files]\nsecret = ".session.json"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Session credentials"):
        remote.sync()
    assert not (store.root / remote.project_id / "workspace/projects/example/scenario.toml").exists()


def test_legacy_and_new_sdks_coexist_with_distinct_opening_behavior(storage, tmp_path):
    from cisei_planning_sdk import PlanningClient as LegacyClient
    from cisei_planning_sdk_v2 import Project
    _, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    folder = sdk.workspace / "projects/empty"
    opened = sdk.open_project(folder)
    assert isinstance(opened, Project)
    assert not (folder / "scenario.toml").exists()
    legacy = LegacyClient("http://testserver", workspace=sdk.workspace, auto_register=False)
    with pytest.raises(FileNotFoundError, match="Project scenario not found"):
        legacy.open_project(folder)
    assert hasattr(legacy, "define_scenario") and not hasattr(sdk, "define_scenario")


def test_validation_before_sync_reports_missing_source(storage, tmp_path):
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/empty")
    report = project.validate()
    assert not report["valid"]
    assert report["stage"] == "input_validation"
    assert report["input_revision"] == 0 and report["input_hash"] is None
    assert report["issues"][0]["code"] == "missing_file"
    assert str(store.root) not in json.dumps(report)


def test_validation_checks_synced_revision_and_returns_recoverable_feedback(sync_project):
    store, _, _, project = sync_project
    project.scenario_path.write_text('[files]\nnodes = "inputs/nodes.csv"\n', encoding="utf-8")
    (project.path / "inputs/nodes.csv").write_text(
        'site_id,device_profile,lat,lon\na,radio,-25,-49\n', encoding="utf-8")
    first = project.sync()
    server = store.root / project.project_id / "workspace" / project.project_path
    # A successful syntax pass neither compiles nor changes the input identity.
    report = project.validate()
    assert report["valid"], report
    assert report["input_hash"] == first["input_hash"]
    assert report["input_revision"] == first["input_revision"]
    assert not (server / "compiled_scenario.json").exists()
    source = project.scenario_path.read_bytes()
    project.scenario_path.write_bytes(b"[broken")
    assert project.validate() == report  # Local edits are not uploaded implicitly.
    changed = project.sync(files=[])
    failed = project.validate()
    assert not failed["valid"] and failed["issues"]
    assert failed["input_hash"] == changed["input_hash"] != first["input_hash"]
    assert failed["input_revision"] == first["input_revision"] + 1
    assert failed["issues"][0]["file"] == "scenario.toml"
    assert str(store.root) not in json.dumps(failed)
    project.scenario_path.write_bytes(source)
    restored = project.sync()
    assert project.validate()["valid"]
    assert project.validate()["input_revision"] == restored["input_revision"]


def test_validation_requires_project_credential(storage):
    _, api = storage
    data = api.post("/projects", json={"project_path": "projects/example"}).json()["data"]
    route = f"/projects/{data['project_id']}/validate"
    assert api.post(route).status_code == 422
    assert api.post(route, headers={"X-Project-Token": "wrong"}).status_code == 403


def test_build_distinguishes_syntax_from_construction_and_requires_auth(storage, tmp_path):
    _, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/empty")
    assert not project.build()["valid"]
    project.scenario_path.write_text('[scenario]\nworking_crs = "EPSG:4326"\n', encoding="utf-8")
    project.sync()
    assert project.validate()["valid"]
    report = project.build()
    assert not report["valid"] and report["stage"] == "scenario_build"
    assert any(issue["code"] == "no_interfaces" for issue in report["issues"])
    route = f"/projects/{project.project_id}/build"
    assert api.post(route).status_code == 422
    assert api.post(route, headers={"X-Project-Token": "wrong"}).status_code == 403


def test_compiled_loader_rejects_corruption_and_unknown_provenance(storage, tmp_path):
    from cisei_lib.project_io.project_operations import load_project_scenario
    store, api = storage
    sdk = sdk_for(tmp_path / "client", api)
    project = sdk.open_project(sdk.workspace / "projects/example")
    identity = store.resume(project.project_id, project._recovery_token)
    artifact = store.root / project.project_id / "workspace" / project.project_path / "compiled_scenario.json"
    for contents, expected in [('[broken', "invalid_compiled_scenario"),
                               ('{"build_report":{"valid":true}}', "compiled_scenario_outdated")]:
        artifact.write_text(contents, encoding="utf-8")
        report, scenario = load_project_scenario(store, identity)
        assert scenario is None and not report["valid"]
        assert report["issues"][0]["code"] == expected
        assert artifact.read_text(encoding="utf-8") == contents
