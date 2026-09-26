"""Deterministic constraints stated explicitly in a recommendation request."""

import re

from .models import WeekendCandidate

_REQUEST_GENRES = {
    "action": ("action",), "adventure": ("adventure",),
    "animation": ("animation",), "comedy": ("comedy",),
    "crime": ("crime",), "documentary": ("documentary",),
    "drama": ("drama",), "family": ("family",), "fantasy": ("fantasy",),
    "history": ("history",), "horror": ("horror",), "scary": ("horror",),
    "music": ("music",), "mystery": ("mystery",), "romance": ("romance",),
    "sci-fi": ("sci-fi",),
    "science fiction": ("sci-fi",),
    "thriller": ("thriller",), "war": ("war",), "western": ("western",),
}

_REQUEST_SERVICES = {
    "Netflix": ("netflix",), "Hulu": ("hulu",), "Max": ("hbo max", "max"),
    "Prime Video": ("amazon prime video", "prime video"),
    "Disney+": ("disney plus", "disney+"),
    "Apple TV+": ("apple tv plus", "apple tv+", "appletv+", "apple tv"),
    "Paramount+": ("paramount plus", "paramount+"), "Peacock": ("peacock",),
    "Crunchyroll": ("crunchyroll",), "Shudder": ("shudder",),
    "MGM+": ("mgm plus", "mgm+"), "AMC+": ("amc plus", "amc+"),
    "BritBox": ("britbox",), "Starz": ("starz",), "Tubi": ("tubi",),
    "Pluto TV": ("pluto tv",), "Criterion Channel": ("criterion channel",),
    "Kanopy": ("kanopy",), "Freevee": ("freevee",),
}

_LANGUAGES = {
    "english": "en", "hindi": "hi", "spanish": "es", "korean": "ko",
    "french": "fr", "japanese": "ja", "chinese": "zh", "mandarin": "zh",
    "tamil": "ta", "telugu": "te", "malayalam": "ml", "bengali": "bn",
    "punjabi": "pa", "arabic": "ar", "german": "de", "italian": "it",
    "portuguese": "pt", "russian": "ru", "turkish": "tr", "thai": "th",
    "indonesian": "id", "dutch": "nl", "swedish": "sv",
}
_GENRE_MATCH_ALIASES = {
    "action": {"action & adventure"}, "thriller": {"mystery"},
    "sci-fi": {"science fiction", "sci-fi & fantasy"},
    "science fiction": {"science fiction", "sci-fi & fantasy"},
}

_LANGUAGES = {
    "english": "en", "hindi": "hi", "spanish": "es", "korean": "ko",
    "french": "fr", "japanese": "ja", "chinese": "zh", "mandarin": "zh",
    "tamil": "ta", "telugu": "te", "malayalam": "ml", "bengali": "bn",
    "punjabi": "pa", "arabic": "ar", "german": "de", "italian": "it",
    "portuguese": "pt", "russian": "ru", "turkish": "tr", "thai": "th",
    "indonesian": "id", "dutch": "nl", "swedish": "sv",
}


def requested_genres(request: str) -> set[str]:
    genres: set[str] = set()
    lowered = request.casefold()
    for phrase, mapped_genres in _REQUEST_GENRES.items():
        if re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", lowered):
            genres.update(mapped_genres)
    return genres


def requested_media_type(request: str) -> str | None:
    lowered = request.casefold()
    wants_tv = bool(re.search(r"\b(?:series|tv|television)\b", lowered))
    wants_movie = bool(re.search(r"\b(?:movies?|films?)\b", lowered))
    if wants_tv == wants_movie:
        return None
    return "tv" if wants_tv else "movie"


def filter_candidates_by_request(candidates: list[WeekendCandidate], request: str, *,
                                 region: str = "US") -> list[WeekendCandidate]:
    """Apply explicit genre, media, and streaming constraints to factual data."""
    genres = requested_genres(request)
    media_type = requested_media_type(request)
    lowered = request.casefold()
    requested_language = next((code for name, code in _LANGUAGES.items()
                               if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", lowered)), None)
    included_services: set[str] = set()
    excluded_services: set[str] = set()
    for service, aliases in _REQUEST_SERVICES.items():
        for alias in aliases:
            match = re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", lowered)
            if not match:
                continue
            prefix = lowered[:match.start()]
            is_excluded = bool(re.search(
                r"(?:\bnot|\bwithout|\bavoid|\bexcluding|\bexcept)"
                r"(?:\s+[\w']+){0,2}\s*$", prefix,
            ))
            (excluded_services if is_excluded else included_services).add(service)
            break

    filtered = []
    for candidate in candidates:
        title_genres = {g.casefold() for g in candidate.title.genres}
        genre_matches = {
            genre: bool(({genre} | _GENRE_MATCH_ALIASES.get(genre, set())) & title_genres)
            for genre in genres
        }
        if genres and not all(genre_matches.values()):
            # A few useful concepts are not present in every TMDB media taxonomy
            # (notably Horror for TV). Only retain items returned by the targeted
            # keyword discovery path; do not add an invented genre to TMDB facts.
            keyword_targeted = (
                candidate.title.media_type == "tv" and "horror" in genres
                and "KEYWORD_MATCH" in candidate.categories
                and all(matched for genre, matched in genre_matches.items() if genre != "horror")
            )
            if not keyword_targeted:
                continue
        if media_type and candidate.title.media_type != media_type:
            continue
        if requested_language and (candidate.title.original_language or "").casefold() != requested_language:
            continue
        available = [item for item in candidate.streaming_availability
                     if item.available and item.region.casefold() == region.casefold()
                     and item.provider_type in {"subscription", "free"}]
        matching = {
            service for service, aliases in _REQUEST_SERVICES.items()
            if any(_provider_matches(item.provider, aliases) for item in available)
        }
        if matching & excluded_services:
            continue
        if included_services and not matching.intersection(included_services):
            continue
        filtered.append(candidate)
    return filtered


def _provider_matches(provider: str, aliases: tuple[str, ...]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", provider.casefold())
    return any(
        normalized.startswith(re.sub(r"[^a-z0-9]+", "", alias.casefold()))
        for alias in aliases
    )
