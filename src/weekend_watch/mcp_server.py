"""Local stdio MCP server exposing high-level Weekend Watch operations."""

from typing import Any, Literal

from mcp.server import MCPServer

from .mcp_tools import WeekendWatchTools


def create_server(tools: WeekendWatchTools | None = None) -> MCPServer:
    """Create the official SDK server; injectable tools keep registration testable."""
    actions = tools or WeekendWatchTools()
    server = MCPServer("Weekend Watch Agent")

    @server.tool()
    def search_movies(query: str, limit: int = 10) -> dict[str, Any]:
        """Search TMDB for movies and return normalized title data."""
        return actions.search_movies(query, limit)

    @server.tool()
    def search_tv(query: str, limit: int = 10) -> dict[str, Any]:
        """Search TMDB for TV series and return normalized title data."""
        return actions.search_tv(query, limit)

    @server.tool()
    def get_movie_details(title_or_id: str) -> dict[str, Any]:
        """Get normalized details for a movie by title or TMDB ID."""
        return actions.get_movie_details(title_or_id)

    @server.tool()
    def get_tv_details(title_or_id: str) -> dict[str, Any]:
        """Get normalized details for a TV series by title or TMDB ID."""
        return actions.get_tv_details(title_or_id)

    @server.tool()
    def get_trending_movies(limit: int = 10) -> dict[str, Any]:
        """Get trending movies from TMDB."""
        return actions.get_trending_movies(limit)

    @server.tool()
    def get_trending_tv(limit: int = 10) -> dict[str, Any]:
        """Get trending TV series from TMDB."""
        return actions.get_trending_tv(limit)

    @server.tool()
    def get_streaming_availability(
        title_or_id: str, media_type: Literal["movie", "tv"] | None = None
    ) -> dict[str, Any]:
        """Find US (or configured-region) streaming, rent, and buy options."""
        return actions.get_streaming_availability(title_or_id, media_type)

    @server.tool()
    def has_watched(title_or_id: str, media_type: Literal["movie", "tv"] | None = None) -> dict[str, Any]:
        """Check watch history by TMDB title identity."""
        return actions.has_watched(title_or_id, media_type)

    @server.tool()
    def mark_watched(
        title_or_id: str, rating: int | None = None, liked: bool | None = None,
        notes: str | None = None, media_type: Literal["movie", "tv"] | None = None,
    ) -> dict[str, Any]:
        """Record a watched movie or TV series, optionally with rating/like/notes."""
        return actions.mark_watched(title_or_id, rating, liked, notes, media_type)

    @server.tool()
    def mark_not_interested(
        title_or_id: str, media_type: Literal["movie", "tv"] | None = None
    ) -> dict[str, Any]:
        """Exclude a title from future recommendations."""
        return actions.mark_not_interested(title_or_id, media_type)

    @server.tool()
    def add_to_watch_later(
        title_or_id: str, media_type: Literal["movie", "tv"] | None = None
    ) -> dict[str, Any]:
        """Add a movie or TV series to the local watch-later list."""
        return actions.add_to_watch_later(title_or_id, media_type)

    @server.tool()
    def get_watch_history(limit: int = 50) -> dict[str, Any]:
        """List recently watched titles and recorded feedback."""
        return actions.get_watch_history(limit)

    @server.tool()
    def get_watch_later(limit: int = 50) -> dict[str, Any]:
        """List titles saved for later."""
        return actions.get_watch_later(limit)

    @server.tool()
    def get_user_preferences() -> dict[str, Any]:
        """Get this local user's recommendation preferences."""
        return actions.get_user_preferences()

    @server.tool()
    def get_weekend_recommendations(limit: int = 10) -> dict[str, Any]:
        """Get ranked personalized recommendations, with graceful AI fallback."""
        return actions.get_weekend_recommendations(limit)

    @server.tool()
    def get_weekend_digest(limit: int = 8,
                           request: str = "Recommend what I should watch this weekend.") -> dict[str, Any]:
        """Get a compact categorized weekend recommendation digest."""
        return actions.get_weekend_digest(limit, request)

    return server


def main() -> None:
    """Run the local MCP server over stdio for Claude Desktop or another host."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
