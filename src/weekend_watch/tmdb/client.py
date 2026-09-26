"""Isolated TMDB v3 HTTP client with typed normalized results."""

import logging
import time
from typing import Any, Literal

import httpx

from ..security import protect_httpx_logs
from .models import Genre, ReleaseInfo, SearchResults, Title

protect_httpx_logs()
logger = logging.getLogger(__name__)

MediaType = Literal["movie", "tv"]


class TmdbError(RuntimeError):
    """TMDB request or response error."""


class TmdbClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, *, api_key: str | None = None, access_token: str | None = None,
                 http_client: httpx.Client | None = None, max_retries: int = 2,
                 retry_delay: float = 0.2):
        if not api_key and not access_token:
            raise ValueError("Set TMDB_API_KEY or TMDB_ACCESS_TOKEN to use TMDB")
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self.retry_delay = max(0, retry_delay)
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._client = http_client or httpx.Client(base_url=self.BASE_URL, headers=headers, timeout=15.0)
        if http_client is not None:
            if access_token:
                self._client.headers["Authorization"] = f"Bearer {access_token}"
            self._client.headers.setdefault("Accept", "application/json")
        self._owns_client = http_client is None
        self._genre_cache: dict[MediaType, dict[int, str]] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TmdbClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = dict(params or {})
        if self.api_key:
            request_params.setdefault("api_key", self.api_key)
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(f"{self.BASE_URL}{path}", params=request_params)
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(response, attempt))
                        continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TmdbError("TMDB returned an unexpected response shape")
                return payload
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    # httpx exceptions can include the request URL, which may contain
                    # the TMDB API key when query-string authentication is used.
                    raise TmdbError("TMDB request failed after retries") from None
                time.sleep(self.retry_delay * (2 ** attempt))
            except httpx.HTTPStatusError as exc:
                raise TmdbError(f"TMDB returned HTTP {exc.response.status_code}") from None
            except ValueError:
                raise TmdbError("TMDB returned invalid JSON") from None
        raise TmdbError("TMDB request failed")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), 5.0)
        return min(self.retry_delay * (2 ** attempt), 5.0)

    def get_genres(self, media_type: MediaType) -> list[Genre]:
        if self._genre_cache.get(media_type):
            return [Genre(id=identifier, name=name)
                    for identifier, name in self._genre_cache[media_type].items()]
        payload = self._get(f"/genre/{media_type}/list", {"language": "en-US"})
        values = payload.get("genres", [])
        if not isinstance(values, list):
            raise TmdbError("TMDB returned an unexpected genre response")
        genres = []
        for item in values:
            try:
                genres.append(Genre.model_validate(item))
            except (TypeError, ValueError):
                logger.warning("Skipping malformed TMDB genre entry for %s", media_type)
        self._genre_cache[media_type] = {genre.id: genre.name for genre in genres}
        return genres

    def _genre_names(self, media_type: MediaType, results: list[dict[str, Any]]) -> dict[int, str]:
        if media_type not in self._genre_cache and any(
            isinstance(item, dict) and item.get("genre_ids") for item in results
        ):
            try:
                self.get_genres(media_type)
            except TmdbError:
                # Search/trending results remain useful without genre labels.
                logger.warning("TMDB genre lookup failed for %s; retaining titles without genre names", media_type)
                self._genre_cache[media_type] = {}
        return self._genre_cache.get(media_type, {})

    def _results(self, media_type: MediaType, payload: dict[str, Any]) -> SearchResults:
        items = payload.get("results", [])
        if not isinstance(items, list):
            raise TmdbError("TMDB returned an unexpected results shape")
        names = self._genre_names(media_type, items)
        titles = []
        for item in items:
            if not isinstance(item, dict):
                logger.warning("Skipping malformed TMDB %s result with non-object data", media_type)
                continue
            try:
                titles.append(Title.from_tmdb(item, media_type, names))
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed TMDB %s result (id=%r)", media_type, item.get("id"))
        return SearchResults(
            page=payload.get("page", 1),
            total_pages=payload.get("total_pages", 0),
            total_results=payload.get("total_results", 0),
            results=titles,
        )

    @staticmethod
    def _detail_title(payload: dict[str, Any], media_type: MediaType) -> Title:
        try:
            return Title.from_tmdb(payload, media_type)
        except (KeyError, TypeError, ValueError) as exc:
            raise TmdbError("TMDB returned incomplete title details") from exc

    def search_movies(self, query: str, *, page: int = 1, **filters: Any) -> SearchResults:
        payload = self._get("/search/movie", {"query": query, "page": page, **filters})
        return self._results("movie", payload)

    def search_tv(self, query: str, *, page: int = 1, **filters: Any) -> SearchResults:
        payload = self._get("/search/tv", {"query": query, "page": page, **filters})
        return self._results("tv", payload)

    def get_movie_details(self, tmdb_id: int, *, append_to_response: str | None = None) -> Title:
        params = {"append_to_response": append_to_response} if append_to_response else None
        return self._detail_title(self._get(f"/movie/{tmdb_id}", params), "movie")

    def get_tv_details(self, tmdb_id: int, *, append_to_response: str | None = None) -> Title:
        params = {"append_to_response": append_to_response} if append_to_response else None
        return self._detail_title(self._get(f"/tv/{tmdb_id}", params), "tv")

    def get_trending_movies(self, *, time_window: Literal["day", "week"] = "week", page: int = 1) -> SearchResults:
        return self._results("movie", self._get(f"/trending/movie/{time_window}", {"page": page}))

    def get_trending_tv(self, *, time_window: Literal["day", "week"] = "week", page: int = 1) -> SearchResults:
        return self._results("tv", self._get(f"/trending/tv/{time_window}", {"page": page}))

    def get_now_playing_movies(self, *, region: str = "US", page: int = 1) -> SearchResults:
        """Return TMDB's current theatrical slate for a country/region."""
        return self._results("movie", self._get("/movie/now_playing", {"region": region, "page": page}))

    def search_keywords(self, query: str, *, page: int = 1) -> list[dict[str, Any]]:
        """Resolve a user concept to TMDB keyword IDs for Discover filters."""
        payload = self._get("/search/keyword", {"query": query, "page": page})
        values = payload.get("results", [])
        if not isinstance(values, list):
            raise TmdbError("TMDB returned an unexpected keyword response")
        return [item for item in values if isinstance(item, dict)
                and isinstance(item.get("id"), int) and isinstance(item.get("name"), str)]

    def discover_movies(self, *, page: int = 1, **filters: Any) -> SearchResults:
        return self._results("movie", self._get("/discover/movie", {"page": page, **filters}))

    def discover_tv(self, *, page: int = 1, **filters: Any) -> SearchResults:
        return self._results("tv", self._get("/discover/tv", {"page": page, **filters}))

    def get_movie_releases(self, tmdb_id: int) -> list[ReleaseInfo]:
        payload = self._get(f"/movie/{tmdb_id}/release_dates")
        return [ReleaseInfo(country_code=result["iso_3166_1"],
                            release_date=release.get("release_date"),
                            release_type=release.get("type"),
                            certification=release.get("certification"),
                            note=release.get("note"))
                for result in payload.get("results", [])
                for release in result.get("release_dates", [])]

    def get_tv_content_ratings(self, tmdb_id: int) -> list[ReleaseInfo]:
        payload = self._get(f"/tv/{tmdb_id}/content_ratings")
        return [ReleaseInfo(country_code=item["iso_3166_1"], certification=item.get("rating"),
                            note=item.get("descriptors", [None])[0] if item.get("descriptors") else None)
                for item in payload.get("results", [])]
