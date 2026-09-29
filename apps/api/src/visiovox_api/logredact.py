"""Keep bearer tokens out of the access log.

EventSource cannot send an Authorization header, so the progress stream takes
the Firebase ID token as `?access_token=`. The web client's comment promises
that URL "is never logged with its query string" - but uvicorn's access log
writes the full path, token included, for every progress stream opened. A log
line is not a place for a credential that is valid for an hour.
"""

from __future__ import annotations

import logging
import re

_TOKEN = re.compile(r"(access_token=)[^&\s\"]+")


def redact(text: str) -> str:
    return _TOKEN.sub(r"\1[redacted]", text)


class RedactTokens(logging.Filter):
    """Rewrite `access_token=` values in a record's message and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install() -> None:
    """Attach the filter to uvicorn's access logger. Idempotent."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokens) for f in access.filters):
        access.addFilter(RedactTokens())
