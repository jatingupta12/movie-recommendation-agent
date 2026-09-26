from datetime import date
from contextlib import ExitStack
from types import SimpleNamespace

import pytest

from weekend_watch.database import connect, initialize_database
from weekend_watch.personalization import PersonalizationService, UserPreferences
from weekend_watch.repositories import WatchmodeRepository
from weekend_watch.recommendations import RecommendationPipeline, RecommendationWeights
from weekend_watch.tmdb.models import SearchResults, Title
from weekend_watch.watchmode.models import StreamingAvailability
from weekend_watch.watchmode import WatchmodeError
from weekend_watch.recommendations.runtime import build_recommendation_pipeline


def title(tmdb_id, name=None, *, media_type="movie", rating=8.2, votes=600,
          popularity=20, genres=None, release_date="2026-09-01", language="en"):
    return Title(
        tmdb_id=tmdb_id, title=name or f"Title {tmdb_id}", media_type=media_type,
        rating=rating, vote_count=votes, popularity=popularity, genres=genres or ["Sci-Fi"],
        release_date=release_date, original_language=language,
    )


class FakeTmdb:
    def __init__(self, *, trending_movies=(), trending_tv=(), new_movies=(), new_tv=(),
                 high_movies=(), high_tv=(), hidden_movies=(), hidden_tv=()):
        self.sources = {
            "trending_movies": list(trending_movies), "trending_tv": list(trending_tv),
            "new_movies": list(new_movies), "new_tv": list(new_tv),
            "high_movies": list(high_movies), "high_tv": list(high_tv),
            "hidden_movies": list(hidden_movies), "hidden_tv": list(hidden_tv),
        }
        self.calls = []

    @staticmethod
    def results(items):
        return SearchResults(results=items)

    def get_trending_movies(self):
        self.calls.append("trending_movies")
        return self.results(self.sources["trending_movies"])

    def get_trending_tv(self):
        self.calls.append("trending_tv")
        return self.results(self.sources["trending_tv"])

    def discover_movies(self, **filters):
        if "primary_release_date.gte" in filters:
            key = "new_movies"
        elif "vote_count.lte" in filters:
            key = "hidden_movies"
        else:
            key = "high_movies"
        self.calls.append(key)
        return self.results(self.sources[key])

    def discover_tv(self, **filters):
        if "first_air_date.gte" in filters:
            key = "new_tv"
        elif "vote_count.lte" in filters:
            key = "hidden_tv"
        else:
            key = "high_tv"
        self.calls.append(key)
        return self.results(self.sources[key])


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "recommendations.db"
    initialize_database(path)
    db = connect(path)
    yield db, PersonalizationService(db)
    db.close()


def pipeline_for(service, tmdb, **kwargs):
    return RecommendationPipeline(tmdb, service, today=date(2026, 9, 25), **kwargs)


def test_removes_already_watched_and_disliked_titles(context):
    db, service = context
    watched = title(1)
    disliked = title(2)
    survivor = title(3)
    for item in (watched, disliked, survivor):
        local = service.titles.upsert(**item.to_repository_fields())
        if item.tmdb_id == watched.tmdb_id:
            service.mark_watched(local)
        if item.tmdb_id == disliked.tmdb_id:
            service.mark_not_interested(local)
    tmdb = FakeTmdb(trending_movies=[watched, disliked, survivor])
    candidates = pipeline_for(service, tmdb).get_weekend_candidates()
    assert [candidate.title.tmdb_id for candidate in candidates] == [3]


def test_excluded_genres_and_minimum_rating_filter_candidates(context):
    _, service = context
    service.update_user_preferences(UserPreferences(
        preferred_genres=["Sci-Fi"], excluded_genres=["Horror"], minimum_rating=8.0,
        new_releases_enabled=False, hidden_gems_enabled=False,
    ))
    keep = title(10, genres=["Sci-Fi"], rating=8.4)
    wrong_genre = title(11, genres=["Horror"], rating=9.0)
    low_rated = title(12, genres=["Sci-Fi"], rating=7.9)
    tmdb = FakeTmdb(trending_movies=[keep, wrong_genre, low_rated], trending_tv=[])
    candidates = pipeline_for(service, tmdb).get_weekend_candidates()
    assert [item.title.tmdb_id for item in candidates] == [10]
    assert candidates[0].score_breakdown["genre_match"] == 1.0


def test_preferred_streaming_provider_filters_and_scores(context):
    _, service = context
    service.update_user_preferences(UserPreferences(
        preferred_streaming_services=["Stream+"], trending_enabled=True,
        new_releases_enabled=False, hidden_gems_enabled=False,
    ))
    matching, unavailable = title(20), title(21)

    def availability(item):
        providers = {20: ["Stream+", "Other"], 21: ["Other"]}[item.tmdb_id]
        return [StreamingAvailability(title_id=item.tmdb_id, provider=provider,
                                      provider_type="subscription", region="US") for provider in providers]

    tmdb = FakeTmdb(trending_movies=[matching, unavailable], trending_tv=[])
    candidates = pipeline_for(service, tmdb, availability_lookup=availability).get_weekend_candidates()
    assert [item.title.tmdb_id for item in candidates] == [20]
    assert candidates[0].score_breakdown["streaming_availability"] == 1.0
    assert [entry.provider for entry in candidates[0].streaming_availability] == ["Stream+", "Other"]


def test_media_type_preferences_disable_tv_candidates(context):
    _, service = context
    service.update_user_preferences(UserPreferences(
        movies_enabled=True, tv_enabled=False, new_releases_enabled=False,
        hidden_gems_enabled=False,
    ))
    movie, show = title(30), title(31, media_type="tv")
    tmdb = FakeTmdb(trending_movies=[movie], trending_tv=[show], high_tv=[show], hidden_tv=[show])
    candidates = pipeline_for(service, tmdb).get_weekend_candidates()
    assert all(item.title.media_type == "movie" for item in candidates)
    assert "trending_tv" not in tmdb.calls and "high_tv" not in tmdb.calls


def test_hidden_gem_favors_low_popularity_and_strong_preference_match(context):
    _, service = context
    service.update_user_preferences(UserPreferences(preferred_genres=["Sci-Fi"], trending_enabled=False,
                                                     new_releases_enabled=False))
    gem = title(40, rating=8.7, votes=450, popularity=8, genres=["Sci-Fi"])
    popular = title(41, rating=8.7, votes=450, popularity=30, genres=["Sci-Fi"])
    unrelated = title(42, rating=9.0, votes=450, popularity=4, genres=["Drama"])
    tmdb = FakeTmdb(hidden_movies=[gem, popular, unrelated], high_movies=[gem, popular, unrelated], high_tv=[])
    candidates = pipeline_for(service, tmdb).get_weekend_candidates()
    by_id = {item.title.tmdb_id: item for item in candidates}
    assert "HIDDEN_GEM" in by_id[40].categories
    assert "HIDDEN_GEM" in by_id[41].categories
    assert "HIDDEN_GEM" not in by_id[42].categories
    assert by_id[40].score_breakdown["popularity"] > by_id[41].score_breakdown["popularity"]
    assert by_id[40].match_score > by_id[41].match_score


def test_new_release_trending_and_duplicate_sources_merge_categories(context):
    _, service = context
    duplicate_a = title(50, name="Same Film")
    duplicate_b = title(50, name="Same Film", genres=["Sci-Fi", "Adventure"])
    tmdb = FakeTmdb(trending_movies=[duplicate_a], new_movies=[duplicate_b], high_movies=[duplicate_a],
                    hidden_movies=[], high_tv=[])
    candidates = pipeline_for(service, tmdb).get_weekend_candidates()
    matched = [item for item in candidates if item.title.tmdb_id == 50]
    assert len(matched) == 1
    assert set(matched[0].categories) == {"TRENDING", "NEW_RELEASE", "HIGHLY_RATED"}
    assert matched[0].title.genres == ["Sci-Fi", "Adventure"]
    assert matched[0].match_score <= 100
    assert matched[0].score_breakdown["rating"] == pytest.approx(0.82)


def test_streaming_exclusions_remove_excluded_provider_from_candidate_data(context):
    _, service = context
    service.update_user_preferences(UserPreferences(excluded_streaming_services=["AvoidTV"],
                                                     new_releases_enabled=False, hidden_gems_enabled=False))
    film = title(60)
    sources = [StreamingAvailability(title_id=60, provider=name, provider_type="subscription")
               for name in ("AvoidTV", "KeepTV")]
    candidates = pipeline_for(service, FakeTmdb(trending_movies=[film], trending_tv=[]),
                              availability_lookup=lambda _: sources).get_weekend_candidates()
    assert [item.provider for item in candidates[0].streaming_availability] == ["KeepTV"]


def test_hidden_gem_category_requires_genre_preference_and_vote_popularity_bounds(context):
    _, service = context
    service.update_user_preferences(UserPreferences(preferred_genres=[], trending_enabled=False,
                                                     new_releases_enabled=False))
    film = title(70, votes=150, popularity=5)
    candidates = pipeline_for(service, FakeTmdb(hidden_movies=[film], high_movies=[film], high_tv=[])).get_weekend_candidates()
    assert candidates == []


def test_recommendation_weights_are_configurable_and_score_is_explainable(context):
    _, service = context
    weights = RecommendationWeights(
        rating=1, vote_count=0, popularity=0, recency=0,
        genre_match=0, streaming_availability=0, trending=0,
    )
    film = title(80, rating=8.4)
    candidates = pipeline_for(service, FakeTmdb(trending_movies=[film], trending_tv=[]),
                              weights=weights).get_weekend_candidates()
    assert candidates[0].match_score == 84.0
    assert candidates[0].score_breakdown["rating"] == 0.84


def test_watchmode_failure_does_not_recommend_from_expired_availability(context):
    db, service = context
    film = title(90)
    local_id = service.titles.upsert(**film.to_repository_fields())
    cache = WatchmodeRepository(db)
    cache.save_mapping(local_id, 9000)
    saved = StreamingAvailability(title_id=local_id, provider="Cached Stream", provider_type="subscription")
    cache.save_availability(local_id, 9000, "US", [saved])
    db.execute("UPDATE watchmode_availability_cache SET fetched_at='2000-01-01T00:00:00+00:00'")
    db.commit()

    class FailingWatchmode:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def get_availability(self, *_args, **_kwargs):
            raise WatchmodeError("upstream unavailable")

    settings = SimpleNamespace(
        watchmode_api_key="fake", watchmode_region="US", watchmode_cache_ttl_hours=1,
    )
    with ExitStack() as stack:
        pipeline = build_recommendation_pipeline(
            settings, db, FakeTmdb(), stack, watchmode_client_factory=FailingWatchmode
        )
        availability = pipeline._availability(film)

    assert availability == []
