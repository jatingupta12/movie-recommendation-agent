# Weekend Watch Agent

A personal, local-first movie and TV recommendation agent. It provides SQLite persistence, TMDB metadata, Watchmode streaming availability, deterministic candidate discovery, optional Groq/Claude recommendation reasoning, and an MCP server for Codex and other local MCP clients.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the CLI, API, and MCP execution flows and the shared recommendation pipeline.

## Requirements and setup

- Python 3.11 or newer
- `pip`

From the repository root, create an isolated environment and install the declared dependencies:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp -n .env.example .env
```

Edit `.env` and add credentials only for services you plan to use. Never put credentials in source files or commit `.env`; the example contains blank credential values. `TMDB_API_KEY` or `TMDB_ACCESS_TOKEN` is required for discovery and title search. `WATCHMODE_API_KEY` is optional and enables live availability refresh; previously cached availability can still be used without it. `AI_RECOMMENDATION_PROVIDER` defaults to `groq`, so recommendations use Groq without calling Claude. Set it to `claude` or `auto` to enable Claude final selection (with Groq prefiltering when configured), or `deterministic` to disable AI. `GROQ_API_KEY` and `ANTHROPIC_API_KEY` are only required for selected provider stages; unavailable AI falls back to deterministic ranking. Explicit requested genres are checked against TMDB genres before selection and remain enforced in fallback. `WEEKEND_WATCH_DATABASE_PATH` selects the SQLite database (default `data/weekend_watch.db`). `WATCHMODE_REGION` defaults to `US`; `WATCHMODE_CACHE_TTL_HOURS` defaults to 24. Override provider models with `ANTHROPIC_MODEL` and `GROQ_MODEL` if needed.

The HTTP listener defaults to `WEEKEND_WATCH_API_HOST=127.0.0.1` and `WEEKEND_WATCH_API_PORT=8000`. The API has no authentication and is intended for local use; keep it on localhost unless network access is separately restricted. Binding to `0.0.0.0` can expose it to other machines on the network.

Initialize/check the database and run the CLI:

```sh
python -m weekend_watch.cli health
python -m weekend_watch.cli digest
```

The health command works without TMDB credentials. Generating a fresh digest requires TMDB credentials.

To run health, generate a digest, and then start the local API and MCP processes together, run:

```sh
./run-local.sh
```

The script uses `.venv/bin/python` when available, otherwise `python3`; press Ctrl-C to stop both services. The MCP server uses stdio, so Codex normally launches its own MCP process from its MCP configuration rather than connecting to the background process started by this helper. The helper starts it as requested, but that instance is not a substitute for Codex's configured stdio connection.

If Claude is missing or unavailable, recommendations fall back to the deterministic pipeline. If Groq is missing or unavailable, Claude receives the complete deterministic candidate set. AI never supplies factual title details: normalized metadata comes from TMDB and confirmed streaming availability comes from Watchmode. Structured recommendations label these sources separately from Claude-assisted or deterministic explanation text. The app renders concise explanations from validated reason codes and structured facts instead of accepting model-written factual claims.

## Health check and TMDB

```sh
weekend-watch health
weekend-watch tmdb-trending
weekend-watch tmdb-trending --time-window day
# equivalent module invocation
python -m weekend_watch.cli health
```

The health command initializes/validates the local database schema. TMDB supports normalized movie/TV search and details, trending, discover, genre lists, movie release dates, and TV content ratings.

## Streaming availability

```sh
weekend-watch availability --tmdb-id 123
weekend-watch availability --tmdb-id 123 --media-type tv
```

Watchmode resolves TMDB IDs through its search API and persists a separate Watchmode ID mapping. Availability is cached per title and region, including empty results.

## Personal watch profile

```sh
weekend-watch watched --title "Dune"
weekend-watch watched --title "Dune" --rating 9
weekend-watch history
weekend-watch preferences
```

The personalization layer supports likes/dislikes, notes, watch dates, watch-later, ratings, recommendation history, and preferences for genres, services, languages, media types, and recommendation categories. Titles use normalized provider/external-ID identity.

## Recommendations

See a deterministic ranked candidate list:

```sh
weekend-watch weekend-candidates
weekend-watch weekend-candidates --limit 10
```

The pipeline discovers new releases, trending titles, highly rated titles, and hidden gems; it removes watched/disliked titles and applies preference filters before calculating a transparent match score.

Get personalized recommendations with optional AI selection and concise explanations:

```sh
weekend-watch recommend
weekend-watch recommend --request "Something smart and funny for tonight" --limit 8
# or: python -m weekend_watch.cli recommend
```

By default, Groq selects and filters deterministic candidates without invoking Claude. Set `AI_RECOMMENDATION_PROVIDER=auto` or `claude` to use the two-stage Groq → Claude flow. Both stages use structured JSON. Claude emits a candidate ID, an evidence category, and confidence; the app renders explanations from structured facts rather than accepting free-form factual claims. No internal reasoning is returned or stored.

Generate a three-section Markdown digest (8 titles by default) and optionally save it:

```sh
weekend-watch digest
weekend-watch digest --limit 10 --save
weekend-watch digest --limit 6 --save --digest-dir data/digests
# equivalent: python -m weekend_watch.cli digest
```

“New This Week” uses the TMDB release/air date and the current Monday-through-today calendar week; titles without a confirming date are not placed there. Ratings and synopsis come from TMDB. Provider names appear only when Watchmode confirms availability for the configured region. The digest always states that watched and not-interested titles were excluded. Saved Markdown uses timestamped filenames in `data/digests/` by default.

## n8n automation API

The local HTTP API exposes the same digest pipeline for workflow automation. It loads saved preferences, discovers and filters candidates (including watched/not-interested exclusions), checks Watchmode availability when configured (or uses cached availability), applies optional Groq/Claude reasoning, then returns the structured digest and Markdown.

Run it from the project directory so `.env` is loaded:

```sh
python -m weekend_watch.api
# equivalent installed command: weekend-watch-api
```

By default it listens only on `127.0.0.1:8000`. `GET http://127.0.0.1:8000/health` checks the local database. Generate a digest with:

```sh
curl -X POST http://127.0.0.1:8000/api/weekend-digest \
  -H 'Content-Type: application/json' \
  -d '{"limit":8,"request":"Recommend what I should watch this weekend."}'
```

The response has `digest` (structured sections/items) and `markdown` fields. In n8n, create a **Schedule Trigger** for Friday afternoon, add an **HTTP Request** node using `POST` and the endpoint above with a JSON body such as `{"limit":8}`, then connect the result to the delivery node you choose and use `{{$json.markdown}}` as its content. This project does not configure email credentials or deploy the API to the cloud.

For n8n running on the same computer, call the local endpoint directly. If n8n runs in a container, `127.0.0.1` points inside that container; on a trusted local network, configure `WEEKEND_WATCH_API_HOST=0.0.0.0` only when needed and use the host address reachable from n8n. `WEEKEND_WATCH_API_HOST` and `WEEKEND_WATCH_API_PORT` control the listener. TMDB credentials are required for fresh discovery; `WATCHMODE_API_KEY` enables live availability refresh, and `ANTHROPIC_API_KEY` / `GROQ_API_KEY` enable the optional AI stages. Missing AI credentials use deterministic recommendations.

## Tests

```sh
python -m pytest
```

Tests use mocked provider responses and do not require real API keys.

## MCP server

Install the project dependencies, set TMDB credentials in `.env`, then run the local stdio server from the project directory:

```sh
python -m weekend_watch.mcp_server
# equivalent installed command: weekend-watch-mcp
```

The server exposes high-level search, details, trending, availability, watch-profile, preferences, recommendation, and digest tools. TMDB credentials are needed for catalog tools. `WATCHMODE_API_KEY` enables live streaming availability; `ANTHROPIC_API_KEY` and `GROQ_API_KEY` enable the optional AI recommendation stages. Missing AI credentials use the deterministic recommendation fallback. The MCP tools return normalized JSON data and never include configuration secrets.

### Codex

Codex CLI, the desktop app, and the IDE extension share MCP configuration. From the project directory, register the local stdio server with:

```sh
codex mcp add weekend-watch -- /bin/zsh -lc 'cd /absolute/path/to/MySmallAgent && exec /absolute/path/to/MySmallAgent/.venv/bin/python -m weekend_watch.mcp_server'
codex mcp list
```

Replace both path prefixes with this checkout's absolute path. The shell command changes into the project first so the process loads `.env`. Or open Codex Settings → MCP servers → Add server, choose STDIO, set the command to the project's `.venv/bin/python`, arguments to `-m weekend_watch.mcp_server`, and working directory to the project root. Restart/reload Codex after adding it. Project-scoped config is also supported in `.codex/config.toml` for trusted projects.

For another MCP-compatible client, configure a local stdio server with the same command, arguments, and working directory. Keep `.env` private and out of version control.
