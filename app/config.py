from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
# agent/config.py
from pathlib import Path
import os

DB_PATH = Path(os.environ.get("AGENT_DB", Path(__file__).parent.parent / "agent_store.db")).resolve()

class Settings(BaseSettings):
    app_name: str = "Voice-Controlled Desktop Agent"
    app_version: str = "0.1.0"
    debug: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

