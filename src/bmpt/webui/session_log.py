"""The current session's log, shared by logging and the settings viewer."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, quote_plus

from ..core.errors import ServiceError

LOG_LINES = 500
LOG_BYTES = 2 * 1024 * 1024
REDACTED = "[redacted]"
_CREDENTIAL = re.compile(
    r"""(?ix)(?<![\w-])(["']?(?:[\w-]*(?:token|secret|password|passwd|api[_-]?key)|authorization|cookie|set-cookie)["']?\s*[:=]\s*)
        ("[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&]+)"""
)
_QUERY = re.compile(r"(?i)([?&](?:t|token|key|access_token|session_token)=)[^\s&#\"']+")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=-]+")
_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)


def redact(text: str, secrets: set[str]) -> str:
    """Hide credentials in log text.

    Args:
        text: The log text.
        secrets: Secret values the App holds (a session token, for one). A value shorter than 8 characters is
            ignored; each is also hidden in its URL-quoted forms.

    Returns:
        The text with those secrets, access tokens in query strings, Bearer and Basic credentials, credential
        fields (token, secret, password, API key, cookie) and the user info of URLs replaced by ``[redacted]``.
    """
    for secret in sorted((s for s in secrets if len(s) >= 8), key=len, reverse=True):
        for value in {secret, secret.replace(" ", "+"), quote(secret, safe=""), quote_plus(secret)}:
            text = re.sub(r"(?<![\w./%+~-])" + re.escape(value) + r"(?![\w./%+~-])", REDACTED, text)
    text = _QUERY.sub(r"\1" + REDACTED, text)
    text = _BEARER.sub(r"\1 " + REDACTED, text)
    text = _CREDENTIAL.sub(r"\1" + REDACTED, text)
    return _USERINFO.sub(r"\1" + REDACTED + "@", text)


def tail(path: Path, secrets: set[str]) -> str:
    """Read the end of a log file, redacted, within 500 lines and 2 MiB.

    A first line cut by the byte limit is dropped, as a cut secret would escape redaction.

    Args:
        path: The log file.
        secrets: Secret values to hide, as ``redact`` takes them.

    Returns:
        The last lines of the file; an empty string when the file does not exist.

    Raises:
        ServiceError: ``library_io_error``, the file could not be read.
    """
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            start = max(0, size - LOG_BYTES)
            stream.seek(start)
            data = stream.read(LOG_BYTES)
        if start:
            data = data.partition(b"\n")[2]
        return redact(b"\n".join(data.splitlines()[-LOG_LINES:]).decode("utf-8", errors="replace"), secrets)
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise ServiceError("library_io_error", operation="read", file=path, error=str(error)) from error


class RedactingFormatter(logging.Formatter):
    """A log formatter that redacts secrets from every record it formats."""

    def __init__(self, secrets: set[str]):
        """Set the log line layout.

        Args:
            secrets: Secret values to hide, as ``redact`` takes them. The set is read at each record, so values
                added to it later are hidden too.
        """
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        """Format a record and redact the result."""
        return redact(super().format(record), self.secrets)


def open_run_log(path: Path, secrets: set[str], *, safe_mode: bool = False) -> logging.StreamHandler:
    """Open this run's log: rotate the earlier logs and write the environment before any other record.

    The log falls back to a temporary file, then to the standard error stream, when the folder cannot be written.

    Args:
        path: The log file; the two logs before it are kept as ``webui.1.log`` and ``webui.2.log`` beside it.
        secrets: Secret values to hide, as ``redact`` takes them.
        safe_mode: Whether the App runs in safe mode, which the environment header records.

    Returns:
        The handler to attach to the root logger, formatted with ``RedactingFormatter``.
    """
    from .diagnostics import COLLECTION_TIMEOUT, Reports, clean, configured_language, environment, private_text
    import time
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for source, target in (
            (path.with_name("webui.1.log"), path.with_name("webui.2.log")),
            (path, path.with_name("webui.1.log")),
        ):
            if source.exists():
                source.replace(target)
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError:
        try:
            import os

            fd, name = tempfile.mkstemp(prefix="BAMT-", suffix=".log")
            os.close(fd)
            handler = logging.FileHandler(name, mode="w", encoding="utf-8")
        except OSError:
            handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(secrets))

    def header_values():
        values = environment(safe_mode, configured_language(path.parent))
        app = values.pop("app")
        return {**clean(values, secrets), "app": app, "dataDir": private_text(str(path.parent), secrets)}

    header = Reports(path.parent).optional("environment", header_values, time.monotonic() + COLLECTION_TIMEOUT)
    try:
        handler.stream.write("Environment " + json.dumps(header, ensure_ascii=False) + "\n")
        handler.flush()
    except OSError:
        handler.close()
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter(secrets))
    return handler
