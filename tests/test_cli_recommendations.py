from datetime import date
from types import SimpleNamespace

from weekend_watch import cli
from weekend_watch.tmdb.models import SearchResults, Title


class FakeRecommendationTmdb:
    def __init__(self, **kwargs):
        self.today = date.today().isoformat()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get_trending_movies(self):
        return SearchResults(results=[self._title()])

    def get_trending_tv(self):
        return SearchResults()

    def discover_movies(self, **filters):
        if "primary_release_date.gte" in filters:
            return SearchResults(results=[self._title()])
        if "vote_count.lte" not in filters:
            return SearchResults(results=[self._title()])
        return SearchResults()

    def discover_tv(self, **filters):
        return SearchResults()

    def _title(self):
        return Title(tmdb_id=345, title="Weekend Pick", media_type="movie", rating=8.6,
                     vote_count=500, popularity=18, genres=["Drama"], release_date=self.today)


def test_weekend_candidates_cli_uses_pipeline_and_prints_match_reasons(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        database_path=tmp_path / "weekend.db", tmdb_api_key="mock-key", tmdb_access_token=None,
        watchmode_api_key=None, watchmode_region="US", watchmode_cache_ttl_hours=24,
        groq_api_key=None, groq_model="mock-groq", anthropic_api_key=None,
        anthropic_model="mock-claude",
    ))
    monkeypatch.setattr(cli, "TmdbClient", FakeRecommendationTmdb)
    monkeypatch.setattr("sys.argv", ["weekend-watch", "weekend-candidates", "--limit", "5"])

    cli.main()

    output = capsys.readouterr().out
    assert "Weekend Pick [movie] Match" in output
    assert "TRENDING" in output
    assert "HIGHLY_RATED" in output


def test_recommend_cli_runs_without_ai_credentials_and_uses_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        database_path=tmp_path / "recommend.db", tmdb_api_key="mock-key", tmdb_access_token=None,
        watchmode_api_key=None, watchmode_region="US", watchmode_cache_ttl_hours=24,
        groq_api_key=None, groq_model="mock-groq", anthropic_api_key=None,
        anthropic_model="mock-claude",
    ))
    monkeypatch.setattr(cli, "TmdbClient", FakeRecommendationTmdb)
    monkeypatch.setattr("sys.argv", ["weekend-watch", "recommend", "--limit", "3"])

    cli.main()

    output = capsys.readouterr().out
    assert "Weekend Pick (" in output
    assert "Deterministic fallback" in output


def test_digest_cli_formats_and_optionally_saves_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        database_path=tmp_path / "digest.db", tmdb_api_key="mock-key", tmdb_access_token=None,
        watchmode_api_key=None, watchmode_region="US", watchmode_cache_ttl_hours=24,
    ))
    monkeypatch.setattr(cli, "TmdbClient", FakeRecommendationTmdb)
    digest_dir = tmp_path / "saved-digests"
    monkeypatch.setattr("sys.argv", ["weekend-watch", "digest", "--limit", "5",
                                    "--save", "--digest-dir", str(digest_dir)])

    cli.main()

    output = capsys.readouterr().out
    saved = list(digest_dir.glob("weekend-watch-*.md"))
    assert len(saved) == 1
    assert "# 🍿 Weekend Watch" in output
    assert "## 🆕 New This Week" in output
    assert "Weekend Pick" in output
    assert "Already watched titles are excluded." in saved[0].read_text(encoding="utf-8")
