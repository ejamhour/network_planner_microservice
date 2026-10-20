"""Verify service-specific wheel selection and joint installation without pip/network."""
import importlib.util
from pathlib import Path
import sys
import tomllib
import json
from io import BytesIO
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("prepare_workspace", ROOT / "examples/prepare_workspace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_joint_install_from_separate_services(tmp_path):
    module = load_script()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "download" in command:
            destination = Path(command[command.index("--dest") + 1])
            package = "cisei_planning_sdk-0.1.36" if "planning" in command[-1] else "cisei_features_sdk-0.1.1"
            (destination / (package + "-py3-none-any.whl")).write_bytes(b"test wheel")

    with patch.object(sys, "argv", ["prepare_workspace.py", "--base-url", "http://network:8080", "--features-base-url", "https://geo.example", "--workspace", str(tmp_path)]), patch.object(module.subprocess, "run", side_effect=run):
        module.main()
    assert len(calls) == 4
    assert "http://network:8080/sdk/simple" in calls[0]
    assert "https://geo.example/sdk/simple" in calls[1]
    assert "--no-deps" in calls[0] and "--no-deps" in calls[1]
    assert "--trusted-host" in calls[0] and "--trusted-host" not in calls[1]
    assert calls[2].count("install") == 1
    assert calls[2][-2].endswith("cisei_planning_sdk-0.1.36-py3-none-any.whl[notebook]")
    assert calls[2][-1].endswith("cisei_features_sdk-0.1.1-py3-none-any.whl[notebook]")
    assert "--index-url" not in calls[2]
    assert "init_workspace" in calls[3][2]


def test_features_download_failure_prevents_install():
    module = load_script()
    def run(command, **kwargs):
        if "cisei-features-sdk" in command[-1]:
            raise module.subprocess.CalledProcessError(1, command)
    with patch.object(sys, "argv", ["prepare_workspace.py"]), patch.object(module.subprocess, "run", side_effect=run) as execute:
        with pytest.raises(module.subprocess.CalledProcessError):
            module.main()
        assert execute.call_count == 2


def test_invalid_features_url_fails_before_pip():
    module = load_script()
    with patch.object(sys, "argv", ["prepare_workspace.py", "--features-base-url", "file:///tmp"]), patch.object(module.subprocess, "run") as execute:
        with pytest.raises(SystemExit):
            module.main()
        execute.assert_not_called()


def test_dependency_metadata_and_bootstrap_copies_agree():
    metadata = tomllib.loads((ROOT / "client_sdk/pyproject.toml").read_text())
    assert "cisei-features-sdk>=0.1.1,<0.2.0" in metadata["project"]["dependencies"]
    assert "cisei-features-sdk[notebook]>=0.1.1,<0.2.0" in metadata["project"]["optional-dependencies"]["notebook"]
    assert metadata["project"]["version"] == "0.1.36"
    assert (ROOT / "examples/prepare_workspace.py").read_bytes() == (ROOT / "tests/jupyter/prepare_workspace.py").read_bytes()


def test_notebook_refresh_backs_up_edits_and_preserves_projects(tmp_path):
    module = load_script()
    notebook = tmp_path / 'notebooks/example.ipynb'
    notebook.parent.mkdir()
    notebook.write_text('local edits')
    project = tmp_path / 'projects/result.json'
    project.parent.mkdir()
    project.write_text('saved result')
    manifest = {'files': ['notebooks/example.ipynb', 'projects/result.json', 'notebooks/../escape.ipynb']}
    new = json.dumps({'cells': [], 'nbformat': 4}).encode()
    def open_url(url, **kwargs):
        return BytesIO(json.dumps(manifest).encode() if url.endswith('manifest.json') else new)
    with patch.object(module, 'urlopen', side_effect=open_url):
        module.refresh_notebooks(tmp_path, 'http://network')
    assert notebook.read_bytes() == new
    assert next((tmp_path / 'notebook_backups').rglob('example.ipynb')).read_text() == 'local edits'
    assert project.read_text() == 'saved result'
    assert not (tmp_path / 'escape.ipynb').exists()
