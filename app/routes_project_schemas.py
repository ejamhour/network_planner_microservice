"""Project format discovery and session-independent validation."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from cisei_lib.legacy.project_schemas import load_document


discovery_router = APIRouter(prefix="/planning/project-schemas", tags=["project schemas"])
validation_router = APIRouter(prefix="/planning/project-schemas", tags=["project schemas"])


class BundleIssue(BaseModel):
    file: str | None
    row: int | None
    column: str | None
    path: list[str | int] | None
    message: str


class BundleValidation(BaseModel):
    schema_version: Literal["v1"] = "v1"
    operation: Literal["mesh-link"] = "mesh-link"
    valid: bool
    issues: list[BundleIssue]


@discovery_router.get("")
def list_project_schemas():
    return {"schemas": [{"operation": "mesh-link", "version": "v1",
                         "schema": "project-schemas/v1/mesh-link",
                         "validate": "project-schemas/v1/mesh-link/validate",
                         "validation_via_hub": "POST /{user_id}/planning/project-schemas/v1/mesh-link/validate with X-Token"}]}


@discovery_router.get("/v1/mesh-link")
def get_mesh_link_schema():
    return {
        "operation": "mesh-link",
        "version": "v1",
        "schema": load_document("mesh-link.schema.json"),
        "files": load_document("mesh-link.files.json"),
        "example": load_document("mesh-link.example.json"),
    }


@validation_router.post("/v1/mesh-link/validate", response_model=BundleValidation)
def validate_mesh_link_bundle(bundle: dict[str, Any]):
    """Compatibility endpoint for the v1 parsed Mesh Link bundle."""
    from cisei_lib.legacy.mesh_link_bundle_validation import validate_mesh_link

    issues = validate_mesh_link(bundle)
    return {"valid": not issues, "issues": issues}
