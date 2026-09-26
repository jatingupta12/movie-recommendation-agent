"""Application-service facade used by the MCP tool declarations.

This module intentionally contains orchestration and result shaping only. Provider
HTTP behavior, persistence, and recommendation rules remain in their existing
clients/services/repositories.
"""

from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import logging
from typing import Any, Iterator, Literal

from .config import Settings, get_settings
from .database import connect, initialize_database
from .personalization import PersonalizationService
from .recommendations.ai import ClaudeClient, GroqClient
from .recommendations.runtime import build_ai_recommendation_service, build_recommendation_pipeline
from .recommendations.digest import WeekendDigestService, format_weekend_digest
from .recommendations.intent import extract_request_intent
from .repositories import TitleRepository, WatchmodeRepository
from .tmdb import TmdbClient, TmdbError
from .tmdb.models import SearchResults, Title
from .tmdb.service import TmdbService
from .watchmode import WatchmodeClient, WatchmodeError, WatchmodeService, WatchmodeTitleNotFound

logger = logging.getLogger(__name__)


class WeekendWatchTools:
    """High-level operations for the single local user."""

    def __init__(self, settings: Settings | None = None, *,
                 tmdb_client_factory=TmdbClient,
                 watchmode_client_factory=WatchmodeClient,
                 groq_client_factory=GroqClient,
                 claude_client_factory=ClaudeClient):
        self.settings = settings or get_settings()
        self.tmdb_client_factory = tmdb_client_factory
        self.watchmode_client_factory = watchmode_client_factory
        self.groq_client_factory = groq_client_factory
        self.claude_client_factory = claude_client_factory

    @contextmanager
    def _catalog(self) -> Iterator[tuple[TmdbClient, Any, TitleRepository]]:
        settings = self.settings
        initialize_database(settings.database_path)
        with closing(connect(settings.database_path)) as db:
            try:
                with self.tmdb_client_factory(
                    api_key=settings.tmdb_api_key,
                    access_token=settings.tmdb_access_token,
                ) as tmdb:
                    yield tmdb, db, TitleRepository(db)
            except TmdbError as exc:
                # Do not echo an upstream request URL: TMDB API-key auth may put
                # credentials in its query string.
                logger.warning("TMDB operation failed (%s)", type(exc).__name__)
                raise RuntimeError("TMDB request failed; check the server logs and configuration.") from None
            except ValueError as exc:
                if "TMDB_API_KEY" in str(exc) or "TMDB_ACCESS_TOKEN" in str(exc):
                    raise RuntimeError("TMDB credentials are not configured.") from None
                raise

    @staticmethod
    def _dump(title: Title) -> dict[str, Any]:
        return title.model_dump(mode="json")

    @staticmethod
    def _pick(results: SearchResults, query: str) -> Title:
        if not results.results:
            raise LookupError(f"No title found for {query!r}.")
        return max(results.results, key=lambda item: (
            item.title.casefold() == query.casefold(), item.popularity or 0
        ))

    def _resolve(self, tmdb: TmdbClient, query: str, *,
                 media_type: Literal["movie", "tv"] | None = None) -> Title:
        value = query.strip()
        if not value:
            raise ValueError("title_or_id must not be empty")
        if value.isdecimal():
            identifier = int(value)
            if media_type == "movie":
                return tmdb.get_movie_details(identifier)
            if media_type == "tv":
                return tmdb.get_tv_details(identifier)
            # TMDB uses separate movie and TV detail routes. Try both namespaces
            # for a bare numeric ID, without assuming the namespaces are shared.
            try:
                return tmdb.get_movie_details(identifier)
            except TmdbError:
                return tmdb.get_tv_details(identifier)
        if media_type == "movie":
            return self._pick(tmdb.search_movies(value), value)
        if media_type == "tv":
            return self._pick(tmdb.search_tv(value), value)
        matches = tmdb.search_movies(value).results + tmdb.search_tv(value).results
        return self._pick(SearchResults(results=matches), value)

    def _resolve_and_save(self, tmdb: TmdbClient, titles: TitleRepository,
                          query: str, *, media_type: Literal["movie", "tv"] | None = None) -> tuple[Title, int]:
        title = self._resolve(tmdb, query, media_type=media_type)
        title_id = TmdbService(tmdb, titles).save(title)
        return title, title_id

    def search_movies(self, query: str, limit: int = 10) -> dict[str, Any]:
        with self._catalog() as (tmdb, _db, _titles):
            result = tmdb.search_movies(query)
            return {"query": query, "total_results": result.total_results,
                    "results": [self._dump(title) for title in result.results[:max(0, min(limit, 50))]]}

    def search_tv(self, query: str, limit: int = 10) -> dict[str, Any]:
        with self._catalog() as (tmdb, _db, _titles):
            result = tmdb.search_tv(query)
            return {"query": query, "total_results": result.total_results,
                    "results": [self._dump(title) for title in result.results[:max(0, min(limit, 50))]]}

    def get_movie_details(self, title_or_id: str) -> dict[str, Any]:
        with self._catalog() as (tmdb, _db, _titles):
            return self._dump(self._resolve(tmdb, title_or_id, media_type="movie"))

    def get_tv_details(self, title_or_id: str) -> dict[str, Any]:
        with self._catalog() as (tmdb, _db, _titles):
            return self._dump(self._resolve(tmdb, title_or_id, media_type="tv"))

    def _trending(self, media_type: Literal["movie", "tv"], limit: int) -> dict[str, Any]:
        with self._catalog() as (tmdb, _db, _titles):
            result = (tmdb.get_trending_movies() if media_type == "movie"
                      else tmdb.get_trending_tv())
            return {"media_type": media_type, "total_results": result.total_results,
                    "results": [self._dump(title) for title in result.results[:max(0, min(limit, 50))]]}

    def get_trending_movies(self, limit: int = 10) -> dict[str, Any]:
        return self._trending("movie", limit)

    def get_trending_tv(self, limit: int = 10) -> dict[str, Any]:
        return self._trending("tv", limit)

    def get_streaming_availability(self, title_or_id: str,
                                   media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        settings = self.settings
        if not settings.watchmode_api_key:
            raise RuntimeError("Watchmode is not configured; set WATCHMODE_API_KEY.")
        with self._catalog() as (tmdb, db, titles):
            title, _ = self._resolve_and_save(tmdb, titles, title_or_id, media_type=media_type)
            try:
                with self.watchmode_client_factory(api_key=settings.watchmode_api_key) as client:
                    service = WatchmodeService(
                        client, titles, WatchmodeRepository(db), region=settings.watchmode_region,
                        cache_ttl_hours=settings.watchmode_cache_ttl_hours,
                    )
                    options = service.availability_for_tmdb(title.tmdb_id, title.media_type)
            except WatchmodeError as exc:
                logger.warning("Watchmode availability failed (%s)", type(exc).__name__)
                raise RuntimeError("Watchmode availability request failed; check server logs and configuration.") from None
            return {"title": title.title, "tmdb_id": title.tmdb_id,
                    "region": settings.watchmode_region.upper(),
                    "availability": [item.model_dump(mode="json") for item in options]}

    def has_watched(self, title_or_id: str,
                    media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        with self._catalog() as (tmdb, db, titles):
            title, _ = self._resolve_and_save(tmdb, titles, title_or_id, media_type=media_type)
            watched = PersonalizationService(db).has_watched("tmdb", title.tmdb_id)
            return {"tmdb_id": title.tmdb_id, "title": title.title,
                    "media_type": title.media_type, "watched": watched}

    def _record(self, title_or_id: str, *, rating: int | None = None,
                liked: bool | None = None, notes: str | None = None,
                media_type: Literal["movie", "tv"] | None = None,
                not_interested: bool = False, watch_later: bool = False) -> dict[str, Any]:
        with self._catalog() as (tmdb, db, titles):
            title, title_id = self._resolve_and_save(tmdb, titles, title_or_id, media_type=media_type)
            service = PersonalizationService(db)
            if not_interested:
                service.mark_not_interested(title_id, notes=notes)
                action = "not_interested"
            elif watch_later:
                service.add_to_watch_later(title_id)
                action = "watch_later"
            else:
                service.add_watched_title(title_id, rating=rating, liked=liked, notes=notes)
                action = "watched"
            return {"action": action, "tmdb_id": title.tmdb_id,
                    "title": title.title, "media_type": title.media_type}

    def mark_watched(self, title_or_id: str, rating: int | None = None,
                     liked: bool | None = None, notes: str | None = None,
                     media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        return self._record(title_or_id, rating=rating, liked=liked, notes=notes, media_type=media_type)

    def mark_not_interested(self, title_or_id: str,
                            media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        return self._record(title_or_id, media_type=media_type, not_interested=True)

    def add_to_watch_later(self, title_or_id: str,
                           media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        return self._record(title_or_id, media_type=media_type, watch_later=True)

    def get_watch_history(self, limit: int = 50) -> dict[str, Any]:
        initialize_database(self.settings.database_path)
        with closing(connect(self.settings.database_path)) as db:
            rows = PersonalizationService(db).get_watch_history()[:max(0, min(limit, 200))]
            return {"count": len(rows), "items": [dict(row) for row in rows]}

    def get_watch_later(self, limit: int = 50) -> dict[str, Any]:
        initialize_database(self.settings.database_path)
        with closing(connect(self.settings.database_path)) as db:
            rows = PersonalizationService(db).get_watch_later()[:max(0, min(limit, 200))]
            return {"count": len(rows), "items": [dict(row) for row in rows]}

    def get_user_preferences(self) -> dict[str, Any]:
        initialize_database(self.settings.database_path)
        with closing(connect(self.settings.database_path)) as db:
            return PersonalizationService(db).get_user_preferences().model_dump(mode="json")

    def _recommendations(self, limit: int) -> list[dict[str, Any]]:
        settings = self.settings
        with self._catalog() as (tmdb, db, _titles):
            with ExitStack() as stack:
                pipeline = build_recommendation_pipeline(
                    settings, db, tmdb, stack,
                    watchmode_client_factory=self.watchmode_client_factory,
                )
                service = build_ai_recommendation_service(
                    settings, pipeline, stack,
                    groq_client_factory=self.groq_client_factory,
                    claude_client_factory=self.claude_client_factory,
                )
                try:
                    result = service.recommend(limit=max(0, min(limit, 50)))
                except (WatchmodeError, WatchmodeTitleNotFound):
                    raise RuntimeError(
                        "Watchmode availability lookup failed; check server logs and configuration."
                    ) from None
                return [item.model_dump(mode="json") for item in result]

    def get_weekend_recommendations(self, limit: int = 10) -> dict[str, Any]:
        recommendations = self._recommendations(limit)
        return {"count": len(recommendations), "recommendations": recommendations}

    def get_weekend_digest(self, limit: int = 8,
                           request: str = "Recommend what I should watch this weekend.") -> dict[str, Any]:
        settings = self.settings
        with self._catalog() as (tmdb, db, _titles):
            intent = extract_request_intent(
                request, tmdb=tmdb, region=settings.watchmode_region
            )
            with ExitStack() as stack:
                pipeline = build_recommendation_pipeline(
                    settings, db, tmdb, stack,
                    watchmode_client_factory=self.watchmode_client_factory,
                )
                ai_recommender = build_ai_recommendation_service(
                    settings, pipeline, stack,
                    groq_client_factory=self.groq_client_factory,
                    claude_client_factory=self.claude_client_factory,
                )
                try:
                    digest = WeekendDigestService(pipeline, ai_recommender).generate(
                        limit=max(0, min(limit, 50)),
                        request=request,
                    )
                except (WatchmodeError, WatchmodeTitleNotFound):
                    raise RuntimeError(
                        "Watchmode availability lookup failed; check server logs and configuration."
                    ) from None
        return {"intent": intent.model_dump(mode="json"),
                "digest": digest.model_dump(mode="json"),
                "markdown": format_weekend_digest(digest)}
