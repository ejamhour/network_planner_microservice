"""Candidate generation/inspection for project-bound planner interfaces."""
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from app.routes_projects import authorized_project
from .guards import operation_prerequisite, operation_failure
from app.routes_planning import CandidateEdgeBuildBody, PlannerKind, prepare_candidate_edges, _candidate_response

router = APIRouter(prefix="/projects", tags=["project candidates"])


@router.post("/{project_id}/candidate_edges/build")
async def build_project_candidates(request: Request, body: CandidateEdgeBuildBody,
                                   authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        issue = await operation_prerequisite(runtime, store, identity)
        if issue:
            return operation_failure(issue)
        project_id = identity["project_id"]
        if body.planner == "mesh" and project_id not in runtime.mesh_planners:
            return operation_failure(("mesh_repeaters_required", "Run the mesh repeater-expansion stage before building mesh candidates."))
        try:
            payload = await run_in_threadpool(prepare_candidate_edges, runtime, project_id, body)
        except ValueError as exc:
            return operation_failure(("candidate_generation_failed", str(exc)))
        payload["data"].update(valid=True, stage="candidate_generation", **runtime.active_project)
        return payload


@router.get("/{project_id}/candidate_edges")
async def inspect_project_candidates(request: Request, planner: PlannerKind | None = None,
                                     authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        issue = await operation_prerequisite(runtime, store, identity)
        if issue:
            return operation_failure(issue)
        project_id = identity["project_id"]
        prepared = runtime.planning_planners.get(project_id)
        if prepared is None:
            return operation_failure(("candidate_graph_missing", "Generate candidate edges before inspecting them."))
        body = runtime.candidate_options.get(project_id)
        if body is None:
            if planner is not None:
                return operation_failure(("candidate_graph_missing", "Generate candidate edges before inspecting them."))
            body = CandidateEdgeBuildBody()
        if planner is not None and planner != body.planner:
            return operation_failure(("candidate_planner_mismatch", f"Current candidates belong to {body.planner}, not {planner}. Generate candidates with the requested planner first."))
        payload = await run_in_threadpool(_candidate_response, prepared, body)
        payload["data"].update(valid=True, stage="candidate_generation", **runtime.active_project)
        return payload
