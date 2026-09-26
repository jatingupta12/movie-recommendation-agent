import asyncio
from datetime import date

from mcp import Client
import pytest

from weekend_watch.config import Settings
from weekend_watch.mcp_tools import WeekendWatchTools
from weekend_watch.mcp_server import create_server
from weekend_watch.tmdb.models import SearchResults, Title


class FakeTmdb:
    def __init__(self, **_kwargs):
        self.title = Title(
            tmdb_id=515, title="Weekend Story", media_type="movie", rating=8.1,
            release_date=date.today().isoformat(), vote_count=800, popularity=20,
            genres=["Drama"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def search_movies(self, _query):
        return SearchResults(results=[self.title], total_results=1)

    def search_tv(self, _query):
        return SearchResults()

    def get_movie_details(self, _tmdb_id):
        return self.title

    def get_tv_details(self, _tmdb_id):
        return self.title.model_copy(update={"media_type": "tv"})


def create_tools(tmp_path):
    settings = Settings(
        database_path=tmp_path / "mcp.sqlite3",
        tmdb_api_key="mock-tmdb-key",
        watchmode_api_key=None,
    )
    return WeekendWatchTools(settings, tmdb_client_factory=FakeTmdb)


def test_mcp_tools_search_mark_watched_and_check_identity(tmp_path):
    tools = create_tools(tmp_path)

    results = tools.search_movies("Weekend Story")
    assert results["results"][0]["tmdb_id"] == 515

    recorded = tools.mark_watched("Weekend Story", rating=9, liked=True, notes="Loved it")
    assert recorded["action"] == "watched"
    state = tools.has_watched("515", media_type="movie")
    assert state == {"tmdb_id": 515, "title": "Weekend Story", "media_type": "movie", "watched": True}

    history = tools.get_watch_history()
    assert history["count"] == 1
    assert history["items"][0]["user_rating"] == 9
    assert history["items"][0]["liked"] == 1


def test_mcp_tools_watch_later_preferences_and_limits(tmp_path):
    tools = create_tools(tmp_path)
    result = tools.add_to_watch_later("Weekend Story")
    assert result["action"] == "watch_later"
    assert tools.get_watch_later(limit=1)["count"] == 1
    assert tools.get_user_preferences()["movies_enabled"] is True
    assert tools.get_watch_history(limit=0)["items"] == []


def test_mcp_tool_errors_do_not_expose_provider_secrets(tmp_path):
    class FailingTmdb(FakeTmdb):
        def __init__(self, **kwargs):
            raise ValueError("Set TMDB_API_KEY or TMDB_ACCESS_TOKEN to use TMDB")

    settings = Settings(database_path=tmp_path / "no-config.sqlite3", tmdb_api_key=None)
    tools = WeekendWatchTools(settings, tmdb_client_factory=FailingTmdb)
    with pytest.raises(RuntimeError, match="credentials are not configured"):
        tools.search_movies("Dune")

    with pytest.raises(RuntimeError, match="Watchmode is not configured"):
        tools.get_streaming_availability("515")


def test_official_mcp_server_registers_tools_and_returns_structured_output(tmp_path):
    server = create_server(create_tools(tmp_path))

    async def exercise_client():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {"search_movies", "mark_watched", "get_weekend_digest"} <= {
                tool.name for tool in tools.tools
            }
            result = await client.call_tool("get_user_preferences", {})
            assert result.is_error is False
            assert result.structured_content["movies_enabled"] is True

    asyncio.run(exercise_client())
