"""Mesh-specific project adapters; source interpretation stays server-side."""
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from app.routes_projects import authorized_project
from .guards import operation_prerequisite, operation_failure
from pydantic import ConfigDict
from app.routes_planning import MeshNetworkBody, prepare_mesh_repeaters
from cisei_lib.planners.mesh.options import MeshNetworkOptions
from cisei_lib.project_io.mesh_inputs import prepare_mesh_inputs
from cisei_lib.project_io.diagnostics import ProjectValidationError

router = APIRouter(prefix="/projects", tags=["project mesh"])


class ProjectMeshRepeaterBody(MeshNetworkOptions):
    """Only execution controls; pole inventory always comes from server files."""
    model_config = ConfigDict(extra="forbid")
    geo_base_url: str | None = None


@router.post("/{project_id}/mesh/add_repeaters")
async def add_project_repeaters(request: Request, body: ProjectMeshRepeaterBody,
                                authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    runtime = request.app.state.runtime
    async with runtime.project_lock:
        issue = await operation_prerequisite(runtime, store, identity)
        if issue:
            return operation_failure(issue, stage="mesh_preparation")
        try:
            controls = body.model_dump(exclude_unset=True, exclude={"geo_base_url"})
            inputs = await run_in_threadpool(prepare_mesh_inputs, store, identity,
                runtime.planning_scenarios[identity["project_id"]], runtime.active_project, controls)
            prepared = MeshNetworkBody(**inputs, geo_base_url=body.geo_base_url)
            result = await prepare_mesh_repeaters(runtime, identity["project_id"], prepared)
        except ProjectValidationError as exc:
            report = exc.to_dict()
            report["stage"] = "mesh_preparation"
            return {"status": "OK", "kind": "json", "data": report}
        except ValueError as exc:
            return operation_failure(("mesh_preparation_failed", str(exc)), stage="mesh_preparation")
        return {"status": "OK", "kind": "json", "data": {
            "valid": True, "stage": "mesh_preparation", "issues": [],
            **runtime.active_project, "result": result}}
