import json

import pytest

from weekend_watch.database import connect, initialize_database
from weekend_watch.personalization import PersonalizationService, UserPreferences
from weekend_watch.repositories import TitleRepository


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "personal.db"
    initialize_database(path)
    with connect(path) as db:
        yield db


def add_title(db, provider="tmdb", external_id="123", name="Dune", media_type="movie"):
    return TitleRepository(db).upsert(
        provider=provider, external_id=external_id, name=name, media_type=media_type
    )


def test_watched_title_rating_notes_timestamp_and_normalized_identity(database):
    title_id = add_title(database)
    service = PersonalizationService(database)
    service.add_watched_title(
        title_id, rating=9, liked=True, notes="Loved it", watched_at="2025-04-05T20:00:00Z"
    )

    history = service.get_watch_history()
    assert len(history) == 1
    assert history[0]["name"] == "Dune"
    assert history[0]["user_rating"] == 9
    assert history[0]["liked"] == 1
    assert history[0]["notes"] == "Loved it"
    assert history[0]["watched_at"] == "2025-04-05T20:00:00Z"
    assert service.has_watched("tmdb", 123) is True
    assert service.has_watched("watchmode", 123) is False
    assert service.has_watched("tmdb", 124) is False


def test_mark_watched_can_record_repeat_views_and_rating_range_is_one_to_ten(database):
    title_id = add_title(database)
    service = PersonalizationService(database)
    service.mark_watched(title_id, notes="First viewing")
    service.mark_watched(title_id, watched_at="2025-06-01T00:00:00Z")
    service.rate_title(title_id, 10)
    assert len(service.get_watch_history()) == 2
    assert service.feedback.get(title_id)["rating"] == 10
    with pytest.raises(ValueError, match="1 to 10"):
        service.rate_title(title_id, 11)
    with pytest.raises(ValueError, match="1 to 10"):
        service.add_watched_title(title_id, rating=0)


def test_likes_dislikes_and_watch_later_are_independent_of_watch_history(database):
    disliked_id = add_title(database, external_id="1", name="Not for me")
    liked_id = add_title(database, external_id="2", name="Good one", media_type="tv")
    service = PersonalizationService(database)
    service.mark_not_interested(disliked_id, notes="Too intense")
    service.mark_liked(liked_id)
    service.add_to_watch_later(disliked_id, notes="Maybe later")
    service.add_to_watch_later(disliked_id, notes="Queue note updated")

    assert service.feedback.get(disliked_id)["sentiment"] == "disliked"
    assert service.feedback.get(disliked_id)["notes"] == "Too intense"
    assert service.feedback.get(liked_id)["sentiment"] == "liked"
    assert len(service.get_watch_history()) == 0
    queue = service.get_watch_later()
    assert len(queue) == 1
    assert queue[0]["notes"] == "Queue note updated"
    assert service.remove_from_watch_later(disliked_id) is True
    assert service.remove_from_watch_later(disliked_id) is False
    assert service.get_watch_later() == []


def test_preferences_cover_all_requested_fields_and_round_trip(database):
    service = PersonalizationService(database)
    default_preferences = service.get_user_preferences()
    assert default_preferences.movies_enabled is True
    configured = UserPreferences(
        preferred_genres=["Sci-Fi", "Drama"], excluded_genres=["Horror"],
        minimum_rating=7.2, preferred_streaming_services=["Netflix"],
        excluded_streaming_services=["Freevee"], preferred_languages=["en", "ja"],
        movies_enabled=True, tv_enabled=False, new_releases_enabled=False,
        trending_enabled=True, hidden_gems_enabled=False,
    )
    service.update_user_preferences(configured)
    assert service.get_user_preferences() == configured
    row = service.preferences.get()
    assert json.loads(row["preferred_genres_json"]) == ["Sci-Fi", "Drama"]
    assert json.loads(row["excluded_streaming_services_json"]) == ["Freevee"]


def test_recommendation_history_repository_methods_remain_available(database):
    title_id = add_title(database)
    service = PersonalizationService(database)
    recommendation_id = service.record_recommendation(
        [title_id], criteria={"genres": ["Sci-Fi"]}, explanation="Matches your tastes"
    )
    row = service.get_recommendation_history()[0]
    assert row["id"] == recommendation_id
    assert json.loads(row["title_ids_json"]) == [title_id]
    assert row["explanation"] == "Matches your tastes"
