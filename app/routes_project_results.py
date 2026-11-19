"""Saved-output retrieval without worker allocation or project activation."""
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from app.routes_projects import authorized_project
from cisei_lib.project_io.planning_results import load_planning_result

router = APIRouter(prefix="/projects", tags=["project results"])


@router.get("/{project_id}/{planner}/result")
def get_planning_result(planner: Literal["graph", "cell"], parameter_hash: str | None = None, authorized=Depends(authorized_project)):
    """Retrieve persisted output without requiring a live worker or activation."""
    store, identity, _ = authorized
    try:
        data = load_planning_result(store, identity, parameter_hash=parameter_hash, planner=planner)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(500, detail={"code": "project_storage_error", "message": "Could not read saved planning result"}) from exc
    return {"status": "OK", "kind": "json", "data": data}
