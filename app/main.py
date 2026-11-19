from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routes_planning import router as planning_router
from app.routes_sdk import router as sdk_router
from app.routes_examples import router as examples_router
from app.routes_projects import router as projects_router
from app.routes_project_results import router as project_results_router
from app.routes_project_runtime import router as project_runtime_router
from app.project_runtime.candidates import router as project_candidates_router
from app.project_runtime.mesh import router as project_mesh_router
from app.project_runtime.graph import router as project_graph_router
from app.project_runtime.cell import router as project_cell_router
from app.project_runtime.placements import router as project_placements_router
from app.routes_project_schemas import discovery_router, validation_router
from app.session import UserRuntime
from app.version import __version__
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from cisei_lib.project_io.project_operations import current_project_inputs


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = UserRuntime()
    try:
        yield
    finally:
        await app.state.runtime.close_geo_pools()


app = FastAPI(
    title="Planning Micro-Service Docker",
    description="Provides methods for evaluating links and planning networks.",
    version=__version__,
    lifespan=lifespan
)

app.include_router(planning_router)
app.include_router(sdk_router)
app.include_router(examples_router)
app.include_router(discovery_router)
app.include_router(validation_router)
app.include_router(projects_router)
app.include_router(project_results_router)
app.include_router(project_runtime_router)
app.include_router(project_candidates_router)
app.include_router(project_mesh_router)
app.include_router(project_graph_router)
app.include_router(project_cell_router)
app.include_router(project_placements_router)
app.mount("/dist", StaticFiles(directory="dist"), name="dist")


@app.middleware("http")
async def guard_active_project(request, call_next):
    """Serialize planning with close/activation and reject stale active state.

    Legacy sessions without an active saved project retain their existing API.
    New project sessions cannot mutate compiled inputs through legacy endpoints.
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not request.url.path.startswith("/planning/"):
        return await call_next(request)
    async with runtime.project_lock:
        active = runtime.active_project
        if active is not None:
            if runtime.project_cleanup_pending:
                return JSONResponse(status_code=503, content={"detail": {
                    "code": "project_cleanup_failed", "message": "Retry close before using this project."}})
            parts = request.url.path.strip("/").split("/")
            if len(parts) >= 3 and parts[1] == "scenarios":
                target = parts[2]
                if target != active["project_id"]:
                    return JSONResponse(status_code=409, content={"detail": {
                        "code": "active_project_exists", "message": "Close the active project before using another scenario.",
                        "active_project_id": active["project_id"]}})
                if request.method != "GET" and (len(parts) == 3 or parts[3] == "configuration"):
                    return JSONResponse(status_code=409, content={"detail": {
                        "code": "compiled_project_read_only", "message": "Edit source files, sync and rebuild instead."}})
            inputs = await run_in_threadpool(current_project_inputs, *runtime.active_project_source)
            if inputs != active:
                return JSONResponse(status_code=409, content={"detail": {
                    "code": "active_project_outdated", "message": "Close the project, rebuild if needed, and activate again."}})
        return await call_next(request)

# --------------------------------------------------------------
# Home
# --------------------------------------------------------------
@app.get("/")
def home():
    return {"message": "You shouldn't be here ... run, Run, RUN!!! or go to /docs, instead"}



