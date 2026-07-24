from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Voice-Controlled Desktop Agent"
    app_version: str = "0.1.0"
    debug: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
