"""Graph execution namespace; common stage plumbing is implemented once."""
from fastapi import APIRouter
from .execution_routes import execution_router

router = APIRouter(prefix="/projects/{project_id}/graph")
router.include_router(execution_router("graph"))
