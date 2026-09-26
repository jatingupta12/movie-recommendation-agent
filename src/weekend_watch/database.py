"""SQLite schema and connection helpers."""

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    name TEXT NOT NULL,
    original_name TEXT,
    overview TEXT,
    release_date TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    rating REAL CHECK (rating IS NULL OR (rating >= 0 AND rating <= 10)),
    popularity REAL,
    vote_count INTEGER,
    poster_path TEXT,
    backdrop_path TEXT,
    original_language TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, external_id)
);

CREATE TABLE IF NOT EXISTS watch_history (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    watched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_rating INTEGER CHECK (user_rating IS NULL OR (user_rating >= 1 AND user_rating <= 10)),
    liked INTEGER CHECK (liked IS NULL OR liked IN (0, 1)),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    favorite_genres_json TEXT NOT NULL DEFAULT '[]',
    preferred_genres_json TEXT NOT NULL DEFAULT '[]',
    excluded_genres_json TEXT NOT NULL DEFAULT '[]',
    minimum_rating REAL NOT NULL DEFAULT 0 CHECK (minimum_rating >= 0 AND minimum_rating <= 10),
    streaming_services_json TEXT NOT NULL DEFAULT '[]',
    preferred_streaming_services_json TEXT NOT NULL DEFAULT '[]',
    excluded_streaming_services_json TEXT NOT NULL DEFAULT '[]',
    preferred_languages_json TEXT NOT NULL DEFAULT '[]',
    movies_enabled INTEGER NOT NULL DEFAULT 1 CHECK (movies_enabled IN (0, 1)),
    tv_enabled INTEGER NOT NULL DEFAULT 1 CHECK (tv_enabled IN (0, 1)),
    new_releases_enabled INTEGER NOT NULL DEFAULT 1 CHECK (new_releases_enabled IN (0, 1)),
    trending_enabled INTEGER NOT NULL DEFAULT 1 CHECK (trending_enabled IN (0, 1)),
    hidden_gems_enabled INTEGER NOT NULL DEFAULT 1 CHECK (hidden_gems_enabled IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS title_feedback (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
    rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 10)),
    sentiment TEXT CHECK (sentiment IS NULL OR sentiment IN ('liked', 'disliked')),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watch_later (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS recommendation_history (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    title_ids_json TEXT NOT NULL DEFAULT '[]',
    criteria_json TEXT NOT NULL DEFAULT '{}',
    explanation TEXT
);

CREATE TABLE IF NOT EXISTS watchmode_title_mappings (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    watchmode_id INTEGER NOT NULL,
    mapped_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (title_id),
    UNIQUE (watchmode_id)
);

CREATE TABLE IF NOT EXISTS streaming_availability (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    watchmode_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'US',
    available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    web_url TEXT,
    price TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE (title_id, provider, provider_type, region, web_url)
);

CREATE TABLE IF NOT EXISTS watchmode_availability_cache (
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    region TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (title_id, region)
);

CREATE INDEX IF NOT EXISTS idx_titles_media_type ON titles(media_type);
CREATE INDEX IF NOT EXISTS idx_titles_release_date ON titles(release_date);
CREATE INDEX IF NOT EXISTS idx_watch_history_title_id ON watch_history(title_id);
CREATE INDEX IF NOT EXISTS idx_watch_history_watched_at ON watch_history(watched_at);
CREATE INDEX IF NOT EXISTS idx_watch_later_added_at ON watch_later(added_at);
CREATE INDEX IF NOT EXISTS idx_streaming_availability_title_region ON streaming_availability(title_id, region, fetched_at);
"""


def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with row objects and foreign keys enabled."""
    path = Path(database_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: str | Path) -> None:
    """Create the database and any missing schema objects."""
    with connect(database_path) as connection:
        if str(database_path) != ":memory:":
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        # Upgrade databases created by the first foundation chunk in place.
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(titles)")}
        for column, declaration in (
            ("vote_count", "INTEGER"),
            ("poster_path", "TEXT"),
            ("backdrop_path", "TEXT"),
            ("original_language", "TEXT"),
        ):
            if column not in existing:
                connection.execute(f"ALTER TABLE titles ADD COLUMN {column} {declaration}")
        # Existing chunk-1 databases constrained user ratings to 1-5. Rebuild this
        # local history table to extend its constraint while retaining every row.
        history_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='watch_history'"
        ).fetchone()["sql"] or ""
        if "user_rating <= 5" in history_sql:
            connection.execute("ALTER TABLE watch_history RENAME TO watch_history_legacy")
            connection.execute("""CREATE TABLE watch_history (
                id INTEGER PRIMARY KEY,
                title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
                watched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                user_rating INTEGER CHECK (user_rating IS NULL OR (user_rating >= 1 AND user_rating <= 10)),
                liked INTEGER CHECK (liked IS NULL OR liked IN (0, 1)),
                notes TEXT
            )""")
            connection.execute("""INSERT INTO watch_history (id, title_id, watched_at, user_rating, liked, notes)
                SELECT id, title_id, watched_at, user_rating, liked, notes FROM watch_history_legacy""")
            connection.execute("DROP TABLE watch_history_legacy")
        preferences_columns = {row["name"] for row in connection.execute("PRAGMA table_info(user_preferences)")}
        for column, declaration in (
            ("preferred_genres_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("preferred_streaming_services_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("excluded_streaming_services_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("preferred_languages_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("movies_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("tv_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("new_releases_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("trending_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("hidden_gems_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if column not in preferences_columns:
                connection.execute(f"ALTER TABLE user_preferences ADD COLUMN {column} {declaration}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_title_id ON watch_history(title_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_watched_at ON watch_history(watched_at)")
