"""Personal watch activity and preference operations for the local user."""

import json
import sqlite3
from typing import Any

from ..repositories import (
    FeedbackRepository,
    PreferencesRepository,
    RecommendationHistoryRepository,
    TitleRepository,
    WatchHistoryRepository,
    WatchLaterRepository,
)
from .models import UserPreferences


class PersonalizationService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.titles = TitleRepository(connection)
        self.history = WatchHistoryRepository(connection)
        self.watch_later = WatchLaterRepository(connection)
        self.feedback = FeedbackRepository(connection)
        self.preferences = PreferencesRepository(connection)
        self.recommendations = RecommendationHistoryRepository(connection)

    def add_watched_title(self, title_id: int, *, rating: int | None = None,
                          liked: bool | None = None, notes: str | None = None,
                          watched_at: str | None = None) -> int:
        if rating is not None:
            self._validate_rating(rating)
        event_id = self.history.add(title_id, user_rating=rating, liked=liked,
                                    notes=notes, watched_at=watched_at)
        if rating is not None:
            self.feedback.set_rating(title_id, rating)
        if liked is not None:
            self.feedback.set_sentiment(title_id, "liked" if liked else "disliked", notes)
        return event_id

    def mark_watched(self, title_id: int, *, watched_at: str | None = None,
                     notes: str | None = None) -> int:
        return self.add_watched_title(title_id, watched_at=watched_at, notes=notes)

    def mark_not_interested(self, title_id: int, *, notes: str | None = None) -> None:
        self.feedback.set_sentiment(title_id, "disliked", notes)

    def mark_liked(self, title_id: int, *, notes: str | None = None) -> None:
        self.feedback.set_sentiment(title_id, "liked", notes)

    def add_to_watch_later(self, title_id: int, *, notes: str | None = None) -> None:
        self.watch_later.add(title_id, notes)

    def remove_from_watch_later(self, title_id: int) -> bool:
        return self.watch_later.remove(title_id)

    def rate_title(self, title_id: int, rating: int) -> None:
        self._validate_rating(rating)
        self.feedback.set_rating(title_id, rating)

    @staticmethod
    def _validate_rating(rating: int) -> None:
        if isinstance(rating, bool) or not 1 <= rating <= 10:
            raise ValueError("Rating must be an integer from 1 to 10")

    def get_watch_history(self) -> list[sqlite3.Row]:
        return self.history.list()

    def has_watched(self, provider: str, external_id: str | int) -> bool:
        """Check history using the normalized provider/external-ID identity."""
        return self.history.has_watched(provider, external_id)

    def get_watch_later(self) -> list[sqlite3.Row]:
        return self.watch_later.list()

    def get_user_preferences(self) -> UserPreferences:
        row = self.preferences.get()
        if row is None:
            return UserPreferences()
        def parse_list(key: str, fallback: str | None = None) -> list[str]:
            value = row[key]
            if not value and fallback:
                value = row[fallback]
            return json.loads(value or "[]")

        return UserPreferences(
            preferred_genres=parse_list("preferred_genres_json", "favorite_genres_json"),
            excluded_genres=parse_list("excluded_genres_json"),
            minimum_rating=row["minimum_rating"],
            preferred_streaming_services=parse_list("preferred_streaming_services_json", "streaming_services_json"),
            excluded_streaming_services=parse_list("excluded_streaming_services_json"),
            preferred_languages=parse_list("preferred_languages_json"),
            movies_enabled=bool(row["movies_enabled"]),
            tv_enabled=bool(row["tv_enabled"]),
            new_releases_enabled=bool(row["new_releases_enabled"]),
            trending_enabled=bool(row["trending_enabled"]),
            hidden_gems_enabled=bool(row["hidden_gems_enabled"]),
        )

    def update_user_preferences(self, preferences: UserPreferences) -> None:
        self.preferences.save(**preferences.model_dump())

    def record_recommendation(self, title_ids: list[int], *, criteria: dict[str, Any] | None = None,
                              explanation: str | None = None) -> int:
        return self.recommendations.add(title_ids, criteria=criteria, explanation=explanation)

    def get_recommendation_history(self) -> list[sqlite3.Row]:
        return self.recommendations.list()
