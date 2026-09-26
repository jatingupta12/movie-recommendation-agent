"""Application-facing TMDB operations and persistence mapping."""

from .client import TmdbClient
from .models import SearchResults, Title
from ..repositories import TitleRepository


class TmdbService:
    def __init__(self, client: TmdbClient, titles: TitleRepository | None = None):
        self.client = client
        self.titles = titles

    def trending(self, *, time_window: str = "week") -> SearchResults:
        """Fetch both trending media types and return one normalized collection."""
        movies = self.client.get_trending_movies(time_window=time_window)
        television = self.client.get_trending_tv(time_window=time_window)
        return SearchResults(
            page=1,
            total_pages=max(movies.total_pages, television.total_pages),
            total_results=movies.total_results + television.total_results,
            results=movies.results + television.results,
        )

    def save(self, title: Title) -> int:
        if self.titles is None:
            raise RuntimeError("A title repository is required to save TMDB titles")
        return self.titles.upsert(**title.to_repository_fields())
