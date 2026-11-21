"""Logging with secret redaction.

Every handler gets :class:`RedactingFilter`, which masks bearer tokens, API
keys, passwords and our own token format before a record is written anywhere.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_PATTERNS = [
    # Authorization headers and bearer tokens
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s'\",]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    # key=value style secrets
    (re.compile(r"(?i)\b(api[_-]?key|apikey|secret|password|passphrase|token|access[_-]?key)(\s*[=:]\s*)['\"]?[^\s'\",&]{4,}"),
     r"\1\2[REDACTED]"),
    # our own token format
    (re.compile(r"\bomos_[A-Za-z0-9_-]{16,}"), "omos_[REDACTED]"),
    # common provider key shapes
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-[REDACTED]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), "sk-ant-[REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "AIza[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg = clean
            record.args = None
        return True


_configured = False


def setup_logging(level: str = "INFO", logs_dir: Path | None = None) -> None:
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactingFilter()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        fileh = logging.handlers.RotatingFileHandler(
            logs_dir / "omos.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fileh.setFormatter(fmt)
        fileh.addFilter(redactor)
        root.addHandler(fileh)
    # httpx logs full URLs (which can contain keys) at INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configured = True
