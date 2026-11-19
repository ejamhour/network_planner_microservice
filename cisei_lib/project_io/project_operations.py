"""Validate, compile or load a synchronized project under the input commit lock.

The sync database lock prevents a committed upload from changing files during
validation. Reports identify the input revision checked, not later local edits.
"""
import sqlite3
from contextlib import contextmanager

from .diagnostics import ProjectIssue, ProjectReport, ProjectValidationError
from .input_validation import validate_inputs
from .compiled_scenario import build_scenario, read_compiled_scenario
from cisei_lib.planners.shared.planning_scenario import PlanningScenario


def project_report(store, identity, *, compile_scenario=False):
    """Return stage feedback tied to one input revision; never sync or plan.

    Compilation atomically saves a complete artifact with its input identity.
    Expected input failures return reports and preserve the previous artifact.
    """
    with _locked_inputs(store, identity) as (folder, state, inputs):
        expected = identity["project_path"] + "/scenario.toml"
        if state.get("scenario_file", expected) != expected:
            report = ProjectReport("input_validation", (ProjectIssue(
                file=state["scenario_file"], code="unsupported_scenario_path",
                message="Project operations require scenario.toml at the project root.",
            ),)).to_dict()
        else:
            try:
                report = (build_scenario(folder, return_report=True, input_identity=inputs)
                          if compile_scenario else validate_inputs(folder))
            except ProjectValidationError as exc:
                report = exc.to_dict()
        return {**report, **inputs}


def load_project_scenario(store, identity):
    """Return (report, scenario) from the current saved artifact, or (report, None).

    Caller supplies an authenticated project identity. No source files are read,
    compiled or modified. Only artifacts matching the current sync revision/hash
    can be loaded; the caller owns subsequent worker/runtime publication.
    """
    with _locked_inputs(store, identity) as (folder, _, inputs):
        artifact = folder / "compiled_scenario.json"
        code, message, scenario = None, None, None
        if artifact.is_symlink():
            code, message = "invalid_compiled_scenario", "Compiled scenario must be a regular project artifact. Rebuild the project."
        else:
            try:
                data = read_compiled_scenario(artifact)
            except FileNotFoundError:
                code, message = "compiled_scenario_missing", "Build the project before loading its compiled scenario."
            except (ValueError, UnicodeError):
                code, message = "invalid_compiled_scenario", "Compiled scenario is unreadable. Rebuild the project."
            else:
                saved = data.get("build_report") if isinstance(data, dict) else None
                if (not isinstance(saved, dict) or saved.get("valid") is not True
                        or not inputs["input_hash"] or saved.get("inputs") != inputs):
                    code, message = "compiled_scenario_outdated", "Compiled scenario does not match synchronized inputs. Rebuild the project."
                else:
                    try:
                        scenario = PlanningScenario.from_config_dict(data)
                    except (ValueError, TypeError, KeyError):
                        code, message = "invalid_compiled_scenario", "Compiled scenario cannot be loaded. Rebuild the project."
        issues = (ProjectIssue("compiled_scenario.json", code, message),) if code else ()
        return {**ProjectReport("scenario_load", issues).to_dict(), **inputs}, scenario


@contextmanager
def _locked_inputs(store, identity):
    """Serialize source/artifact reads with sync and build publication."""
    directory = store.root / identity["project_id"]
    folder = directory / "workspace" / identity["project_path"]
    connection = sqlite3.connect(directory / ".sync.sqlite", timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        state = dict(connection.execute("SELECT key, value FROM state"))
        inputs = {"project_id": identity["project_id"],
                  "input_revision": int(state.get("input_revision", "0")),
                  "input_hash": state.get("input_hash")}
        yield folder, state, inputs
    finally:
        connection.rollback()
        connection.close()


def current_project_inputs(store, identity):
    """Read synchronized input identity without parsing or loading project files."""
    with _locked_inputs(store, identity) as (_, _, inputs):
        return inputs
