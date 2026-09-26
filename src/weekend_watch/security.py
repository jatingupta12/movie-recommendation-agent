"""Small logging safeguards for provider credentials carried in URLs."""

import logging
import re


class ProviderCredentialRedactionFilter(logging.Filter):
    """Redact known query-string credential names from HTTPX log messages."""

    _credential = re.compile(r"(?i)(api_key|apikey)=([^&\s\"']+)")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = self._credential.sub(r"\1=[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def protect_httpx_logs() -> None:
    """Ensure even verbose HTTPX request logs cannot expose API query keys."""
    logger = logging.getLogger("httpx")
    if not any(isinstance(item, ProviderCredentialRedactionFilter) for item in logger.filters):
        logger.addFilter(ProviderCredentialRedactionFilter())
