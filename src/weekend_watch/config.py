"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the local application."""

    database_path: Path = Path("data/weekend_watch.db")
    tmdb_api_key: str | None = Field(default=None, validation_alias="TMDB_API_KEY")
    tmdb_access_token: str | None = Field(default=None, validation_alias="TMDB_ACCESS_TOKEN")
    watchmode_api_key: str | None = Field(default=None, validation_alias="WATCHMODE_API_KEY")
    watchmode_region: str = Field(default="US", validation_alias="WATCHMODE_REGION")
    watchmode_cache_ttl_hours: int = Field(default=24, validation_alias="WATCHMODE_CACHE_TTL_HOURS", ge=0)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", validation_alias="ANTHROPIC_MODEL")
    groq_model: str = Field(default="openai/gpt-oss-20b", validation_alias="GROQ_MODEL")
    api_host: str = Field(default="127.0.0.1", validation_alias="WEEKEND_WATCH_API_HOST")
    api_port: int = Field(default=8000, validation_alias="WEEKEND_WATCH_API_PORT", ge=1, le=65535)

    model_config = SettingsConfigDict(
        env_prefix="WEEKEND_WATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # TMDB names are deliberately unprefixed as these are provider-specific standard env vars.
    @property
    def tmdb_credentials(self) -> dict[str, str | None]:
        return {"api_key": self.tmdb_api_key, "access_token": self.tmdb_access_token}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings; tests can clear this cache as needed."""
    return Settings()
