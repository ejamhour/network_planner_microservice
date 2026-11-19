"""Persistent temporary project identities, independent of worker sessions.

Only creation and authenticated recovery live here. Explicit upload lives in
project_sync.py. No compilation, planning or expiry deletion occurs. Input files live below workspace/;
the private project metadata stays outside that synchronized subtree.
"""
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
from tempfile import TemporaryDirectory
from uuid import uuid4


class ProjectNotFound(FileNotFoundError):
    pass


class ProjectAccessDenied(PermissionError):
    pass


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def create(self, project_path):
        """Create an empty project and return its recovery credential on creation."""
        relative = self._relative_path(project_path)
        project_id = uuid4().hex
        token = secrets.token_urlsafe(32)
        record = {
            "version": 1,
            "project_id": project_id,
            "project_path": relative.as_posix(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "credential_sha256": sha256(token.encode()).hexdigest(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".creating-", dir=self.root) as staging:
            staged = Path(staging).resolve()
            if not staged.is_relative_to(self.root):
                raise ValueError("Project staging must remain inside the temporary root")
            workspace = staged / "workspace"
            workspace.mkdir(mode=0o700)
            (workspace / relative).mkdir(parents=True, mode=0o700)
            metadata = staged / ".project.json"
            with metadata.open("x", encoding="utf-8") as stream:
                metadata.chmod(0o600)
                json.dump(record, stream)
            staged.rename(self.root / project_id)
        return {**self._public(record), "recovery_token": token}

    def resume(self, project_id, recovery_token):
        """Authenticate against disk metadata, including after process restarts."""
        if not re.fullmatch(r"[0-9a-f]{32}", project_id):
            raise ProjectNotFound("Remote project does not exist")
        directory = self.root / project_id
        # Do not follow aliases out of the storage root, including metadata links.
        if directory.is_symlink() or directory.resolve() != directory:
            raise ProjectNotFound("Remote project does not exist")
        metadata = directory / ".project.json"
        if metadata.is_symlink():
            raise ProjectNotFound("Remote project does not exist")
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProjectNotFound("Remote project does not exist") from exc
        if not isinstance(record, dict) or record.get("version") != 1 or record.get("project_id") != project_id:
            raise ValueError("Invalid stored project identity")
        expected = record.get("credential_sha256")
        if not isinstance(expected, str):
            raise ValueError("Invalid stored project credential")
        if not isinstance(recovery_token, str):
            raise ProjectAccessDenied("Invalid project recovery credential")
        provided = sha256(recovery_token.encode()).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise ProjectAccessDenied("Invalid project recovery credential")
        relative = self._relative_path(record["project_path"])
        workspace = directory / "workspace"
        local = workspace / relative
        if (workspace.is_symlink() or not local.is_dir()
                or not local.resolve().is_relative_to(workspace)):
            raise ValueError("Stored project workspace is unavailable")
        return self._public(record)

    @staticmethod
    def _relative_path(value):
        if not isinstance(value, str) or not value.strip() or "\\" in value or "\x00" in value:
            raise ValueError("Project path must be a nonempty workspace-relative POSIX path")
        path = PurePosixPath(value)
        if (path.is_absolute() or PureWindowsPath(value).anchor
                or ".." in path.parts or not path.parts):
            raise ValueError("Project path must remain inside the workspace")
        return path

    @staticmethod
    def _public(record):
        return {key: record[key] for key in ("project_id", "project_path", "created_at")}
