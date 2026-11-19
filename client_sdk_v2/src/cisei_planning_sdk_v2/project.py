"""Local project recovery metadata and noninteractive hub-level project opening.

This is independent of the legacy scenario-loading path. Opening creates/resumes
storage only. The separate sync() operation uploads files without compiling or
planning.
"""
from dataclasses import dataclass, field
from functools import cached_property
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from .planners import GraphPlanning, CellPlanning, MeshPlanning



class ProjectChoiceRequired(ValueError):
    """An interface or agent must choose resume/new; the SDK never calls input()."""
    choices = ("resume", "new")

    def __init__(self, project_id, server_url):
        self.project_id, self.server_url = project_id, server_url
        super().__init__(f"This folder has saved metadata for remote project {project_id} at {server_url}. Choose mode='resume' or mode='new'.")


class ProjectMetadataSaveError(OSError):
    """Recovery data is available explicitly, never printed in the message."""
    def __init__(self, recovery_metadata):
        self.recovery_metadata = recovery_metadata
        super().__init__("Remote project opened, but .session.json could not be saved. Recovery metadata is available on this exception.")


@dataclass
class Project:
    path: Path
    project_id: str
    server_url: str
    project_path: str
    created_at: str
    resumed: bool
    _recovery_token: str = field(repr=False)
    _client: object = field(repr=False)
    sync_report: dict | None = field(default=None, repr=False)

    @property
    def scenario_path(self):
        return self.path / "scenario.toml"

    def sync(self, *, files=None):
        """Explicitly upload inputs without validation, compilation or planning."""
        from .project_sync import sync_project
        return sync_project(self, files=files)

    def validate(self):
        """Check uploaded syntax and return its report, including blocking issues.

        Does not sync local changes or compile. Inspect report['valid'] and
        report['issues']; transport/authentication failures remain exceptions.
        """
        return self._client._request(
            "post", f"/projects/{self.project_id}/validate",
            headers={"X-Project-Token": self._recovery_token},
        )["data"]


    def build(self):
        """Compile uploaded inputs remotely; return errors/warnings as report data.

        No implicit upload or planning. Failed builds preserve the previous
        compiled artifact; successful builds replace it on the server.
        """
        return self._client._request(
            "post", f"/projects/{self.project_id}/build",
            headers={"X-Project-Token": self._recovery_token},
        )["data"]

    def activate(self):
        """Load the current compiled artifact in this user's worker, without build."""
        return self._client._worker_request(
            "post", f"/projects/{self.project_id}/activate",
            headers={"X-Project-Token": self._recovery_token},
        )["data"]

    def close(self):
        """Release active planning/features resources; preserve saved project files."""
        return self._client._worker_request(
            "post", f"/projects/{self.project_id}/close",
            headers={"X-Project-Token": self._recovery_token},
        )["data"]

    @cached_property
    def graph(self):
        """Static Graph planning interface; accessing it performs no API call."""
        return GraphPlanning(self)

    def export_placements(self):
        """Save/export effective placements on the server, separate from input files."""
        return self._client._worker_request(
            "post", f"/projects/{self.project_id}/antenna_placements/export",
            headers={"X-Project-Token": self._recovery_token},
        )["data"]

    def save_placements(self, path=None):
        """Save the server-produced placement TOML locally; never rewrite inputs implicitly."""
        from .artifacts import write_text_artifact
        report = self.export_placements()
        if report["valid"]:
            destination = self.path / report["file"] if path is None else path
            saved = write_text_artifact(destination, report["content"])
            report = {**report, "local_path": str(saved)}
        return report

    @cached_property
    def cell(self):
        """Cell planning interface, separate from project lifecycle."""
        return CellPlanning(self)

    @cached_property
    def mesh(self):
        """Mesh planning interface; owns repeater and future link operations."""
        return MeshPlanning(self)


def open_project(client, folder, *, mode=None):
    """Create/resume a remote directory associated with a local project folder.

    With saved metadata, omitting mode raises ProjectChoiceRequired. A failed
    resume never creates another project. A new project preserves prior recovery
    records in the same private session file rather than orphaning them locally.
    """
    if mode not in {None, "resume", "new"}:
        raise ValueError("mode must be 'resume' or 'new'")
    projects_root = (client.workspace / "projects").resolve()
    candidate = Path(folder).expanduser().resolve()
    if not candidate.is_relative_to(projects_root):
        raise ValueError("Project must be inside the workspace projects directory")
    candidate.mkdir(parents=True, exist_ok=True)
    session_path = candidate / ".session.json"
    if session_path.is_symlink():
        raise ValueError(".session.json must not be a symbolic link")
    existing = None
    if session_path.exists():
        existing = json.loads(session_path.read_text(encoding="utf-8"))
        required = ("server_url", "project_id", "project_path", "recovery_token")
        if (not isinstance(existing, dict) or existing.get("version") != 1
                or any(not isinstance(existing.get(key), str) or not existing[key] for key in required)
                or not re.fullmatch(r"[0-9a-f]{32}", existing.get("project_id", ""))
                or not isinstance(existing.get("previous_projects", []), list)):
            raise ValueError("Invalid .session.json recovery metadata")
    if existing is not None and mode is None:
        raise ProjectChoiceRequired(existing["project_id"], existing["server_url"])
    if mode == "resume":
        if existing is None:
            raise FileNotFoundError("No .session.json to resume; choose mode='new' to create a project")
        if existing["server_url"].rstrip("/") != client.base_url:
            raise ValueError("Saved project belongs to another server; its recovery credential was not sent")
        try:
            data = client._request(
                "post", f"/projects/{existing['project_id']}/resume",
                headers={"X-Project-Token": existing["recovery_token"]}, allow_redirects=False,
            )["data"]
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise FileNotFoundError("Remote project no longer exists. No replacement was created; choose mode='new' explicitly.") from exc
            raise
        if data["project_id"] != existing["project_id"] or data["project_path"] != existing["project_path"]:
            raise ValueError("Server returned a different project identity")
        metadata = dict(existing)
        metadata.update(data)
    else:
        relative = candidate.relative_to(client.workspace).as_posix()
        data = client._request("post", "/projects",
                               json={"project_path": relative}, allow_redirects=False)["data"]
        metadata = {"version": 1, "server_url": client.base_url, **data}
        if existing is not None:
            previous = list(existing.get("previous_projects", []))
            previous.append({key: value for key, value in existing.items() if key != "previous_projects"})
            metadata["previous_projects"] = previous
    try:
        _save_session(session_path, metadata)
    except OSError as exc:
        raise ProjectMetadataSaveError(metadata) from exc
    handle = Project(candidate, metadata["project_id"], client.base_url,
                           metadata["project_path"], metadata["created_at"], mode == "resume",
                           metadata["recovery_token"], client)
    client.project = handle
    return handle


def _save_session(path, metadata):
    """Replace recovery metadata atomically using a private temporary file."""
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".session-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(metadata, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
