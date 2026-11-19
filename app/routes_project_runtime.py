"""Worker-owned active project lifecycle; planning adapters live separately."""
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from app.routes_projects import authorized_project
from cisei_lib.project_io.project_operations import load_project_scenario, current_project_inputs

router = APIRouter(prefix="/projects", tags=["project runtime"])


@router.get("/active")
async def active_project(request: Request):
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        return {"status": "OK", "kind": "json", "data": {"active_project": runtime.active_project}}


@router.post("/{project_id}/activate")
async def activate_project(request: Request, authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        active = runtime.active_project
        if active is not None and active["project_id"] != identity["project_id"]:
            raise HTTPException(409, detail={"code": "active_project_exists",
                "message": "Close the active project before activating another.",
                "active_project_id": active["project_id"]})
        if active is None and runtime.planning_scenarios:
            raise HTTPException(409, detail={"code": "active_project_exists",
                "message": "This worker contains legacy scenarios. Close/reset that session before activating a project."})
        if active is not None:
            if runtime.project_cleanup_pending:
                raise HTTPException(503, detail={"code": "project_cleanup_failed", "message": "Retry close before using this project."})
            # Do not silently discard computed planners or features on repeat activation.
            inputs = await run_in_threadpool(current_project_inputs, store, identity)
            if inputs != active:
                return {"status": "OK", "kind": "json", "data": {
                    "valid": False, "stage": "scenario_load", **inputs,
                    "issues": [{"code": "active_project_outdated", "severity": "error",
                                "message": "Close the active project, rebuild if needed, and activate again."}]}}
            return {"status": "OK", "kind": "json", "data": {
                "valid": True, "stage": "scenario_load", "issues": [], **active, "reused": True}}
        report, scenario = await run_in_threadpool(load_project_scenario, store, identity)
        if report["valid"]:
            runtime.planning_scenarios[identity["project_id"]] = scenario
            runtime.active_project = {key: report[key] for key in ("project_id", "input_revision", "input_hash")}
            runtime.active_project_source = (store, identity)
        return {"status": "OK", "kind": "json", "data": {**report, "reused": False}}


@router.post("/{project_id}/close")
async def close_project(request: Request, authorized=Depends(authorized_project)):
    _, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        active = runtime.active_project
        if active is None:
            return {"status": "OK", "kind": "json", "data": {"project_id": identity["project_id"], "closed": True}}
        if active["project_id"] != identity["project_id"]:
            raise HTTPException(409, detail={"code": "active_project_exists",
                "message": "Close the active project explicitly.", "active_project_id": active["project_id"]})
        if any(state.get("status") == "running" for state in runtime.mesh_progress.values()):
            raise HTTPException(409, detail={"code": "project_busy", "message": "Wait for planning to finish before closing the project."})
        try:
            await runtime.close_geo_pools()
        except Exception as exc:
            runtime.project_cleanup_pending = True
            raise HTTPException(503, detail={"code": "project_cleanup_failed",
                "message": "Features cleanup failed. The project remains active; retry close."}) from exc
        for name in ("planning_scenarios", "planning_planners", "mesh_planners", "cell_planners", "mesh_progress", "feature_caches", "candidate_options", "planning_runs"):
            getattr(runtime, name).clear()
        runtime.active_project = None
        runtime.active_project_source = None
        runtime.project_cleanup_pending = False
        return {"status": "OK", "kind": "json", "data": {"project_id": identity["project_id"], "closed": True}}
