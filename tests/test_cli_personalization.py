from types import SimpleNamespace

from weekend_watch import cli
from weekend_watch.database import connect
from weekend_watch.personalization import PersonalizationService
from weekend_watch.tmdb.models import SearchResults, Title


class FakeTmdbClient:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def search_movies(self, query):
        return SearchResults(results=[Title(tmdb_id=991, title="Dune", media_type="movie", popularity=10)])

    def search_tv(self, query):
        return SearchResults(results=[])


def test_watched_cli_resolves_title_through_tmdb_and_records_rating(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "cli.db"
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        database_path=database_path, tmdb_api_key="fake-key", tmdb_access_token=None
    ))
    monkeypatch.setattr(cli, "TmdbClient", FakeTmdbClient)
    monkeypatch.setattr("sys.argv", ["weekend-watch", "watched", "--title", "Dune", "--rating", "9"])

    cli.main()

    assert "Recorded watched: Dune [movie] with rating 9/10" in capsys.readouterr().out
    with connect(database_path) as db:
        service = PersonalizationService(db)
        assert service.has_watched("tmdb", 991)
        assert service.get_watch_history()[0]["user_rating"] == 9


def test_history_and_preferences_cli_commands(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "cli.db"
    from weekend_watch.database import initialize_database
    from weekend_watch.repositories import TitleRepository

    initialize_database(database_path)
    with connect(database_path) as db:
        title_id = TitleRepository(db).upsert(
            provider="tmdb", external_id="77", media_type="tv", name="Example Show"
        )
        PersonalizationService(db).mark_watched(title_id)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(database_path=database_path))

    monkeypatch.setattr("sys.argv", ["weekend-watch", "history"])
    cli.main()
    assert "Example Show [tv]" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["weekend-watch", "preferences"])
    cli.main()
    output = capsys.readouterr().out
    assert '"preferred_genres": []' in output
    assert '"hidden_gems_enabled": true' in output
