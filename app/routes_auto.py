from fastapi import APIRouter
from app.auto_api import register_get
from cisei_lib.dem.dem_utils import get_quadkey, get_canopy_height

router = APIRouter()

register_get(
    router,
    path="/quadkey",
    fn=get_quadkey,
    summary="Compute Bing quadkey",
    response_key="quadkey",
)

register_get(
    router,
    path="/get_canopy_height",
    fn=get_canopy_height,
    summary="Get tree height",
    response_key="tree_height",
)