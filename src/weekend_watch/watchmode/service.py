"""Application-facing Watchmode lookup, ID mapping, caching, and persistence."""

from typing import Literal

from .client import WatchmodeClient
from .models import StreamingAvailability
from ..repositories import TitleRepository, WatchmodeRepository


class WatchmodeTitleNotFound(LookupError):
    """No Watchmode title could be mapped from the supplied TMDB ID."""


class WatchmodeService:
    def __init__(self, client: WatchmodeClient, titles: TitleRepository,
                 watchmode: WatchmodeRepository, *, region: str = "US",
                 cache_ttl_hours: int = 24):
        self.client = client
        self.titles = titles
        self.watchmode = watchmode
        self.region = region.upper()
        self.cache_ttl_hours = cache_ttl_hours

    def availability_for_tmdb(self, tmdb_id: int,
                               media_type: Literal["movie", "tv"] | None = None
                               ) -> list[StreamingAvailability]:
        title = self.titles.get_by_provider_external("tmdb", tmdb_id)
        type_changed = bool(title and media_type and title["media_type"] != media_type)
        watchmode_id = self.watchmode.get_mapping(title["id"]) if title and not type_changed else None
        if type_changed:
            self.watchmode.clear_availability_cache(int(title["id"]), self.region)

        # Resolve through Watchmode's TMDB search field; these IDs are distinct namespaces.
        if watchmode_id is None:
            expected_type = media_type or (title["media_type"] if title else None)
            matches = self.client.search_by_tmdb_id(tmdb_id, expected_type)
            if not matches:
                raise WatchmodeTitleNotFound(f"No Watchmode match found for TMDB title {tmdb_id}")
            match = matches[0]
            if title is None:
                title_id = self.titles.upsert(
                    provider="tmdb", external_id=str(tmdb_id), media_type=match.media_type,
                    name=match.title,
                )
            else:
                title_id = int(title["id"])
                if type_changed:
                    self.titles.update_identity_details(title_id, media_type=match.media_type,
                                                        name=match.title)
            self.watchmode.save_mapping(title_id, match.watchmode_id)
            watchmode_id = match.watchmode_id
        else:
            title_id = int(title["id"])

        cached = self.watchmode.get_cached_availability(title_id, self.region, self.cache_ttl_hours)
        if cached is not None:
            return [StreamingAvailability.model_validate(dict(row)) for row in cached]

        availability = self.client.get_availability(
            watchmode_id, title_id=title_id, region=self.region
        )
        self.watchmode.save_availability(title_id, watchmode_id, self.region, availability)
        return availability
