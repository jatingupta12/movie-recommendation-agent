"""Typed single-user personalization settings."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(frozen=True)

    preferred_genres: list[str] = Field(default_factory=list)
    excluded_genres: list[str] = Field(default_factory=list)
    minimum_rating: float = Field(default=0, ge=0, le=10)
    preferred_streaming_services: list[str] = Field(default_factory=list)
    excluded_streaming_services: list[str] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=list)
    movies_enabled: bool = True
    tv_enabled: bool = True
    new_releases_enabled: bool = True
    trending_enabled: bool = True
    hidden_gems_enabled: bool = True
