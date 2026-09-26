import httpx
import pytest

from weekend_watch.config import Settings
from weekend_watch.database import connect, initialize_database
from weekend_watch.repositories import TitleRepository, WatchmodeRepository
from weekend_watch.watchmode import WatchmodeClient, WatchmodeError, WatchmodeService
from weekend_watch.watchmode.models import StreamingAvailability


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
        assert requests[0].headers["x-api-key"] == "mock-api-key"
        assert "apiKey" not in requests[0].url.params
        assert requests[0].url.params["search_field"] == "tmdb_movie_id"
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
        assert calls == 3  # movie/TV ID lookups and sources call
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


def test_tv_tmdb_mapping_uses_tv_specific_search_field():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"id": 781, "title": "Series", "type": "tv_series", "tmdb_id": 1396}])
    client, http = make_client(handler)
    try:
        assert client.search_by_tmdb_id(1396, "tv")[0].watchmode_id == 781
        assert seen[0].url.params["search_field"] == "tmdb_tv_id"
    finally:
        http.close()


def test_untyped_tmdb_mapping_queries_movie_and_tv_fields():
    fields = []
    def handler(request):
        fields.append(request.url.params["search_field"])
        return httpx.Response(200, json=[])
    client, http = make_client(handler)
    try:
        assert client.search_by_tmdb_id(25) == []
        assert fields == ["tmdb_movie_id", "tmdb_tv_id"]
    finally:
        http.close()


def test_watchmode_error_detail_is_useful_and_redacted():
    secret = "private-test-key"
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        400, json={"error": f"Invalid search field; {secret}"},
    )))
    client = WatchmodeClient(api_key=secret, http_client=http, max_retries=0)
    try:
        with pytest.raises(WatchmodeError) as error:
            client.search_titles("A title")
        assert "Invalid search field" in str(error.value)
        assert secret not in str(error.value)
    finally:
        http.close()


def test_repeated_watchmode_sources_are_collapsed_before_storage(tmp_path):
    source = {"source_id": 2, "name": "Stream Co", "type": "sub", "web_url": "https://stream.test/x"}
    def handler(request):
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, json=[{"id": 780, "title": "Film", "type": "movie", "tmdb_id": 250}])
        return httpx.Response(200, json=[source, source.copy()])
    db_path = tmp_path / "duplicate-source.db"
    initialize_database(db_path)
    client, http = make_client(handler)
    try:
        with connect(db_path) as db:
            service = WatchmodeService(client, TitleRepository(db), WatchmodeRepository(db))
            options = service.availability_for_tmdb(250, "movie")
            rows = list(db.execute("SELECT * FROM streaming_availability"))
        assert len(options) == len(rows) == 1
    finally:
        http.close()


def test_repository_deduplicates_duplicate_rows_with_null_urls(tmp_path):
    db_path = tmp_path / "duplicate-null-url.db"
    initialize_database(db_path)
    with connect(db_path) as db:
        title_id = TitleRepository(db).upsert(
            provider="tmdb", external_id="1", media_type="movie", name="Example",
        )
        items = [StreamingAvailability(
            title_id=title_id, provider="Stream Co", provider_type="subscription", web_url=None,
            price=price,
        ) for price in ("$5.99", "$6.99")]
        repo = WatchmodeRepository(db)
        repo.save_availability(title_id, 2, "US", items)
        repo.save_availability(title_id, 2, "US", items)
        rows = list(db.execute("SELECT * FROM streaming_availability"))
    assert len(rows) == 1
    assert rows[0]["price"] == "$6.99"


def test_explicit_media_type_repairs_stale_tmdb_mapping(tmp_path):
    fields = []
    def handler(request):
        if request.url.path.endswith("/search/"):
            fields.append(request.url.params["search_field"])
            return httpx.Response(200, json=[{"id": 973, "title": "Series 123", "type": "tv_series", "tmdb_id": 123}])
        return httpx.Response(200, json=[])
    db_path = tmp_path / "stale-type.db"
    initialize_database(db_path)
    client, http = make_client(handler)
    try:
        with connect(db_path) as db:
            titles = TitleRepository(db)
            watchmode = WatchmodeRepository(db)
            title_id = titles.upsert(provider="tmdb", external_id="123", media_type="movie", name="Wrong")
            watchmode.save_mapping(title_id, 972)
            service = WatchmodeService(client, titles, watchmode)
            service.availability_for_tmdb(123, "tv")
            assert titles.get(title_id)["media_type"] == "tv"
            assert titles.get(title_id)["name"] == "Series 123"
            assert watchmode.get_mapping(title_id) == 973
            assert fields == ["tmdb_tv_id"]
    finally:
        http.close()


def test_tv_movie_result_normalizes_as_movie():
    result = WatchmodeClient._normalize_titles([{"id": 1685639, "title": "TV Movie", "type": "tv_movie"}])
    assert result[0].media_type == "movie"
