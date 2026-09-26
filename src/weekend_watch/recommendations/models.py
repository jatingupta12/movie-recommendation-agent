"""Recommendation candidate models and configurable deterministic weights."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..tmdb.models import Title
from ..watchmode.models import StreamingAvailability

CandidateCategory = Literal["NEW_RELEASE", "TRENDING", "HIGHLY_RATED", "HIDDEN_GEM", "RECOMMENDED", "IN_THEATERS", "KEYWORD_MATCH", "ACTOR_MATCH"]


class RecommendationWeights(BaseModel):
    """Nonnegative weights for normalized 0–1 scoring dimensions."""

    model_config = ConfigDict(frozen=True)

    rating: float = Field(default=0.24, ge=0)
    vote_count: float = Field(default=0.10, ge=0)
    popularity: float = Field(default=0.12, ge=0)
    recency: float = Field(default=0.15, ge=0)
    genre_match: float = Field(default=0.17, ge=0)
    streaming_availability: float = Field(default=0.15, ge=0)
    trending: float = Field(default=0.07, ge=0)


class WeekendCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: Title
    categories: list[CandidateCategory]
    match_score: float = Field(ge=0, le=100)
    score_breakdown: dict[str, float]
    streaming_availability: list[StreamingAvailability] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    """User-facing selection whose factual fields are copied from TMDB/Watchmode."""

    model_config = ConfigDict(frozen=True)

    tmdb_id: int
    title: str
    media_type: Literal["movie", "tv"]
    rating: float | None = None
    release_date: str | None = None
    genres: list[str] = Field(default_factory=list)
    streaming_services: list[str] = Field(default_factory=list)
    category: CandidateCategory
    recommendation_reason: str
    confidence: float = Field(ge=0, le=1)
    explanation_source: Literal["claude", "groq", "deterministic"]
    groq_used: bool = False
    metadata_source: Literal["TMDB"] = "TMDB"
    availability_source: Literal["Watchmode", "not_confirmed"] = "not_confirmed"
