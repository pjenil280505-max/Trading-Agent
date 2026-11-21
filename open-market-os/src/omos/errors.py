"""Error hierarchy.

Every error carries a short machine code and a message written for a person.
The API turns these into JSON responses; stack traces never reach normal users.
"""

from __future__ import annotations


class OmosError(Exception):
    code = "error"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}


class ValidationError(OmosError):
    code = "invalid_input"
    http_status = 422


class NotFound(OmosError):
    code = "not_found"
    http_status = 404


class Conflict(OmosError):
    code = "conflict"
    http_status = 409


class PermissionDenied(OmosError):
    code = "permission_denied"
    http_status = 403


class AuthRequired(OmosError):
    code = "auth_required"
    http_status = 401


class RateLimited(OmosError):
    code = "rate_limited"
    http_status = 429


class DataUnavailable(OmosError):
    """A data source could not supply data. Never replaced with made-up values."""

    code = "data_unavailable"
    http_status = 503


class ProviderError(DataUnavailable):
    code = "provider_error"


class ScriptError(ValidationError):
    code = "script_error"


class RiskRejected(OmosError):
    code = "risk_rejected"
    http_status = 403


class TradingHalted(RiskRejected):
    code = "trading_halted"


class InvalidState(Conflict):
    code = "invalid_state"


class SecretStoreError(OmosError):
    code = "secret_store_error"
    http_status = 500
