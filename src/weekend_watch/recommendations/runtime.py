"""Runtime assembly for recommendation services using shared app settings."""

from contextlib import ExitStack
import logging
import sqlite3
from typing import Callable

from ..personalization import PersonalizationService
from .ai import AIRecommendationService, ClaudeClient, GroqClient
from ..repositories import TitleRepository, WatchmodeRepository
from ..tmdb.client import TmdbClient
from ..tmdb.models import Title
from ..watchmode import WatchmodeClient, WatchmodeError, WatchmodeService, WatchmodeTitleNotFound
from ..watchmode.models import StreamingAvailability
from .pipeline import RecommendationPipeline

logger = logging.getLogger(__name__)


def build_recommendation_pipeline(settings, db: sqlite3.Connection, tmdb: TmdbClient,
                                  stack: ExitStack, *,
                                  watchmode_client_factory: Callable = WatchmodeClient
                                  ) -> RecommendationPipeline:
    """Assemble the existing pipeline and keep optional provider clients scoped."""
    titles = TitleRepository(db)
    personalization = PersonalizationService(db)
    watchmode_repository = WatchmodeRepository(db)
    availability_lookup = None
    if settings.watchmode_api_key:
        watchmode_client = stack.enter_context(watchmode_client_factory(
            api_key=settings.watchmode_api_key
        ))
        watchmode_service = WatchmodeService(
            watchmode_client, titles, watchmode_repository,
            region=settings.watchmode_region,
            cache_ttl_hours=settings.watchmode_cache_ttl_hours,
        )

        def availability_lookup(title: Title, region: str | None = None):
            selected_region = (region or settings.watchmode_region).upper()
            local_id = titles.upsert(**title.to_repository_fields())
            try:
                return watchmode_service.availability_for_tmdb(
                    title.tmdb_id, title.media_type, region=selected_region
                )
            except WatchmodeTitleNotFound:
                logger.info("Watchmode has no mapping for TMDB title %s", title.tmdb_id)
                return []
            except WatchmodeError as exc:
                logger.warning(
                    "Watchmode lookup failed for TMDB title %s (%s); using unexpired cached availability if present",
                    title.tmdb_id, str(exc),
                )
                cached = watchmode_repository.get_cached_availability(
                    local_id, selected_region, ttl_hours=settings.watchmode_cache_ttl_hours
                )
                return [StreamingAvailability.model_validate(dict(row)) for row in cached or []]

    return RecommendationPipeline(
        tmdb, personalization, watchmode=watchmode_repository,
        availability_lookup=availability_lookup,
        region=settings.watchmode_region,
        availability_cache_ttl_hours=settings.watchmode_cache_ttl_hours,
    )


def build_ai_recommendation_service(settings, pipeline: RecommendationPipeline,
                                    stack: ExitStack, *,
                                    groq_client_factory: Callable = GroqClient,
                                    claude_client_factory: Callable = ClaudeClient
                                    ) -> AIRecommendationService:
    """Create optional AI clients, scoped to the caller's ExitStack."""
    groq_key = getattr(settings, "groq_api_key", None)
    claude_key = getattr(settings, "anthropic_api_key", None)
    provider = getattr(settings, "ai_recommendation_provider", "groq")
    groq = stack.enter_context(groq_client_factory(
        api_key=groq_key, model=getattr(settings, "groq_model", "openai/gpt-oss-20b")
    )) if groq_key and provider in {"groq", "auto", "claude"} else None
    claude = stack.enter_context(claude_client_factory(
        api_key=claude_key, model=getattr(settings, "anthropic_model", "claude-sonnet-5")
    )) if claude_key and provider in {"auto", "claude"} else None
    return AIRecommendationService(pipeline, groq=groq, claude=claude)
