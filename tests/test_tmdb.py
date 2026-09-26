import httpx
import pytest

from weekend_watch.config import Settings
from weekend_watch.database import connect, initialize_database
from weekend_watch.repositories import TitleRepository
from weekend_watch.tmdb import TmdbClient, TmdbError
from weekend_watch.tmdb.service import TmdbService


def client_with(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return TmdbClient(http_client=http, retry_delay=0, **kwargs), http


def test_settings_read_unprefixed_tmdb_environment(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    monkeypatch.setenv("TMDB_ACCESS_TOKEN", "test-token")
    settings = Settings()
    assert settings.tmdb_api_key == "test-key"
    assert settings.tmdb_access_token == "test-token"


def test_search_trending_and_details_are_normalized():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/genre/movie/list"):
            return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drama"}]})
        if request.url.path.endswith("/search/movie"):
            return httpx.Response(200, json={"page": 1, "total_results": 1, "total_pages": 1,
                "results": [{"id": 42, "title": "Film", "overview": "A story", "release_date": "2025-01-02",
                             "vote_average": 8.1, "vote_count": 100, "popularity": 20.5,
                             "genre_ids": [18], "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
                             "original_language": "en"}]})
        if request.url.path.endswith("/trending/tv/week"):
            return httpx.Response(200, json={"results": [{"id": 7, "name": "Show", "first_air_date": "2024-01-01"}]})
        if request.url.path.endswith("/trending/movie/week"):
            return httpx.Response(200, json={"results": []})
        if request.url.path.endswith("/movie/42"):
            return httpx.Response(200, json={"id": 42, "title": "Film", "genres": [{"id": 18, "name": "Drama"}]})
        raise AssertionError(request.url)

    client, http = client_with(handler, api_key="not-a-real-secret")
    try:
        found = client.search_movies("Film").results[0]
        assert found.title == "Film"
        assert found.genres == ["Drama"]
        assert found.tmdb_id == 42 and found.media_type == "movie"
        assert found.to_repository_fields()["provider"] == "tmdb"
        assert client.get_movie_details(42).genres == ["Drama"]
        trending = client.get_trending_tv().results[0]
        assert trending.media_type == "tv"
        assert len(seen) == 4
        assert seen[0].url.params["api_key"] == "not-a-real-secret"
    finally:
        client.close()
        http.close()


def test_tv_search_discover_and_release_endpoints():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/release_dates"):
            return httpx.Response(200, json={"results": [{"iso_3166_1": "US", "release_dates": [
                {"release_date": "2025-03-01T00:00:00Z", "type": 4, "certification": "PG-13", "note": "wide"}]}]})
        if request.url.path.endswith("/content_ratings"):
            return httpx.Response(200, json={"results": [{"iso_3166_1": "US", "rating": "TV-14"}]})
        if request.url.path.endswith("/discover/movie"):
            return httpx.Response(200, json={"results": [{"id": 6, "title": "Film"}]})
        return httpx.Response(200, json={"results": [{"id": 5, "name": "Series", "first_air_date": "2023-01-01"}]})

    client, http = client_with(handler, access_token="mock-token")
    try:
        assert client.search_tv("Series").results[0].title == "Series"
        assert client.discover_tv(with_genres="18").results[0].media_type == "tv"
        assert client.discover_movies(sort_by="popularity.desc").results[0].media_type == "movie"
        assert client.get_movie_releases(5)[0].certification == "PG-13"
        assert client.get_tv_content_ratings(5)[0].certification == "TV-14"
        assert paths == ["/3/search/tv", "/3/discover/tv", "/3/discover/movie", "/3/movie/5/release_dates", "/3/tv/5/content_ratings"]
    finally:
        client.close()
        http.close()


def test_now_playing_and_keyword_search_are_normalized_and_keep_tmdb_params():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/search/keyword"):
            return httpx.Response(200, json={"results": [{"id": 1234, "name": "horror"}]})
        return httpx.Response(200, json={"results": [{"id": 8, "title": "A Film",
            "genre_ids": [27], "original_language": "hi"}]})

    client, http = client_with(handler, api_key="mock")
    try:
        film = client.get_now_playing_movies(region="IN").results[0]
        keywords = client.search_keywords("horror")
        assert film.title == "A Film" and film.original_language == "hi"
        assert keywords == [{"id": 1234, "name": "horror"}]
        assert seen[0].url.path == "/3/movie/now_playing"
        assert seen[0].url.params["region"] == "IN"
        keyword_request = next(request for request in seen
                              if request.url.path == "/3/search/keyword")
        assert keyword_request.url.params["query"] == "horror"
    finally:
        client.close()
        http.close()


def test_person_search_returns_normalized_people():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [
            {"id": 500, "name": "Tom Cruise", "popularity": 40.1},
            {"invalid": True},
        ]})

    client, http = client_with(handler, api_key="mock")
    try:
        people = client.search_people("Tom Cruise")
        assert [(person.id, person.name) for person in people] == [(500, "Tom Cruise")]
        assert requests[0].url.path == "/3/search/person"
        assert requests[0].url.params["query"] == "Tom Cruise"
    finally:
        client.close()
        http.close()


def test_retry_transient_server_failure_and_surface_permanent_error():
    calls = 0

    def flaky(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={"results": []})

    client, http = client_with(flaky, api_key="fake", max_retries=1)
    try:
        assert client.get_trending_movies().results == []
        assert calls == 2
    finally:
        client.close()
        http.close()


def test_transport_error_does_not_include_tmdb_api_key():
    secret = "tmdb-secret-must-not-leak"

    def fail(request):
        raise httpx.ConnectError("connection failed", request=request)

    client, http = client_with(fail, api_key=secret, max_retries=0)
    try:
        with pytest.raises(TmdbError) as error:
            client.get_trending_movies()
        assert secret not in str(error.value)
    finally:
        client.close()
        http.close()


def test_retry_after_is_bounded(monkeypatch):
    delays = []
    monkeypatch.setattr("weekend_watch.tmdb.client.time.sleep", delays.append)
    calls = 0
    def rate_limited(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"}, json={})
        return httpx.Response(200, json={"results": []})
    client, http = client_with(rate_limited, api_key="fake", max_retries=1)
    try:
        client.get_trending_movies()
        assert delays == [5.0]
    finally:
        client.close()
        http.close()


def test_malformed_tmdb_records_are_skipped_and_title_requires_name():
    client, http = client_with(
        lambda request: httpx.Response(200, json={"results": [None, {"id": 2},
            {"id": 3, "title": "Valid"}]}), api_key="mock", max_retries=0
    )
    try:
        assert [title.tmdb_id for title in client.search_movies("x").results] == [3]
    finally:
        client.close()
        http.close()

    failed, http = client_with(lambda request: httpx.Response(401, json={}), api_key="fake", max_retries=0)
    try:
        with pytest.raises(TmdbError, match="HTTP 401"):
            failed.get_trending_movies()
    finally:
        failed.close()
        http.close()


def test_normalized_title_persists_in_existing_database(tmp_path):
    path = tmp_path / "watch.db"
    initialize_database(path)
    title = client_payload_title()
    with connect(path) as db:
        repository = TitleRepository(db)
        local_id = TmdbService(None, repository).save(title)
        row = repository.get(local_id)
        assert row["external_id"] == "91"
        assert row["vote_count"] == 12
        assert row["poster_path"] == "/poster.jpg"
        assert row["original_language"] == "fr"


def client_payload_title():
    from weekend_watch.tmdb.models import Title

    return Title(tmdb_id=91, title="Localized", media_type="movie", vote_count=12,
                 poster_path="/poster.jpg", original_language="fr")
