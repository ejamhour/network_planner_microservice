"""Common active-project guards and structured stage failures."""
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from cisei_lib.project_io.project_operations import current_project_inputs


async def operation_prerequisite(runtime, store, identity):
    """Check active ownership/freshness without activating or rebuilding."""
    active = runtime.active_project
    if active is None:
        return "project_not_active", "Activate the compiled project before running planning operations."
    if active["project_id"] != identity["project_id"]:
        raise HTTPException(409, detail={"code": "active_project_exists",
            "message": "Close the active project before using another.", "active_project_id": active["project_id"]})
    if runtime.project_cleanup_pending:
        raise HTTPException(503, detail={"code": "project_cleanup_failed", "message": "Retry close before using this project."})
    if await run_in_threadpool(current_project_inputs, store, identity) != active:
        return "active_project_outdated", "Close the project, rebuild if needed, and activate again."
    if any(state.get("status") == "running" for state in runtime.mesh_progress.values()):
        return "project_busy", "Wait for background planning to finish before starting another operation."


def operation_failure(issue, *, stage="candidate_generation"):
    code, message = issue
    return {"status": "OK", "kind": "json", "data": {
        "valid": False, "stage": stage, "issues": [
            {"code": code, "severity": "error", "message": message}]}}
