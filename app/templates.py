"""Template engine configuration for server-rendered HTML pages."""

from pathlib import Path
from fastapi.templating import Jinja2Templates

# Calculate absolute path to app/templates
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Jinja2 template renderer instance
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
