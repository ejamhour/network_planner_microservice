"""Verify and apply raw input uploads without interpreting planning files.

Uploads are streamed ZIP_STORED archives. A manifest describes every member.
All bytes are checked in private staging before workspace updates; SQLite
serializes commits and retains input hashes independently of worker sessions.
"""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
from typing import Literal
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MANIFEST_NAME = ".sync-manifest.json"
CHUNK_SIZE = 1024 * 1024


class SyncInputError(ValueError):
    pass


class SyncFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    size: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SyncManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    project_path: str
    scenario_file: str
    files: list[SyncFile]


def sync_archive(store, project_id, recovery_token, archive_path):
    """Add/update supplied inputs; retain unmentioned files and compiled state."""
    identity = store.resume(project_id, recovery_token)
    directory = store.root / project_id
    workspace = directory / "workspace"
    try:
        with TemporaryDirectory(prefix=".sync-", dir=directory) as temporary:
            staging = Path(temporary).resolve()
            if not staging.is_relative_to(directory):
                raise SyncInputError("Sync staging escaped the project directory")
            with ZipFile(archive_path) as archive:
                manifest, destinations = _stage_archive(store, archive, identity, workspace, staging)
            return _commit_batch(directory, workspace, staging, manifest, destinations)
    except SyncInputError:
        raise
    except (BadZipFile, ValidationError, UnicodeError, ValueError) as exc:
        raise SyncInputError("Invalid input upload archive or manifest") from exc


def _stage_archive(store, archive, identity, workspace, staging):
    members = archive.infolist()
    names = [member.filename for member in members]
    if len(set(names)) != len(names) or names.count(MANIFEST_NAME) != 1:
        raise SyncInputError("Upload requires unique paths and exactly one manifest")
    for member in members:
        if member.is_dir() or member.compress_type != ZIP_STORED or stat.S_ISLNK(member.external_attr >> 16):
            raise SyncInputError("Upload accepts only uncompressed regular files")
    manifest = SyncManifest.model_validate_json(archive.read(MANIFEST_NAME))
    if manifest.project_path != identity["project_path"]:
        raise SyncInputError("Upload belongs to a different project directory")
    inventory = [entry.path for entry in manifest.files]
    if len(set(inventory)) != len(inventory) or set(names) != {MANIFEST_NAME, *inventory}:
        raise SyncInputError("Archive files must match the manifest exactly")
    scenario = store._relative_path(manifest.scenario_file)
    if not scenario.is_relative_to(PurePosixPath(identity["project_path"])) or manifest.scenario_file not in inventory:
        raise SyncInputError("The project scenario must be included in the upload")
    destinations = []
    seen = set()
    for entry in manifest.files:
        try:
            relative = store._relative_path(entry.path)
        except ValueError as exc:
            raise SyncInputError(str(exc)) from exc
        if relative.as_posix() != entry.path:
            raise SyncInputError("Upload paths must use canonical relative components")
        if any(part.casefold() in {".session.json", ".project.json", ".sync.sqlite", ".git", MANIFEST_NAME} for part in relative.parts):
            raise SyncInputError("Private metadata and version-control files cannot be uploaded")
        project = PurePosixPath(identity["project_path"])
        if relative.as_posix().casefold() in {(project / "compiled_scenario.json").as_posix().casefold(),
                                             (project / "result.json").as_posix().casefold()}:
            raise SyncInputError("Generated project artifacts cannot be overwritten by input sync")
        target = workspace / relative
        if not target.resolve().is_relative_to(workspace) or target.resolve() != target:
            raise SyncInputError("Upload path escapes the project workspace")
        if target in seen or (target.exists() and not target.is_file()):
            raise SyncInputError("Upload paths conflict with existing workspace entries")
        seen.add(target)
        member = archive.getinfo(entry.path)
        if member.file_size != entry.size:
            raise SyncInputError(f"Size mismatch for {entry.path}")
        staged = staging / "files" / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        with archive.open(member) as source, staged.open("wb") as output:
            while chunk := source.read(CHUNK_SIZE):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != entry.sha256:
            raise SyncInputError(f"SHA-256 mismatch for {entry.path}")
        destinations.append((entry, target, staged))
    return manifest, destinations


def _commit_batch(directory, workspace, staging, manifest, destinations):
    applied = []
    connection = sqlite3.connect(directory / ".sync.sqlite", timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS inputs (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        state = dict(connection.execute("SELECT key, value FROM state"))
        report_files = []
        for entry, target, staged in destinations:
            # Recheck under the commit lock before touching a workspace path.
            if not target.resolve().is_relative_to(workspace) or target.resolve() != target:
                raise SyncInputError("Upload destination changed outside the workspace")
            present = target.exists()
            if present and not target.is_file():
                raise SyncInputError("Upload destination is not a regular file")
            unchanged = False
            if present and target.stat().st_size == entry.size:
                with target.open("rb") as stream:
                    digest = sha256()
                    while chunk := stream.read(CHUNK_SIZE):
                        digest.update(chunk)
                unchanged = digest.hexdigest() == entry.sha256
            action = "unchanged" if unchanged else "updated" if present else "added"
            if not unchanged:
                backup = staging / "backup" / entry.path
                if present:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, backup)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                applied.append((target, backup if present else None))
            connection.execute("INSERT OR REPLACE INTO inputs VALUES (?, ?, ?)",
                               (entry.path, entry.sha256, entry.size))
            report_files.append({"path": entry.path, "size": entry.size, "sha256": entry.sha256, "action": action})
        all_inputs = list(connection.execute("SELECT path, sha256, size FROM inputs ORDER BY path"))
        fingerprint = sha256(json.dumps({"scenario_file": manifest.scenario_file, "files": all_inputs}, separators=(",", ":")).encode()).hexdigest()
        changed = fingerprint != state.get("input_hash") or any(row["action"] != "unchanged" for row in report_files)
        revision = int(state.get("input_revision", "0")) + int(changed)
        for key, value in {"input_hash": fingerprint, "input_revision": str(revision),
                           "scenario_file": manifest.scenario_file}.items():
            connection.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))
        connection.commit()
        return {"project_id": directory.name, "input_revision": revision, "input_hash": fingerprint,
                "inputs_changed": changed, "files": report_files}
    except BaseException:
        # Restore files if a commit/write fails. Files not mentioned by sync are never removed.
        for target, backup in reversed(applied):
            if not target.resolve().is_relative_to(workspace):
                raise SyncInputError("Cannot roll back a destination outside the workspace")
            if backup is not None:
                os.replace(backup, target)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        connection.rollback()
        connection.close()
