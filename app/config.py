from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Voice-Controlled Desktop Agent"
    app_version: str = "0.1.0"
    debug: bool = False

    # Speech-to-text (faster-whisper). Used from the STT chunk onward.
    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"
    whisper_model_dir: str | None = None

    # Frontend origin(s) allowed to call the API (Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

