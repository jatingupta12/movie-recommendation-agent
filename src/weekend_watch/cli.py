"""Command-line entry point."""

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys

from .config import get_settings
from .database import connect, initialize_database
from .repositories import TitleRepository
from .tmdb import TmdbClient, TmdbError
from .tmdb.models import Title
from .tmdb.service import TmdbService
from .repositories import WatchmodeRepository
from .watchmode import WatchmodeClient, WatchmodeError, WatchmodeService, WatchmodeTitleNotFound
from .personalization import PersonalizationService
from .recommendations.digest import WeekendDigestService, format_weekend_digest
from .recommendations.runtime import build_ai_recommendation_service, build_recommendation_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="weekend-watch", description="Weekend Watch Agent local tools")
    parser.add_argument("command", choices=["health", "tmdb-trending", "availability", "watched", "history", "preferences", "weekend-candidates", "recommend", "digest"], help="Command to run")
    parser.add_argument("--time-window", choices=["day", "week"], default="week")
    parser.add_argument("--tmdb-id", type=int, help="TMDB ID for availability lookup")
    parser.add_argument("--media-type", choices=["movie", "tv"], help="Disambiguate the TMDB title")
    parser.add_argument("--title", help="Movie or TV title to resolve through TMDB")
    parser.add_argument("--rating", type=int, help="Personal rating from 1 to 10")
    parser.add_argument("--limit", type=int, help="Maximum results (digest defaults to 8; candidate commands to 20)")
    parser.add_argument("--request", help="Optional natural-language request for recommendations")
    parser.add_argument("--save", action="store_true", help="Save a digest Markdown file")
    parser.add_argument("--digest-dir", type=Path, default=Path("data/digests"),
                        help="Directory for saved digests (default: data/digests)")
    args = parser.parse_args()
    if args.command == "health":
        settings = get_settings()
        initialize_database(settings.database_path)
        print(f"OK: database initialized at {settings.database_path}")
    elif args.command == "tmdb-trending":
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, TmdbClient(
                api_key=settings.tmdb_api_key, access_token=settings.tmdb_access_token
            ) as client:
                service = TmdbService(client, TitleRepository(db))
                results = service.trending(time_window=args.time_window)
                for title in results.results:
                    service.save(title)
                    year = f" ({title.release_date[:4]})" if title.release_date else ""
                    rating = f"{title.rating:.1f}/10" if title.rating is not None else "unrated"
                    print(f"[{title.media_type}] {title.title}{year} — {rating}")
                print(f"Retrieved {len(results.results)} trending titles.")
        except (TmdbError, ValueError) as exc:
            print(f"TMDB error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "availability":
        if args.tmdb_id is None:
            parser.error("availability requires --tmdb-id")
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, WatchmodeClient(
                api_key=settings.watchmode_api_key or ""
            ) as client:
                service = WatchmodeService(
                    client, TitleRepository(db), WatchmodeRepository(db),
                    region=settings.watchmode_region,
                    cache_ttl_hours=settings.watchmode_cache_ttl_hours,
                )
                options = service.availability_for_tmdb(args.tmdb_id, args.media_type)
                if not options:
                    print(f"No streaming availability found in {settings.watchmode_region.upper()}.")
                for item in options:
                    details = f" — {item.web_url}" if item.web_url else ""
                    if item.price:
                        details += f" ({item.price})"
                    print(f"{item.provider} [{item.provider_type}]{details}")
        except (WatchmodeError, WatchmodeTitleNotFound, ValueError) as exc:
            print(f"Watchmode error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "watched":
        if not args.title:
            parser.error("watched requires --title")
        if args.rating is not None and not 1 <= args.rating <= 10:
            parser.error("--rating must be from 1 to 10")
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, TmdbClient(
                api_key=settings.tmdb_api_key, access_token=settings.tmdb_access_token
            ) as client:
                movies = client.search_movies(args.title).results
                television = client.search_tv(args.title).results
                matches: list[Title] = movies + television
                if not matches:
                    raise TmdbError(f"No TMDB title found for {args.title!r}")
                # Prefer exact name matches, then the provider's popularity ranking.
                selected = max(matches, key=lambda item: (
                    item.title.casefold() == args.title.casefold(), item.popularity or 0
                ))
                service = TmdbService(client, TitleRepository(db))
                title_id = service.save(selected)
                PersonalizationService(db).add_watched_title(title_id, rating=args.rating)
                rating_text = f" with rating {args.rating}/10" if args.rating else ""
                print(f"Recorded watched: {selected.title} [{selected.media_type}]{rating_text}")
        except (TmdbError, ValueError) as exc:
            print(f"TMDB error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "history":
        settings = get_settings()
        initialize_database(settings.database_path)
        with connect(settings.database_path) as db:
            history = PersonalizationService(db).get_watch_history()
            if not history:
                print("Watch history is empty.")
            for row in history:
                rating = f" — {row['user_rating']}/10" if row["user_rating"] else ""
                print(f"{row['watched_at']}  {row['name']} [{row['media_type']}]{rating}")
    elif args.command == "preferences":
        settings = get_settings()
        initialize_database(settings.database_path)
        with connect(settings.database_path) as db:
            preferences = PersonalizationService(db).get_user_preferences()
            print(json.dumps(preferences.model_dump(), indent=2))
    elif args.command == "weekend-candidates":
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, TmdbClient(
                api_key=settings.tmdb_api_key, access_token=settings.tmdb_access_token
            ) as tmdb:
                with ExitStack() as stack:
                    pipeline = build_recommendation_pipeline(settings, db, tmdb, stack)
                    candidates = pipeline.get_weekend_candidates(
                        limit=max(0, args.limit if args.limit is not None else 20)
                    )
                if not candidates:
                    print("No weekend candidates matched your current preferences.")
                for candidate in candidates:
                    categories = ", ".join(candidate.categories)
                    print(f"{candidate.title.title} [{candidate.title.media_type}] "
                          f"Match {candidate.match_score:.1f} — {categories}")
                    for reason in candidate.reasons:
                        print(f"  • {reason}")
        except (TmdbError, WatchmodeError, WatchmodeTitleNotFound, ValueError) as exc:
            print(f"Recommendation error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "recommend":
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, TmdbClient(
                api_key=settings.tmdb_api_key, access_token=settings.tmdb_access_token
            ) as tmdb:
                with ExitStack() as stack:
                    pipeline = build_recommendation_pipeline(settings, db, tmdb, stack)
                    service = build_ai_recommendation_service(settings, pipeline, stack)
                    recommendations = service.recommend(
                        request=args.request or "Recommend what I might enjoy this weekend.",
                        limit=max(0, args.limit if args.limit is not None else 20),
                    )
                if not recommendations:
                    print("No weekend candidates matched your current preferences.")
                for item in recommendations:
                    rating = f" — {item.rating:.1f}/10" if item.rating is not None else ""
                    year = f" ({item.release_date[:4]})" if item.release_date else ""
                    services = f" — Streaming: {', '.join(item.streaming_services)}" if item.streaming_services else ""
                    source = {"claude": "Claude", "groq": "Groq",
                              "deterministic": "Deterministic fallback"}[item.explanation_source]
                    print(f"[{item.category}] {item.title}{year} [{item.media_type}]{rating} — {source}")
                    print(f"  {item.recommendation_reason}{services}")
        except (TmdbError, WatchmodeError, WatchmodeTitleNotFound, ValueError) as exc:
            print(f"Recommendation error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.command == "digest":
        settings = get_settings()
        try:
            initialize_database(settings.database_path)
            with connect(settings.database_path) as db, TmdbClient(
                api_key=settings.tmdb_api_key, access_token=settings.tmdb_access_token
            ) as tmdb:
                with ExitStack() as stack:
                    pipeline = build_recommendation_pipeline(settings, db, tmdb, stack)
                    ai_recommender = build_ai_recommendation_service(settings, pipeline, stack)
                    digest = WeekendDigestService(pipeline, ai_recommender).generate(
                        limit=args.limit if args.limit is not None else 8,
                        request=args.request or "Recommend what I should watch this weekend.",
                    )
            markdown = format_weekend_digest(digest)
            print(markdown, end="")
            if args.save:
                args.digest_dir.mkdir(parents=True, exist_ok=True)
                filename = f"weekend-watch-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md"
                destination = args.digest_dir / filename
                destination.write_text(markdown, encoding="utf-8")
                print(f"Saved digest to {destination}")
        except (TmdbError, WatchmodeError, WatchmodeTitleNotFound, ValueError) as exc:
            print(f"Digest error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
