# Development Status

## Chunk 1: Local application foundation

Completed the initial Python package structure, Python 3.11 project metadata, `.env`-based settings, SQLite schema and initialization, repository methods for titles/watch history/preferences/recommendation history, and the `weekend-watch health` CLI command. Added setup documentation and initial pytest coverage for schema creation, provider-agnostic title identity, repository persistence, and media type constraints.

## Chunk 2: TMDB integration

Added an isolated synchronous `httpx` TMDB v3 client, application service, and typed Pydantic models for normalized movie/TV titles, genres, search results, and release/content-rating information. The client supports movie/TV search and details, trending, discover, genre lists, movie release dates, and TV content ratings. It accepts `TMDB_API_KEY` or `TMDB_ACCESS_TOKEN`, retries transient transport/429/5xx failures, and reports permanent errors clearly.

Normalized titles map into the generic provider/external-id repository. The title schema now has vote count, poster/backdrop paths, and original language columns; initialization safely adds these columns to databases created in chunk 1. Added `weekend-watch tmdb-trending [--time-window day|week]`, which prints and persists results. `.env.example` and README now document both TMDB credential variables.

Validation completed with Python 3.11: `pytest` passed (10 tests), including mocked HTTP calls without real credentials, retries, error handling, persistence, and schema migration. The health CLI succeeds. The trending CLI reports a clear missing-credentials error when neither TMDB variable is set.

## Chunk 3: Watchmode availability

Added a standalone `httpx` Watchmode v1 client and application service. It supports title-name search, TMDB-ID search/mapping, region-specific source availability, provider labels/categories, URLs, and optional prices. Watchmode IDs are explicitly stored in a mapping table and are never assumed to equal TMDB IDs.

Added typed `StreamingAvailability` and Watchmode title models, SQLite mapping/availability/cache timestamp tables, per-title/per-region caching (including empty availability responses), and configurable `WATCHMODE_REGION` (default `US`) and `WATCHMODE_CACHE_TTL_HOURS` (default 24). Added `WATCHMODE_API_KEY` to settings and `.env.example`, plus `weekend-watch availability --tmdb-id <id> [--media-type movie|tv]`.

Validation completed with Python 3.11: `pytest` passed (14 tests), using mocked Watchmode HTTP responses only. Tests cover config, separate TMDB/Watchmode IDs, provider types/URLs/prices, availability persistence, cache behavior, and empty-result caching. The CLI argument path was exercised and returns a clear missing-key error without making a request.

## Chunk 4: Personalization data layer

Added single-user typed preferences, watch activity, title feedback, watch-later, and recommendation-history service/repository methods. Watched history records a 1–10 rating, like state, notes, and optional `watched_at`; likes/dislikes and ratings can also be updated independently. Watch-later entries are idempotent, and recommendation history uses the existing table.

Preferences now cover preferred/excluded genres, minimum rating, preferred/excluded streaming services, languages, movies/TV toggles, and new release/trending/hidden gem toggles. SQLite initialization upgrades the former 1–5 watch rating constraint to 1–10 while preserving old history and expands existing preference tables in place. `has_watched(provider, external_id)` queries the normalized title identity.

Added `weekend-watch watched --title <name> [--rating 1-10]`, which searches TMDB movie and TV results, saves the normalized title, and records the watch. Added `history` and `preferences` display commands. Validation completed on Python 3.11: `pytest` passed (22 tests), including legacy migrations, activity/preferences behavior, normalized identity matching, and mocked-TMDB CLI resolution.

## Chunk 5: Deterministic recommendation pipeline

Added TMDB candidate discovery for recent releases, trending movies/TV, highly rated titles, and hidden-gem candidates. Candidate sets deduplicate by normalized TMDB identity, then filter watched and disliked titles, disabled media types, excluded genres, minimum rating, preferred languages, and preferred/excluded streaming services. Availability uses Watchmode through an injectable lookup or fresh local cache.

Added structured `WeekendCandidate` results with categories `NEW_RELEASE`, `TRENDING`, `HIGHLY_RATED`, and `HIDDEN_GEM`; deterministic normalized score components; configurable nonnegative `RecommendationWeights`; and user-facing match reasons. Hidden gems favor strong preferred-genre matches, ratings of at least 7, reasonable vote counts, and lower popularity. Added `weekend-watch weekend-candidates [--limit N]`, which prints categories, match score, and reasons without labeling the score as objective quality.

Validation completed with Python 3.11: `pytest` passed (32 tests), including watched/disliked exclusion, genre/rating/provider/media filtering, hidden-gem behavior, deduplication, configurable scoring, and mocked CLI output. Python compile checks also pass.

## Chunk 6: AI recommendation layer (Groq and Claude)

Added optional `GROQ_API_KEY` and `ANTHROPIC_API_KEY` settings plus configurable model names. The Groq client uses schema-constrained JSON output to filter/classify IDs from the deterministic candidate set; Claude uses schema-constrained JSON to select/rank candidate IDs and return constrained evidence codes with confidence. The final Recommendation model contains normalized title facts, category, concise explanation, confidence, and source metadata. Ratings, dates, genres, and streaming providers are copied from TMDB/Watchmode candidates. Explanations are rendered from the selected evidence code and structured facts; raw model prose, plot/cast claims, and chain-of-thought are not exposed.

Added `weekend-watch recommend` / `python -m weekend_watch.cli recommend`, with optional `--request` and `--limit`. Groq failures skip filtering; Claude failures or missing credentials return deterministic recommendations. Missing Groq credentials lets Claude work directly on the deterministic candidate set. Watchmode availability is loaded when configured and already-cached data is still available otherwise.

Validation completed on Python 3.11 with mocked HTTP responses only. AI tests cover settings, structured request schemas, fact grounding, provider-stage behavior, invalid IDs, and fallbacks; CLI tests cover deterministic fallback without AI credentials. `python -m pytest` passed (39 tests), `python -m compileall -q src` passed, and `python -m weekend_watch.cli --help` lists the new `recommend` command.

## Chunk 7: Local MCP server

Added the official MCP Python SDK v2 dependency (`mcp>=2,<3`) and a `weekend-watch-mcp` console command / `python -m weekend_watch.mcp_server` stdio entry point. It registers 16 high-level tools for TMDB searches/details/trending, Watchmode availability, normalized watched/not-interested/watch-later actions, watch history, preferences, recommendations, and a categorized digest. MCP-facing orchestration delegates to the existing TMDB/Watchmode clients and services, repositories, personalization service, and recommendation pipeline; it contains no duplicate provider or SQL implementation.

Created an injectable `WeekendWatchTools` application-service facade so tool behavior can be tested independently and the MCP layer returns structured JSON-friendly dictionaries. Resolved title actions persist normalized TMDB identity before personalization operations. Provider errors crossing the MCP boundary use sanitized messages so API-key-bearing request URLs are not exposed. Watchmode and AI tools remain optional/configured by the same `.env` variables as the existing application.

README includes stdio startup instructions, Codex CLI/Desktop/IDE configuration, and credential notes. Tests cover high-level tool behavior, watch/profile persistence, configuration errors, registered tools, and structured output through the official SDK's in-process client. Validation completed on Python 3.11: `python -m pytest` passed (43 tests), `python -m compileall -q src` passed, and the installed `weekend-watch-mcp` entry point started successfully over stdio. The official MCP Python SDK 2.2.0 was installed in the local virtual environment for validation.

## Chunk 8: Weekend Watch Digest

Added typed digest and digest-item models plus a clean Markdown formatter. The digest has Recommended, New This Week, and Hidden Gems sections; chooses a configurable number of distinct candidates (default 8); and includes media type, source rating/date/genres, synopsis shortened to 320 characters, confirmed provider names, preference match reasons, and category. Missing ratings/dates/synopses/providers render as unavailable rather than being inferred. “New This Week” requires a TMDB release/air date between Monday and the current date; watched and not-interested exclusions come from the existing deterministic pipeline.

Added `weekend-watch digest [--limit N] [--save] [--digest-dir PATH]` (also `python -m weekend_watch.cli digest`). Saving is opt-in and creates timestamped Markdown files in `data/digests/` by default. The MCP `get_weekend_digest` tool now returns the same structured digest and Markdown. Consolidated CLI/MCP pipeline assembly in a shared runtime helper.

README documents digest generation and grounded-date/provider behavior. Python 3.11 validation: `python -m pytest` passed (47 tests), `python -m compileall -q src` passed, and CLI help lists `digest` and save options.

## Chunk 9: Local HTTP API for n8n

Added a FastAPI application with `GET /health` (initializes/checks the local SQLite database) and `POST /api/weekend-digest`. The digest endpoint delegates to the existing application facade, which loads preferences, discovers and filters TMDB candidates, excludes watched/not-interested titles, checks Watchmode live availability when configured (otherwise uses its existing cache), applies optional Groq/Claude reasoning, and returns the structured digest plus Markdown. Requests accept a bounded `limit` and a natural-language `request`; validation and sanitized provider errors return HTTP error responses.

Added `weekend-watch-api` / `python -m weekend_watch.api`, configurable `WEEKEND_WATCH_API_HOST` and `WEEKEND_WATCH_API_PORT` (localhost:8000 by default), FastAPI/Uvicorn dependencies, endpoint tests, and README instructions for a Friday n8n Schedule Trigger → HTTP Request → delivery node workflow. No email credentials or cloud deployment were added. Replaced populated credential values previously present in `.env.example` with empty placeholders; use local `.env` for actual credentials.

Validation on Python 3.11: `python -m pytest` passed (50 tests), `python -m compileall -q src` passed, and `git diff --check` passed. The environment denied a local socket bind during a direct Uvicorn startup smoke check, so endpoint behavior was verified through FastAPI's in-process test client instead.

## Production review and current runnable state

### Completed features

- Local Python 3.11+ CLI, SQLite persistence, TMDB movie/TV metadata and discovery, Watchmode ID mapping/availability caching, single-user preferences/watch history/feedback/watch-later, deterministic ranked candidate discovery, optional Groq filtering and Claude selection, Markdown digest, local MCP stdio server, and local n8n HTTP API.
- External provider models are normalized Pydantic models. Recommendation metadata is sourced from TMDB; confirmed streaming availability is sourced from Watchmode. Recommendation/digest structures distinguish those facts from Claude-assisted or deterministic explanation text. LLM output is constrained to IDs and evidence codes; ratings, dates, providers, and synopsis are not generated from LLM text.
- Provider transport errors no longer include raw request details in application errors. HTTPX request log query keys are redacted. Retries for transient TMDB/Watchmode failures are bounded; malformed TMDB/Watchmode rows are skipped or reported as sanitized provider errors. Failed Claude output falls back to deterministic recommendations; failed Groq filtering lets Claude see the complete candidate set. Watchmode failures use only unexpired cached data and otherwise leave availability unconfirmed.
- `.env` is ignored by Git; `.env.example` contains blank credentials; the local API binds to localhost by default. SQLite uses a 10-second busy timeout and WAL mode for file databases.

### Known limitations

- The application is single-user and local-first. The HTTP API has no authentication, so keep it bound to localhost unless access is otherwise protected. It is not cloud-deployed.
- Fresh recommendations require TMDB access. The app does not maintain an offline catalog/discovery snapshot; a TMDB outage produces a sanitized operation failure. Watchmode and AI are optional. Without Watchmode, availability is shown only when an unexpired local cache entry exists; a preferred-provider filter can consequently yield no matches.
- TMDB discovery currently reads the first result page only. Provider calls have bounded retries but no shared request queue or global rate limiter. AI provider calls do not retry; they fall back safely.
- SQLite schema upgrades are incremental but not managed by a versioned migration framework. WAL plus a busy timeout supports ordinary local concurrency; this is not a multi-user database service.
- Genre labels depend on TMDB's genre endpoint. If it fails, titles are retained without genre labels for that client session, which can reduce genre personalization.
- Streaming availability changes over time and is only as current as the Watchmode fetch timestamp/cache policy. No guarantee is made for titles with unconfirmed availability.

### Future improvements

- Add versioned SQLite migrations, candidate/catalog caching for offline or degraded recommendations, and optional additional pages of discovery with a shared provider rate limiter.
- Add authentication if the HTTP API needs to bind beyond localhost; add an explicit freshness timestamp in digest display for provider availability.
- Add periodic tests for provider schema changes and an optional static type/lint toolchain.

### Exact local commands

From the repository root, create/install the environment with `python3.11 -m venv .venv`, `source .venv/bin/activate`, and `python -m pip install -e '.[dev]'`; copy `.env.example` to `.env` and add the required TMDB credential. Run the application/health check with:

```sh
python -m weekend_watch.cli health
```

Run the complete tests with:

```sh
python -m pytest
```

Start the MCP stdio server with:

```sh
python -m weekend_watch.mcp_server
```

Start the local n8n HTTP API with:

```sh
python -m weekend_watch.api
```

### Review validation

Python 3.11 validation: `python -m pytest` passed (58 tests); `python -m compileall -q src`, `python -m pip check`, and `git diff --check` passed. `python -m weekend_watch.cli health` initialized and checked `data/weekend_watch.db`. The sandbox blocks binding a live TCP socket, so `/health` and `POST /api/weekend-digest` are verified with FastAPI's in-process test client; live HTTP listener startup must be confirmed in the user's local environment.

## Follow-up fixes

- Watchmode uses the `X-API-Key` header and media-type-specific TMDB search fields. A conflicting stale local type mapping is refreshed; duplicate provider rows are collapsed before persistence; `tv_movie` results normalize as movies.
- Watchmode lookup warnings now include the sanitized provider error detail while continuing to use unexpired cached availability when available.
- Recommendation provider defaults to Groq without initializing Claude. Explicit requested genres are checked against TMDB genre metadata and stay enforced during deterministic fallback. Set `AI_RECOMMENDATION_PROVIDER` to `claude`, `auto`, or `deterministic` to change the mode.
- Recovered 33 zero-byte tracked source, test, and documentation files from the last committed version after the health command failed to import `get_settings`. Preserved the local SQLite database file; `health` initialized it successfully.
- Removed generated `src/weekend_watch_agent.egg-info/` metadata from version control; setuptools recreates it from `pyproject.toml`, and `.gitignore` excludes it.

Python 3.11 validation after recovery: `python -m weekend_watch.cli health` succeeded, `python -m pytest -q` passed (68 tests), `git diff --check` passed, and `.env` is ignored by Git.
