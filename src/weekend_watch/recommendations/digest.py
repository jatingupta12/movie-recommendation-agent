"""Weekend digest selection and deterministic Markdown rendering."""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIRecommendationService, filter_by_requested_genres
from .models import WeekendCandidate
from .pipeline import RecommendationPipeline

DigestCategory = Literal["RECOMMENDED", "TRENDING", "HIGHLY_RATED", "NEW_THIS_WEEK", "HIDDEN_GEM"]
DigestSectionName = Literal["recommended", "new_this_week", "hidden_gems"]


class WeekendDigestItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    tmdb_id: int
    title: str
    media_type: Literal["movie", "tv"]
    rating: float | None = None
    release_date: str | None = None
    genres: list[str] = Field(default_factory=list)
    streaming_services: list[str] = Field(default_factory=list)
    synopsis: str
    why_it_matches: str
    category: DigestCategory
    why_source: Literal["claude", "groq", "deterministic"] = "deterministic"
    metadata_source: Literal["TMDB"] = "TMDB"
    availability_source: Literal["Watchmode", "not_confirmed"] = "not_confirmed"


class WeekendDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_on: date
    recommended: list[WeekendDigestItem] = Field(default_factory=list)
    new_this_week: list[WeekendDigestItem] = Field(default_factory=list)
    hidden_gems: list[WeekendDigestItem] = Field(default_factory=list)

    @property
    def total_titles(self) -> int:
        return len(self.recommended) + len(self.new_this_week) + len(self.hidden_gems)


class WeekendDigestService:
    """Select a compact, deduplicated digest from deterministic candidates."""

    def __init__(self, pipeline: RecommendationPipeline,
                 ai_recommender: AIRecommendationService | None = None):
        self.pipeline = pipeline
        self.ai_recommender = ai_recommender

    def generate(self, *, limit: int = 8, today: date | None = None,
                 request: str = "Recommend what I should watch this weekend.") -> WeekendDigest:
        total = max(0, min(limit, 50))
        current_date = today or date.today()
        candidates = self.pipeline.get_weekend_candidates(limit=total * 4) if total else []
        # Apply explicit request constraints before building any section. AI
        # selection may reorder candidates, but must not allow out-of-genre
        # titles to reappear through New This Week or Hidden Gems buckets.
        candidates = filter_by_requested_genres(candidates, request)
        if candidates and self.ai_recommender is not None:
            preferences = self.pipeline.personalization.get_user_preferences()
            reasoning = self.ai_recommender.recommend_candidates(
                candidates, request=request, limit=len(candidates), preferences=preferences
            )
            by_id = {candidate.title.tmdb_id: candidate for candidate in candidates}
            ai_order = [item.tmdb_id for item in reasoning if item.tmdb_id in by_id]
            candidates = [by_id[identifier] for identifier in ai_order] + [
                item for item in candidates if item.title.tmdb_id not in set(ai_order)
            ]
            reasons_by_id = {item.tmdb_id: item for item in reasoning}
        else:
            reasons_by_id = {}
        return build_weekend_digest(
            candidates, limit=total, today=current_date,
            region=getattr(self.pipeline, "region", "US"),
            reasons_by_id=reasons_by_id,
        )


def build_weekend_digest(candidates: list[WeekendCandidate], *, limit: int = 8,
                         today: date | None = None, region: str = "US",
                         reasons_by_id: dict[int, object] | None = None) -> WeekendDigest:
    """Build three non-overlapping sections; week membership requires a real date."""
    max_titles = max(0, min(limit, 50))
    current_date = today or date.today()
    week_start = current_date - timedelta(days=current_date.weekday())

    buckets: dict[DigestSectionName, list[WeekendCandidate]] = {
        "recommended": [], "new_this_week": [], "hidden_gems": [],
    }
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        identity = ("tmdb", candidate.title.tmdb_id)
        if identity in seen:
            continue
        seen.add(identity)
        if _released_this_week(candidate, week_start, current_date):
            buckets["new_this_week"].append(candidate)
        elif "HIDDEN_GEM" in candidate.categories:
            buckets["hidden_gems"].append(candidate)
        else:
            buckets["recommended"].append(candidate)

    # Allocate half the space to broad picks and a quarter to each focused section.
    # Fill unused slots from the original ranked order when a section lacks supply.
    if max_titles:
        new_quota = max_titles // 4
        hidden_quota = max_titles // 4
        recommended_quota = max_titles - new_quota - hidden_quota
    else:
        recommended_quota = new_quota = hidden_quota = 0
    selected: dict[DigestSectionName, list[WeekendCandidate]] = {
        "recommended": buckets["recommended"][:recommended_quota],
        "new_this_week": buckets["new_this_week"][:new_quota],
        "hidden_gems": buckets["hidden_gems"][:hidden_quota],
    }
    selected_ids = {candidate.title.tmdb_id for group in selected.values() for candidate in group}
    remaining = max_titles - len(selected_ids)
    if remaining:
        source_section: dict[int, DigestSectionName] = {}
        for section_name, entries in buckets.items():
            for entry in entries:
                source_section[entry.title.tmdb_id] = section_name
        for candidate in candidates:
            if remaining <= 0:
                break
            identifier = candidate.title.tmdb_id
            if identifier in selected_ids or identifier not in source_section:
                continue
            section = source_section[identifier]
            selected[section].append(candidate)
            selected_ids.add(identifier)
            remaining -= 1

    return WeekendDigest(
        generated_on=current_date,
        recommended=[_to_item(item, "recommended", current_date, region, reasons_by_id) for item in selected["recommended"]],
        new_this_week=[_to_item(item, "new_this_week", current_date, region, reasons_by_id) for item in selected["new_this_week"]],
        hidden_gems=[_to_item(item, "hidden_gems", current_date, region, reasons_by_id) for item in selected["hidden_gems"]],
    )


def _released_this_week(candidate: WeekendCandidate, week_start: date, today: date) -> bool:
    release_date = candidate.title.release_date
    if not release_date:
        return False
    try:
        parsed = date.fromisoformat(release_date[:10])
    except ValueError:
        return False
    return week_start <= parsed <= today


def _to_item(candidate: WeekendCandidate, section: DigestSectionName,
             today: date, region: str, reasons_by_id: dict[int, object] | None = None
             ) -> WeekendDigestItem:
    title = candidate.title
    available_services = sorted({
        item.provider for item in candidate.streaming_availability
        if item.available and item.region.upper() == region.upper()
    })
    if section == "new_this_week":
        category: DigestCategory = "NEW_THIS_WEEK"
    elif section == "hidden_gems":
        category = "HIDDEN_GEM"
    elif "TRENDING" in candidate.categories:
        category = "TRENDING"
    elif "HIGHLY_RATED" in candidate.categories:
        category = "HIGHLY_RATED"
    else:
        category = "RECOMMENDED"

    reasons = [reason for reason in candidate.reasons
               if reason != "New release" or _released_this_week(
                   candidate, today - timedelta(days=today.weekday()), today
               )]
    ai_reason = (reasons_by_id or {}).get(title.tmdb_id)
    source = getattr(ai_reason, "explanation_source", "deterministic")
    is_ai_reason = source in {"claude", "groq"}
    if is_ai_reason:
        why = ai_reason.recommendation_reason
    else:
        match_reasons = [reason for reason in reasons if reason not in {"Trending now"}]
        why = "; ".join(match_reasons[:3]) or "Matches your current recommendation preferences."
    synopsis = _short_synopsis(title.overview)
    return WeekendDigestItem(
        tmdb_id=title.tmdb_id,
        title=title.title,
        media_type=title.media_type,
        rating=title.rating,
        release_date=title.release_date,
        genres=title.genres,
        streaming_services=available_services,
        synopsis=synopsis,
        why_it_matches=why,
        why_source=source if is_ai_reason else "deterministic",
        category=category,
        availability_source="Watchmode" if available_services else "not_confirmed",
    )


def _short_synopsis(overview: str, *, max_chars: int = 320) -> str:
    synopsis = " ".join(overview.split())
    if not synopsis:
        return "Synopsis unavailable from TMDB."
    if len(synopsis) <= max_chars:
        return synopsis
    excerpt = synopsis[:max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return excerpt + "…"


def format_weekend_digest(digest: WeekendDigest) -> str:
    """Render a deterministic Markdown digest with explicit unknown values."""
    sections = (
        ("🔥 Recommended", digest.recommended),
        ("🆕 New This Week", digest.new_this_week),
        ("💎 Hidden Gems", digest.hidden_gems),
    )
    output = ["# 🍿 Weekend Watch", f"_Generated {digest.generated_on.isoformat()}._"]
    for heading, entries in sections:
        output.extend(["", f"## {heading}"])
        if not entries:
            output.append("No matching titles this week.")
            continue
        for item in entries:
            media_label = "Movie" if item.media_type == "movie" else "TV series"
            rating = f"⭐ {item.rating:.1f}/10 (TMDB)" if item.rating is not None else "⭐ Rating unavailable"
            release_date = item.release_date or "Release date unavailable"
            genres = " / ".join(item.genres) if item.genres else "Genres unavailable"
            providers = ", ".join(item.streaming_services) if item.streaming_services else "Availability not confirmed"
            output.extend([
                "",
                f"### {item.title}",
                f"🎬 TMDB · {media_label} · {rating} · {release_date} · {genres}",
                f"📺 Watchmode · {providers}" if item.streaming_services else "📺 Availability not confirmed by Watchmode",
                "",
                f"**Synopsis (TMDB):** {item.synopsis}",
                "",
                f"**Why you might like it ({'Claude-assisted' if item.why_source == 'claude' else 'preference match'}):** {item.why_it_matches}",
                f"**Category:** {item.category}",
                "",
                "---",
            ])
    output.extend([
        "",
        "## Notes",
        "- Already watched titles are excluded.",
        "- Titles marked not interested are excluded.",
    ])
    return "\n".join(output).rstrip() + "\n"
