"""Export current effective installations from active planning state."""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from app.routes_projects import authorized_project
from cisei_lib.project_io.diagnostics import ProjectValidationError
from cisei_lib.project_io.placement_output import save_project_placements
from .guards import operation_prerequisite, operation_failure

router = APIRouter(prefix="/projects", tags=["project placement output"])


@router.post("/{project_id}/antenna_placements/export")
async def export_placements(request: Request, authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        issue = await operation_prerequisite(runtime, store, identity)
        if issue:
            return operation_failure(issue, stage="placement_export")
        graph = runtime.planning_planners.get(identity["project_id"])
        scenario = graph.scenario if graph is not None else runtime.planning_scenarios[identity["project_id"]]
        try:
            report = await run_in_threadpool(save_project_placements, store, identity, runtime.active_project, scenario)
        except ProjectValidationError as exc:
            report = exc.to_dict()
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not save placement output"}) from exc
        return {"status": "OK", "kind": "json", "data": report}
