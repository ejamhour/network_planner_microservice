"""Hub-level project identities survive worker timeout and replacement."""
import os
import sqlite3
from tempfile import TemporaryFile

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from cisei_lib.project_io.project_store import ProjectAccessDenied, ProjectNotFound, ProjectStore
from cisei_lib.project_io.project_sync import SyncInputError, sync_archive
from cisei_lib.project_io.project_operations import project_report

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_path: str


def get_project_store():
    return ProjectStore(os.environ.get("PLANNING_PROJECT_ROOT", "temp"))


@router.post("", status_code=201)
def create_project(body: CreateProjectBody, store=Depends(get_project_store)):
    try:
        data = store.create(body.project_path)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "invalid_project_path", "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not create project storage"}) from exc
    return {"status": "OK", "kind": "json", "data": data}


def authorized_project(project_id: str, x_project_token: str = Header(...), store=Depends(get_project_store)):
    try:
        data = store.resume(project_id, x_project_token)
    except ProjectNotFound as exc:
        raise HTTPException(404, detail={"code": "project_not_found", "message": str(exc)}) from exc
    except ProjectAccessDenied as exc:
        raise HTTPException(403, detail={"code": "project_access_denied", "message": str(exc)}) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not recover project storage"}) from exc
    return store, data, x_project_token


@router.post("/{project_id}/resume")
def resume_project(authorized=Depends(authorized_project)):
    _, data, _ = authorized
    return {"status": "OK", "kind": "json", "data": data}


@router.post("/{project_id}/sync")
async def sync_project(request: Request, authorized=Depends(authorized_project)):
    store, identity, token = authorized
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/zip":
        raise HTTPException(415, detail={"code": "invalid_upload_type", "message": "Expected an application/zip upload"})
    try:
        with TemporaryFile(mode="w+b") as upload:
            async for chunk in request.stream():
                await run_in_threadpool(upload.write, chunk)
            upload.seek(0)
            data = await run_in_threadpool(sync_archive, store, identity["project_id"], token, upload)
    except SyncInputError as exc:
        raise HTTPException(422, detail={"code": "invalid_sync", "message": str(exc)}) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not synchronize project inputs"}) from exc
    return {"status": "OK", "kind": "json", "data": data}


@router.post("/{project_id}/validate")
def validate_project_inputs(authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    try:
        data = project_report(store, identity)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not validate project inputs"}) from exc
    return {"status": "OK", "kind": "json", "data": data}


@router.post("/{project_id}/build")
def build_project_scenario(authorized=Depends(authorized_project)):
    store, identity, _ = authorized
    try:
        data = project_report(store, identity, compile_scenario=True)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not compile project inputs"}) from exc
    return {"status": "OK", "kind": "json", "data": data}
