import json

import pytest

from weekend_watch.database import connect, initialize_database
from weekend_watch.repositories import (
    PreferencesRepository,
    RecommendationHistoryRepository,
    TitleRepository,
    WatchHistoryRepository,
)


def test_initialize_database_creates_required_tables(tmp_path):
    path = tmp_path / "nested" / "watch.db"
    initialize_database(path)
    with connect(path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"titles", "watch_history", "user_preferences", "recommendation_history"} <= names


def test_title_upsert_and_provider_agnostic_identifier(tmp_path):
    path = tmp_path / "watch.db"
    initialize_database(path)
    with connect(path) as db:
        repo = TitleRepository(db)
        first = repo.upsert(provider="tmdb", external_id="123", media_type="tv", name="Example", genres=["Drama"])
        again = repo.upsert(provider="tmdb", external_id="123", media_type="tv", name="Updated")
        assert first == again
        assert repo.get(first)["name"] == "Updated"
        assert json.loads(repo.get(first)["genres_json"]) == []
        second = repo.upsert(provider="future-provider", external_id="123", media_type="movie", name="Other")
        assert second != first


def test_repositories_persist_preferences_history_and_recommendations(tmp_path):
    path = tmp_path / "watch.db"
    initialize_database(path)
    with connect(path) as db:
        title_id = TitleRepository(db).upsert(provider="tmdb", external_id="8", media_type="movie", name="Film")
        history = WatchHistoryRepository(db)
        history.add(title_id, user_rating=5, liked=True)
        assert history.list()[0]["name"] == "Film"
        preferences = PreferencesRepository(db)
        preferences.save(favorite_genres=["Comedy"], minimum_rating=7.5, streaming_services=["Netflix"])
        assert json.loads(preferences.get()["favorite_genres_json"]) == ["Comedy"]
        recs = RecommendationHistoryRepository(db)
        recs.add([title_id], criteria={"minimum_rating": 7.5}, explanation="A good fit")
        assert json.loads(recs.list()[0]["title_ids_json"]) == [title_id]


def test_database_constraints_reject_invalid_media_type(tmp_path):
    path = tmp_path / "watch.db"
    initialize_database(path)
    with connect(path) as db, pytest.raises(Exception):
        TitleRepository(db).upsert(provider="tmdb", external_id="1", media_type="book", name="No")


def test_initialize_database_upgrades_original_titles_table(tmp_path):
    path = tmp_path / "old.db"
    with connect(path) as db:
        db.execute("""CREATE TABLE titles (
            id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
            media_type TEXT NOT NULL, name TEXT NOT NULL, original_name TEXT, overview TEXT,
            release_date TEXT, genres_json TEXT NOT NULL DEFAULT '[]', rating REAL,
            popularity REAL, metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, external_id))""")
    initialize_database(path)
    with connect(path) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(titles)")}
    assert {"vote_count", "poster_path", "backdrop_path", "original_language"} <= columns


def test_initialize_database_migrates_legacy_watch_ratings_and_history(tmp_path):
    path = tmp_path / "legacy-history.db"
    with connect(path) as db:
        db.execute("""CREATE TABLE titles (
            id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
            media_type TEXT NOT NULL, name TEXT NOT NULL, original_name TEXT, overview TEXT,
            release_date TEXT, genres_json TEXT NOT NULL DEFAULT '[]', rating REAL,
            popularity REAL, metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, external_id))""")
        db.execute("INSERT INTO titles (id, provider, external_id, media_type, name) VALUES (1, 'tmdb', '55', 'movie', 'Old')")
        db.execute("""CREATE TABLE watch_history (
            id INTEGER PRIMARY KEY, title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
            watched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            user_rating INTEGER CHECK (user_rating IS NULL OR (user_rating >= 1 AND user_rating <= 5)),
            liked INTEGER CHECK (liked IS NULL OR liked IN (0, 1)), notes TEXT)""")
        db.execute("INSERT INTO watch_history (id, title_id, user_rating, liked, notes) VALUES (8, 1, 5, 1, 'kept')")
        db.execute("""CREATE TABLE user_preferences (
            id INTEGER PRIMARY KEY, favorite_genres_json TEXT NOT NULL DEFAULT '[]',
            minimum_rating REAL NOT NULL DEFAULT 0, streaming_services_json TEXT NOT NULL DEFAULT '[]',
            excluded_genres_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    initialize_database(path)
    with connect(path) as db:
        row = db.execute("SELECT * FROM watch_history WHERE id=8").fetchone()
        db.execute("UPDATE watch_history SET user_rating=9 WHERE id=8")
        pref_cols = {item["name"] for item in db.execute("PRAGMA table_info(user_preferences)")}
    assert row["user_rating"] == 5 and row["notes"] == "kept"
    assert {"preferred_genres_json", "preferred_languages_json", "hidden_gems_enabled"} <= pref_cols


