"""Logging estructurado (JSON) con redacción de secretos."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_SECRET_PATTERNS = [
    re.compile(r"(apiKey=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(x-auth-token[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.IGNORECASE),
    re.compile(r"(x-apisports-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.IGNORECASE),
    re.compile(r"(bot)\d+:[A-Za-z0-9_-]+"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(r"\1***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload["ctx"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if as_json else RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
