"""Errors raised by the services behind the web UI.

Services return data, not sentences: an error carries a code and parameters, and the front end shows the locale
string ``webui.error.<code>`` filled with the parameters.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .error_codes import code_of, level_of


class ServiceError(Exception):
    """A failure the user should see, identified by a code (e.g. ``"game_not_found"``)."""

    def __init__(self, code: str, **params: Any):
        super().__init__(code)
        self.code = code
        self.params = params

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code}
        if ref := code_of(self.code):
            result["ref"] = ref
        result["params"] = {k: _jsonable(v) for k, v in self.params.items()}
        return result

    def __repr__(self) -> str:
        return f"ServiceError({self.code!r}, {self.params!r})"


def log_service_error(logger: logging.Logger, error: ServiceError) -> None:
    """Record a service verdict once, where RPC or a worker hands it to the UI."""
    level = logging.INFO if level_of(error.code) == "notice" else logging.WARNING
    payload = error.to_json()
    if ref := payload.get("ref"):
        logger.log(level, "%s %s %s", ref, error.code,
                   json.dumps(payload["params"], ensure_ascii=False, separators=(",", ":")))
    else:
        logger.log(level, "%s", error.code)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)
