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
