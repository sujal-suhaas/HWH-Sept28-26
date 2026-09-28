"""Logging setup with secret redaction.

Tokens and API keys must never reach a log line, a CI log, or a screenshot.
"""

from __future__ import annotations

import logging
import sys

from src.config import Settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class _Redactor(logging.Filter):
    """Replace known secret values with a placeholder in log records."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # Longest first so a secret that contains another is fully replaced.
        self._secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***REDACTED***")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    secrets = [
        settings.hindsight_api_key.get_secret_value(),
        settings.groq_api_key.get_secret_value(),
    ]
    handler.addFilter(_Redactor(secrets))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
