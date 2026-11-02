"""Bounded model tools for editing proposed geometry, never the observed world.

The draft starts empty. Only an explicit model submit supplies the construction
stages and witnesses; geometry editing never invents a placeholder witness.
"""

from __future__ import annotations

import copy
import re
from typing import Callable

from ._reference_contract import (
    ContractError, Entity, _entity_box, _overlap, canonical_bytes, load_json,
    parse_proposal, parse_snapshot,
)
from .context import validate_context
from .entity_configuration import (
    configuration_schema, is_legacy_configuration, validate_capability_catalog,
    validate_entity_configuration,
)
from .private_config import PrivateConfigError, assert_no_credentials
from .proposal_schema import from_wire, proposal_schema, validate_wire
from .request import validate_request
from .rich_proposal import (canonical_wire, circuit_wire_schema, parse_design,
                            validate_circuit_wires, validate_configured_entities, wire_key)


MAX_TOOL_ARGUMENT_BYTES = 64_000
MAX_TOOL_TRACE_BYTES = 200_000
MAX_TOOL_CALLS = 12
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOOLS = ("read_grid", "get_entity_capabilities", "place_entity", "move_entity",
          "rotate_entity", "upgrade_entity", "remove_entity", "select_entity",
          "set_recipe", "configure_entity", "connect_entities", "disconnect_entities", "submit_plan")


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def tool_schemas():
    """Responses function definitions, closed and strict like the wire schema."""
    proposal = proposal_schema()
    entity = proposal["properties"]["entities"]["items"]["properties"]
    number = entity["position"]["properties"]
    schemas = {
        "read_grid": _object({}),
        "get_entity_capabilities": _object({"prototype": entity["prototype"]}),
        "place_entity": _object({
            "id": entity["id"], "prototype": entity["prototype"],
            "x": number["x"], "y": number["y"],
            "direction": entity["direction"], "recipe": entity["recipe"],
        }),
        "move_entity": _object({
            "id": entity["id"], "x": number["x"], "y": number["y"],
            "direction": {"type": ["integer", "null"], "enum": [0, 4, 8, 12, None]},
        }),
        "rotate_entity": _object({"id": entity["id"], "direction": entity["direction"]}),
        "upgrade_entity": _object({"id": entity["id"], "prototype": entity["prototype"]}),
        "remove_entity": _object({"id": entity["id"]}),
        "select_entity": _object({"id": entity["id"]}),
        "set_recipe": _object({"id": entity["id"], "recipe": entity["recipe"]}),
        "configure_entity": _object({"id": entity["id"], "configuration": configuration_schema()}),
        "connect_entities": circuit_wire_schema(),
        "disconnect_entities": circuit_wire_schema(),
        "submit_plan": _object({
            "plan_id": proposal["properties"]["plan_id"],
            "stages": proposal["properties"]["stages"],
        }),
    }
    descriptions = {
        "connect_entities": "Connect two draft entities using declared red, green or copper connector IDs and wire reach.",
        "disconnect_entities": "Remove an existing draft circuit connection. Endpoint order may be reversed.",
        "read_grid": "Inspect the declared survey and current proposed entities. This is not in-game construction.",
        "get_entity_capabilities": (
            "Inspect the pinned prototype's geometry and declared configuration limits. Rich quality, "
            "modules and control settings require an explicitly supplied capability catalog; absent "
            "capabilities permit only normal geometry. Native recipe compatibility remains unqualified."
        ),
        "place_entity": "Add a proposed entity with a unique ID. Respect footprint collision, bounds, prototype and recipe rules.",
        "move_entity": "Move a proposed entity atomically; null direction keeps its current direction.",
        "rotate_entity": "Rotate one proposed entity to a cardinal direction, checking its rotated footprint atomically.",
        "upgrade_entity": (
            "Replace a proposed entity's prototype with another pinned allowed prototype, checking its "
            "footprint and recipe name atomically. A supplied capability catalog also requires its "
            "declared upgrade relationship and compatible configuration; this is not native qualification."
        ),
        "remove_entity": "Remove a proposed entity. This cannot remove an observed world entity.",
        "select_entity": "Select one proposed entity for inspection; this changes no observed machine.",
        "set_recipe": "Set an unlocked recipe on a proposed entity, or null to clear it. Native category compatibility remains unqualified.",
        "configure_entity": (
            "Set an entity's quality, recipe quality, modules and typed control settings atomically. "
            "Rich choices require supplied per-prototype capabilities and an implemented blueprint adapter. "
            "Unsupported settings are rejected instead of silently dropped."
        ),
        "submit_plan": (
            "Submit the current tool-authored geometry with your plan ID and functional construction stages. "
            "Each stage must own nonempty entities, declare their placement bill and include a same-stage "
            "planned product, extraction, energy or research_progress_delta witness. research_progress_delta "
            "targets a lab with null item and min_delta >= 1 research progress ticks/units. A power-only stage "
            "may use power_connected on a steam engine or pole with null item/min_delta. "
            "Declare manual_supplies (fuel/input) or null. "
            "Submission checks known prototype facts, resource/drop geometry, fuel, pole power networks, "
            "crafting input and recipe categories. These witnesses are requirements, not achieved events. "
            "Rotate fluid positions and outward directions; normal ports mutually target one another a tile "
            "away, underground ports link nearest opposite-facing same-prototype ports within range. "
            "Separate fluid boxes stay separate; never mix water and steam. Boilers need water from an "
            "offshore pump on a known shore; steam engines need a water-fed boiler's steam. Labs need "
            "power and an input manual supply of automation-science-pack or a feeding inserter. "
            "A rejected submission leaves the draft editable. Successful submission freezes it."
        ),
    }
    return copy.deepcopy([
        {"type": "function", "name": name, "description": descriptions[name],
         "strict": True, "parameters": schemas[name]}
        for name in _TOOLS
    ])


class _EditError(ValueError):
    """Fixed error code only; never carries rejected model text."""


_PLAN_ERRORS = {
    "Functional stage lacks a planned native flow witness": "stage_needs_flow_witness",
    "Invalid stage ID": "invalid_stage_id",
    "Stage entity missing or assigned twice": "stage_entity_ownership_invalid",
    "Invalid planned bill": "invalid_placement_bill",
    "Planned bill omits placement items": "placement_bill_incomplete",
    "Unknown planned bill item": "unknown_bill_item",
    "Invalid stage witness": "witness_must_target_same_stage_entity",
    "Unexpected witness quantity": "unexpected_witness_quantity",
    "Invalid witness minimum": "invalid_witness_minimum",
    "Unknown witness item": "unknown_witness_item",
    "Energy witness must not claim an item": "energy_witness_item_forbidden",
    "Research witness must not claim an item": "research_witness_item_forbidden",
    "Research witness must target a lab": "research_witness_requires_lab",
    "Every entity must belong to exactly one stage": "stage_entity_ownership_invalid",
    "Unknown or self-dependent stage": "invalid_stage_dependency",
    "Stage dependency cycle": "stage_dependency_cycle",
    "Duplicate stage ID": "duplicate_stage_id",
    "Duplicate dependency": "duplicate_stage_dependency",
    "Duplicate stage entity": "duplicate_stage_entity",
    "Duplicate planned bill item": "duplicate_bill_item",
    "Invalid manual supplies": "invalid_manual_supplies",
    "Manual supply entity is not in this or an earlier stage": "manual_supply_entity_unavailable",
    "Unknown manual supply item": "unknown_manual_supply_item",
    "Invalid manual supply slot": "invalid_manual_supply_slot",
    "Duplicate manual supply": "duplicate_manual_supply",
}

_CONFIGURATION_ERRORS = {
    "Entity has no configuration capabilities": "entity_capabilities_unavailable",
    "Entity quality is unavailable": "entity_quality_unavailable",
    "Recipe quality is unavailable": "recipe_quality_unavailable",
    "Workstation upgrade is unavailable": "workstation_upgrade_unavailable",
    "Module item or quality is unavailable": "module_item_or_quality_unavailable",
    "Module capacity exceeded": "module_capacity_exceeded",
    "Duplicate module configuration": "duplicate_module_configuration",
    "Settings are unsupported by this entity": "entity_settings_unsupported",
    "Signal absent from capabilities": "signal_capabilities_unavailable",
    "Signal quality is unavailable": "signal_quality_unavailable",
    "Operand must choose exactly one signal or constant": "operand_requires_signal_or_constant",
}
_BLUEPRINT_ERRORS = {
    "arithmetic_blueprint_mapping_unavailable", "splitter_blueprint_mapping_unavailable",
    "inserter_blueprint_mapping_unavailable", "inserter_filter_mapping_unavailable",
    "inserter_stack_size_mapping_unavailable", "enable_condition_blueprint_mapping_unavailable",
    "enable_condition_first_constant_unsupported", "enable_condition_network_selection_unsupported",
    "rich_settings_mapping_unavailable", "module_inventory_mapping_unavailable",
    "module_recipe_mapping_unavailable", "recipe_blueprint_mapping_unavailable",
    "recipe_capability_mapping_unavailable", "recipe_quality_blueprint_mapping_unavailable",
}


def _default_configuration():
    return {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None}


class DraftGridController:
    """An isolated, deterministic proposed grid bound to one validated request."""

    def __init__(self, request, context, *, on_change: Callable[[dict], None] | None = None):
        self._context = validate_context(context)
        self._request = validate_request(request, self._context)
        self._snapshot = parse_snapshot(self._context["geometry_snapshot"])
        capability_fact = self._context.get("entity_capabilities")
        self._capability_catalog = None
        if capability_fact is not None and capability_fact["knowledge"] == "known":
            identity = self._context["identity"]
            if capability_fact["observed_tick"] != identity["observed_tick"] \
                    or capability_fact["revision"] != identity["revision"]:
                raise ContractError("Capability observations are stale")
            self._capability_catalog = validate_capability_catalog(capability_fact["value"], identity)
        if on_change is not None and not callable(on_change):
            raise ContractError("Invalid draft change observer")
        self._on_change = on_change
        self._entities = {}
        self._configurations = {}
        self._wires = []
        self._selected = None
        self._revision = 0
        self._submitted_wire = None
        self._submitted_proposal = None
        self._schemas = {row["name"]: row["parameters"] for row in tool_schemas()}

    @property
    def submitted_wire(self):
        return copy.deepcopy(self._submitted_wire)

    @property
    def submitted_proposal(self):
        return copy.deepcopy(self._submitted_proposal)

    @property
    def submitted_geometry(self):
        return None if self._submitted_wire is None else copy.deepcopy(self._submitted_wire["entities"])

    @property
    def state(self):
        return self.read_grid()

    def read_grid(self):
        return copy.deepcopy({
            "schema": "designer-draft-grid-v1",
            "request_id": self._request["request_id"],
            "context_sha256": self._request["context_sha256"],
            "snapshot_sha256": self._snapshot.sha256,
            "identity": self._request["identity"],
            "survey_bounds": list(self._snapshot.bounds),
            "occupied_boxes": [list(box) for box in self._snapshot.occupied],
            "prototypes": self._snapshot.prototypes,
            "unlocked_recipes": sorted(self._snapshot.recipes),
            "max_entities": self._request["constraints"]["max_entities"],
            "max_stages": self._request["constraints"]["max_stages"],
            "entities": list(self._entities.values()),
            "entity_configurations": self._configurations,
            "circuit_wires": self._wires,
            "configuration_capabilities_known": self._capability_catalog is not None,
            "configuration_export_scope": "rich_declared_configuration" if self._capability_catalog is not None
                                          else "legacy_normal_geometry_only",
            "selected_entity_id": self._selected,
            "revision": self._revision,
            "editable": self._submitted_wire is None,
            "construction_status": "proposed_unbuilt",
            "native_flow_qualified": False,
        })

    def _result(self, name, *, status, error=None):
        result = {"ok": error is None, "tool": name, "status": status, "state": self.read_grid()}
        if error is not None:
            result["error"] = error
        if status == "accepted_offline":
            result["submitted_wire"] = self.submitted_wire
        return result

    def _notify(self):
        if self._on_change is not None:
            try:
                self._on_change(self.read_grid())
            except Exception:
                # UI observation is not construction or acceptance authority.
                # A local display failure cannot undo or retry a model edit.
                pass

    def _validate_geometry(self, entities):
        if len(entities) > self._request["constraints"]["max_entities"]:
            raise _EditError("entity_limit_exceeded")
        entity_schema = proposal_schema()["properties"]["entities"]["items"]
        boxes = []
        for row in entities.values():
            validate_wire(row, entity_schema)
            if row["prototype"] not in self._snapshot.prototypes:
                raise _EditError("unsupported_prototype")
            if row["recipe"] is not None and row["recipe"] not in self._snapshot.recipes:
                raise _EditError("recipe_not_unlocked")
            entity = Entity(row["id"], row["prototype"], row["position"]["x"],
                            row["position"]["y"], row["direction"], row["recipe"])
            box = _entity_box(entity, self._snapshot.prototypes[entity.prototype])
            bounds = self._snapshot.bounds
            if box[0] < bounds[0] or box[1] < bounds[1] or box[2] > bounds[2] or box[3] > bounds[3]:
                raise _EditError("outside_survey_bounds")
            if any(_overlap(box, other) for other in boxes + list(self._snapshot.occupied)):
                raise _EditError("declared_collision")
            boxes.append(box)

    def _validate_configurations(self, entities, configurations, *, upgraded_id=None, previous_prototype=None):
        if set(entities) != set(configurations):
            raise _EditError("entity_configuration_ownership_invalid")
        for entity_id, row in entities.items():
            configuration = configurations[entity_id]
            validate_wire(configuration, configuration_schema())
            if configuration["recipe_quality"] is not None and row["recipe"] is None:
                raise _EditError("recipe_quality_requires_recipe")
            if self._capability_catalog is None:
                if not is_legacy_configuration(configuration):
                    raise _EditError("configuration_capabilities_required")
                continue
            spec = self._capability_catalog["entities"].get(row["prototype"])
            if spec is None:
                raise _EditError("entity_capabilities_unavailable")
            if row["direction"] not in spec["directions"]:
                raise _EditError("orientation_capability_unavailable")
            try:
                validate_entity_configuration(
                    configuration, row["prototype"], self._capability_catalog,
                    previous_prototype=previous_prototype if entity_id == upgraded_id else None,
                )
            except ContractError as exc:
                raise _EditError(_CONFIGURATION_ERRORS.get(str(exc), "entity_configuration_invalid")) from None
        if self._capability_catalog is not None:
            try:
                validate_configured_entities(entities, configurations, self._capability_catalog)
            except ContractError:
                raise _EditError("entity_recipe_or_module_capability_invalid") from None

    def _submit(self, arguments):
        if not self._entities:
            raise _EditError("empty_draft")
        if len(arguments["stages"]) > self._request["constraints"]["max_stages"]:
            raise _EditError("stage_limit_exceeded")
        self._validate_configurations(self._entities, self._configurations)
        rich = bool(self._wires) or any(not is_legacy_configuration(configuration)
                                      for configuration in self._configurations.values())
        snapshot = self._snapshot.raw
        wire = {key: snapshot[key] for key in
                ("game_version", "source_hash", "world_id", "force_id", "observed_tick", "quality")}
        wire.update({"schema": "designer-proposal-v1", "snapshot_sha256": self._snapshot.sha256,
                     "plan_id": arguments["plan_id"], "entities": list(self._entities.values()),
                     "stages": arguments["stages"]})
        wire = copy.deepcopy(wire)
        if rich:
            wire.update(schema="designer-proposal-v2", entity_configurations=copy.deepcopy(self._configurations),
                        circuit_wires=copy.deepcopy(self._wires))
        try:
            parsed, manifest, catalog = parse_design(wire, self._context)
            if rich:
                from .rich_blueprint import encode_rich_blueprint
                encode_rich_blueprint(parsed, wire["entity_configurations"], wire["circuit_wires"], catalog)
        except ContractError as exc:
            from .functional_validation import FunctionalValidationError
            if isinstance(exc, FunctionalValidationError):
                from .diagnostics import rejection_validation
                result = self._result("submit_plan", status="rejected", error=str(exc))
                result["validation"] = rejection_validation(wire, exc)
                return result
            message = str(exc)
            raise _EditError(message if message in _BLUEPRINT_ERRORS else
                             _PLAN_ERRORS.get(message, "plan_validation_failed")) from None
        self._submitted_wire = wire
        self._submitted_proposal = parsed
        self._revision += 1
        self._notify()
        return self._result("submit_plan", status="accepted_offline")

    def call(self, name, arguments):
        """Apply one whitelisted function; every rejected operation is atomic."""
        if type(name) is not str or name not in self._schemas:
            return self._result(None, status="rejected", error="unknown_tool")
        try:
            if type(arguments) is not dict or len(canonical_bytes(arguments)) > MAX_TOOL_ARGUMENT_BYTES:
                raise _EditError("invalid_tool_arguments")
            assert_no_credentials(arguments)
            validate_wire(arguments, self._schemas[name])
            arguments = copy.deepcopy(arguments)
            if name == "read_grid":
                return self._result(name, status="grid_read")
            if name == "get_entity_capabilities":
                prototype = arguments["prototype"]
                if prototype not in self._snapshot.prototypes:
                    raise _EditError("unsupported_prototype")
                capabilities = copy.deepcopy(self._snapshot.prototypes[prototype])
                capabilities.update({
                    "prototype": prototype, "supported_directions": [0, 4, 8, 12],
                    "supported_recipe_names": sorted(self._snapshot.recipes),
                    "recipe_validation": "unlocked_name_only",
                    "native_recipe_compatibility_qualified": False,
                    "supported_qualities": ["normal"], "modules": False,
                    "parameter_settings": False, "upgrade_relationships_qualified": False,
                    "configuration_capabilities_known": False,
                })
                if self._capability_catalog is not None:
                    spec = self._capability_catalog["entities"].get(prototype)
                    if spec is not None:
                        capabilities.update({
                            "supported_directions": spec["directions"],
                            "supported_qualities": spec["qualities"],
                            "modules": any(spec["module_slots"].values()) and bool(spec["allowed_modules"]),
                            "module_blueprint_mapping_known": spec.get("module_inventory_index") is not None,
                            "circuit_connectors": spec.get("circuit_connectors", {}),
                            "parameter_settings": bool(spec["settings_kinds"]),
                            "configuration_capabilities_known": True,
                            "configuration_spec": spec,
                            "capability_catalog_source": self._capability_catalog["source"],
                            "module_capabilities": {item: self._capability_catalog["modules"][item]
                                                    for item in spec["allowed_modules"]},
                            "signal_capabilities": self._capability_catalog["signals"],
                            "upgrade_validation": "declared_catalog_relationship",
                        })
                result = self._result(name, status="capabilities_read")
                result["capabilities"] = copy.deepcopy(capabilities)
                return result
            if self._submitted_wire is not None:
                raise _EditError("draft_frozen_after_submit")
            if name == "submit_plan":
                return self._submit(arguments)
            if name in ("connect_entities", "disconnect_entities"):
                wires = copy.deepcopy(self._wires)
                key = wire_key(arguments)
                if name == "connect_entities":
                    wires.append(canonical_wire(arguments))
                else:
                    matches = [wire for wire in wires if wire_key(wire) == key]
                    if not matches:
                        raise _EditError("circuit_connection_missing")
                    wires = [wire for wire in wires if wire_key(wire) != key]
                try:
                    wires = validate_circuit_wires(wires, self._entities, self._capability_catalog)
                except ContractError:
                    raise _EditError("circuit_connection_invalid") from None
                self._wires = wires
                self._revision += 1
                self._notify()
                return self._result(name, status="draft_updated")
            entities = copy.deepcopy(self._entities)
            configurations = copy.deepcopy(self._configurations)
            wires = copy.deepcopy(self._wires)
            selected = self._selected
            entity_id = arguments["id"]
            previous_prototype = None
            if name == "place_entity":
                if entity_id in entities:
                    raise _EditError("duplicate_entity_id")
                entities[entity_id] = {
                    "id": entity_id, "prototype": arguments["prototype"],
                    "position": {"x": arguments["x"], "y": arguments["y"]},
                    "direction": arguments["direction"], "recipe": arguments["recipe"],
                }
                configurations[entity_id] = _default_configuration()
            else:
                if entity_id not in entities:
                    raise _EditError("unknown_draft_entity")
                if name == "move_entity":
                    entities[entity_id]["position"] = {"x": arguments["x"], "y": arguments["y"]}
                    if arguments["direction"] is not None:
                        entities[entity_id]["direction"] = arguments["direction"]
                elif name == "rotate_entity":
                    entities[entity_id]["direction"] = arguments["direction"]
                elif name == "upgrade_entity":
                    previous_prototype = entities[entity_id]["prototype"]
                    entities[entity_id]["prototype"] = arguments["prototype"]
                elif name == "remove_entity":
                    del entities[entity_id]
                    del configurations[entity_id]
                    wires = [wire for wire in wires if entity_id not in (wire["from_id"], wire["to_id"])]
                    if selected == entity_id:
                        selected = None
                elif name == "select_entity":
                    selected = entity_id
                elif name == "set_recipe":
                    entities[entity_id]["recipe"] = arguments["recipe"]
                elif name == "configure_entity":
                    configurations[entity_id] = arguments["configuration"]
            self._validate_geometry(entities)
            self._validate_configurations(entities, configurations,
                                          upgraded_id=entity_id if name == "upgrade_entity" else None,
                                          previous_prototype=previous_prototype)
            try:
                wires = validate_circuit_wires(wires, entities, self._capability_catalog)
            except ContractError:
                raise _EditError("circuit_connection_invalid") from None
            self._entities = entities
            self._configurations = configurations
            self._wires = wires
            self._selected = selected
            self._revision += 1
            self._notify()
            return self._result(name, status="draft_updated")
        except _EditError as exc:
            return self._result(name, status="rejected", error=str(exc))
        except (ContractError, PrivateConfigError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            return self._result(name, status="rejected", error="invalid_tool_arguments")


def replay_tool_trace(request, context, trace):
    """Bind accepted geometry to the exact recorded model actions and results.

    This is an offline replay. It neither calls a provider nor reads a file, key
    or game process. No output can substitute different geometry or witnesses.
    """
    try:
        assert_no_credentials(trace)
        if type(trace) is not dict or set(trace) != {"schema", "calls"} \
                or trace["schema"] != "designer-model-tool-trace-v1" \
                or len(canonical_bytes(trace)) > MAX_TOOL_TRACE_BYTES:
            raise ContractError("Invalid model tool trace")
        calls = trace["calls"]
        if type(calls) is not list or not 1 <= len(calls) <= MAX_TOOL_CALLS:
            raise ContractError("Invalid model tool trace")
        controller = DraftGridController(request, context)
        call_ids = set()
        for row in calls:
            if controller.submitted_wire is not None:
                raise ContractError("Invalid model tool trace")
            if type(row) is not dict or set(row) != {"call_id", "name", "arguments", "raw_arguments", "result"}:
                raise ContractError("Invalid model tool trace")
            call_id = row["call_id"]
            if type(call_id) is not str or not _CALL_ID.fullmatch(call_id) or call_id in call_ids:
                raise ContractError("Invalid model tool trace")
            call_ids.add(call_id)
            if type(row["name"]) is not str or row["name"] not in _TOOLS \
                    or type(row["raw_arguments"]) is not str \
                    or len(row["raw_arguments"].encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
                raise ContractError("Invalid model tool trace")
            parsed_arguments = load_json(row["raw_arguments"])
            if canonical_bytes(parsed_arguments) != canonical_bytes(row["arguments"]):
                raise ContractError("Invalid model tool trace")
            actual = controller.call(row["name"], parsed_arguments)
            if canonical_bytes(actual) != canonical_bytes(row["result"]):
                raise ContractError("Invalid model tool trace")
        if controller.submitted_wire is None:
            raise ContractError("Invalid model tool trace")
        return controller.submitted_wire
    except (ContractError, PrivateConfigError, ValueError, TypeError, KeyError,
            RecursionError, UnicodeError, OverflowError):
        raise ContractError("Invalid model tool trace") from None
