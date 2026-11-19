"""Publish effective placement output without replacing synchronized source files."""
from contextlib import closing
import sqlite3

from .diagnostics import ProjectIssue, ProjectValidationError
from .placements import save_placements
from .project_operations import _locked_inputs


def save_project_placements(store, identity, input_identity, scenario):
    with _locked_inputs(store, identity) as (folder, _, inputs):
        if inputs != input_identity:
            raise ProjectValidationError([ProjectIssue("scenario.toml", "active_project_outdated",
                "Inputs changed. Rebuild and activate before exporting placements.")], stage="placement_export")
        relative = "outputs/antenna_placements.toml"
        workspace_path = identity["project_path"] + "/" + relative
        with closing(sqlite3.connect(store.root / identity["project_id"] / ".sync.sqlite")) as connection:
            is_input = connection.execute("SELECT 1 FROM inputs WHERE path = ?", (workspace_path,)).fetchone()
        if is_input:
            raise ProjectValidationError([ProjectIssue(relative, "placement_output_is_input",
                "The output path is a synchronized input. Move/promote input placements to a separate source path.")], stage="placement_export")
        path = folder / relative
        if path.resolve() != path:
            raise ProjectValidationError([ProjectIssue(relative, "invalid_output_path",
                "Placement output must not use symbolic links.")], stage="placement_export")
        save_placements(scenario, path)
        return {"valid": True, "stage": "placement_export", "issues": [], **inputs,
                "file": relative, "content": path.read_text(encoding="utf-8")}
