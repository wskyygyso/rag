from typing import Dict

from fastapi import FastAPI

from app.health import get_health
from app.settings import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/health")
async def health() -> Dict:
    return await get_health(settings)


@app.get("/")
async def root() -> Dict[str, str]:
    return {"name": settings.app_name, "status": "running"}

