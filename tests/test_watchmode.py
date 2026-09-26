import httpx
import pytest

from weekend_watch.config import Settings
from weekend_watch.database import connect, initialize_database
from weekend_watch.repositories import TitleRepository, WatchmodeRepository
from weekend_watch.watchmode import WatchmodeClient, WatchmodeService


def make_client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return WatchmodeClient(api_key="mock-api-key", http_client=http, retry_delay=0), http


def test_watchmode_settings_use_configured_key_region_and_cache(monkeypatch):
    monkeypatch.setenv("WATCHMODE_API_KEY", "test-key")
    monkeypatch.setenv("WATCHMODE_REGION", "CA")
    monkeypatch.setenv("WATCHMODE_CACHE_TTL_HOURS", "12")
    settings = Settings()
    assert settings.watchmode_api_key == "test-key"
    assert settings.watchmode_region == "CA"
    assert settings.watchmode_cache_ttl_hours == 12


def test_search_by_tmdb_id_maps_to_distinct_watchmode_id_and_sources():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, json=[{
                "id": 78001, "title": "Film", "type": "movie", "tmdb_id": 123, "year": 2024
            }])
        return httpx.Response(200, json=[
            {"source_id": 203, "name": "Example Plus", "type": "sub", "web_url": "https://example.test/watch/film"},
            {"source_id": 7, "name": "Rent Store", "type": "rent", "price": "$3.99"},
            {"source_id": 8, "name": "Buy Store", "type": "buy", "price": 12.99},
        ])

    client, http = make_client(handler)
    try:
        match = client.search_by_tmdb_id(123, "movie")[0]
        assert match.tmdb_id == 123
        assert match.watchmode_id == 78001
        assert match.watchmode_id != match.tmdb_id
        options = client.get_availability(match.watchmode_id, title_id=4, region="US")
        assert [item.provider_type for item in options] == ["subscription", "rent", "buy"]
        assert options[0].web_url == "https://example.test/watch/film"
        assert options[1].price == "$3.99"
        assert requests[0].url.params["apiKey"] == "mock-api-key"
        assert requests[0].url.params["search_field"] == "tmdb_id"
        assert requests[0].url.params["search_value"] == "123"
        assert requests[1].url.params["regions"] == "US"
    finally:
        http.close()


def test_name_search_and_service_persistence_mapping_and_cache(tmp_path):
    request_paths = []

    def handler(request):
        request_paths.append(request.url.path)
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, json={"title_results": [
                {"id": 601, "title": "A Series", "type": "tv_series", "tmdb_id": 87}
            ]})
        return httpx.Response(200, json=[
            {"source_id": 1, "name": "Stream Co", "type": "sub", "web_url": "https://stream.test/a"}
        ])

    path = tmp_path / "watch.db"
    initialize_database(path)
    client, http = make_client(handler)
    try:
        assert client.search_titles("A Series", media_type="tv")[0].media_type == "tv"
        with connect(path) as db:
            titles = TitleRepository(db)
            repository = WatchmodeRepository(db)
            service = WatchmodeService(client, titles, repository, region="US", cache_ttl_hours=24)
            first = service.availability_for_tmdb(87, "tv")
            second = service.availability_for_tmdb(87, "tv")
            saved_title = titles.get_by_provider_external("tmdb", 87)
            assert saved_title["name"] == "A Series"
            assert repository.get_mapping(saved_title["id"]) == 601
            assert first[0].provider == "Stream Co"
            assert second[0].web_url == first[0].web_url
            assert request_paths.count("/v1/search/") == 2  # one name lookup and one ID map
            assert request_paths.count("/v1/title/601/sources/") == 1  # second lookup used cache
            assert second[0].fetched_at.tzinfo is not None
    finally:
        http.close()


def test_empty_availability_is_cached(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, json=[{"id": 910, "title": "Unavailable", "type": "movie", "tmdb_id": 10}])
        return httpx.Response(200, json=[])

    path = tmp_path / "watch.db"
    initialize_database(path)
    client, http = make_client(handler)
    try:
        with connect(path) as db:
            service = WatchmodeService(client, TitleRepository(db), WatchmodeRepository(db))
            assert service.availability_for_tmdb(10) == []
            assert service.availability_for_tmdb(10) == []
        assert calls == 2  # search + one sources call; empty result still has a cache timestamp
    finally:
        http.close()


def test_watchmode_transport_error_does_not_include_query_api_key():
    secret = "watchmode-secret-must-not-leak"
    def fail(request):
        raise httpx.ConnectError("connection failed", request=request)
    http = httpx.Client(transport=httpx.MockTransport(fail))
    client = WatchmodeClient(api_key=secret, http_client=http, max_retries=0)
    try:
        with pytest.raises(Exception) as error:
            client.search_titles("Film")
        assert secret not in str(error.value)
    finally:
        http.close()
