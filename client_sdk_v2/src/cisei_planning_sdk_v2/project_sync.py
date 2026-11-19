"""Select local input paths and stream raw file snapshots to project storage.

Only [files] is read for transfer selection; no profiles, placements, metrics or
planning settings are interpreted. An explicit files list supports uploading a
malformed/partial TOML for subsequent server-side validation.
"""
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryFile
from zipfile import ZIP_STORED, ZipFile

import tomlkit

CHUNK_SIZE = 1024 * 1024
MANIFEST_NAME = ".sync-manifest.json"


def sync_project(project, *, files=None):
    client = project._client
    if client.base_url != project.server_url:
        raise ValueError("Remote project belongs to another server; credential was not sent")
    if project.path.relative_to(client.workspace).as_posix() != project.project_path:
        raise ValueError("Project moved within the workspace; restore its relative path or create a new remote project before sync")
    scenario = _checked_source(project.scenario_path, client.workspace)
    scenario_bytes = None
    if files is None:
        scenario_bytes = scenario.read_bytes()
        document = tomlkit.parse(scenario_bytes.decode("utf-8")).unwrap()
        paths = list(_file_paths(document.get("files", {})))
    else:
        if isinstance(files, (str, Path)):
            raise TypeError("files must be a sequence of project-relative paths")
        paths = [str(path) for path in files]
    selected = {scenario.relative_to(client.workspace).as_posix(): scenario}
    for name in paths:
        if not isinstance(name, str) or not name.strip() or "\\" in name or PureWindowsPath(name).anchor or Path(name).is_absolute():
            raise ValueError("Input references must be relative paths using '/' separators")
        source = Path(os.path.abspath(scenario.parent / name))
        source = _checked_source(source, client.workspace)
        selected[source.relative_to(client.workspace).as_posix()] = source
    manifest = {"version": 1, "project_path": project.project_path,
                "scenario_file": scenario.relative_to(client.workspace).as_posix(), "files": []}
    with TemporaryFile(mode="w+b") as payload:
        with ZipFile(payload, mode="w", compression=ZIP_STORED, allowZip64=True) as archive:
            for relative, source in sorted(selected.items()):
                digest = sha256()
                size = 0
                with archive.open(relative, "w", force_zip64=True) as destination:
                    if source == scenario and scenario_bytes is not None:
                        destination.write(scenario_bytes)
                        digest.update(scenario_bytes)
                        size = len(scenario_bytes)
                    else:
                        with source.open("rb") as stream:
                            while chunk := stream.read(CHUNK_SIZE):
                                destination.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                manifest["files"].append({"path": relative, "size": size, "sha256": digest.hexdigest()})
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        payload.seek(0)
        report = client._request(
            "post", f"/projects/{project.project_id}/sync",
            headers={"X-Project-Token": project._recovery_token, "Content-Type": "application/zip"},
            data=payload, allow_redirects=False,
        )["data"]
    project.sync_report = report
    return report


def _checked_source(source, workspace):
    if (not source.is_relative_to(workspace) or not source.resolve().is_relative_to(workspace)
            or source.is_symlink() or source.resolve() != source):
        raise ValueError("Input file must remain inside the workspace without symlink aliases")
    if any(part.casefold() in {".session.json", ".git"} for part in source.relative_to(workspace).parts):
        raise ValueError("Session credentials and Git metadata are not project inputs")
    if not source.is_file():
        raise FileNotFoundError(f"Missing sync input: {source.name}")
    return source


def _file_paths(value):
    """Walk file-list containers only; the server interprets their role names."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _file_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _file_paths(child)
    else:
        raise ValueError("[files] entries must be paths, arrays or tables of paths")
