"""Deterministic, inspectable extraction of recommendation request constraints."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .request_filters import (
    _LANGUAGES, _REQUEST_SERVICES, requested_genres, requested_media_type,
    requested_people,
)
_REGIONS = {
    "us": "US", "usa": "US", "united states": "US", "america": "US",
    "uk": "GB", "united kingdom": "GB", "britain": "GB",
    "canada": "CA", "india": "IN", "australia": "AU", "germany": "DE",
    "france": "FR", "spain": "ES", "mexico": "MX", "japan": "JP",
    "south korea": "KR", "korea": "KR",
}
_REGION_CODES = {"US", "GB", "CA", "IN", "AU", "DE", "FR", "ES", "MX", "JP", "KR"}
# Provider IDs are TMDB's current US catalog IDs; names remain user-facing identifiers.
_TMDB_PROVIDER_IDS = {
    "Netflix": 8, "Prime Video": 119, "Max": 384, "Disney+": 337,
    "Apple TV+": 350, "Hulu": 15,
}
_GENRE_TAXONOMY_ALIASES = {
    "action": {"action", "action & adventure"},
    "sci-fi": {"science fiction", "sci-fi", "sci-fi & fantasy"},
    "science fiction": {"science fiction", "sci-fi", "sci-fi & fantasy"},
    "thriller": {"thriller", "mystery"},
}
_KNOWN_CONCEPTS = (
    "space travel", "time loop", "time loops", "enemies to lovers", "found family",
    "post-apocalyptic", "coming of age", "based on a true story", "mind-bending",
    "multiverse", "dystopian", "survival", "heist",
)


class RecommendationIntent(BaseModel):
    """Parsed request data; contains no private reasoning or generated facts."""

    model_config = ConfigDict(frozen=True)

    media_type: Literal["movie", "tv", "both"] = "both"
    region: str = "US"
    genre_names: list[str] = Field(default_factory=list)
    genre_ids: dict[str, list[int]] = Field(default_factory=dict)
    person_names: list[str] = Field(default_factory=list)
    person_ids: list[int] = Field(default_factory=list)
    language_name: str | None = None
    language_code: str | None = None
    keywords: list[str] = Field(default_factory=list)
    streaming_services: list[str] = Field(default_factory=list)
    provider_ids: list[int] = Field(default_factory=list)
    theatrical: bool = False
    ott: bool = False
    monetization_types: str | None = None

    def tmdb_discover_params(self, media_type: Literal["movie", "tv"]) -> dict[str, str]:
        """TMDB Discover arguments for the selected media, excluding empty filters."""
        params: dict[str, str] = {}
        if self.region and self.ott:
            params.update(watch_region=self.region, with_watch_monetization_types="flatrate")
            if self.provider_ids:
                params["with_watch_providers"] = "|".join(map(str, self.provider_ids))
        ids = self.genre_ids.get(media_type, [])
        if ids:
            params["with_genres"] = ",".join(map(str, ids))
        if self.person_ids:
            params["with_cast"] = ",".join(map(str, self.person_ids))
        if self.language_code:
            params["with_original_language"] = self.language_code
        return params


def extract_request_intent(request: str, *, tmdb=None, region: str = "US") -> RecommendationIntent:
    """Map explicit request terms to normalized filters and TMDB taxonomy IDs."""
    lowered = request.casefold()
    media = requested_media_type(request) or "both"
    target_region = region.upper()
    # Prefer longest region name first so "United States" wins over "US".
    for name in sorted(_REGIONS, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", lowered):
            target_region = _REGIONS[name]
            break
    explicit_code = re.search(r"\b(?:region|country|in)\s+([a-z]{2})\b", lowered)
    if explicit_code and explicit_code.group(1).upper() in _REGION_CODES:
        target_region = explicit_code.group(1).upper()

    language_name = language_code = None
    for name in sorted(_LANGUAGES, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", lowered):
            language_name, language_code = name.title(), _LANGUAGES[name]
            break

    selected_genres = requested_genres(request)
    genre_names = sorted(selected_genres)
    person_names = requested_people(request)
    person_ids: list[int] = []
    if tmdb is not None:
        for name in person_names:
            try:
                matches = tmdb.search_people(name)
            except Exception:
                matches = []
            exact = [person for person in matches if person.name.casefold() == name.casefold()]
            selected_person = max(exact or matches, key=lambda person: person.popularity or 0,
                                  default=None)
            if selected_person is not None:
                person_ids.append(selected_person.id)
    genre_ids: dict[str, list[int]] = {}
    if tmdb is not None and genre_names:
        media_types = [media] if media != "both" else ["movie", "tv"]
        for media_type in media_types:
            try:
                aliases = set(selected_genres)
                for name in selected_genres:
                    aliases.update(_GENRE_TAXONOMY_ALIASES.get(name, set()))
                genre_ids[media_type] = [genre.id for genre in tmdb.get_genres(media_type)
                                         if genre.name.casefold() in aliases]
            except Exception:
                genre_ids[media_type] = []

    services = []
    for service, aliases in _REQUEST_SERVICES.items():
        if any(re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", lowered) for alias in aliases):
            services.append(service)
    provider_ids = [_TMDB_PROVIDER_IDS[name] for name in services if name in _TMDB_PROVIDER_IDS]
    theatrical = bool(re.search(r"\b(?:in theaters?|in cinemas?|playing in theaters?)\b", lowered))
    ott = bool(services or re.search(r"\b(?:stream(?:ing)?|ott|on demand)\b", lowered))
    # Capture explicit abstract-topic phrases. Genre terms are represented separately.
    keywords = []
    for concept in _KNOWN_CONCEPTS:
        if re.search(rf"(?<![\w]){re.escape(concept)}(?![\w])", lowered):
            keywords.append(concept)
    keyword_match = re.search(r"\b(?:about|featuring|with the theme of)\s+([^,.!?]+)", lowered)
    if keyword_match and not keywords:
        phrase = keyword_match.group(1).strip()
        if phrase not in keywords:
            keywords.append(phrase)
    return RecommendationIntent(
        media_type=media, region=target_region, genre_names=genre_names,
        person_names=person_names, person_ids=person_ids,
        genre_ids=genre_ids, language_name=language_name, language_code=language_code,
        keywords=keywords, streaming_services=services, provider_ids=provider_ids,
        theatrical=theatrical, ott=ott,
        monetization_types="flatrate" if ott and provider_ids else None,
    )
