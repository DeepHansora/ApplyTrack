"""ApplyTrack application entry point."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers.public import router as public_router

# Calculate paths for static assets
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="ApplyTrack")

# Mount static files directory at /static
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register public router (contains GET / and GET /health)
app.include_router(public_router)
