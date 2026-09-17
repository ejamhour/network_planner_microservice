from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routes_planning import router as planning_router
from app.routes_sdk import router as sdk_router
from app.session import UserRuntime


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
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(planning_router)
app.include_router(sdk_router)
app.mount("/dist", StaticFiles(directory="dist"), name="dist")

# --------------------------------------------------------------
# Home
# --------------------------------------------------------------
@app.get("/")
def home():
    return {"message": "You shouldn't be here ... run, Run, RUN!!! or go to /docs, instead"}



