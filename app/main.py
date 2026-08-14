"""
AthleteOS Web App — FastAPI entry point.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import ensure_dirs, DATA_DIR
from app.api.routes import router

# Ensure data directories exist on startup
ensure_dirs()

# Load persistent .env from DATA_DIR (survives Railway redeployments)
# This overrides any values already in environment so user settings take effect
_persistent_env = DATA_DIR / ".env"
if _persistent_env.exists():
    load_dotenv(dotenv_path=str(_persistent_env), override=True)

app = FastAPI(title="AthleteOS", version="1.0.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(router)

# Static files (frontend SPA)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str = ""):
    """Serve the SPA for all non-API routes."""
    # Don't catch API routes
    if full_path.startswith("api/") or full_path == "docs":
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Not found"}, status_code=404)
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AthleteOS API running. Frontend not found."}