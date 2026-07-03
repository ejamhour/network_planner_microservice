import asyncio
import socket
import secrets
from typing import Dict
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from pydantic import BaseModel
import time
import secrets
from contextlib import asynccontextmanager
import httpx
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

# Configuration
MAX_IDLE_SECONDS = 600  # 10 minutes

STARTUP_LOCK = asyncio.Lock()

# Registry structure: 
# user_id -> {"token": str, "port": Optional[int], "process": Optional[Process], "last_active": float}
user_registry: Dict[str, dict] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background reaper
    reaper_task = asyncio.create_task(idle_worker_reaper())
    yield
    # Cleanup on shutdown
    reaper_task.cancel()
    for user_id in list(user_registry.keys()):
        await stop_worker(user_id)

class RegisterResponse(BaseModel):
    user_id: str
    token: str

def find_free_port(start_port: int = 15000, max_attempts: int = 200) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free ports available")

async def start_worker_instance(user_id: str) -> int:
    port = find_free_port()
    
    # Change: Capture PIPE instead of DEVNULL to see errors
    process = await asyncio.create_subprocess_exec(
        "uvicorn", "app.main:app", # Ensure this path is correct
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "error",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    max_retries = 30
    async with httpx.AsyncClient() as client:
        for i in range(max_retries):
            # Check if the process crashed immediately
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                print(f"ERROR: Worker process died for user {user_id}")
                print(f"STDERR: {stderr.decode()}")
                raise RuntimeError("Worker process failed to start.")

            try:
                # Use a minimal timeout for the health check
                await client.get(f"http://127.0.0.1:{port}/", timeout=0.1)
                
                # Success: update registry
                user_registry[user_id]["port"] = port
                user_registry[user_id]["process"] = process
                user_registry[user_id]["last_active"] = time.time()
                return port
            except (httpx.ConnectError, httpx.TimeoutException):
                await asyncio.sleep(0.2)
    
    process.terminate()
    raise RuntimeError(f"Worker timed out after {max_retries} attempts on port {port}")

async def verify_token(user_id: str, x_token: str = Header(...)):
    """Dependency to validate user existence and token authenticity"""
    
    if user_id not in user_registry:
        raise HTTPException(status_code=404, detail="User not registered")
    if user_registry[user_id]["token"] != x_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id
       
async def stop_worker(user_id: str):
    """Gracefully terminates a worker and clears its registry data"""
    data = user_registry.get(user_id)
    if not data or not data.get("process"):
        return

    proc = data["process"]
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (asyncio.TimeoutError, ProcessLookupError):
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    
    data["port"] = None
    data["process"] = None

async def idle_worker_reaper():
    """Background task to kill workers with no recent activity"""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for user_id, data in list(user_registry.items()):
            if data["port"] and (now - data["last_active"] > MAX_IDLE_SECONDS):
                print(f"Reaping idle worker for user: {user_id}")
                await stop_worker(user_id)

async def proxy_request(worker_port: int, path: str, request: Request):
    """
    Transparently forwards any HTTP request to the local worker.
    """
    url = f"http://127.0.0.1:{worker_port}/{path}"
    
    # Copy original headers excluding 'host' to avoid routing issues
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    client = httpx.AsyncClient()
    
    # Build the outgoing request as a clone of the incoming one
    rp_req = client.build_request(
        method=request.method,
        url=url,
        headers=headers,
        params=request.query_params,
        content=request.stream() # Streams body to prevent memory spikes
    )

    # Execute the request and stream the response back to the user
    rp_resp = await client.send(rp_req, stream=True)

    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=dict(rp_resp.headers),
        background=BackgroundTask(rp_resp.aclose) # Ensures client closure
    )

#----------------------------------------------

hub_app = FastAPI(lifespan=lifespan)

@hub_app.post("/register")
async def register_user(user_id: str):
    """
    Creates user or respawns worker if user exists.
    If the user exists, the old process is killed to allow a clean state.
    """
    if user_id in user_registry:
        # Kill existing worker to force a "respawn" on next request
        await stop_worker(user_id)
        token = user_registry[user_id]["token"]
    else:
        token = secrets.token_urlsafe(32)
        user_registry[user_id] = {
            "token": token, 
            "port": None, 
            "process": None, 
            "last_active": time.time()
        }
    
    return {"user_id": user_id, "token": token, "status": "ready_for_provisioning"}


@hub_app.api_route("/{user_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def route_request(user_id: str, path: str, request: Request):
    # Manual Authentication and Debugging
    token = request.headers.get("X-Token")
    
    if not token:
        print(f"DEBUG: Missing X-Token for {user_id}. Headers: {dict(request.headers)}")
        raise HTTPException(status_code=401, detail="X-Token header missing")
    
    if user_id not in user_registry:
        raise HTTPException(status_code=404, detail="User not registered")
        
    if user_registry[user_id]["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token")

    user_data = user_registry[user_id]
    user_data["last_active"] = time.time()

    # Just-In-Time Provisioning
    if user_data["port"] is None or (user_data["process"] and user_data["process"].returncode is not None):
        async with STARTUP_LOCK:
            
            # 3. Double-check: Did another request already start it while we waited?
            if user_data["port"] is None or (user_data["process"] and user_data["process"].returncode is not None):
                print(f"DEBUG: Starting worker for {user_id} under lock protection.")
                await start_worker_instance(user_id)

    worker_port = user_data["port"]
    url = f"http://127.0.0.1:{worker_port}/{path}"
    
    # Transparent Proxy Logic
    proxy_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    client = httpx.AsyncClient(timeout=600.0) # 10 minutes
    
    try:
        rp_req = client.build_request(
            method=request.method,
            url=url,
            headers=proxy_headers,
            params=request.query_params,
            content=request.stream()
        )
        
        rp_resp = await client.send(rp_req, stream=True)
        
        return StreamingResponse(
            rp_resp.aiter_raw(),
            status_code=rp_resp.status_code,
            headers=dict(rp_resp.headers),
            background=BackgroundTask(rp_resp.aclose)
        )
        
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Worker error: {exc}")