"""Deterministic recommendation candidate discovery, filtering, and scoring."""

import math
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from ..personalization import PersonalizationService
from ..tmdb.client import TmdbClient
from ..tmdb.models import Title
from ..watchmode.models import StreamingAvailability
from ..repositories import WatchmodeRepository
from .models import CandidateCategory, RecommendationWeights, WeekendCandidate


class RecommendationPipeline:
    def __init__(self, tmdb: TmdbClient, personalization: PersonalizationService, *,
                 watchmode: WatchmodeRepository | None = None,
                 availability_lookup: Callable[[Title], list[StreamingAvailability]] | None = None,
                 weights: RecommendationWeights | None = None,
                 today: date | None = None, new_release_days: int = 90,
                 minimum_votes: int = 200, hidden_gem_min_votes: int = 100,
                 hidden_gem_max_votes: int = 3000, hidden_gem_max_popularity: float = 35.0,
                 region: str = "US", availability_cache_ttl_hours: int = 24):
        self.tmdb = tmdb
        self.personalization = personalization
        self.watchmode = watchmode
        self.availability_lookup = availability_lookup
        self.weights = weights or RecommendationWeights()
        self.today = today or date.today()
        self.new_release_days = new_release_days
        self.minimum_votes = minimum_votes
        self.hidden_gem_min_votes = hidden_gem_min_votes
        self.hidden_gem_max_votes = hidden_gem_max_votes
        self.hidden_gem_max_popularity = hidden_gem_max_popularity
        self.region = region.upper()
        self.availability_cache_ttl_hours = availability_cache_ttl_hours

    def get_weekend_candidates(self, *, limit: int = 20) -> list[WeekendCandidate]:
        """Discover sources, filter against the local profile, and rank the survivors."""
        preferences = self.personalization.get_user_preferences()
        discovered: dict[tuple[str, int], dict[str, Any]] = {}

        def add(titles: list[Title], category: CandidateCategory) -> None:
            for title in titles:
                key = ("tmdb", title.tmdb_id)
                record = discovered.setdefault(key, {"title": title, "categories": set()})
                # Keep the richest version if overlapping source responses vary.
                if len(title.genres) > len(record["title"].genres) or title.overview and not record["title"].overview:
                    record["title"] = title
                record["categories"].add(category)

        today = self.today
        date_floor = (today - timedelta(days=self.new_release_days)).isoformat()
        date_ceiling = today.isoformat()

        if preferences.trending_enabled:
            if preferences.movies_enabled:
                add(self.tmdb.get_trending_movies().results, "TRENDING")
            if preferences.tv_enabled:
                add(self.tmdb.get_trending_tv().results, "TRENDING")

        if preferences.new_releases_enabled:
            if preferences.movies_enabled:
                add(self.tmdb.discover_movies(
                    page=1, **{"primary_release_date.gte": date_floor,
                              "primary_release_date.lte": date_ceiling,
                              "sort_by": "primary_release_date.desc"},
                ).results, "NEW_RELEASE")
            if preferences.tv_enabled:
                add(self.tmdb.discover_tv(
                    page=1, **{"first_air_date.gte": date_floor,
                              "first_air_date.lte": date_ceiling,
                              "sort_by": "first_air_date.desc"},
                ).results, "NEW_RELEASE")

        rating_floor = max(preferences.minimum_rating, 7.0)
        if preferences.movies_enabled:
            add(self.tmdb.discover_movies(
                page=1, sort_by="vote_average.desc", **{
                    "vote_average.gte": rating_floor,
                    "vote_count.gte": self.minimum_votes,
                },
            ).results, "HIGHLY_RATED")
        if preferences.tv_enabled:
            add(self.tmdb.discover_tv(
                page=1, sort_by="vote_average.desc", **{
                    "vote_average.gte": rating_floor,
                    "vote_count.gte": self.minimum_votes,
                },
            ).results, "HIGHLY_RATED")

        if preferences.hidden_gems_enabled:
            hidden_floor = max(preferences.minimum_rating, 7.0)
            if preferences.movies_enabled:
                add(self.tmdb.discover_movies(
                    page=1, sort_by="vote_average.desc", **{
                        "vote_average.gte": hidden_floor,
                        "vote_count.gte": self.hidden_gem_min_votes,
                        "vote_count.lte": self.hidden_gem_max_votes,
                    },
                ).results, "HIDDEN_GEM")
            if preferences.tv_enabled:
                add(self.tmdb.discover_tv(
                    page=1, sort_by="vote_average.desc", **{
                        "vote_average.gte": hidden_floor,
                        "vote_count.gte": self.hidden_gem_min_votes,
                        "vote_count.lte": self.hidden_gem_max_votes,
                    },
                ).results, "HIDDEN_GEM")

        results = []
        for item in discovered.values():
            title: Title = item["title"]
            categories: set[CandidateCategory] = item["categories"]
            if not self._passes_filters(title, preferences):
                continue
            categories = set(categories)
            if title.rating is None or title.rating < 7.0 or title.vote_count < self.minimum_votes:
                categories.discard("HIGHLY_RATED")
            availability = self._availability(title)
            excluded_services = {service.casefold() for service in preferences.excluded_streaming_services}
            availability = [item for item in availability if item.provider.casefold() not in excluded_services]
            if not self._matches_streaming_preferences(availability, preferences):
                continue
            if not self._is_hidden_gem(title, preferences):
                categories.discard("HIDDEN_GEM")
            if not categories:
                continue
            score, breakdown = self._score(title, categories, availability, preferences)
            reasons = self._reasons(title, categories, availability, preferences, breakdown)
            results.append(WeekendCandidate(
                title=title,
                categories=sorted(categories),
                match_score=round(score, 2),
                score_breakdown={key: round(value, 4) for key, value in breakdown.items()},
                streaming_availability=availability,
                reasons=reasons,
            ))
        results.sort(key=lambda candidate: (-candidate.match_score, candidate.title.title.casefold(), candidate.title.tmdb_id))
        return results[:max(0, limit)]

    def _passes_filters(self, title: Title, preferences) -> bool:
        if title.media_type == "movie" and not preferences.movies_enabled:
            return False
        if title.media_type == "tv" and not preferences.tv_enabled:
            return False
        if self.personalization.has_watched("tmdb", title.tmdb_id):
            return False
        local = self.personalization.titles.get_by_provider_external("tmdb", title.tmdb_id)
        if local is not None:
            feedback = self.personalization.feedback.get(local["id"])
            if feedback and feedback["sentiment"] == "disliked":
                return False
        title_genres = {genre.casefold() for genre in title.genres}
        excluded = {genre.casefold() for genre in preferences.excluded_genres}
        if title_genres & excluded:
            return False
        if title.rating is not None and title.rating < preferences.minimum_rating:
            return False
        if title.rating is None and preferences.minimum_rating > 0:
            return False
        preferred_languages = {language.casefold() for language in preferences.preferred_languages}
        if preferred_languages and (title.original_language or "").casefold() not in preferred_languages:
            return False
        return True

    def _availability(self, title: Title) -> list[StreamingAvailability]:
        if self.availability_lookup:
            return self.availability_lookup(title)
        if self.watchmode is None:
            return []
        local = self.personalization.titles.get_by_provider_external("tmdb", title.tmdb_id)
        if local is None:
            return []
        rows = self.watchmode.get_cached_availability(
            int(local["id"]), self.region, self.availability_cache_ttl_hours
        )
        return [StreamingAvailability.model_validate(dict(row)) for row in rows or []]

    @staticmethod
    def _matches_streaming_preferences(availability: list[StreamingAvailability], preferences) -> bool:
        providers = {item.provider.casefold() for item in availability if item.available}
        excluded = {name.casefold() for name in preferences.excluded_streaming_services}
        if providers & excluded:
            return False
        preferred = {name.casefold() for name in preferences.preferred_streaming_services}
        return not preferred or bool(providers & preferred)

    def _is_hidden_gem(self, title: Title, preferences) -> bool:
        if not preferences.hidden_gems_enabled:
            return False
        if title.rating is None or title.rating < max(7.0, preferences.minimum_rating):
            return False
        if not self.hidden_gem_min_votes <= title.vote_count <= self.hidden_gem_max_votes:
            return False
        if title.popularity is None or title.popularity > self.hidden_gem_max_popularity:
            return False
        preferred_genres = {genre.casefold() for genre in preferences.preferred_genres}
        title_genres = {genre.casefold() for genre in title.genres}
        return bool(preferred_genres and title_genres & preferred_genres)

    def _score(self, title: Title, categories: set[CandidateCategory],
               availability: list[StreamingAvailability], preferences) -> tuple[float, dict[str, float]]:
        hidden = "HIDDEN_GEM" in categories
        title_genres = {genre.casefold() for genre in title.genres}
        preferred_genres = {genre.casefold() for genre in preferences.preferred_genres}
        genre_match = len(title_genres & preferred_genres) / len(preferred_genres) if preferred_genres else 0.0
        providers = {item.provider.casefold() for item in availability if item.available}
        preferred_services = {service.casefold() for service in preferences.preferred_streaming_services}
        streaming = (1.0 if providers & preferred_services else 0.0) if preferred_services else (0.7 if providers else 0.0)
        age_days = None
        if title.release_date:
            try:
                age_days = (self.today - date.fromisoformat(title.release_date[:10])).days
            except ValueError:
                age_days = None
        recency = max(0.0, min(1.0, math.exp(-max(0, age_days or 0) / 45))) if age_days is not None else 0.0
        popularity = min(1.0, (title.popularity or 0.0) / 40.0)
        if hidden:
            popularity = 1.0 - popularity
        breakdown = {
            "rating": max(0.0, min(1.0, (title.rating or 0.0) / 10.0)),
            "vote_count": min(1.0, math.log1p(max(0, title.vote_count)) / math.log1p(1000)),
            "popularity": popularity,
            "recency": recency,
            "genre_match": genre_match,
            "streaming_availability": streaming,
            "trending": 1.0 if "TRENDING" in categories else 0.0,
        }
        weight_values = self.weights.model_dump()
        active_weights = {key: value for key, value in weight_values.items()
                          if not (key == "genre_match" and not preferred_genres)
                          and not (key == "streaming_availability" and not availability)}
        denominator = sum(active_weights.values())
        score = 100 * sum(breakdown[key] * weight for key, weight in active_weights.items()) / denominator if denominator else 0.0
        return score, breakdown

    @staticmethod
    def _reasons(title: Title, categories: set[CandidateCategory],
                 availability: list[StreamingAvailability], preferences,
                 breakdown: dict[str, float]) -> list[str]:
        reasons = []
        if "NEW_RELEASE" in categories:
            reasons.append("New release")
        if "TRENDING" in categories:
            reasons.append("Trending now")
        if "HIGHLY_RATED" in categories:
            reasons.append(f"Rated {title.rating:.1f}/10 by {title.vote_count:,} voters")
        if "HIDDEN_GEM" in categories:
            reasons.append("Strong genre match with lower popularity")
        if breakdown["genre_match"] > 0:
            reasons.append("Matches preferred genres")
        if availability:
            providers = sorted({item.provider for item in availability if item.available})
            if providers:
                reasons.append("Available on " + ", ".join(providers[:3]))
        return reasons
