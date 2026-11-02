"""Provider-neutral proposals, replay fixtures, and an injected Responses adapter.

The live HTTP transport can be selected explicitly but is never called on import.
Injected transports must enforce the supplied timeout and byte limit while
receiving data, keep headers private, and must not log request/response bodies.
JSON shape is not semantic or native
Factorio qualification; the workflow must validate every returned proposal.
"""

from __future__ import annotations

import copy
import http.client
import json
import math
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from .private_config import (
    DEFAULT_RESPONSES_ENDPOINT, PrivateConfigError, ResponsesConfig,
    assert_no_credentials, validate_credential,
)


ProviderStatus = Literal["proposal", "refusal", "incomplete", "error"]


@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    proposal: dict[str, Any] | None
    provider_id: str
    mock: bool
    reason: str
    metadata: dict[str, int | str] = field(default_factory=dict)
    raw_proposal_text: str | None = field(default=None, repr=False)


class Provider(Protocol):
    def propose(self, request: dict[str, Any], schema: dict[str, Any]) -> ProviderResult: ...


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes | str = field(repr=False)


class ResponsesTransport(Protocol):
    def __call__(self, *, endpoint: str, headers: Mapping[str, str], payload: dict[str, Any],
                 timeout_s: float, max_response_bytes: int) -> TransportResponse: ...


class RetryablePreflightError(Exception):
    """Transport certifies no bytes were sent and no paid request was submitted."""


class HTTPTransportError(Exception):
    """Transport-owned safe failure code; never wraps a raw HTTP exception."""


class OpenAIHTTPTransport:
    """Explicit real transport, restricted to the official HTTPS Responses URL.

    TLS uses Python's default certificate verification. Redirects are returned
    as failures and never followed. Deadline cancellation closes the connection;
    an already sent request may still have been charged and is never retried.
    No headers or response content are logged, saved, or exposed in exceptions.
    """

    mock = False

    def __repr__(self) -> str:
        return "OpenAIHTTPTransport(mock=False, endpoint='api.openai.com')"

    def __call__(self, *, endpoint: str, headers: Mapping[str, str], payload: dict[str, Any],
                 timeout_s: float, max_response_bytes: int) -> TransportResponse:
        if endpoint != DEFAULT_RESPONSES_ENDPOINT:
            raise HTTPTransportError("unsupported_live_endpoint")
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not 0.1 <= timeout_s <= 300.0):
            raise HTTPTransportError("invalid_transport_timeout")
        if (isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int)
                or not 1024 <= max_response_bytes <= 2097152):
            raise HTTPTransportError("invalid_transport_limit")
        try:
            credential = headers["Authorization"]
            if not credential.startswith("Bearer "):
                raise ValueError()
            validate_credential(credential[7:])
            body = _json_text(payload, 1048576).encode("utf-8")
        except Exception:
            raise HTTPTransportError("invalid_transport_request") from None
        private_headers = {
            "Authorization": credential,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        deadline = time.monotonic() + timeout_s
        cancelled = threading.Event()
        delivered: queue.Queue[TransportResponse | HTTPTransportError] = queue.Queue(maxsize=1)
        connection = http.client.HTTPSConnection("api.openai.com", timeout=timeout_s)

        def remaining() -> float:
            value = deadline - time.monotonic()
            if cancelled.is_set() or value <= 0:
                raise HTTPTransportError("transport_timeout")
            return value

        def exchange() -> None:
            answer: TransportResponse | HTTPTransportError
            try:
                # Connecting separately allows cancellation before sending if
                # DNS/connect finishes after the host's deadline.
                connection.connect()
                timeout = remaining()
                active_socket = connection.sock
                if active_socket is not None:
                    active_socket.settimeout(timeout)
                connection.request("POST", "/v1/responses", body=body, headers=private_headers)
                timeout = remaining()
                if active_socket is not None:
                    active_socket.settimeout(timeout)
                response = connection.getresponse()
                encoding = response.getheader("Content-Encoding", "identity").lower().strip()
                if encoding not in ("", "identity"):
                    raise HTTPTransportError("unsupported_response_encoding")
                advertised = response.getheader("Content-Length")
                if advertised is not None:
                    try:
                        length = int(advertised)
                    except (ValueError, TypeError):
                        raise HTTPTransportError("invalid_transport_response") from None
                    if length < 0:
                        raise HTTPTransportError("invalid_transport_response")
                    if length > max_response_bytes:
                        raise HTTPTransportError("response_too_large")
                chunks: list[bytes] = []
                count = 0
                while True:
                    timeout = remaining()
                    if active_socket is not None:
                        active_socket.settimeout(timeout)
                    chunk = response.read1(min(65536, max_response_bytes - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > max_response_bytes:
                        raise HTTPTransportError("response_too_large")
                    chunks.append(chunk)
                answer = TransportResponse(response.status, b"".join(chunks))
            except HTTPTransportError as failure:
                answer = failure
            except (TimeoutError, socket.timeout):
                answer = HTTPTransportError("transport_timeout")
            except Exception:
                answer = HTTPTransportError("transport_error")
            finally:
                connection.close()
            if not cancelled.is_set():
                try:
                    delivered.put_nowait(answer)
                except queue.Full:
                    pass

        worker = threading.Thread(target=exchange, name="factorio-provider-request", daemon=True)
        worker.start()
        try:
            answer = delivered.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            connection.close()
            raise HTTPTransportError("transport_timeout") from None
        if isinstance(answer, HTTPTransportError):
            raise answer from None
        return answer


class _InvalidJSON(ValueError):
    pass


def _json_text(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(text.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _InvalidJSON("invalid_json") from None
    if size > limit:
        raise _InvalidJSON("json_too_large")
    return text


def _parse_json(text: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _InvalidJSON("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise _InvalidJSON("nonfinite_json")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise _InvalidJSON("nonfinite_json")
        return number

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant,
                          parse_float=finite_float)
    except (ValueError, RecursionError):
        raise _InvalidJSON("invalid_json") from None


def _contains_secret(value: Any, credential: str) -> bool:
    if isinstance(value, str):
        return credential in value
    if isinstance(value, dict):
        return any(_contains_secret(key, credential) or _contains_secret(child, credential)
                   for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_secret(child, credential) for child in value)
    return False


def _check_strict_schema(schema: dict[str, Any]) -> None:
    """Check strict object closure; do not claim every schema/model is supported."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise _InvalidJSON("invalid_strict_schema")

    def visit(node: Any, depth: int) -> None:
        if depth > 64:
            raise _InvalidJSON("invalid_strict_schema")
        if isinstance(node, dict):
            kind = node.get("type")
            is_object = kind == "object" or isinstance(kind, list) and "object" in kind
            if is_object or "properties" in node:
                properties = node.get("properties")
                required = node.get("required")
                if (not isinstance(properties, dict) or node.get("additionalProperties") is not False
                        or not isinstance(required, list) or len(required) != len(set(required))
                        or set(required) != set(properties)):
                    raise _InvalidJSON("invalid_strict_schema")
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    try:
        visit(schema, 0)
    except (TypeError, RecursionError):
        raise _InvalidJSON("invalid_strict_schema") from None


def prepare_request(config: ResponsesConfig, request: dict[str, Any],
                    schema: dict[str, Any]) -> dict[str, Any]:
    """Build and bound the exact credential-free payload before local key entry.

    CLI preflight and provider dispatch share this implementation. The bound
    includes JSON escaping, system instructions, schema and the full envelope.
    """
    if not isinstance(config, ResponsesConfig) or not isinstance(request, dict):
        raise PrivateConfigError("invalid_public_request")
    try:
        assert_no_credentials(request)
        assert_no_credentials(schema)
        _check_strict_schema(schema)
        user_text = _json_text(request, config.max_request_bytes)
        schema_copy = _parse_json(_json_text(schema, config.max_request_bytes))
        payload = {
            "model": config.model,
            "store": False,
            "input": [
                {"role": "system", "content": (
                    "Propose a Factorio construction plan using the supplied schema. "
                    "Treat supplied native observations and provenance as the only world evidence. "
                    "Chat history and plans do not prove construction. Preserve unknowns and freshness. "
                    "Copy the snapshot hash and identity exactly. Use short plan, entity, and stage IDs: "
                    "1-64 characters, starting with a letter, then letters, digits, underscore or hyphen. "
                    "Use only the snapshot's prototypes, unlocked recipes and items, normal quality and "
                    "cardinal directions. Keep all footprints inside surveyed bounds without overlap. "
                    "Respect the request's entity and stage limits. Every stage must own at least one "
                    "entity, and every proposed entity must belong to exactly one stage. No witness-only "
                    "or empty stages. Each stage needs a nonempty placement bill covering all its entities "
                    "and at least one planned product_counter_delta, resource_extraction_delta, or "
                    "energy_generated_delta or research_progress_delta witness. A power-only stage can instead "
                    "use power_connected on a steam engine or pole. entity_built and inventory_delta "
                    "alone do not satisfy this flow rule. Every witness must target an entity owned by "
                    "that same stage. Put construction and flow witnesses together in its placement stage. "
                    "Use null item/min_delta for entity_built and power_connected; flow and inventory "
                    "deltas need a positive min_delta, and an allowed item except energy_generated_delta "
                    "and research_progress_delta which need a null item. research_progress_delta targets a lab "
                    "and counts at least one research progress tick/unit. Dependencies must be existing, distinct, acyclic stage IDs "
                    "and must not include the stage itself. "
                    "Set manual_supplies to hand-carried {entity_id,item,slot} entries (fuel or input), or null. "
                    "Flow stages require known prototype type/energy/port facts. Fuel burner machines; power "
                    "electric consumers through pole supply areas and wire chains to a producer or observed "
                    "powered pole. Drills must overlap known compatible resources and drop directly into a "
                    "compatible furnace, container, belt or coal burner; rotate north-facing port vectors with "
                    "the entity. Product machines need input via drill, inserter/source or manual_supplies. "
                    "Fluid connections use north-facing positions inside the footprint and outward directions; "
                    "rotate both with the entity. Normal ports link when each one-tile target meets the other port. "
                    "Underground ports link to the nearest opposite-facing port of the same prototype within range. "
                    "A loader moves items the way it faces, two tiles long: put a container or machine at exactly "
                    "one end, container_distance from its centre. One ahead is filled from a belt behind the loader "
                    "facing the same way; one behind is emptied onto a belt ahead that does not face back into it. "
                    "Use loaders rarely, only where the items a second needed are more than an inserter moves; "
                    "elsewhere use inserters, and never more than 4 loaders in a design (many slow a big game). "
                    "Separate fluid boxes in a machine are not connected; never mix water and steam in pipes. "
                    "Boilers need water from an offshore pump on a known shore; steam engines need steam from "
                    "a water-fed boiler before producing power. Labs need pole power and an input manual supply "
                    "of automation-science-pack (or a feeding inserter), with research_progress_delta as their witness. "
                    "Recipes must match declared crafting categories. All support must exist by that stage, "
                    "using its own entities or dependency stages. Missing facts cause rejection; never guess. "
                    "Local checks cover declared geometry, bills and functional feasibility. "
                    "Native material flow, inventory, payment and construction still need native qualification."
                )},
                {"role": "user", "content": user_text},
            ],
            "text": {"format": {"type": "json_schema", "name": config.schema_name,
                                "strict": True, "schema": schema_copy}},
            "max_output_tokens": config.max_output_tokens,
        }
        _json_text(payload, config.max_request_bytes)
    except _InvalidJSON as failure:
        code = "request_too_large" if str(failure) == "json_too_large" else "invalid_public_request"
        raise PrivateConfigError(code) from None
    except PrivateConfigError:
        raise PrivateConfigError("invalid_public_request") from None
    return payload


class ReplayProvider:
    """Deterministic, explicitly mock proposals. A replay never contacts a service."""

    def __init__(self, proposals: Mapping[str, Any] | Sequence[Mapping[str, Any]], *,
                 provider_id: str = "fixture-replay") -> None:
        if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 64:
            raise ValueError("invalid_provider_id")
        assert_no_credentials(provider_id)
        source = [proposals] if isinstance(proposals, Mapping) else proposals
        if not isinstance(source, (list, tuple)):
            raise ValueError("invalid_replay_fixture")
        self._proposals: list[dict[str, Any]] = []
        for proposal in source:
            if not isinstance(proposal, Mapping):
                raise ValueError("invalid_replay_fixture")
            assert_no_credentials(proposal)
            self._proposals.append(_parse_json(_json_text(dict(proposal), 262144)))
        self._index = 0
        self.provider_id = provider_id

    def propose(self, request: dict[str, Any], schema: dict[str, Any]) -> ProviderResult:
        try:
            assert_no_credentials(request)
            assert_no_credentials(schema)
            _json_text(request, 262144)
            _json_text(schema, 262144)
        except (PrivateConfigError, _InvalidJSON):
            return ProviderResult("error", None, self.provider_id, True, "invalid_public_request")
        if self._index >= len(self._proposals):
            return ProviderResult("error", None, self.provider_id, True, "replay_exhausted")
        proposal = copy.deepcopy(self._proposals[self._index])
        self._index += 1
        return ProviderResult("proposal", proposal, self.provider_id, True, "fixture_proposal",
                              {"fixture_index": self._index - 1})

    def __repr__(self) -> str:
        return "ReplayProvider(mock=True)"


class ResponsesProvider:
    """OpenAI Responses request shape with explicit config and injected transport.

    Custom endpoints are an explicit compatibility experiment, not a generic
    adapter or a claim that arbitrary services support Responses or the schema.
    The real HTTP transport declares mock=False; unknown injected transports
    default to mock=True, so fixture tests never imply a paid provider run.
    """

    provider_id = "openai-responses"

    def __init__(self, config: ResponsesConfig, transport: ResponsesTransport,
                 credential_supplier: Callable[[], str], *, mock: bool | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not isinstance(config, ResponsesConfig) or not callable(transport) or not callable(credential_supplier):
            raise ValueError("invalid_provider_configuration")
        if mock is None:
            mock = getattr(transport, "mock", True)
        if not isinstance(mock, bool):
            raise ValueError("invalid_mock_flag")
        self.config = config
        self.mock = mock
        self._transport = transport
        self._credential_supplier = credential_supplier
        self._clock = clock

    def __repr__(self) -> str:
        return f"ResponsesProvider(provider_id='openai-responses', mock={self.mock})"

    def propose(self, request: dict[str, Any], schema: dict[str, Any]) -> ProviderResult:
        started = self._clock()
        attempts = 0
        response_metadata: Mapping[str, Any] = {}

        def result(status: ProviderStatus, reason: str, proposal: dict[str, Any] | None = None,
                   usage: Mapping[str, Any] | None = None, raw_text: str | None = None,
                   response_info: Mapping[str, Any] | None = None) -> ProviderResult:
            metadata = {
                "latency_ms": max(0, min(60000, int((self._clock() - started) * 1000))),
                "attempt_count": attempts,
                "model": self.config.model,
            }
            for name in ("input_tokens", "output_tokens", "total_tokens"):
                value = (usage or {}).get(name)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000000000:
                    metadata[name] = value
            for source, target in (("id", "response_id"), ("model", "response_model")):
                value = (response_info or response_metadata).get(source)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value):
                    metadata[target] = value
            return ProviderResult(status, proposal, self.provider_id, self.mock, reason, metadata, raw_text)

        try:
            payload = prepare_request(self.config, request, schema)
        except PrivateConfigError as failure:
            return result("error", str(failure))
        try:
            payload_text = _json_text(payload, self.config.max_request_bytes)
        except _InvalidJSON:
            return result("error", "request_too_large")
        try:
            credential = validate_credential(self._credential_supplier())
        except Exception:
            return result("error", "credential_unavailable")
        if credential in payload_text or _contains_secret(request, credential) or _contains_secret(schema, credential):
            return result("error", "credentials_forbidden")
        headers = {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

        while True:
            remaining = self.config.total_deadline_s - (self._clock() - started)
            if remaining <= 0:
                return result("error", "deadline_exceeded")
            attempts += 1
            try:
                response = self._transport(
                    endpoint=self.config.endpoint, headers=headers, payload=copy.deepcopy(payload),
                    timeout_s=min(self.config.timeout_s, remaining),
                    max_response_bytes=self.config.max_response_bytes,
                )
            except RetryablePreflightError:
                if attempts <= self.config.max_retries:
                    continue
                return result("error", "transport_unavailable")
            except HTTPTransportError as failure:
                code = str(failure)
                allowed_codes = {"unsupported_live_endpoint", "invalid_transport_timeout",
                                 "invalid_transport_limit", "invalid_transport_request",
                                 "transport_timeout", "transport_error", "response_too_large",
                                 "unsupported_response_encoding", "invalid_transport_response"}
                return result("error", code if code in allowed_codes else "transport_error")
            except Exception:
                # A sent/charged request may have lost its response. Do not retry.
                return result("error", "transport_error")
            if self._clock() - started > self.config.total_deadline_s:
                return result("error", "deadline_exceeded")
            break

        if not isinstance(response, TransportResponse) or isinstance(response.status_code, bool):
            return result("error", "invalid_transport_response")
        if not isinstance(response.status_code, int):
            return result("error", "invalid_transport_response")
        if not 200 <= response.status_code < 300:
            code = ("credentials_rejected" if response.status_code in (401, 403)
                    else "provider_rate_limited" if response.status_code == 429 else "provider_http_error")
            return result("error", code)
        if not isinstance(response.body, (str, bytes)):
            return result("error", "invalid_transport_response")
        try:
            raw = response.body.encode("utf-8") if isinstance(response.body, str) else response.body
            if len(raw) > self.config.max_response_bytes:
                return result("error", "response_too_large")
            if credential.encode("ascii") in raw:
                return result("error", "sensitive_response")
            decoded = raw.decode("utf-8")
            parsed = _parse_json(decoded)
            if _contains_secret(parsed, credential):
                return result("error", "sensitive_response")
            assert_no_credentials(parsed)
        except (UnicodeError, _InvalidJSON, PrivateConfigError):
            return result("error", "invalid_provider_json")
        if not isinstance(parsed, dict):
            return result("error", "invalid_provider_json")
        response_metadata = parsed
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        if parsed.get("error") is not None:
            return result("error", "provider_error", usage=usage)
        status = parsed.get("status")
        if status == "incomplete":
            details = parsed.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            safe_reason = ("output_budget_exhausted" if reason == "max_output_tokens"
                           else "content_filter" if reason == "content_filter" else "provider_incomplete")
            return result("incomplete", safe_reason, usage=usage)
        if status != "completed":
            return result("error", "unexpected_provider_status", usage=usage)
        output = parsed.get("output")
        if not isinstance(output, list) or len(output) > 128:
            return result("error", "invalid_provider_output", usage=usage)
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                return result("error", "invalid_provider_output", usage=usage)
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list) or len(content) > 128:
                return result("error", "invalid_provider_output", usage=usage)
            for part in content:
                if not isinstance(part, dict):
                    return result("error", "invalid_provider_output", usage=usage)
                if part.get("type") == "refusal":
                    return result("refusal", "provider_refusal", usage=usage)
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        return result("error", "invalid_provider_output", usage=usage)
                    chunks.append(part["text"])
        if not chunks:
            return result("error", "missing_proposal", usage=usage)
        try:
            proposal = _parse_json("".join(chunks))
            if not isinstance(proposal, dict):
                return result("error", "proposal_not_object", usage=usage)
            if _contains_secret(proposal, credential):
                return result("error", "sensitive_response", usage=usage)
            assert_no_credentials(proposal)
        except (_InvalidJSON, PrivateConfigError):
            return result("error", "invalid_proposal_json", usage=usage)
        raw_text = "".join(chunks)
        return result("proposal", "proposal_received", proposal, usage, raw_text, parsed)
