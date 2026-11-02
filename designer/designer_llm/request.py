"""Request identity, freshness, explicit demand units and revision bounds."""

import copy
import math
import re

from ._reference_contract import ContractError, canonical_bytes, parse_snapshot
from .context import context_sha256, validate_context


_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def make_request(context, goal, *, request_id="request-1", demand=None,
                 revision=1, parent_design_hash=None, max_entities=128, max_stages=64):
    current = validate_context(context)
    request = {
        "schema": "designer-request-v1", "request_id": request_id,
        "identity": current["identity"], "context_sha256": context_sha256(current),
        "snapshot_sha256": parse_snapshot(current["geometry_snapshot"]).sha256,
        "goal": goal, "demand": [] if demand is None else demand,
        "constraints": {"max_entities": max_entities, "max_stages": max_stages,
                        "normal_quality_only": True},
        "revision": revision, "parent_design_hash": parent_design_hash,
    }
    return validate_request(request, current)


def validate_request(request, context, *, max_age_ticks=600):
    current = validate_context(context)
    fields = {"schema", "request_id", "identity", "context_sha256", "snapshot_sha256",
              "goal", "demand", "constraints", "revision", "parent_design_hash"}
    if type(request) is not dict or set(request) != fields:
        raise ContractError("Request fields mismatch")
    if len(canonical_bytes(request)) > 16_000:
        raise ContractError("Request exceeds byte limit")
    if request["schema"] != "designer-request-v1" or type(request["request_id"]) is not str \
            or not _ID.fullmatch(request["request_id"]):
        raise ContractError("Unsupported request identity")
    if request["identity"] != current["identity"]:
        raise ContractError("Request world, session or observation identity changed")
    if request["context_sha256"] != context_sha256(current) \
            or request["snapshot_sha256"] != parse_snapshot(current["geometry_snapshot"]).sha256:
        raise ContractError("Stale request context or geometry hash")
    goal = request["goal"]
    if type(goal) is not str or not goal.strip() or len(goal) > 4000:
        raise ContractError("Invalid goal")
    from .private_config import assert_no_credentials
    assert_no_credentials(request)
    constraints = request["constraints"]
    if type(constraints) is not dict or set(constraints) != {"max_entities", "max_stages", "normal_quality_only"}:
        raise ContractError("Invalid request constraints")
    for key, maximum in (("max_entities", 128), ("max_stages", 64)):
        if type(constraints[key]) is not int or not 1 <= constraints[key] <= maximum:
            raise ContractError("Invalid request geometry bound")
    if constraints["normal_quality_only"] is not True:
        raise ContractError("Only normal quality is supported")
    if type(request["revision"]) is not int or not 1 <= request["revision"] <= 1_000_000:
        raise ContractError("Invalid plan revision")
    parent = request["parent_design_hash"]
    if (request["revision"] == 1 and parent is not None) or \
            (request["revision"] > 1 and (type(parent) is not str or not _SHA.fullmatch(parent))):
        raise ContractError("Revision must bind its previous accepted design")
    demands = request["demand"]
    if type(demands) is not list or len(demands) > 32:
        raise ContractError("Invalid demand list")
    known_items = parse_snapshot(current["geometry_snapshot"]).items
    seen = set()
    for row in demands:
        if type(row) is not dict or set(row) != {"item", "metric", "amount", "window_ticks"}:
            raise ContractError("Demand needs explicit item, metric, amount and window")
        if type(row["item"]) is not str or row["item"] not in known_items:
            raise ContractError("Demand item absent from pinned snapshot")
        metric = row["metric"]
        if metric not in ("stock", "rate_items_per_second"):
            raise ContractError("Unsupported demand metric")
        amount = row["amount"]
        if type(amount) not in (int, float) or not math.isfinite(amount) or not 0 < amount <= 1_000_000:
            raise ContractError("Invalid demand amount")
        if (row["item"], metric) in seen:
            raise ContractError("Duplicate demand metric")
        seen.add((row["item"], metric))
        window = row["window_ticks"]
        if metric == "rate_items_per_second":
            if type(window) is not int or not 60 <= window <= 3_600_000:
                raise ContractError("Rate demand needs a measured window in ticks")
        elif window is not None or type(amount) is not int:
            raise ContractError("Stock demand requires integer items and no rate window")
    if type(max_age_ticks) is not int or max_age_ticks < 0:
        raise ContractError("Invalid freshness bound")
    capabilities = current.get("entity_capabilities")
    if capabilities and capabilities["knowledge"] == "known" \
            and current["identity"]["observed_tick"] - capabilities["observed_tick"] > max_age_ticks:
        raise ContractError("Entity capabilities are stale")
    # Freshness of every fact remains visible; stale operational groups cannot
    # authorize a new offline geometry acceptance.
    for name in ("built_machines", "terrain", "recipes"):
        fact = current["facts"][name]
        if fact["knowledge"] != "known" or current["identity"]["observed_tick"] - fact["observed_tick"] > max_age_ticks:
            raise ContractError("Critical design observations are unknown or stale")
        def check_nested(value):
            if isinstance(value, dict):
                if value.get("knowledge") == "known" and "observed_tick" in value:
                    if current["identity"]["observed_tick"] - value["observed_tick"] > max_age_ticks:
                        raise ContractError("Nested critical observation is stale")
                for child in value.values():
                    check_nested(child)
            elif isinstance(value, list):
                for child in value:
                    check_nested(child)
        check_nested(fact["value"])
    return copy.deepcopy(request)
