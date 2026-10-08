"""Public, read-only distribution of curated planning examples."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/examples", tags=["examples"])
EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _files() -> list[str]:
    return sorted(
        path.relative_to(EXAMPLES_DIR).as_posix()
        for path in EXAMPLES_DIR.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(EXAMPLES_DIR).parts)
        and "__pycache__" not in path.relative_to(EXAMPLES_DIR).parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.resolve().is_relative_to(EXAMPLES_DIR.resolve())
    )


@router.get("/manifest.json")
@router.get("")
def examples_manifest():
    """List downloadable files relative to /examples/."""
    return {"files": _files()}


@router.get("/{file_path:path}")
def example_file(file_path: str):
    """Download one curated example; never expose files outside the collection."""
    if file_path not in _files():
        raise HTTPException(status_code=404, detail="Unknown example")
    return FileResponse(EXAMPLES_DIR / file_path, filename=Path(file_path).name)
