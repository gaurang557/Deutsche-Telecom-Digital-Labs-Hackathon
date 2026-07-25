import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DB_PATH = Path(
    os.environ.get(
        "AGENT_DB",
        Path(__file__).parent.parent / "agent_store.db",
    )
).resolve()


class Settings(BaseSettings):
    app_name: str = "Voice desk"
    app_version: str = "0.1.0"
    debug: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 60.0
    demo_mode: bool = False
    planner_provider: str = "ollama"
    bedrock_region: str = "us-east-1"
    bedrock_model_id: str = "amazon.nova-micro-v1:0"
    demo_sandbox_dir: str = "/tmp/voice-desk-demo"

    # Speech-to-text (faster-whisper). Used from the STT chunk onward.
    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"
    whisper_model_dir: str | None = None
    warm_whisper_on_startup: bool = False

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
