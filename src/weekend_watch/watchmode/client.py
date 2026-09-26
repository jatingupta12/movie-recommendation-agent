"""Watchmode v1 HTTP client. API-specific request/response shapes stay here."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from ..security import protect_httpx_logs
from .models import StreamingAvailability, WatchmodeTitle, normalize_provider_type

protect_httpx_logs()
logger = logging.getLogger(__name__)


class WatchmodeError(RuntimeError):
    """Watchmode request or response error."""


class WatchmodeClient:
    BASE_URL = "https://api.watchmode.com/v1"

    def __init__(self, *, api_key: str, http_client: httpx.Client | None = None,
                 max_retries: int = 2, retry_delay: float = 0.2):
        if not api_key:
            raise ValueError("Set WATCHMODE_API_KEY to use Watchmode")
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self.retry_delay = max(0, retry_delay)
        self._client = http_client or httpx.Client(timeout=15.0)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WatchmodeClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        request_params = {"apiKey": self.api_key, **(params or {})}
        url = f"{self.BASE_URL}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(url, params=request_params)
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    if attempt < self.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after), 5.0) if retry_after and retry_after.isdigit() else min(self.retry_delay * (2 ** attempt), 5.0)
                        time.sleep(delay)
                        continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    # Watchmode puts its key in the query string; do not expose
                    # httpx exception text, which may include the full URL.
                    raise WatchmodeError("Watchmode request failed after retries") from None
                time.sleep(self.retry_delay * (2 ** attempt))
            except httpx.HTTPStatusError as exc:
                raise WatchmodeError(f"Watchmode returned HTTP {exc.response.status_code}") from None
            except ValueError as exc:
                raise WatchmodeError("Watchmode returned invalid JSON") from None
        raise WatchmodeError("Watchmode request failed")

    def search_by_tmdb_id(self, tmdb_id: int, media_type: Literal["movie", "tv"] | None = None) -> list[WatchmodeTitle]:
        payload = self._get("/search/", {"search_field": "tmdb_id", "search_value": tmdb_id})
        titles = self._search_records(payload)
        normalized = self._normalize_titles(titles, tmdb_id)
        return [item for item in normalized if item.tmdb_id == tmdb_id
                and (media_type is None or item.media_type == media_type)]

    def search_titles(self, query: str, *, media_type: Literal["movie", "tv"] | None = None) -> list[WatchmodeTitle]:
        params: dict[str, Any] = {"search_field": "name", "search_value": query}
        if media_type:
            params["types"] = "movie" if media_type == "movie" else "tv_series"
        payload = self._get("/search/", params)
        titles = self._search_records(payload)
        return [item for item in self._normalize_titles(titles)
                if media_type is None or item.media_type == media_type]

    @staticmethod
    def _search_records(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            records = payload.get("title_results", payload.get("results", []))
            if isinstance(records, list):
                return records
        raise WatchmodeError("Watchmode returned an unexpected search response")

    @classmethod
    def _normalize_titles(cls, records: list[Any], tmdb_id: int | None = None) -> list[WatchmodeTitle]:
        normalized = []
        for record in records:
            if not isinstance(record, dict):
                logger.warning("Skipping malformed Watchmode title result")
                continue
            try:
                normalized.append(cls._title(record, tmdb_id))
            except (TypeError, ValueError, WatchmodeError):
                logger.warning("Skipping malformed Watchmode title result (id=%r)", record.get("id"))
        return normalized

    @staticmethod
    def _title(data: dict[str, Any], tmdb_id: int | None = None) -> WatchmodeTitle:
        raw_type = str(data.get("type", data.get("title_type", ""))).lower()
        if raw_type in {"movie", "film", "1"}:
            media_type = "movie"
        elif raw_type in {"tv", "tv_series", "tv_miniseries", "tv_special", "2", "3", "4"}:
            media_type = "tv"
        else:
            raise WatchmodeError("Watchmode returned a title with an unknown media type")
        identifier = data.get("id", data.get("watchmode_id"))
        title = data.get("title") or data.get("name")
        if not identifier or not title:
            raise WatchmodeError("Watchmode returned incomplete title data")
        try:
            return WatchmodeTitle(
                watchmode_id=int(identifier),
                title=str(title),
                media_type=media_type,
                tmdb_id=data.get("tmdb_id") or tmdb_id,
                year=data.get("year"),
            )
        except (TypeError, ValueError) as exc:
            raise WatchmodeError("Watchmode returned invalid title data") from exc

    def get_sources(self, watchmode_id: int, *, region: str = "US") -> list[dict[str, Any]]:
        payload = self._get(f"/title/{watchmode_id}/sources/", {"regions": region.upper()})
        if not isinstance(payload, list):
            raise WatchmodeError("Watchmode returned an unexpected sources response")
        return payload

    def get_availability(self, watchmode_id: int, *, title_id: int, region: str = "US") -> list[StreamingAvailability]:
        sources = self.get_sources(watchmode_id, region=region)
        fetched_at = datetime.now(timezone.utc)
        results = []
        for source in sources:
            if not isinstance(source, dict):
                logger.warning("Skipping malformed Watchmode availability source")
                continue
            provider = source.get("name") or source.get("source_name")
            if not provider:
                provider = f"Provider {source.get('source_id', 'unknown')}"
            try:
                results.append(StreamingAvailability(
                    title_id=title_id,
                    provider=str(provider),
                    provider_type=normalize_provider_type(str(source.get("type") or "")),
                    region=region.upper(),
                    available=True,
                    web_url=source.get("web_url") or source.get("url"),
                    price=None if source.get("price") is None else str(source["price"]),
                    fetched_at=fetched_at,
                ))
            except (TypeError, ValueError):
                logger.warning("Skipping malformed Watchmode source (id=%r)", source.get("source_id"))
        return results
