"""Atomic planning-result persistence, separate from planners and HTTP adapters.

The last successful result stays on disk. Input identity and an execution
parameter hash determine whether it can be returned; input sync never deletes it.
"""
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .diagnostics import ProjectIssue, ProjectReport, ProjectValidationError
from .project_operations import _locked_inputs


def load_planning_result(store, identity, *, parameter_hash=None, planner="graph"):
    with _locked_inputs(store, identity) as (folder, _, inputs):
        path = folder / "result.json"
        code, message, result = None, None, None
        if path.is_symlink():
            code, message = "invalid_saved_result", "Saved result must be a regular project artifact."
        else:
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                code, message = "result_missing", "Solve the project before retrieving a saved result."
            except (ValueError, UnicodeError):
                code, message = "invalid_saved_result", "Saved result is unreadable. Run planning again."
            else:
                metadata = result.get("project_run") if isinstance(result, dict) else None
                if not isinstance(metadata, dict) or metadata.get("planner") not in {"graph", "cell"}:
                    code, message = "invalid_saved_result", "Saved result has no supported execution metadata."
                elif metadata["planner"] != planner:
                    code, message = "result_planner_mismatch", f"Saved result belongs to {metadata['planner']}, not {planner}."
                elif (not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("parameter_hash", "")))
                      or not isinstance(result.get("counts"), dict)
                      or not isinstance(result.get("planned_nodes"), list)
                      or not isinstance(result.get("planned_edges"), list)):
                    code, message = "invalid_saved_result", "Saved result has incomplete execution data. Run planning again."
                elif metadata.get("inputs") != inputs:
                    code, message = "result_outdated", "Saved result belongs to older project inputs. Rebuild and plan again."
                elif parameter_hash is not None and metadata.get("parameter_hash") != parameter_hash:
                    code, message = "result_parameters_mismatch", "Saved result belongs to different execution parameters."
        if code:
            return {**ProjectReport("result_retrieval", (ProjectIssue("result.json", code, message),)).to_dict(), **inputs}
        return {**ProjectReport("result_retrieval").to_dict(), **inputs,
                "parameter_hash": result["project_run"]["parameter_hash"], "result": result}


def save_planning_result(store, identity, input_identity, result):
    """Publish a full result only if its inputs still match synchronized storage."""
    with _locked_inputs(store, identity) as (folder, _, inputs):
        if inputs != input_identity:
            raise ProjectValidationError([ProjectIssue("result.json", "active_project_outdated",
                "Inputs changed during planning. Rebuild and activate again.")], stage=f"{result['project_run']['planner']}_solve")
        path = folder / "result.json"
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
                temporary = stream.name
                json.dump(result, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return path.name
