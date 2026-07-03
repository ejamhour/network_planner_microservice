from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes_geo_info import router as geo_info_router
from app.routes_auto import router as auto_router
from app.session import UserRuntime


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = UserRuntime()
    yield


app = FastAPI(
    title="Planning Micro-Service Docker",
    description="Provides methods for evaluating links and planning networks.",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(geo_info_router)
app.include_router(auto_router)

# --------------------------------------------------------------
# Home
# --------------------------------------------------------------
@app.get("/")
def home():
    return {"message": "You shouldn't be here ... run, Run, RUN!!! or go to /docs, instead"}



