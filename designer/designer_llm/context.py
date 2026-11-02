"""Bounded designer observations; a declaration of provenance is not authentication."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from ._reference_contract import ContractError, parse_snapshot

CONTEXT_SCHEMA = "designer-context-v1"
FACT_GROUPS = (
    "built_machines", "blocks", "connections", "recipes", "inventories",
    "demand", "item_metrics", "terrain", "pending_work",
)
METRICS = (
    "cumulative_produced", "stock", "rate_items_per_second",
    "measured_window_ticks", "utilization", "spare_capacity_items_per_second",
)
MAX_CONTEXT_BYTES = 128_000
MAX_JSON_NODES = 20_000
MAX_DEPTH = 16
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CREDENTIAL_KEY = re.compile(
    r"(?:^|[\s_-])(?:api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|"
    r"auth(?:orization)?|password|passwd|credential|client[\s_-]*secret|"
    r"provider[\s_-]*key|private[\s_-]*key|secret)(?:$|[\s_-])", re.I)
_CREDENTIAL_VALUE = re.compile(
    r"(?:\bBearer\s+\S{8,}|\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}|"
    r"\b(?:api[_ -]?key|access[_ -]?token|password|client[_ -]?secret)"
    r"\s*[:=]\s*\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)", re.I)
_WRAPPER_KEYS = {"knowledge", "value", "observed_tick", "revision", "provenance"}


class ContextError(ValueError):
    """Invalid, too large, or credential-bearing context (without echoing data)."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContextError("Data must be finite UTF-8 JSON") from exc


def reject_credentials(value: Any) -> None:
    """Reject recognizable secrets before persistence/export; never echo matches."""
    remaining = MAX_JSON_NODES

    def walk(node: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_DEPTH:
            raise ContextError("JSON structure exceeds limit")
        if node is None or type(node) is bool:
            return
        if type(node) is int:
            if not -(2**63 - 1) <= node <= 2**63 - 1:
                raise ContextError("JSON integer exceeds limit")
            return
        if type(node) is float:
            if not math.isfinite(node):
                raise ContextError("Nonfinite JSON number")
            return
        if isinstance(node, str):
            if len(node) > 32_000:
                raise ContextError("JSON string exceeds limit")
            if _CREDENTIAL_VALUE.search(node):
                raise ContextError("Credential-like content is forbidden")
            return
        if isinstance(node, dict):
            if len(node) > 4096:
                raise ContextError("JSON object exceeds limit")
            for key, child in node.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise ContextError("Invalid JSON object key")
                if _CREDENTIAL_KEY.search(key):
                    raise ContextError("Credential-like fields are forbidden")
                walk(child, depth + 1)
            return
        if isinstance(node, list):
            if len(node) > 4096:
                raise ContextError("JSON array exceeds limit")
            for child in node:
                walk(child, depth + 1)
            return
        raise ContextError("Only JSON values are supported")

    walk(value, 0)
    canonical_bytes(value)


def _keys(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - (
            required | (optional or set())):
        raise ContextError("Missing or unsupported context field")
    return value


def identifier(value: Any, label: str = "identifier") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ContextError("Invalid " + label)
    return value


def integer(value: Any, label: str, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContextError("Invalid " + label)
    return value


def digest(value: Any, label: str = "hash") -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ContextError("Invalid " + label)
    return value


def source_identifier(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(
            ord(character) < 32 for character in value):
        raise ContextError("Invalid provenance source")
    return value


def epoch(value: Any) -> str | int:
    if type(value) is int:
        return integer(value, "session epoch")
    return identifier(value, "session epoch")


def _wrapper(row: Any, identity: dict, mode: str) -> dict:
    row = _keys(row, _WRAPPER_KEYS)
    if row["knowledge"] not in ("known", "unknown"):
        raise ContextError("Invalid knowledge state")
    if row["knowledge"] == "unknown" and row["value"] is not None:
        raise ContextError("Unknown observation must have null value")
    if row["knowledge"] == "known" and row["value"] is None:
        raise ContextError("Known observation must have a value")
    tick = integer(row["observed_tick"], "fact observation tick")
    revision = integer(row["revision"], "fact revision")
    if tick > identity["observed_tick"] or revision > identity["revision"]:
        raise ContextError("Fact is ahead of enclosing observation")
    source = _keys(row["provenance"], {"kind", "source_id"})
    source_identifier(source["source_id"])
    allowed = {"fixture"} if mode == "fixture" else {"native_snapshot", "native_event"}
    if source["kind"] not in allowed:
        raise ContextError("Provenance does not match evidence mode")
    return row


def _nested_wrappers(node: Any, identity: dict, mode: str) -> None:
    if isinstance(node, dict):
        if "knowledge" in node:
            _wrapper(node, identity, mode)
        for value in node.values():
            _nested_wrappers(value, identity, mode)
    elif isinstance(node, list):
        for value in node:
            _nested_wrappers(value, identity, mode)


def validate_context(row: dict) -> dict:
    """Validate exact identity, rich JSON knowledge wrappers and metric semantics.

    Group values may use domain-specific records. Nested observations use the same
    wrapper when any subfield is unknown. Empty known collections mean observed
    empty; unknown never means zero. Returned data is owned by the caller.
    """
    reject_credentials(row)
    if len(canonical_bytes(row)) > MAX_CONTEXT_BYTES:
        raise ContextError("Context exceeds byte limit")
    owned = copy.deepcopy(row)
    _keys(owned, {"schema", "identity", "evidence_mode", "geometry_snapshot", "facts"},
          {"geometry_snapshot_sha256", "entity_capabilities"})
    if owned["schema"] != CONTEXT_SCHEMA or owned["evidence_mode"] not in (
            "fixture", "native_export"):
        raise ContextError("Unsupported context schema or evidence mode")
    identity = _keys(owned["identity"], {
        "world_id", "force_id", "surface_id", "actor_id", "life_id", "session_epoch",
        "game_version", "source_hash", "prototype_hash", "observed_tick", "revision",
    })
    for key in ("world_id", "force_id", "surface_id", "actor_id", "life_id"):
        identifier(identity[key], key)
    epoch(identity["session_epoch"])
    integer(identity["observed_tick"], "observation tick")
    integer(identity["revision"], "observation revision")
    digest(identity["source_hash"], "source hash")
    digest(identity["prototype_hash"], "prototype hash")
    try:
        geometry = parse_snapshot(owned["geometry_snapshot"])
    except (ContractError, TypeError, KeyError) as exc:
        raise ContextError("Invalid reference geometry snapshot") from exc
    for key in ("world_id", "force_id", "game_version", "source_hash", "observed_tick"):
        if type(identity[key]) is not type(geometry.raw[key]) or identity[key] != geometry.raw[key]:
            raise ContextError("Geometry snapshot identity mismatch")
    if "geometry_snapshot_sha256" in owned and owned["geometry_snapshot_sha256"] != geometry.sha256:
        raise ContextError("Geometry snapshot hash mismatch")
    owned["geometry_snapshot_sha256"] = geometry.sha256
    facts = _keys(owned["facts"], set(FACT_GROUPS))
    if "entity_capabilities" in owned:
        capability_fact = _wrapper(owned["entity_capabilities"], identity, owned["evidence_mode"])
        if capability_fact["knowledge"] == "known":
            from .entity_configuration import validate_capability_catalog
            catalog = validate_capability_catalog(capability_fact["value"], identity)
            expected_source = "synthetic_fixture" if owned["evidence_mode"] == "fixture" else "native_export_unverified"
            if catalog["source"] != expected_source:
                raise ContextError("Capability catalog evidence source mismatch")
    for name, group in facts.items():
        _wrapper(group, identity, owned["evidence_mode"])
        if group["knowledge"] == "known" and not isinstance(group["value"], (dict, list)):
            raise ContextError("Fact group must contain structured records")
        _nested_wrappers(group["value"], identity, owned["evidence_mode"])
    metrics = facts["item_metrics"]
    if metrics["knowledge"] == "known":
        if not isinstance(metrics["value"], dict):
            raise ContextError("Item metrics must be keyed by item")
        for item, measurements in metrics["value"].items():
            identifier(item, "metric item")
            _keys(measurements, set(METRICS))
            for name, observation in measurements.items():
                _wrapper(observation, identity, owned["evidence_mode"])
                if observation["knowledge"] == "unknown":
                    continue
                value = observation["value"]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ContextError("Invalid metric quantity")
                if name in ("cumulative_produced", "stock", "measured_window_ticks"):
                    integer(value, "count or measured window")
                if name == "utilization" and value > 1:
                    raise ContextError("Utilization must be a fraction")
            rate = measurements["rate_items_per_second"]
            window = measurements["measured_window_ticks"]
            if rate["knowledge"] == "known" and (
                    window["knowledge"] != "known" or window["value"] <= 0):
                raise ContextError("Measured rate requires a positive known window")
    if len(canonical_bytes(owned)) > MAX_CONTEXT_BYTES:
        raise ContextError("Validated context exceeds byte limit")
    return owned


def context_sha256(row: dict) -> str:
    return hashlib.sha256(canonical_bytes(validate_context(row))).hexdigest()


def freshness(row: dict) -> dict:
    """Derived age/revision lag; no guessed stale threshold or implied zero."""
    checked = validate_context(row)
    identity = checked["identity"]
    result: dict[str, dict] = {}

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "knowledge" in node:
                age = identity["observed_tick"] - node["observed_tick"]
                lag = identity["revision"] - node["revision"]
                result[path] = {
                    "knowledge": node["knowledge"], "age_ticks": age,
                    "revision_lag": lag, "stale": bool(age or lag),
                    "freshness": "unknown" if node["knowledge"] == "unknown" else (
                        "current" if not age and not lag else "stale"),
                }
            for key, value in node.items():
                if key not in ("provenance", "knowledge", "observed_tick", "revision"):
                    visit(value, path + "/" + key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, path + "/" + str(index))

    visit(checked["facts"], "facts")
    return result
