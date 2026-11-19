"""Reusable stage adapters bound to an explicit planner namespace.

Graph and Cell register sibling routers; no planner calls another planner's API.
Algorithm selection and Cell result enrichment stay in execution.py.
"""
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import ConfigDict
from starlette.concurrency import run_in_threadpool
from app.routes_projects import authorized_project
from app.routes_planning import MetricComputeBody, SolveBody, _metrics_response
from cisei_lib.project_io.diagnostics import ProjectValidationError
from cisei_lib.project_io.planning_results import load_planning_result
from .guards import operation_prerequisite, operation_failure
from .execution import compute_metrics as compute_execution_metrics, solve_execution, solution_payload


class MetricBody(MetricComputeBody):
    model_config = ConfigDict(extra="forbid")
    include_features: Literal[False] = False


class NetworkSolveBody(SolveBody):
    model_config = ConfigDict(extra="forbid")


async def _execution_issue(runtime, store, identity, *, planner_kind):
    issue = await operation_prerequisite(runtime, store, identity)
    if issue:
        return issue
    project_id = identity["project_id"]
    options = runtime.candidate_options.get(project_id)
    if project_id not in runtime.planning_planners or options is None:
        return "candidate_graph_missing", "Generate candidates with the requested planner first."
    if options.planner != planner_kind:
        return "candidate_planner_mismatch", "Current candidates belong to another planner. Generate candidates with the requested planner first."
    if planner_kind == "cell":
        cell = runtime.cell_planners.get(project_id)
        if cell is None or cell.graph is not runtime.planning_planners[project_id]:
            return "cell_candidates_required", "Generate Cell candidates before running Cell execution stages."



def execution_router(planner_kind):
    """Register the same stage protocol for an explicitly supported planner."""
    if planner_kind not in {"graph", "cell"}:
        raise ValueError("Unsupported network execution adapter")
    router = APIRouter(tags=[f"project {planner_kind} execution"])

    @router.post("/metrics/compute")
    async def compute_metrics(request: Request, body: MetricBody, authorized=Depends(authorized_project)):
        store, identity, _ = authorized
        runtime = request.app.state.runtime
        async with runtime.project_lock:
            issue = await _execution_issue(runtime, store, identity, planner_kind=planner_kind)
            if issue:
                return operation_failure(issue, stage=f"{planner_kind}_metrics")
            try:
                data = await compute_execution_metrics(runtime, identity["project_id"], body)
            except (ValueError, KeyError, TypeError) as exc:
                return operation_failure(("metric_computation_failed", str(exc)), stage=f"{planner_kind}_metrics")
            return {"status": "OK", "kind": "json", "data": {
                "valid": True, "stage": f"{planner_kind}_metrics", "issues": [], **runtime.active_project, **data}}


    @router.get("/metrics")
    async def get_metrics(request: Request, authorized=Depends(authorized_project)):
        store, identity, _ = authorized
        runtime = request.app.state.runtime
        async with runtime.project_lock:
            issue = await _execution_issue(runtime, store, identity, planner_kind=planner_kind)
            if issue:
                return operation_failure(issue, stage=f"{planner_kind}_metrics")
            state = runtime.planning_runs.get(identity["project_id"])
            if state is None or not state.metric_requests:
                return operation_failure(("metrics_missing", "Compute metrics with the requested planner first."), stage=f"{planner_kind}_metrics")
            data = await run_in_threadpool(_metrics_response, runtime.planning_planners[identity["project_id"]], include_features=False)
            data["data"].update(valid=True, stage=f"{planner_kind}_metrics", issues=[], complete=state.metrics_ready, **runtime.active_project)
            return data


    @router.post("/solution/solve")
    async def solve(request: Request, body: NetworkSolveBody, authorized=Depends(authorized_project)):
        store, identity, _ = authorized
        runtime = request.app.state.runtime
        async with runtime.project_lock:
            issue = await _execution_issue(runtime, store, identity, planner_kind=planner_kind)
            if issue:
                return operation_failure(issue, stage=f"{planner_kind}_solve")
            state = runtime.planning_runs.get(identity["project_id"])
            planner = runtime.planning_planners[identity["project_id"]]
            if state is None or not state.metrics_ready or any(edge not in planner.edge_metrics for edge in planner.candidate_edges):
                return operation_failure(("metrics_incomplete", "Compute all candidate metrics before solving."), stage=f"{planner_kind}_solve")
            try:
                data = await run_in_threadpool(solve_execution, runtime, store, identity, body, planner_kind=planner_kind)
            except ProjectValidationError as exc:
                return {"status": "OK", "kind": "json", "data": exc.to_dict()}
            except (ValueError, KeyError, TypeError) as exc:
                return operation_failure((f"{planner_kind}_solve_failed", str(exc)), stage=f"{planner_kind}_solve")
            except (OSError, sqlite3.Error) as exc:
                raise HTTPException(500, detail={"code": "project_storage_error",
                    "message": "Could not persist the planning result. Previous output was preserved."}) from exc
            return {"status": "OK", "kind": "json", "data": {
                "valid": True, "stage": f"{planner_kind}_solve", "issues": [], **runtime.active_project, **data}}


    @router.get("/solution")
    async def get_solution(request: Request, authorized=Depends(authorized_project)):
        store, identity, _ = authorized
        runtime = request.app.state.runtime
        async with runtime.project_lock:
            issue = await _execution_issue(runtime, store, identity, planner_kind=planner_kind)
            if issue:
                return operation_failure(issue, stage=f"{planner_kind}_solution")
            state = runtime.planning_runs.get(identity["project_id"])
            if state is None or state.solution_hash is None:
                return operation_failure(("solution_missing", "Solve the current planner metrics first."), stage=f"{planner_kind}_solution")
            report = await run_in_threadpool(load_planning_result, store, identity, parameter_hash=state.solution_hash, planner=planner_kind)
            if not report["valid"]:
                return {"status": "OK", "kind": "json", "data": report}
            result = report.pop("result")
            report.update(stage=f"{planner_kind}_solution", **solution_payload(result))
            return {"status": "OK", "kind": "json", "data": report}


    return router
