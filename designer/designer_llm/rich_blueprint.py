"""Separate offline compiler for the documented Factorio 2.0.77 rich fields.

Field mappings were checked against the official version-pinned Runtime API:
https://lua-api.factorio.com/2.0.77/concepts/BlueprintEntity.html
https://lua-api.factorio.com/2.0.77/concepts/InventoryPosition.html
https://lua-api.factorio.com/2.0.77/concepts/BlueprintInsertPlan.html
https://lua-api.factorio.com/2.0.77/concepts/ArithmeticCombinatorParameters.html
https://lua-api.factorio.com/2.0.77/concepts/CircuitCondition.html
https://lua-api.factorio.com/2.0.77/concepts/BlueprintWire.html

Only explicitly declared capabilities are used. Compiling a blueprint does
not establish native import compatibility, construction, payment or flow.
"""

from __future__ import annotations

import base64
import copy
import math
import re
import zlib

from ._reference_contract import (
    MAX_BLUEPRINT_BYTES, MAX_ENTITIES, SUPPORTED_BLUEPRINT_VERSION,
    ContractError, _assert_proposal_integrity, canonical_bytes, load_json,
)
from .entity_configuration import (
    configuration_schema, validate_capability_catalog, validate_entity_configuration,
)
from .private_config import PrivateConfigError, assert_no_credentials
from .proposal_schema import validate_wire


MAX_RICH_WIRES = 256
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_RECIPE_TYPES = {"assembling-machine", "rocket-silo"}
_ENABLE_TYPES = {"assembling-machine", "furnace", "inserter", "pump"}


def _bad(code):
    raise ContractError(code)


def _signal(signal):
    result = {"type": signal["type"], "name": signal["name"]}
    if signal["quality"] is not None:
        result["quality"] = signal["quality"]
    return result


def _arithmetic_operand(parameters, prefix, operand):
    if operand["signal"] is not None:
        parameters[prefix + "_signal"] = _signal(operand["signal"])
    else:
        parameters[prefix + "_constant"] = operand["constant"]
    # These documented fields are preserved even for a constant operand;
    # native arithmetic only uses their network selection when reading signals.
    parameters[prefix + "_signal_networks"] = copy.deepcopy(operand["networks"])


def _compile_settings(entry, settings, spec):
    if settings is None:
        return
    if settings["kind"] == "bundle":
        for value in settings["values"]:
            _compile_settings(entry, value, spec)
        return
    entity_type = spec.get("entity_type")
    kind = settings["kind"]
    if kind == "arithmetic":
        if entity_type != "arithmetic-combinator":
            _bad("arithmetic_blueprint_mapping_unavailable")
        parameters = {"operation": settings["operation"], "output_signal": _signal(settings["output_signal"])}
        _arithmetic_operand(parameters, "first", settings["first"])
        _arithmetic_operand(parameters, "second", settings["second"])
        entry["control_behavior"] = {"arithmetic_conditions": parameters}
    elif kind == "splitter":
        if entity_type != "splitter":
            _bad("splitter_blueprint_mapping_unavailable")
        entry["input_priority"] = settings["input_priority"]
        entry["output_priority"] = settings["output_priority"]
        if settings["filter"] is not None:
            entry["filter"] = settings["filter"]
    elif kind == "inserter":
        if entity_type != "inserter":
            _bad("inserter_blueprint_mapping_unavailable")
        filter_slots = spec.get("filter_slots")
        if type(filter_slots) is not int or len(settings["filters"]) > filter_slots:
            _bad("inserter_filter_mapping_unavailable")
        entry["filter_mode"] = settings["filter_mode"]
        entry["use_filters"] = bool(settings["filters"])
        entry["filters"] = [{"index": index, "name": name}
                            for index, name in enumerate(settings["filters"], 1)]
        if settings["stack_size"] is not None:
            maximum = spec.get("max_stack_size")
            if type(maximum) is not int or settings["stack_size"] > maximum:
                _bad("inserter_stack_size_mapping_unavailable")
            entry["override_stack_size"] = settings["stack_size"]
    elif kind == "enable_condition":
        if entity_type not in _ENABLE_TYPES:
            _bad("enable_condition_blueprint_mapping_unavailable")
        first, second = settings["first"], settings["second"]
        if first["signal"] is None or first["constant"] is not None:
            _bad("enable_condition_first_constant_unsupported")
        if first["networks"] != {"red": True, "green": True} \
                or second["networks"] != {"red": True, "green": True}:
            _bad("enable_condition_network_selection_unsupported")
        condition = {"comparator": settings["comparator"], "first_signal": _signal(first["signal"])}
        if second["signal"] is not None:
            condition["second_signal"] = _signal(second["signal"])
        else:
            condition["constant"] = second["constant"]
        entry["control_behavior"] = {"circuit_enabled": True, "circuit_condition": condition}
    else:
        _bad("rich_settings_mapping_unavailable")


def _compile_modules(entry, configuration, entity, spec):
    modules = configuration["modules"]
    if not modules:
        return
    inventory = spec.get("module_inventory_index")
    if type(inventory) is not int or not 1 <= inventory <= 255:
        _bad("module_inventory_mapping_unavailable")
    if entity.recipe is not None:
        recipe_modules = spec.get("recipe_modules", {})
        if entity.recipe not in recipe_modules \
                or any(row["item"] not in recipe_modules[entity.recipe] for row in modules):
            _bad("module_recipe_mapping_unavailable")
    requests, stack = [], 0
    for row in modules:
        positions = []
        for _ in range(row["count"]):
            # Module inventories use one module per slot. The stable runtime
            # InventoryPosition explicitly documents zero-based stack indices.
            positions.append({"inventory": inventory, "stack": stack, "count": 1})
            stack += 1
        requests.append({"id": {"name": row["item"], "quality": row["quality"]},
                         "items": {"in_inventory": positions}})
    entry["items"] = requests


def _compile_wires(wires, entities, catalog):
    if type(wires) is not list or len(wires) > MAX_RICH_WIRES:
        _bad("invalid_rich_wires")
    by_id = {entity.id: entity for entity in entities}
    numbers = {entity.id: number for number, entity in enumerate(entities, 1)}
    compiled, seen = [], set()
    for wire in wires:
        if type(wire) is not dict or set(wire) != {"from_id", "from_connector", "to_id", "to_connector", "color"}:
            _bad("invalid_rich_wires")
        source_id, target_id = wire["from_id"], wire["to_id"]
        if type(source_id) is not str or type(target_id) is not str \
                or source_id not in by_id or target_id not in by_id:
            _bad("rich_wire_entity_unavailable")
        if source_id == target_id:
            _bad("rich_wire_same_entity")
        source_connector, target_connector = wire["from_connector"], wire["to_connector"]
        if type(source_connector) is not int or type(target_connector) is not int \
                or not 1 <= source_connector <= 255 or not 1 <= target_connector <= 255 \
                or wire["color"] not in ("red", "green", "copper"):
            _bad("invalid_rich_wires")
        source, target = by_id[source_id], by_id[target_id]
        source_capability = catalog["entities"][source.prototype].get("circuit_connectors", {}).get(str(source_connector))
        target_capability = catalog["entities"][target.prototype].get("circuit_connectors", {}).get(str(target_connector))
        if source_capability is None or target_capability is None \
                or source_capability["color"] != wire["color"] or target_capability["color"] != wire["color"]:
            _bad("rich_wire_connector_mapping_unavailable")
        a, b = (numbers[source_id], source_connector), (numbers[target_id], target_connector)
        if a == b:
            _bad("rich_wire_same_connector")
        edge = tuple(sorted((a, b)))
        if edge in seen:
            _bad("duplicate_rich_wire")
        seen.add(edge)
        distance = math.hypot(source.x - target.x, source.y - target.y)
        if distance > min(source_capability["max_distance"], target_capability["max_distance"]):
            _bad("rich_wire_exceeds_declared_reach")
        compiled.append([numbers[source_id], source_connector, numbers[target_id], target_connector])
    return compiled


def rich_blueprint_document(proposal, configurations, wires, catalog):
    """Build exact documented fields; unsupported mappings fail without loss."""
    try:
        _assert_proposal_integrity(proposal)
        assert_no_credentials(configurations)
        assert_no_credentials(wires)
        if type(catalog) is not dict or type(catalog.get("identity")) is not dict:
            _bad("invalid_rich_capability_catalog")
        catalog = validate_capability_catalog(catalog, catalog["identity"])
        # Frozen Proposal has geometry identity. The caller's rich acceptance
        # additionally binds actor/session/life and full context to this catalog.
        for key in ("game_version", "source_hash", "world_id", "force_id", "observed_tick"):
            value = catalog["identity"].get(key)
            expected = proposal.snapshot.raw[key]
            if type(value) is not type(expected) or value != expected:
                _bad("rich_capability_geometry_identity_mismatch")
        if type(configurations) is not dict or set(configurations) != {entity.id for entity in proposal.entities}:
            _bad("rich_entity_configuration_ownership_invalid")
        from .functional_validation import loader_ends
        ends = loader_ends({e.id: e for e in proposal.entities}, proposal.snapshot.prototypes)[0]
        entries = []
        for number, entity in enumerate(proposal.entities, 1):
            configuration = configurations[entity.id]
            validate_wire(configuration, configuration_schema())
            validate_entity_configuration(configuration, entity.prototype, catalog)
            spec = catalog["entities"][entity.prototype]
            if entity.direction not in spec["directions"]:
                _bad("rich_orientation_capability_unavailable")
            entry = {"entity_number": number, "name": entity.prototype,
                     "position": {"x": entity.x, "y": entity.y}, "direction": entity.direction,
                     "quality": configuration["quality"]}
            if entity.id in ends:   # a loader's end
                entry["type"] = ends[entity.id]
            if entity.recipe is not None:
                if spec.get("entity_type") not in _RECIPE_TYPES:
                    _bad("recipe_blueprint_mapping_unavailable")
                if entity.recipe not in spec.get("allowed_recipes", []):
                    _bad("recipe_capability_mapping_unavailable")
                entry["recipe"] = entity.recipe
            if configuration["recipe_quality"] is not None:
                if entity.recipe is None or spec.get("entity_type") not in _RECIPE_TYPES:
                    _bad("recipe_quality_blueprint_mapping_unavailable")
                entry["recipe_quality"] = configuration["recipe_quality"]
            _compile_modules(entry, configuration, entity, spec)
            _compile_settings(entry, configuration["settings"], spec)
            entries.append(entry)
        blueprint = {"item": "blueprint", "label": proposal.raw["plan_id"],
                     "version": SUPPORTED_BLUEPRINT_VERSION, "entities": entries}
        compiled_wires = _compile_wires(wires, proposal.entities, catalog)
        if compiled_wires:
            blueprint["wires"] = compiled_wires
        result = {"blueprint": blueprint}
        if len(canonical_bytes(result)) > MAX_BLUEPRINT_BYTES:
            _bad("rich_blueprint_exceeds_byte_limit")
        return result
    except (PrivateConfigError, TypeError, KeyError, RecursionError, OverflowError):
        raise ContractError("invalid_rich_blueprint_input") from None


def encode_rich_blueprint(proposal, configurations, wires, catalog):
    payload = canonical_bytes(rich_blueprint_document(proposal, configurations, wires, catalog))
    result = "0" + base64.b64encode(zlib.compress(payload, 9)).decode("ascii")
    if len(result) > MAX_BLUEPRINT_BYTES:
        _bad("rich_blueprint_exceeds_byte_limit")
    return result


def decode_rich_blueprint(encoded):
    """Bounded decoder for this compiler's fields, separate from frozen v1."""
    try:
        if type(encoded) is not str or not encoded.startswith("0") or len(encoded) > MAX_BLUEPRINT_BYTES:
            _bad("invalid_rich_blueprint")
        compressed = base64.b64decode(encoded[1:], validate=True)
        stream = zlib.decompressobj()
        payload = stream.decompress(compressed, MAX_BLUEPRINT_BYTES + 1)
        if len(payload) > MAX_BLUEPRINT_BYTES or not stream.eof or stream.unused_data or stream.unconsumed_tail:
            _bad("invalid_rich_blueprint")
        result = load_json(payload.decode("utf-8"))
        canonical_bytes(result)  # Reject number overflow as well as JSON NaN.
        if set(result) != {"blueprint"} or type(result["blueprint"]) is not dict:
            _bad("invalid_rich_blueprint")
        blueprint = result["blueprint"]
        required = {"item", "label", "version", "entities"}
        if required - set(blueprint) or set(blueprint) - (required | {"wires"}) \
                or blueprint["item"] != "blueprint" \
                or type(blueprint["version"]) is not int or blueprint["version"] != SUPPORTED_BLUEPRINT_VERSION \
                or type(blueprint["label"]) is not str or not _ID.fullmatch(blueprint["label"]):
            _bad("invalid_rich_blueprint")
        entities = blueprint["entities"]
        if type(entities) is not list or not 1 <= len(entities) <= MAX_ENTITIES:
            _bad("invalid_rich_blueprint")
        fields = {"entity_number", "name", "position", "direction", "quality", "recipe", "recipe_quality",
                  "items", "control_behavior", "input_priority", "output_priority", "filter",
                  "filters", "filter_mode", "use_filters", "override_stack_size", "type"}
        numbers = set()
        for entity in entities:
            if type(entity) is not dict or {"entity_number", "name", "position", "direction", "quality"} - set(entity) \
                    or set(entity) - fields:
                _bad("invalid_rich_blueprint")
            number = entity["entity_number"]
            if type(number) is not int or not 1 <= number <= MAX_ENTITIES or number in numbers:
                _bad("invalid_rich_blueprint")
            numbers.add(number)
            if type(entity["name"]) is not str or not _NAME.fullmatch(entity["name"]) \
                    or type(entity["quality"]) is not str or not _NAME.fullmatch(entity["quality"]) \
                    or type(entity["direction"]) is not int or entity["direction"] not in (0, 4, 8, 12) \
                    or entity.get("type", "input") not in ("input", "output"):
                _bad("invalid_rich_blueprint")
            position = entity["position"]
            if type(position) is not dict or set(position) != {"x", "y"} \
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in position.values()):
                _bad("invalid_rich_blueprint")
        wires = blueprint.get("wires", [])
        if type(wires) is not list or len(wires) > MAX_RICH_WIRES:
            _bad("invalid_rich_blueprint")
        for wire in wires:
            if type(wire) is not list or len(wire) != 4 \
                    or any(type(value) is not int for value in wire) \
                    or wire[0] not in numbers or wire[2] not in numbers \
                    or not 1 <= wire[1] <= 255 or not 1 <= wire[3] <= 255:
                _bad("invalid_rich_blueprint")
        return result
    except (ContractError, ValueError, TypeError, KeyError, UnicodeError, zlib.error, RecursionError, OverflowError):
        raise ContractError("invalid_rich_blueprint") from None


def rich_blueprint_matches(encoded, proposal, configurations, wires, catalog):
    expected = rich_blueprint_document(proposal, configurations, wires, catalog)
    try:
        actual = decode_rich_blueprint(encoded)
    except ContractError:
        return False
    return canonical_bytes(actual) == canonical_bytes(expected)
