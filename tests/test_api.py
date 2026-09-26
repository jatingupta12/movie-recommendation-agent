from datetime import date

from fastapi.testclient import TestClient

from weekend_watch.api import create_app
from weekend_watch.config import Settings
from weekend_watch.recommendations.digest import WeekendDigest, format_weekend_digest


class FakeDigestTools:
    def __init__(self):
        self.call = None

    def get_weekend_digest(self, limit=8, request=""):
        self.call = {"limit": limit, "request": request}
        digest = WeekendDigest(generated_on=date(2026, 9, 25))
        return {"digest": digest.model_dump(mode="json"),
                "markdown": format_weekend_digest(digest)}


def test_weekend_digest_endpoint_delegates_and_returns_digest_and_markdown(tmp_path):
    fake = FakeDigestTools()
    settings = Settings(database_path=tmp_path / "api.sqlite")
    with TestClient(create_app(settings=settings, tools=fake)) as client:
        response = client.post("/api/weekend-digest", json={
            "limit": 6, "request": "A mystery for Friday night",
        })

    assert response.status_code == 200
    result = response.json()
    assert result["digest"]["generated_on"] == "2026-09-25"
    assert "# 🍿 Weekend Watch" in result["markdown"]
    assert fake.call == {"limit": 6, "request": "A mystery for Friday night"}


def test_health_endpoint_checks_local_database(tmp_path):
    settings = Settings(database_path=tmp_path / "health.sqlite")
    with TestClient(create_app(settings=settings, tools=FakeDigestTools())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert (tmp_path / "health.sqlite").exists()


def test_weekend_digest_endpoint_validates_limit(tmp_path):
    settings = Settings(database_path=tmp_path / "invalid.sqlite")
    with TestClient(create_app(settings=settings, tools=FakeDigestTools())) as client:
        response = client.post("/api/weekend-digest", json={"limit": 51})

    assert response.status_code == 422
