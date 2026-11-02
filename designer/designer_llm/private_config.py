"""Host-side settings and explicitly selected, non-persistent credentials.

This module never searches for secrets. No credential belongs in a config file,
request artifact, world save, blueprint, memory record, or normal log.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


DEFAULT_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_SENSITIVE_FIELDS = {
    "apikey", "accesstoken", "refreshtoken", "authorization", "password",
    "secret", "clientsecret", "privatekey", "credential", "credentials",
}
_TOKEN_TEXT = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{10,}|\bBearer\s+[A-Za-z0-9._~+/-]{8,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)


class PrivateConfigError(ValueError):
    """A safe code-only error: it never includes rejected input."""


def assert_no_credentials(value: Any) -> None:
    """Reject obvious secret fields/text before data enters public artifacts.

    This is defense in depth, not a universal secret detector. A provider also
    rejects its actual credential if echoed anywhere in a request or response.
    """
    def visit(item: Any, depth: int) -> None:
        if depth > 64:
            raise PrivateConfigError("public_data_too_deep")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise PrivateConfigError("public_data_invalid_key")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _SENSITIVE_FIELDS or _TOKEN_TEXT.search(key):
                    raise PrivateConfigError("credentials_forbidden")
                visit(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str) and _TOKEN_TEXT.search(item):
            raise PrivateConfigError("credentials_forbidden")

    visit(value, 0)


def _bounded_number(value: Any, low: float, high: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrivateConfigError(code)
    if not low <= value <= high:
        raise PrivateConfigError(code)
    return float(value)


def _bounded_int(value: Any, low: int, high: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PrivateConfigError(code)
    return value


@dataclass(frozen=True)
class ResponsesConfig:
    """Non-secret settings; model availability is not assumed or verified."""

    model: str
    endpoint: str = DEFAULT_RESPONSES_ENDPOINT
    allow_custom_endpoint: bool = False
    schema_name: str = "factorio_design_proposal"
    timeout_s: float = 30.0
    total_deadline_s: float = 45.0
    max_output_tokens: int = 4096
    max_retries: int = 0
    max_request_bytes: int = 262144
    max_response_bytes: int = 524288

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", self.model):
            raise PrivateConfigError("explicit_model_required")
        if not isinstance(self.endpoint, str) or len(self.endpoint) > 2048:
            raise PrivateConfigError("invalid_endpoint")
        if not isinstance(self.allow_custom_endpoint, bool):
            raise PrivateConfigError("invalid_custom_endpoint_flag")
        try:
            endpoint = urlsplit(self.endpoint)
            endpoint.port  # Validate malformed ports without exposing URL text.
        except ValueError:
            raise PrivateConfigError("invalid_endpoint") from None
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment
                or any(char.isspace() for char in self.endpoint)):
            raise PrivateConfigError("invalid_endpoint")
        if self.endpoint != DEFAULT_RESPONSES_ENDPOINT and not self.allow_custom_endpoint:
            raise PrivateConfigError("custom_endpoint_requires_opt_in")
        if not isinstance(self.schema_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.schema_name):
            raise PrivateConfigError("invalid_schema_name")
        _bounded_number(self.timeout_s, 0.1, 300.0, "invalid_timeout")
        _bounded_number(self.total_deadline_s, 0.1, 300.0, "invalid_deadline")
        _bounded_int(self.max_output_tokens, 1, 32768, "invalid_output_budget")
        _bounded_int(self.max_retries, 0, 2, "invalid_retry_budget")
        _bounded_int(self.max_request_bytes, 1024, 1048576, "invalid_request_budget")
        _bounded_int(self.max_response_bytes, 1024, 2097152, "invalid_response_budget")

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "ResponsesConfig":
        if not isinstance(settings, Mapping):
            raise PrivateConfigError("invalid_config")
        assert_no_credentials(settings)
        permitted = set(cls.__dataclass_fields__)
        if set(settings) - permitted:
            raise PrivateConfigError("unknown_config_field")
        try:
            return cls(**dict(settings))
        except TypeError:
            raise PrivateConfigError("explicit_model_required") from None


def load_private_config(path: str | Path) -> ResponsesConfig:
    """Read only a path explicitly supplied by the host; never discover keys."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise PrivateConfigError("config_too_large")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise PrivateConfigError("duplicate_config_key")
                result[key] = value
            return result

        settings = json.loads(raw, object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PrivateConfigError("config_unreadable") from None
    return ResponsesConfig.from_mapping(settings)


def validate_credential(value: Any) -> str:
    """Validate opaque Bearer material without returning it in errors."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise PrivateConfigError("credential_unavailable")
    return value


class EnvironmentCredentialSupplier:
    """Read one explicitly selected variable only when invoked by the host."""

    def __init__(self, variable_name: str) -> None:
        if not isinstance(variable_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", variable_name):
            raise PrivateConfigError("invalid_environment_variable")
        self._variable_name = variable_name

    def __call__(self) -> str:
        return validate_credential(os.environ.get(self._variable_name))

    def __repr__(self) -> str:
        return "EnvironmentCredentialSupplier(<explicit host variable>)"


class MaskedCredentialSupplier:
    """Local terminal prompt. The key is never saved; chat is not a key UI."""

    def __init__(self, prompt: Callable[[str], str] | None = None) -> None:
        self._prompt = prompt or getpass.getpass

    def __call__(self) -> str:
        try:
            with warnings.catch_warnings():
                # getpass may otherwise fall back to echoed stdin. Reject that
                # fallback before it reads a credential in a redirected shell.
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = self._prompt("Provider API key (masked, local only): ")
        except Exception:
            raise PrivateConfigError("masked_input_unavailable") from None
        return validate_credential(value)

    def __repr__(self) -> str:
        return "MaskedCredentialSupplier(<local masked prompt>)"
