from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional because first-run model downloads should not prevent API startup.
    if get_settings().warm_whisper_on_startup:
        from app.voice.stt import load_model

        load_model()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        description="Local API for a voice-controlled desktop automation agent.",
        lifespan=lifespan,
    )
    application.include_router(router, prefix="/api/v1")

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"

    @application.get("/", tags=["system"], response_model=None)
    async def root() -> dict[str, str] | FileResponse:
        if settings.demo_mode and frontend_dist.is_dir():
            return FileResponse(frontend_dist / "index.html")
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    if frontend_dist.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return application


app = create_app()
