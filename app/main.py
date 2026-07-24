from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.voice.stt import load_model


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional because first-run model downloads should not prevent API startup.
    if get_settings().warm_whisper_on_startup:
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

    @application.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    return application


app = create_app()
