"""Small SQLite data-access layer."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


class TitleRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert(self, *, provider: str, external_id: str, media_type: str,
               name: str, original_name: str | None = None,
               overview: str | None = None, release_date: str | None = None,
               genres: list[str] | None = None, rating: float | None = None,
               popularity: float | None = None, vote_count: int | None = None,
               poster_path: str | None = None, backdrop_path: str | None = None,
               original_language: str | None = None,
               metadata: dict[str, Any] | None = None) -> int:
        """Insert or update a provider title, returning its local id."""
        self.connection.execute(
            """INSERT INTO titles
               (provider, external_id, media_type, name, original_name, overview,
                release_date, genres_json, rating, popularity, vote_count, poster_path,
                backdrop_path, original_language, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, external_id) DO UPDATE SET
                 media_type=excluded.media_type, name=excluded.name,
                 original_name=excluded.original_name, overview=excluded.overview,
                 release_date=excluded.release_date, genres_json=excluded.genres_json,
                 rating=excluded.rating, popularity=excluded.popularity,
                 vote_count=excluded.vote_count, poster_path=excluded.poster_path,
                 backdrop_path=excluded.backdrop_path, original_language=excluded.original_language,
                 metadata_json=excluded.metadata_json, updated_at=CURRENT_TIMESTAMP""",
            (provider, str(external_id), media_type, name, original_name, overview,
             release_date, _json(genres or []), rating, popularity, vote_count,
             poster_path, backdrop_path, original_language, _json(metadata or {})),
        )
        row = self.connection.execute(
            "SELECT id FROM titles WHERE provider = ? AND external_id = ?",
            (provider, str(external_id)),
        ).fetchone()
        self.connection.commit()
        return int(row["id"])

    def get(self, title_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()

    def update_identity_details(self, title_id: int, *, media_type: str, name: str) -> None:
        self.connection.execute(
            "UPDATE titles SET media_type = ?, name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_type, name, title_id),
        )
        self.connection.commit()

    def get_by_provider_external(self, provider: str, external_id: str | int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM titles WHERE provider = ? AND external_id = ?",
            (provider, str(external_id)),
        ).fetchone()

    def list_titles(self, media_type: str | None = None) -> list[sqlite3.Row]:
        if media_type is None:
            rows = self.connection.execute("SELECT * FROM titles ORDER BY name")
        else:
            rows = self.connection.execute("SELECT * FROM titles WHERE media_type = ? ORDER BY name", (media_type,))
        return list(rows)


class WatchHistoryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def add(self, title_id: int, *, user_rating: int | None = None,
            liked: bool | None = None, notes: str | None = None,
            watched_at: str | None = None) -> int:
        cursor = self.connection.execute(
            """INSERT INTO watch_history (title_id, user_rating, liked, notes, watched_at)
               VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
            (title_id, user_rating, None if liked is None else int(liked), notes, watched_at),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT wh.*, t.name, t.media_type, t.provider, t.external_id FROM watch_history wh
               JOIN titles t ON t.id = wh.title_id ORDER BY wh.watched_at DESC, wh.id DESC"""
        ))

    def has_watched(self, provider: str, external_id: str | int) -> bool:
        return self.connection.execute(
            """SELECT 1 FROM watch_history wh JOIN titles t ON t.id = wh.title_id
               WHERE t.provider = ? AND t.external_id = ? LIMIT 1""",
            (provider, str(external_id)),
        ).fetchone() is not None


class WatchLaterRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def add(self, title_id: int, notes: str | None = None) -> None:
        self.connection.execute(
            """INSERT INTO watch_later (title_id, notes) VALUES (?, ?)
               ON CONFLICT(title_id) DO UPDATE SET notes=excluded.notes""",
            (title_id, notes),
        )
        self.connection.commit()

    def remove(self, title_id: int) -> bool:
        cursor = self.connection.execute("DELETE FROM watch_later WHERE title_id = ?", (title_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def list(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT wl.*, t.name, t.media_type, t.provider, t.external_id
               FROM watch_later wl JOIN titles t ON t.id = wl.title_id
               ORDER BY wl.added_at DESC, wl.id DESC"""
        ))


class FeedbackRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def set_rating(self, title_id: int, rating: int) -> None:
        self.connection.execute(
            """INSERT INTO title_feedback (title_id, rating) VALUES (?, ?)
               ON CONFLICT(title_id) DO UPDATE SET rating=excluded.rating, updated_at=CURRENT_TIMESTAMP""",
            (title_id, rating),
        )
        self.connection.commit()

    def set_sentiment(self, title_id: int, sentiment: str, notes: str | None = None) -> None:
        self.connection.execute(
            """INSERT INTO title_feedback (title_id, sentiment, notes) VALUES (?, ?, ?)
               ON CONFLICT(title_id) DO UPDATE SET sentiment=excluded.sentiment,
                 notes=COALESCE(excluded.notes, title_feedback.notes), updated_at=CURRENT_TIMESTAMP""",
            (title_id, sentiment, notes),
        )
        self.connection.commit()

    def get(self, title_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM title_feedback WHERE title_id = ?", (title_id,)).fetchone()


class PreferencesRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get(self) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM user_preferences WHERE id = 1").fetchone()

    def save(self, *, favorite_genres: list[str] | None = None,
             minimum_rating: float = 0, streaming_services: list[str] | None = None,
             excluded_genres: list[str] | None = None,
             preferred_genres: list[str] | None = None,
             preferred_streaming_services: list[str] | None = None,
             excluded_streaming_services: list[str] | None = None,
             preferred_languages: list[str] | None = None,
             movies_enabled: bool = True, tv_enabled: bool = True,
             new_releases_enabled: bool = True, trending_enabled: bool = True,
             hidden_gems_enabled: bool = True) -> None:
        genres = preferred_genres if preferred_genres is not None else (favorite_genres or [])
        services = preferred_streaming_services if preferred_streaming_services is not None else (streaming_services or [])
        self.connection.execute(
            """INSERT INTO user_preferences
               (id, favorite_genres_json, preferred_genres_json, minimum_rating,
                streaming_services_json, preferred_streaming_services_json,
                excluded_genres_json, excluded_streaming_services_json, preferred_languages_json,
                movies_enabled, tv_enabled, new_releases_enabled, trending_enabled, hidden_gems_enabled)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 favorite_genres_json=excluded.favorite_genres_json,
                 preferred_genres_json=excluded.preferred_genres_json,
                 minimum_rating=excluded.minimum_rating,
                 streaming_services_json=excluded.streaming_services_json,
                 preferred_streaming_services_json=excluded.preferred_streaming_services_json,
                 excluded_genres_json=excluded.excluded_genres_json,
                 excluded_streaming_services_json=excluded.excluded_streaming_services_json,
                 preferred_languages_json=excluded.preferred_languages_json,
                 movies_enabled=excluded.movies_enabled, tv_enabled=excluded.tv_enabled,
                 new_releases_enabled=excluded.new_releases_enabled,
                 trending_enabled=excluded.trending_enabled,
                 hidden_gems_enabled=excluded.hidden_gems_enabled,
                 updated_at=CURRENT_TIMESTAMP""",
            (_json(genres), _json(genres), minimum_rating, _json(services), _json(services),
             _json(excluded_genres or []), _json(excluded_streaming_services or []),
             _json(preferred_languages or []), int(movies_enabled), int(tv_enabled),
             int(new_releases_enabled), int(trending_enabled), int(hidden_gems_enabled)),
        )
        self.connection.commit()


class RecommendationHistoryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def add(self, title_ids: list[int], *, criteria: dict[str, Any] | None = None,
            explanation: str | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO recommendation_history (title_ids_json, criteria_json, explanation) VALUES (?, ?, ?)",
            (_json(title_ids), _json(criteria or {}), explanation),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM recommendation_history ORDER BY created_at DESC, id DESC"))


class WatchmodeRepository:
    """Persists Watchmode ID mappings and cached regional availability."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_mapping(self, title_id: int) -> int | None:
        row = self.connection.execute(
            "SELECT watchmode_id FROM watchmode_title_mappings WHERE title_id = ?", (title_id,)
        ).fetchone()
        return int(row["watchmode_id"]) if row else None

    def save_mapping(self, title_id: int, watchmode_id: int) -> None:
        self.connection.execute(
            """INSERT INTO watchmode_title_mappings (title_id, watchmode_id)
               VALUES (?, ?) ON CONFLICT(title_id) DO UPDATE SET
               watchmode_id=excluded.watchmode_id, mapped_at=CURRENT_TIMESTAMP""",
            (title_id, watchmode_id),
        )
        self.connection.commit()

    def clear_availability_cache(self, title_id: int, region: str) -> None:
        self.connection.execute(
            "DELETE FROM streaming_availability WHERE title_id = ? AND region = ?",
            (title_id, region.upper()),
        )
        self.connection.execute(
            "DELETE FROM watchmode_availability_cache WHERE title_id = ? AND region = ?",
            (title_id, region.upper()),
        )
        self.connection.commit()

    def get_cached_availability(self, title_id: int, region: str, ttl_hours: int | None):
        cache = self.connection.execute(
            "SELECT fetched_at FROM watchmode_availability_cache WHERE title_id = ? AND region = ?",
            (title_id, region.upper()),
        ).fetchone()
        if cache is None:
            return None
        fetched_at = datetime.fromisoformat(cache["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if ttl_hours is not None and datetime.now(timezone.utc) - fetched_at > timedelta(hours=ttl_hours):
            return None
        rows = list(self.connection.execute(
            """SELECT * FROM streaming_availability WHERE title_id = ? AND region = ?
               ORDER BY provider_type, provider""", (title_id, region.upper())
        ))
        return rows

    def save_availability(self, title_id: int, watchmode_id: int, region: str,
                          availability: list) -> None:
        fetched_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "DELETE FROM streaming_availability WHERE title_id = ? AND region = ?",
            (title_id, region.upper()),
        )
        unique: dict[tuple[str, str, str, str | None], Any] = {}
        for item in availability:
            unique[(item.provider, item.provider_type, region.upper(), item.web_url)] = item
        self.connection.executemany(
            """INSERT INTO streaming_availability
               (title_id, watchmode_id, provider, provider_type, region, available, web_url, price, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(title_id, provider, provider_type, region, web_url) DO UPDATE SET
                 watchmode_id=excluded.watchmode_id, available=excluded.available,
                 price=excluded.price, fetched_at=excluded.fetched_at""",
            [(title_id, watchmode_id, item.provider, item.provider_type, region.upper(),
              int(item.available), item.web_url, item.price, fetched_at)
             for item in unique.values()],
        )
        self.connection.execute(
            """INSERT INTO watchmode_availability_cache (title_id, region, fetched_at)
               VALUES (?, ?, ?) ON CONFLICT(title_id, region) DO UPDATE SET fetched_at=excluded.fetched_at""",
            (title_id, region.upper(), fetched_at),
        )
        self.connection.commit()
