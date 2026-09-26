from pathlib import Path
import logging

from weekend_watch.security import protect_httpx_logs

ROOT = Path(__file__).resolve().parents[1]


def test_env_file_is_ignored_and_example_credentials_are_blank():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore

    credential_names = {
        "TMDB_API_KEY", "TMDB_ACCESS_TOKEN", "WATCHMODE_API_KEY",
        "ANTHROPIC_API_KEY", "GROQ_API_KEY",
    }
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value.strip()
    assert all(values.get(name) == "" for name in credential_names)


def test_httpx_request_logs_redact_provider_query_keys(caplog):
    protect_httpx_logs()
    caplog.set_level(logging.INFO, logger="httpx")
    secret = "do-not-log-this-key"
    logging.getLogger("httpx").info(
        'HTTP Request: GET https://provider.test/path?api_key=%s&query=title "HTTP/1.1 200 OK"',
        secret,
    )
    assert secret not in caplog.text
    assert "api_key=[REDACTED]" in caplog.text
    logging.getLogger("httpx").info(
        'HTTP Request: GET https://provider.test/path?apiKey=%s "HTTP/1.1 200 OK"', secret
    )
    assert secret not in caplog.text
    assert "apiKey=[REDACTED]" in caplog.text
