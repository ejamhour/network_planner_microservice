"""Cell-specific tuning; policy and applied orientations live in effective profiles."""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict
from starlette.concurrency import run_in_threadpool

from app.routes_projects import authorized_project
from app.routes_planning import CandidateTuneBody, CellPlanner, _table_records
from .guards import operation_prerequisite, operation_failure
from .execution_routes import execution_router

router = APIRouter(prefix="/projects/{project_id}/cell", tags=["project cell"])


class CellTuneBody(CandidateTuneBody):
    model_config = ConfigDict(extra="forbid")
    planner: Literal["cell"] = "cell"
    apply_rotation: bool = True


@router.post("/sectors/tune")
async def tune_sectors(request: Request, body: CellTuneBody, authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        issue = await operation_prerequisite(runtime, store, identity)
        if issue:
            return operation_failure(issue, stage="cell_tuning")
        project_id = identity["project_id"]
        previous = runtime.candidate_options.get(project_id)
        if previous is None or previous.planner != "cell":
            return operation_failure(("cell_candidates_required", "Generate Cell candidates before sector tuning."), stage="cell_tuning")
        cell = runtime.cell_planners.get(project_id)
        if cell is None or cell.primary_tech != body.primary_tech:
            return operation_failure(("cell_candidates_required", "Generate Cell candidates using the requested primary technology first."), stage="cell_tuning")
        try:
            tuned = await run_in_threadpool(cell.tune_sectors,
                **body.model_dump(exclude={"planner", "preserve_existing", "primary_tech"}))
        except (ValueError, KeyError, TypeError) as exc:
            return operation_failure(("cell_tuning_failed", str(exc)), stage="cell_tuning")
        runtime.planning_planners[project_id] = cell.graph
        if body.apply_rotation or body.prune_empty:
            runtime.candidate_options[project_id] = body
            runtime.planning_runs.pop(project_id, None)
        return {"status": "OK", "kind": "json", "data": {
            "valid": True, "stage": "cell_tuning", "issues": [], **runtime.active_project,
            "candidate_edges": tuned["candidate_edges"],
            **{key: _table_records(tuned[key]) for key in ("rotation", "prune", "sectors")}}}


# Cell opts into network execution as a sibling of Graph, not a Graph subclass.
router.include_router(execution_router("cell"))
