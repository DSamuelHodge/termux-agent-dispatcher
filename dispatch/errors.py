"""Structured error codes for the HTTP verb API (and MCP data.error.code)."""

from __future__ import annotations

from typing import Any


class AgentError(Exception):
    """Machine-readable dispatcher failure. HTTP handlers map http_status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        **extra: Any,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.extra = extra
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message, "code": self.code}
        body.update(self.extra)
        return body


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"error": message, "code": code}
    body.update(extra)
    return body


# Stable codes. HTTP status is conventional, not part of the code name.
UNAUTHORIZED = "UNAUTHORIZED"
NOT_FOUND = "NOT_FOUND"
UNKNOWN_VERB = "UNKNOWN_VERB"
INVALID_JSON = "INVALID_JSON"
INVALID_BODY = "INVALID_BODY"
INVALID_ARGS = "INVALID_ARGS"
INVALID_ROUTE = "INVALID_ROUTE"
MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
CONFIRM_DENIED = "CONFIRM_DENIED"
CONFIRM_PENDING = "CONFIRM_PENDING"
CONFIRM_NOT_FOUND = "CONFIRM_NOT_FOUND"
CIRCUIT_OPEN = "CIRCUIT_OPEN"
EXECUTION_FAILED = "EXECUTION_FAILED"
TIMEOUT = "TIMEOUT"
TIER_C_UNAVAILABLE = "TIER_C_UNAVAILABLE"
ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
