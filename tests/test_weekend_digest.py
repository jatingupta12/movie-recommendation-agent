from datetime import date

from weekend_watch.recommendations.digest import (
    WeekendDigestService, build_weekend_digest, format_weekend_digest,
)
from weekend_watch.recommendations.models import WeekendCandidate
from weekend_watch.tmdb.models import Title
from weekend_watch.watchmode.models import StreamingAvailability


TODAY = date(2026, 9, 25)


def make_candidate(tmdb_id, title, *, categories=None, release_date=None, rating=8.2,
                   overview="A catalog synopsis.", genres=None, availability=None, reasons=None):
    return WeekendCandidate(
        title=Title(
            tmdb_id=tmdb_id, title=title, media_type="movie", rating=rating,
            release_date=release_date, genres=genres or ["Drama"], overview=overview,
            vote_count=400, popularity=15,
        ),
        categories=categories or ["TRENDING"],
        match_score=90 - tmdb_id,
        score_breakdown={},
        streaming_availability=availability or [],
        reasons=reasons or ["Matches preferred genres", "Trending now"],
    )


def test_digest_uses_calendar_week_date_and_categorizes_unique_titles():
    candidates = [
        make_candidate(1, "Trending Pick", release_date="2024-04-01"),
        make_candidate(2, "New This Week", categories=["NEW_RELEASE"], release_date="2026-09-23"),
        make_candidate(3, "Recent But Not This Week", categories=["NEW_RELEASE"], release_date="2026-09-18"),
        make_candidate(4, "Hidden Pick", categories=["HIDDEN_GEM"], release_date="2021-02-02"),
        make_candidate(5, "Undated Release", categories=["NEW_RELEASE"], release_date=None),
        make_candidate(1, "Duplicate Trending Pick", release_date="2024-04-01"),
    ]

    digest = build_weekend_digest(candidates, limit=8, today=TODAY)

    assert digest.total_titles == 5
    assert [item.title for item in digest.new_this_week] == ["New This Week"]
    assert [item.title for item in digest.hidden_gems] == ["Hidden Pick"]
    assert {item.title for item in digest.recommended} == {
        "Trending Pick", "Recent But Not This Week", "Undated Release"
    }
    assert len({item.tmdb_id for group in (
        digest.recommended, digest.new_this_week, digest.hidden_gems
    ) for item in group}) == digest.total_titles


def test_digest_displays_each_section_by_release_year_descending_and_undated_last():
    candidates = [
        make_candidate(11, "Older", release_date="2021-01-01"),
        make_candidate(12, "Newest", release_date="2025-05-01"),
        make_candidate(13, "Undated", release_date=None),
        make_candidate(14, "Middle", release_date="2023-08-15"),
    ]

    digest = build_weekend_digest(candidates, limit=10, today=TODAY)

    assert [item.title for item in digest.recommended] == [
        "Newest", "Middle", "Older", "Undated",
    ]


def test_digest_never_invents_provider_rating_or_synopsis():
    title = make_candidate(
        10, "Incomplete Data", rating=None, release_date=None, overview="",
        availability=[
            StreamingAvailability(title_id=10, provider="Unconfirmed Service", provider_type="subscription",
                                  region="US", available=False),
            StreamingAvailability(title_id=10, provider="Canada Stream", provider_type="subscription",
                                  region="CA", available=True),
            StreamingAvailability(title_id=10, provider="Real US Service", provider_type="subscription",
                                  region="US", available=True),
        ],
        reasons=["Matches preferred genres"],
    )

    digest = build_weekend_digest([title], limit=3, today=TODAY)
    item = digest.recommended[0]
    markdown = format_weekend_digest(digest)

    assert item.rating is None
    assert item.release_date is None
    assert item.streaming_services == ["Real US Service"]
    assert item.metadata_source == "TMDB"
    assert item.availability_source == "Watchmode"
    assert item.synopsis == "Synopsis unavailable from TMDB."
    assert "Rating unavailable" in markdown
    assert "Release date unavailable" in markdown
    assert "Availability not confirmed" not in markdown
    assert "Real US Service" in markdown
    assert "Unconfirmed Service" not in markdown
    assert "Canada Stream" not in markdown
    assert "Synopsis unavailable from TMDB." in markdown
    assert item.why_source == "deterministic"
    assert "preference match" in markdown


def test_digest_markdown_has_all_sections_and_exclusion_notes():
    markdown = format_weekend_digest(build_weekend_digest([], limit=8, today=TODAY))

    assert markdown.startswith("# 🍿 Weekend Watch")
    assert "## 🔥 Recommended" in markdown
    assert "## 🆕 New This Week" in markdown
    assert "## 💎 Hidden Gems" in markdown
    assert "Already watched titles are excluded." in markdown
    assert "Titles marked not interested are excluded." in markdown


def test_explicit_requested_genre_filters_every_digest_section():
    class FakePipeline:
        region = "US"

        def __init__(self, candidates):
            self.candidates = candidates

        def get_weekend_candidates(self, *, limit):
            return self.candidates[:limit]

    candidates = [
        make_candidate(31, "Horror Trend", genres=["Horror"], categories=["TRENDING"]),
        make_candidate(32, "Comedy Trend", genres=["Comedy"], categories=["TRENDING"]),
        make_candidate(33, "Horror Release", genres=["Horror"], categories=["NEW_RELEASE"],
                       release_date="2026-09-23"),
        make_candidate(34, "Romance Release", genres=["Romance"], categories=["NEW_RELEASE"],
                       release_date="2026-09-24"),
        make_candidate(35, "Horror Hidden Gem", genres=["Horror"], categories=["HIDDEN_GEM"]),
        make_candidate(36, "Drama Hidden Gem", genres=["Drama"], categories=["HIDDEN_GEM"]),
    ]

    digest = WeekendDigestService(FakePipeline(candidates)).generate(
        limit=8, today=TODAY, request="Something horror for tonight",
    )
    items = digest.recommended + digest.new_this_week + digest.hidden_gems

    assert {item.title for item in items} == {
        "Horror Trend", "Horror Release", "Horror Hidden Gem",
    }
    assert all("Horror" in item.genres for item in items)


def test_digest_requires_requested_series_and_confirmed_streaming_service():
    class FakePipeline:
        region = "US"

        def __init__(self, candidates):
            self.candidates = candidates

        def get_weekend_candidates(self, *, limit):
            return self.candidates[:limit]

    def streaming_title(identifier, name, *, media_type, genres, provider, provider_type="subscription",
                        region="US", category="TRENDING", release_date=None):
        item = make_candidate(identifier, name, categories=[category], release_date=release_date, genres=genres)
        return item.model_copy(update={
            "title": item.title.model_copy(update={"media_type": media_type}),
            "streaming_availability": [StreamingAvailability(
                title_id=identifier, provider=provider, provider_type=provider_type, region=region,
            )],
        })

    candidates = [
        streaming_title(41, "Netflix Horror Series", media_type="tv", genres=["Horror"], provider="Netflix"),
        streaming_title(42, "Hulu Horror Series", media_type="tv", genres=["Horror"], provider="Hulu"),
        streaming_title(43, "Netflix Horror Movie", media_type="movie", genres=["Horror"], provider="Netflix"),
        streaming_title(44, "Netflix Drama Series", media_type="tv", genres=["Drama"], provider="Netflix"),
        streaming_title(45, "Netflix Horror New Show", media_type="tv", genres=["Horror"], provider="Netflix",
                        category="NEW_RELEASE", release_date="2026-09-23"),
        streaming_title(46, "Netflix Horror Rent", media_type="tv", genres=["Horror"], provider="Netflix",
                        provider_type="rent"),
    ]
    digest = WeekendDigestService(FakePipeline(candidates)).generate(
        limit=8, today=TODAY, request="Could you recommend something horror series on Netflix?",
    )
    items = digest.recommended + digest.new_this_week + digest.hidden_gems

    assert {item.title for item in items} == {"Netflix Horror Series", "Netflix Horror New Show"}
    assert all(item.media_type == "tv" for item in items)
    assert all("Horror" in item.genres for item in items)
    assert all(item.streaming_services == ["Netflix"] for item in items)
