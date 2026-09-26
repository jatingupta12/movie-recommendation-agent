"""Normalized Watchmode title, provider, and availability models."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WatchmodeTitle(BaseModel):
    model_config = ConfigDict(frozen=True)

    watchmode_id: int
    title: str
    media_type: Literal["movie", "tv"]
    tmdb_id: int | None = None
    year: int | None = None


class StreamingAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    title_id: int
    provider: str
    provider_type: Literal["subscription", "rent", "buy", "free", "tv", "other"]
    region: str = "US"
    available: bool = True
    web_url: str | None = None
    price: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def normalize_provider_type(value: str | None) -> Literal["subscription", "rent", "buy", "free", "tv", "other"]:
    normalized = (value or "").strip().lower()
    return {
        "sub": "subscription", "subscription": "subscription",
        "rent": "rent", "buy": "buy", "free": "free",
        "tve": "tv", "tv": "tv", "tv_everywhere": "tv",
    }.get(normalized, "other")
