"""Request -> provider -> local validation -> offline acceptance -> blueprint.

No method places entities, grants materials, acquires a controller lease or
qualifies native factory flow. That authority belongs to a future Lua bridge.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib

from ._reference_contract import (ContractError, blueprint_matches_proposal,
    canonical_bytes, decode_blueprint, encode_blueprint, load_json, parse_proposal, parse_snapshot)
from .context import context_sha256, validate_context
from .diagnostics import rejection_validation
from .private_config import assert_no_credentials
from .proposal_schema import from_wire, proposal_schema
from .request import validate_request
from .rich_proposal import parse_design

_CHECKED = ["identity_and_hash", "declared_bounds_and_collision", "cardinal_normal_geometry",
            "unlocked_recipe_names", "placement_bill_declarations", "stage_dependency_dag",
            "same_stage_planned_witnesses", "declared_functional_feasibility", "blueprint_roundtrip"]
_UNQUALIFIED = ["native_terrain_and_reach", "native_recipe_and_fluid_ports",
                "payment_and_material_conservation", "native_belt_inserter_pipe_power_flow",
                "executed_construction", "sustained_native_output"]
_CHECKED_RICH = ["identity_and_hash", "declared_bounds_and_collision", "cardinal_declared_quality_configuration",
                "unlocked_recipe_names", "placement_bill_declarations", "configured_item_requirements",
                "stage_dependency_dag", "same_stage_planned_witnesses", "declared_recipe_module_compatibility",
                "declared_circuit_connectors_and_reach", "declared_functional_feasibility", "rich_blueprint_roundtrip"]


def _compile_design(parsed, manifest, catalog):
    if catalog is None:
        return encode_blueprint(parsed)
    from .rich_blueprint import encode_rich_blueprint
    return encode_rich_blueprint(parsed, manifest["entity_configurations"], manifest["circuit_wires"], catalog)


def _blueprint_entities(blueprint, catalog):
    if catalog is None:
        return decode_blueprint(blueprint)["blueprint"]["entities"]
    from .rich_blueprint import decode_rich_blueprint
    return decode_rich_blueprint(blueprint)["blueprint"]["entities"]


def sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class AcceptedPlan:
    document: dict = field(repr=False)
    integrity_sha256: str

    def checked(self):
        """Revalidate persisted or mutable data at every export/preview boundary."""
        if sha256(self.document) != self.integrity_sha256:
            raise ContractError("Accepted package changed after validation")
        doc = copy.deepcopy(self.document)
        fields = {"schema", "acceptance_scope", "construction_status", "native_flow_qualified",
                  "request", "context", "context_sha256", "snapshot_sha256", "revision",
                  "parent_design_hash", "provider", "provider_proposal", "manifest", "design_sha256",
                  "blueprint", "blueprint_sha256", "geometry_sha256", "validation"}
        if set(doc) - (fields | {"raw_proposal_text", "model_tool_trace"}) or fields - set(doc):
            raise ContractError("Accepted package fields mismatch")
        context = validate_context(doc["context"])
        request = validate_request(doc["request"], context)
        snapshot = parse_snapshot(context["geometry_snapshot"])
        proposal, manifest, catalog = parse_design(doc["provider_proposal"], context)
        if len(proposal.entities) > request["constraints"]["max_entities"] \
                or len(proposal.stages) > request["constraints"]["max_stages"] \
                or doc["parent_design_hash"] != request["parent_design_hash"]:
            raise ContractError("Accepted request bounds or revision lineage changed")
        provenance = doc["provider"]
        if type(provenance) is not dict or set(provenance) != {"provider_id", "mock", "metadata"} \
                or type(provenance["mock"]) is not bool or type(provenance["metadata"]) is not dict \
                or type(provenance["provider_id"]) is not str or not 1 <= len(provenance["provider_id"]) <= 64:
            raise ContractError("Invalid provider provenance")
        if "raw_proposal_text" in doc and load_json(doc["raw_proposal_text"]) != doc["provider_proposal"]:
            raise ContractError("Raw provider text does not match parsed proposal")
        if provenance["metadata"].get("generation_mode") == "tool_calls":
            from .editor import replay_tool_trace
            trace = doc.get("model_tool_trace")
            if "raw_proposal_text" in doc or replay_tool_trace(request, context, trace) != doc["provider_proposal"] \
                    or sha256(trace) != provenance["metadata"].get("tool_trace_sha256"):
                raise ContractError("Model tool actions do not match accepted geometry")
        elif "model_tool_trace" in doc:
            raise ContractError("Unexpected model tool trace")
        if canonical_bytes(manifest) != canonical_bytes(doc["manifest"]) or sha256(manifest) != doc["design_sha256"]:
            raise ContractError("Accepted manifest does not match provider output")
        if _compile_design(proposal, manifest, catalog) != doc["blueprint"]:
            raise ContractError("Blueprint does not match accepted manifest")
        geometry = _blueprint_entities(doc["blueprint"], catalog)
        if sha256({"entities": geometry}) != doc["geometry_sha256"] \
                or hashlib.sha256(doc["blueprint"].encode("ascii")).hexdigest() != doc["blueprint_sha256"]:
            raise ContractError("Blueprint geometry hash mismatch")
        if doc["context_sha256"] != context_sha256(context) \
                or doc["snapshot_sha256"] != snapshot.sha256 \
                or doc["revision"] != request["revision"]:
            raise ContractError("Accepted package identity mismatch")
        expected_schema = "designer-accepted-v1" if catalog is None else "designer-accepted-v2"
        if doc["schema"] != expected_schema \
                or doc["acceptance_scope"] != "offline_structural" \
                or doc["construction_status"] != "proposed_unbuilt" \
                or doc["native_flow_qualified"] is not False:
            raise ContractError("Unsupported acceptance scope")
        checks = _CHECKED if catalog is None else _CHECKED_RICH
        if doc["validation"] != {"checked": checks, "unqualified": _UNQUALIFIED}:
            raise ContractError("Validation report claims changed after acceptance")
        assert_no_credentials(doc)
        return doc


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    accepted: AcceptedPlan | None = None
    evidence: dict = field(default_factory=dict, repr=False)


def propose(request, context, provider, *, latest_context=None, memory=None,
            max_input_bytes=64_000):
    """Request only bounded JSON data; never execute model-authored code."""
    original = validate_context(context)
    request = validate_request(request, original)
    if type(max_input_bytes) is not int or not 32_000 <= max_input_bytes <= 128_000:
        raise ContractError("Invalid model context byte budget")
    history_budget = max(1024, max_input_bytes - len(canonical_bytes(original))
                         - len(canonical_bytes(request)) - 1024)
    history = memory.context(original, max_bytes=min(history_budget, 16_000)) if memory else {}
    envelope = {"request": request, "context": original, "memory": history}
    assert_no_credentials(envelope)
    if len(canonical_bytes(envelope)) > max_input_bytes:
        raise ContractError("Model request exceeds context budget")
    response = provider.propose(copy.deepcopy(envelope),
                                proposal_schema(original["geometry_snapshot"]["game_version"]))
    safe_provenance = {"provider_id": response.provider_id, "mock": response.mock,
                       "metadata": response.metadata}
    assert_no_credentials(safe_provenance)
    if len(canonical_bytes(safe_provenance)) > 8_000:
        return Outcome("rejected", "provider_metadata_too_large")
    tool_trace = copy.deepcopy(getattr(provider, "trace", None))
    if tool_trace is not None:
        assert_no_credentials(tool_trace)
        if type(tool_trace) is not dict or len(canonical_bytes(tool_trace)) > 200_000:
            return Outcome("rejected", "provider_tool_trace_invalid", evidence={"provider": safe_provenance})
    if response.status != "proposal":
        details = {"provider": safe_provenance, "reason": response.reason}
        if tool_trace is not None:
            details["model_tool_trace"] = tool_trace
        return Outcome("rejected", "provider_" + response.status,
                       evidence=details)
    wire = copy.deepcopy(response.proposal)
    assert_no_credentials(wire)
    try:
        if len(canonical_bytes(wire)) > 200_000:
            return Outcome("rejected", "provider_output_too_large", evidence={"provider": safe_provenance})
    except (ContractError, RecursionError):
        return Outcome("rejected", "provider_output_invalid_json", evidence={"provider": safe_provenance})
    evidence = {"provider": safe_provenance, "provider_proposal": wire}
    if tool_trace is not None:
        evidence["model_tool_trace"] = tool_trace
    raw_text = getattr(response, "raw_proposal_text", None)
    if raw_text is not None:
        assert_no_credentials(raw_text)
        if type(raw_text) is not str or len(raw_text.encode("utf-8")) > 200_000:
            return Outcome("rejected", "provider_output_too_large")
        evidence["raw_proposal_text"] = raw_text
    try:
        # Callback rereads current native-export file in the live CLI. A changed
        # observation invalidates the pending response, even at the same tick.
        current = validate_context(latest_context() if latest_context else original)
        validate_request(request, current)
        parsed, manifest, catalog = parse_design(wire, current)
        design_hash = sha256(manifest)
        if len(parsed.entities) > request["constraints"]["max_entities"] \
                or len(parsed.stages) > request["constraints"]["max_stages"]:
            raise ContractError("Requested plan bound exceeded")
        if memory:
            old = memory.get_plan(parsed.raw["plan_id"])
            if request["revision"] == 1 and old is not None \
                    and old["design_hash"] != design_hash:
                raise ContractError("Plan ID already has an accepted revision")
            if request["revision"] > 1 and (old is None \
                    or old["revision"] != request["revision"] - 1 \
                    or old["design_hash"] != request["parent_design_hash"]):
                raise ContractError("Previous accepted revision missing or mismatched")
        elif request["revision"] > 1:
            raise ContractError("Revision requires persisted accepted history")
        blueprint = _compile_design(parsed, manifest, catalog)
        geometry = _blueprint_entities(blueprint, catalog)
        document = {
            "schema": "designer-accepted-v1" if catalog is None else "designer-accepted-v2", "acceptance_scope": "offline_structural",
            "construction_status": "proposed_unbuilt", "native_flow_qualified": False,
            "request": request, "context": current, "context_sha256": context_sha256(current),
            "snapshot_sha256": parsed.snapshot_sha256, "revision": request["revision"],
            "parent_design_hash": request["parent_design_hash"], "provider": safe_provenance,
            "provider_proposal": wire, "manifest": manifest,
            "design_sha256": design_hash, "blueprint": blueprint,
            "blueprint_sha256": hashlib.sha256(blueprint.encode("ascii")).hexdigest(),
            "geometry_sha256": sha256({"entities": geometry}),
            "validation": {"checked": list(_CHECKED if catalog is None else _CHECKED_RICH), "unqualified": list(_UNQUALIFIED)},
        }
        if raw_text is not None:
            document["raw_proposal_text"] = raw_text
        if tool_trace is not None:
            document["model_tool_trace"] = tool_trace
        accepted = AcceptedPlan(copy.deepcopy(document), sha256(document))
        accepted.checked()
        if memory:
            try:
                memory.record_plan(parsed.raw["plan_id"], request["revision"], design_hash,
                                   parsed.snapshot_sha256, parent_design_hash=request["parent_design_hash"])
                evidence["memory_record_status"] = "persisted"
            except Exception:
                # Preserve a validated provider result even if host history
                # storage fails. Export/recovery must never trigger another call.
                evidence["memory_record_status"] = "local_history_write_failed"
        return Outcome("accepted_offline", "structural_checks_passed", accepted, evidence)
    except (ContractError, ValueError, KeyError, TypeError) as failure:
        # Invalid model input never leaks exception values, arbitrary code or
        # credentials into ordinary diagnostics.
        evidence["validation"] = rejection_validation(wire, failure)
        return Outcome("rejected", "local_validation_failed", evidence=evidence)
