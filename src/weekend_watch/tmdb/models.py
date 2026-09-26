"""Normalized, typed TMDB data used by the application."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Genre(BaseModel):
    id: int
    name: str


class Title(BaseModel):
    """Application view of a movie or TV title, independent of raw TMDB JSON."""

    model_config = ConfigDict(frozen=True)

    tmdb_id: int = Field(gt=0)
    title: str = Field(min_length=1)
    media_type: Literal["movie", "tv"]
    overview: str = ""
    release_date: str | None = None
    rating: float | None = Field(default=None, ge=0, le=10)
    vote_count: int = Field(default=0, ge=0)
    popularity: float | None = Field(default=None, ge=0)
    genres: list[str] = Field(default_factory=list)
    poster_path: str | None = None
    backdrop_path: str | None = None
    original_language: str | None = None

    @field_validator("title")
    @classmethod
    def title_must_contain_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @classmethod
    def from_tmdb(cls, data: dict, media_type: Literal["movie", "tv"],
                  genre_names: dict[int, str] | None = None) -> "Title":
        is_movie = media_type == "movie"
        genres = data.get("genres")
        if genres is not None:
            names = [genre["name"] for genre in genres if genre.get("name")]
        else:
            names = [(genre_names or {})[genre_id] for genre_id in data.get("genre_ids", [])
                     if genre_id in (genre_names or {})]
        return cls(
            tmdb_id=data["id"],
            title=data.get("title" if is_movie else "name") or data.get("original_title" if is_movie else "original_name") or "",
            media_type=media_type,
            overview=data.get("overview") or "",
            release_date=data.get("release_date" if is_movie else "first_air_date") or None,
            rating=data.get("vote_average"),
            vote_count=data.get("vote_count", 0),
            popularity=data.get("popularity"),
            genres=names,
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
            original_language=data.get("original_language"),
        )

    def to_repository_fields(self) -> dict:
        """Map to existing generic title repository without persisting raw responses."""
        return {
            "provider": "tmdb",
            "external_id": str(self.tmdb_id),
            "media_type": self.media_type,
            "name": self.title,
            "overview": self.overview,
            "release_date": self.release_date,
            "rating": self.rating,
            "vote_count": self.vote_count,
            "popularity": self.popularity,
            "genres": self.genres,
            "poster_path": self.poster_path,
            "backdrop_path": self.backdrop_path,
            "original_language": self.original_language,
        }


class ReleaseInfo(BaseModel):
    country_code: str
    release_date: str | None = None
    release_type: int | None = None
    certification: str | None = None
    note: str | None = None


class SearchResults(BaseModel):
    page: int = 1
    total_pages: int = 0
    total_results: int = 0
    results: list[Title] = Field(default_factory=list)
