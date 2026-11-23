import os
import sys
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.throttle_engine import (
    apply_limit,
    remove_limit,
    get_current_status,
    is_admin,
    elevate_if_needed
)
from backend.network_monitor import get_stats

app = FastAPI(title="Bandwidth Controller API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Determine frontend static directory (handles PyInstaller bundle)
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

class ApplyLimitRequest(BaseModel):
    limit_kb: float = Field(..., ge=0, le=204800, description="Bandwidth limit in KB/s (0 to 204,800 KB/s = 200 MB/s)")
    unit: str = Field(default="MB", description="Selected UI unit: 'KB' or 'MB'")

@app.get("/api/status")
def status_endpoint():
    status = get_current_status()
    return status

@app.post("/api/apply")
def apply_endpoint(payload: ApplyLimitRequest):
    result = apply_limit(limit_kb=payload.limit_kb, unit=payload.unit)
    return result

@app.post("/api/disable")
def disable_endpoint():
    result = remove_limit()
    return result

@app.get("/api/stats")
def stats_endpoint():
    return get_stats()

@app.post("/api/elevate")
def elevate_endpoint():
    if not is_admin():
        elevate_if_needed()
        return {"status": "requesting_elevation"}
    return {"status": "already_admin"}

# Mount frontend
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/")
def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({"message": "Bandwidth Controller Backend running. Frontend not found."}, status_code=404)
