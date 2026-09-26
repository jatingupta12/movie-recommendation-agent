from weekend_watch.recommendations.intent import extract_request_intent
from weekend_watch.recommendations.request_filters import filter_candidates_by_request
from weekend_watch.recommendations.models import WeekendCandidate
from weekend_watch.tmdb.models import Genre, Title


class IntentTmdb:
    def get_genres(self, media_type):
        return ([Genre(id=27, name="Horror"), Genre(id=878, name="Science Fiction")]
                if media_type == "movie" else [Genre(id=10765, name="Sci-Fi & Fantasy")])


def test_extract_hindi_horror_movies_series_netflix_to_tmdb_request_intent():
    intent = extract_request_intent(
        "show me hindi horror movie or series on ott platforms like netflix and prime video",
        tmdb=IntentTmdb(),
    )
    assert intent.media_type == "both"
    assert intent.region == "US"
    assert intent.language_name == "Hindi"
    assert intent.language_code == "hi"
    assert intent.genre_names == ["horror"]
    assert intent.genre_ids["movie"] == [27]
    assert intent.genre_ids["tv"] == []
    assert intent.streaming_services == ["Netflix", "Prime Video"]
    assert intent.provider_ids == [8, 119]
    assert intent.ott is True
    assert intent.tmdb_discover_params("movie") == {
        "watch_region": "US",
        "with_watch_providers": "8|119",
        "with_watch_monetization_types": "flatrate",
        "with_genres": "27",
        "with_original_language": "hi",
    }


def test_intent_extracts_region_theatrical_and_abstract_keyword():
    intent = extract_request_intent("Hindi films in theaters in India about time loops")
    assert intent.region == "IN"
    assert intent.media_type == "movie"
    assert intent.language_code == "hi"
    assert intent.theatrical is True
    assert intent.keywords == ["time loops"]


def test_tv_horror_keyword_target_does_not_fabricate_genre_metadata():
    show = Title(tmdb_id=123, title="Example", media_type="tv", genres=["Drama"])
    candidate = WeekendCandidate(
        title=show, categories=["RECOMMENDED", "KEYWORD_MATCH"], match_score=80,
        score_breakdown={},
    )
    assert filter_candidates_by_request([candidate], "horror series") == [candidate]


def test_multiple_explicit_genres_are_all_required():
    comedy = Title(tmdb_id=124, title="Comedy", media_type="movie", genres=["Comedy"])
    candidate = WeekendCandidate(title=comedy, categories=["TRENDING"], match_score=70,
                                 score_breakdown={})
    assert filter_candidates_by_request([candidate], "horror comedy movie") == []
