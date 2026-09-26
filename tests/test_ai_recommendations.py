import json

import httpx
import pytest

from weekend_watch.config import Settings
from weekend_watch.personalization import UserPreferences
from weekend_watch.recommendations.ai import (
    AIProviderError,
    AIRecommendationService,
    ClaudeClient,
    GroqClient,
)
from weekend_watch.recommendations.models import WeekendCandidate
from weekend_watch.tmdb.models import Title
from weekend_watch.watchmode.models import StreamingAvailability


def candidate(tmdb_id, name):
    normalized = Title(
        tmdb_id=tmdb_id, title=name, media_type="movie", rating=8.7,
        release_date="2025-03-01", genres=["Sci-Fi", "Adventure"], vote_count=400,
    )
    return WeekendCandidate(
        title=normalized,
        categories=["HIGHLY_RATED", "HIDDEN_GEM"],
        match_score=91,
        score_breakdown={"rating": 0.87},
        streaming_availability=[StreamingAvailability(
            title_id=tmdb_id, provider="StreamCo", provider_type="subscription", region="US"
        )],
        reasons=["Rated 8.7/10", "Matches preferred genres"],
    )


class FakePipeline:
    def __init__(self, candidates, preferences=None):
        self.candidates = candidates
        self.personalization = type("Personalization", (), {
            "get_user_preferences": lambda _: preferences or UserPreferences(preferred_genres=["Sci-Fi"])
        })()

    def get_weekend_candidates(self, *, limit):
        return self.candidates[:limit]


def fake_http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ai_environment_configuration(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-model-test")
    monkeypatch.setenv("GROQ_MODEL", "groq-model-test")
    settings = Settings()
    assert settings.anthropic_api_key == "claude-test"
    assert settings.groq_api_key == "groq-test"
    assert settings.anthropic_model == "claude-model-test"
    assert settings.groq_model == "groq-model-test"


def test_provider_clients_use_structured_json_schemas():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request, body))
        if request.url.host == "api.groq.com":
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "selected_tmdb_ids": [1],
                "classifications": [{"tmdb_id": 1, "strong_match": True, "match_reason": "Genre fit"}],
            })}}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({
            "recommendations": [{"tmdb_id": 1, "reason_code": "streaming_available", "confidence": 0.93}]
        })}]})

    http = fake_http(handler)
    groq = GroqClient(api_key="fake-groq", http_client=http)
    claude = ClaudeClient(api_key="fake-claude", http_client=http)
    film = candidate(1, "Dune")
    try:
        result = groq.filter_candidates([film], UserPreferences(), "Sci-Fi")
        final = claude.select_recommendations([film], UserPreferences(), "Sci-Fi", 4)
        assert result.selected_tmdb_ids == [1]
        assert final.recommendations[0].reason_code == "streaming_available"
        groq_request, groq_body = seen[0]
        claude_request, claude_body = seen[1]
        assert groq_request.headers["authorization"] == "Bearer fake-groq"
        assert groq_body["response_format"]["json_schema"]["strict"] is True
        assert claude_request.headers["x-api-key"] == "fake-claude"
        assert claude_body["output_config"]["format"]["type"] == "json_schema"
        assert claude_body["output_config"]["format"]["schema"]["additionalProperties"] is False
        prompt = claude_body["messages"][0]["content"]
        assert "chain-of-thought" in prompt
        assert '"overview"' not in prompt
    finally:
        http.close()


def test_malformed_claude_json_becomes_sanitized_provider_error():
    secret = "claude-key-must-not-leak"
    http = fake_http(lambda request: httpx.Response(200, json={
        "content": [{"type": "text", "text": "not json"}],
    }))
    client = ClaudeClient(api_key=secret, http_client=http)
    try:
        with pytest.raises(AIProviderError) as error:
            client.select_recommendations([candidate(1, "Dune")], UserPreferences(), "weekend", 1)
        assert "invalid recommendation response" in str(error.value)
        assert secret not in str(error.value)
    finally:
        http.close()


def test_both_stages_select_ids_but_final_facts_come_from_candidates():
    def handler(request):
        if request.url.host == "api.groq.com":
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "selected_tmdb_ids": [2],
                "classifications": [{"tmdb_id": 2, "strong_match": True, "match_reason": "genre"}],
            })}}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({
            "recommendations": [{"tmdb_id": 2, "reason_code": "highly_rated", "confidence": 0.88}]
        })}]})

    http = fake_http(handler)
    service = AIRecommendationService(
        FakePipeline([candidate(1, "Other"), candidate(2, "Dune")]),
        groq=GroqClient(api_key="fake", http_client=http),
        claude=ClaudeClient(api_key="fake", http_client=http),
    )
    try:
        results = service.recommend(request="Sci-Fi please", limit=1)
        assert len(results) == 1
        recommendation = results[0]
        assert recommendation.tmdb_id == 2
        assert recommendation.title == "Dune"
        assert recommendation.rating == 8.7
        assert recommendation.release_date == "2025-03-01"
        assert recommendation.genres == ["Sci-Fi", "Adventure"]
        assert recommendation.streaming_services == ["StreamCo"]
        assert recommendation.metadata_source == "TMDB"
        assert recommendation.availability_source == "Watchmode"
        assert recommendation.recommendation_reason == "Rated 8.7/10 and a match for your preferences."
        assert recommendation.explanation_source == "claude"
        assert recommendation.groq_used is True
    finally:
        http.close()


def test_groq_failure_skips_filtering_and_claude_still_runs():
    class FailedGroq:
        def filter_candidates(self, *_args):
            raise AIProviderError("offline")

    class KeepAllClaude:
        def __init__(self):
            self.received = None

        def select_recommendations(self, candidates, *_args):
            self.received = candidates
            from weekend_watch.recommendations.ai import ClaudeSelection, ClaudeSelectionResult
            return ClaudeSelectionResult(recommendations=[
                ClaudeSelection(tmdb_id=candidates[0].title.tmdb_id,
                                reason_code="preferred_genres", confidence=0.8)
            ])

    claude = KeepAllClaude()
    service = AIRecommendationService(FakePipeline([candidate(1, "Dune"), candidate(2, "Arrival")]),
                                       groq=FailedGroq(), claude=claude)
    result = service.recommend(limit=1)
    assert len(claude.received) == 2
    assert result[0].groq_used is False
    assert result[0].explanation_source == "claude"


def test_claude_failure_returns_deterministic_recommendations():
    class SelectFirstGroq:
        def filter_candidates(self, candidates, *_args):
            from weekend_watch.recommendations.ai import GroqClassification, GroqFilterResult
            first = candidates[0].title.tmdb_id
            return GroqFilterResult(selected_tmdb_ids=[first], classifications=[
                GroqClassification(tmdb_id=first, strong_match=True, match_reason="fit")
            ])

    class FailedClaude:
        def select_recommendations(self, *_args):
            raise AIProviderError("offline")

    service = AIRecommendationService(FakePipeline([candidate(1, "Dune"), candidate(2, "Arrival")]),
                                      groq=SelectFirstGroq(), claude=FailedClaude())
    results = service.recommend(limit=2)
    assert [item.tmdb_id for item in results] == [1, 2]
    assert all(item.explanation_source == "deterministic" for item in results)
    assert all(not item.groq_used for item in results)


def test_invalid_model_ids_fall_back_without_inventing_titles():
    class InventingGroq:
        def filter_candidates(self, *_args):
            from weekend_watch.recommendations.ai import GroqClassification, GroqFilterResult
            return GroqFilterResult(selected_tmdb_ids=[999], classifications=[
                GroqClassification(tmdb_id=999, strong_match=True, match_reason="unknown")
            ])

    class ClaudeForKnown:
        def select_recommendations(self, candidates, *_args):
            from weekend_watch.recommendations.ai import ClaudeSelection, ClaudeSelectionResult
            assert [item.title.tmdb_id for item in candidates] == [3]
            return ClaudeSelectionResult(recommendations=[
                ClaudeSelection(tmdb_id=3, reason_code="trending", confidence=0.75)
            ])

    service = AIRecommendationService(FakePipeline([candidate(3, "Arrival")]),
                                      groq=InventingGroq(), claude=ClaudeForKnown())
    results = service.recommend()
    assert len(results) == 1 and results[0].tmdb_id == 3
    assert results[0].title == "Arrival"
    assert results[0].groq_used is False
