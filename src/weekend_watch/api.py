"""Small local HTTP API for workflow tools such as n8n."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .database import connect, initialize_database
from .mcp_tools import WeekendWatchTools
from .recommendations.digest import WeekendDigest


class WeekendDigestRequest(BaseModel):
    limit: int = Field(default=8, ge=0, le=50)
    request: str = Field(
        default="Recommend what I should watch this weekend.", min_length=1, max_length=1000
    )


class WeekendDigestResponse(BaseModel):
    digest: WeekendDigest
    markdown: str


def create_app(*, settings: Settings | None = None,
               tools: WeekendWatchTools | None = None) -> FastAPI:
    """Build the API, allowing service injection for local tests."""
    app = FastAPI(title="Weekend Watch Agent API", version="0.1.0")
    runtime_settings = settings or get_settings()
    actions = tools or WeekendWatchTools(runtime_settings)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            initialize_database(runtime_settings.database_path)
            with connect(runtime_settings.database_path) as database:
                database.execute("SELECT 1").fetchone()
        except Exception:
            raise HTTPException(status_code=503, detail="Local database is unavailable") from None
        return {"status": "ok", "database": "ok"}

    @app.post("/api/weekend-digest", response_model=WeekendDigestResponse)
    def weekend_digest(payload: WeekendDigestRequest) -> dict:
        try:
            return actions.get_weekend_digest(limit=payload.limit, request=payload.request)
        except (RuntimeError, LookupError) as exc:
            # The application facade emits sanitized provider/configuration errors.
            raise HTTPException(status_code=502, detail=str(exc)) from None

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("weekend_watch.api:app", host=settings.api_host,
                port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
