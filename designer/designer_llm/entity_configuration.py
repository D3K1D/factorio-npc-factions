"""Typed richer entity intent, gated by declared per-prototype capabilities.

This contract is deliberately separate from the frozen normal-geometry v1
blueprint importer. It preserves requested quality/modules/control settings;
it never drops them to force acceptance through the older path.
"""

from __future__ import annotations

import copy
import re

from ._reference_contract import ContractError, canonical_bytes
from .private_config import assert_no_credentials

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_OPERATIONS = {"*", "/", "+", "-", "%", "^", "<<", ">>", "AND", "OR", "XOR"}
_COMPARATORS = {"<", ">", "=", ">=", "<=", "!="}


def configuration_schema():
    """Strict function schema; nullable alternatives never allow extra keys."""
    def obj(fields):
        return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}

    name = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"}
    nullable_name = {"type": ["string", "null"], "pattern": name["pattern"]}
    signal = obj({"type": {"type": "string", "enum": ["item", "fluid", "virtual"]},
                  "name": name, "quality": nullable_name})
    operand = obj({"signal": {"anyOf": [signal, {"type": "null"}]},
                   "constant": {"type": ["integer", "null"], "minimum": -(2**31), "maximum": 2**31 - 1},
                   "networks": obj({"red": {"type": "boolean"}, "green": {"type": "boolean"}})})
    arithmetic = obj({"kind": {"type": "string", "enum": ["arithmetic"]},
                      "first": operand, "second": operand,
                      "operation": {"type": "string", "enum": sorted(_OPERATIONS)}, "output_signal": signal})
    splitter = obj({"kind": {"type": "string", "enum": ["splitter"]},
                    "input_priority": {"type": "string", "enum": ["none", "left", "right"]},
                    "output_priority": {"type": "string", "enum": ["none", "left", "right"]},
                    "filter": nullable_name})
    inserter = obj({"kind": {"type": "string", "enum": ["inserter"]},
                    "filter_mode": {"type": "string", "enum": ["whitelist", "blacklist"]},
                    "filters": {"type": "array", "items": name, "maxItems": 128},
                    "stack_size": {"type": ["integer", "null"], "minimum": 1, "maximum": 255}})
    condition = obj({"kind": {"type": "string", "enum": ["enable_condition"]},
                     "first": operand, "second": operand,
                     "comparator": {"type": "string", "enum": sorted(_COMPARATORS)}})
    bundle = obj({"kind": {"type": "string", "enum": ["bundle"]},
                  "values": {"type": "array", "minItems": 1, "maxItems": 4,
                             "items": {"anyOf": [arithmetic, splitter, inserter, condition]}}})
    return copy.deepcopy(obj({
        "quality": name, "recipe_quality": nullable_name,
        "modules": {"type": "array", "maxItems": 64, "items": obj({
            "item": name, "quality": name, "count": {"type": "integer", "minimum": 1, "maximum": 64}})},
        "settings": {"anyOf": [arithmetic, splitter, inserter, condition, bundle, {"type": "null"}]},
    }))


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError("Configuration fields mismatch")


def _name(value):
    if type(value) is not str or not _NAME.fullmatch(value):
        raise ContractError("Invalid configuration prototype name")
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ContractError("Invalid configuration quantity")
    return value


def _names(values):
    if type(values) is not list or len(values) > 128:
        raise ContractError("Invalid capability names")
    names = [_name(value) for value in values]
    if len(names) != len(set(names)):
        raise ContractError("Duplicate capability name")
    return names


def validate_capability_catalog(catalog, identity):
    """Require explicit catalog provenance and exact observed world identity.

    Values are observations supplied by a future exporter or synthetic tests;
    this module never infers module slots/unlocks from a prototype's name.
    """
    assert_no_credentials(catalog)
    if len(canonical_bytes(catalog)) > 64_000:
        raise ContractError("Capability catalog exceeds bound")
    _keys(catalog, {"schema", "identity", "source", "entities", "modules", "signals"})
    if type(identity) is not dict or type(catalog["identity"]) is not dict \
            or catalog["schema"] != "designer-entity-capabilities-v1" \
            or canonical_bytes(catalog["identity"]) != canonical_bytes(identity):
        raise ContractError("Capability catalog identity mismatch")
    if catalog["source"] not in ("synthetic_fixture", "native_export_unverified"):
        raise ContractError("Capability catalog source unsupported")
    if type(catalog["entities"]) is not dict or not 1 <= len(catalog["entities"]) <= 128 \
            or type(catalog["modules"]) is not dict or len(catalog["modules"]) > 128:
        raise ContractError("Invalid capability catalog entries")
    if type(catalog["signals"]) is not dict or len(catalog["signals"]) > 128:
        raise ContractError("Invalid signal capability catalog")
    for name, signal in catalog["signals"].items():
        _name(name)
        _keys(signal, {"type", "qualities"})
        if signal["type"] not in ("item", "fluid", "virtual"):
            raise ContractError("Invalid signal type")
        qualities = _names(signal["qualities"])
        if signal["type"] == "fluid" and qualities:
            raise ContractError("Fluid capability cannot claim quality")
    for item, qualities in catalog["modules"].items():
        _name(item)
        if not _names(qualities):
            raise ContractError("Module qualities are unknown")
    for prototype, spec in catalog["entities"].items():
        _name(prototype)
        required = {"qualities", "directions", "upgrade_targets", "module_slots",
                    "allowed_modules", "settings_kinds", "recipe_qualities"}
        optional = {"entity_type", "module_inventory_index", "circuit_connectors", "allowed_recipes",
                    "recipe_modules", "filter_slots", "max_stack_size"}
        if type(spec) is not dict or required - set(spec) or set(spec) - (required | optional):
            raise ContractError("Configuration fields mismatch")
        qualities = _names(spec["qualities"])
        if not qualities or type(spec["directions"]) is not list or not spec["directions"] \
                or any(type(value) is not int or value not in (0, 4, 8, 12) for value in spec["directions"]):
            raise ContractError("Invalid entity quality or orientation capability")
        if len(spec["directions"]) != len(set(spec["directions"])):
            raise ContractError("Duplicate allowed direction")
        targets = _names(spec["upgrade_targets"])
        if any(target not in catalog["entities"] for target in targets):
            raise ContractError("Upgrade target absent from catalog")
        if type(spec["module_slots"]) is not dict or set(spec["module_slots"]) != set(qualities):
            raise ContractError("Module slots require each entity quality")
        for slots in spec["module_slots"].values():
            _integer(slots, 0, 64)
        allowed = _names(spec["allowed_modules"])
        if any(item not in catalog["modules"] for item in allowed):
            raise ContractError("Allowed module absent from catalog")
        settings = _names(spec["settings_kinds"])
        if any(kind not in ("arithmetic", "splitter", "inserter", "enable_condition") for kind in settings):
            raise ContractError("Configuration adapter is not implemented")
        _names(spec["recipe_qualities"])
        if "entity_type" in spec:
            _name(spec["entity_type"])
        inventory = spec.get("module_inventory_index")
        if inventory is not None:
            _integer(inventory, 1, 255)
        connectors = spec.get("circuit_connectors", {})
        if type(connectors) is not dict or len(connectors) > 16:
            raise ContractError("Invalid circuit connector capabilities")
        for connector_id, connector in connectors.items():
            if type(connector_id) is not str or not connector_id.isascii() \
                    or not connector_id.isdecimal() or str(int(connector_id)) != connector_id:
                raise ContractError("Invalid circuit connector capabilities")
            _integer(int(connector_id), 1, 255)
            _keys(connector, {"color", "max_distance"})
            distance = connector["max_distance"]
            if connector["color"] not in ("red", "green", "copper") \
                    or type(distance) not in (int, float) or not 0 < distance <= 1_000:
                raise ContractError("Invalid circuit connector capabilities")
        if "allowed_recipes" in spec:
            _names(spec["allowed_recipes"])
        if "recipe_modules" in spec:
            if type(spec["recipe_modules"]) is not dict or len(spec["recipe_modules"]) > 128:
                raise ContractError("Invalid recipe module capabilities")
            for recipe, module_names in spec["recipe_modules"].items():
                _name(recipe)
                if any(item not in allowed for item in _names(module_names)):
                    raise ContractError("Recipe module absent from entity capabilities")
        if "filter_slots" in spec:
            _integer(spec["filter_slots"], 0, 128)
        if "max_stack_size" in spec:
            _integer(spec["max_stack_size"], 1, 255)
    return copy.deepcopy(catalog)


def _signal(signal, catalog):
    _keys(signal, {"type", "name", "quality"})
    name = _name(signal["name"])
    capability = catalog["signals"].get(name)
    if capability is None or signal["type"] != capability["type"]:
        raise ContractError("Signal absent from capabilities")
    if signal["quality"] is not None:
        if _name(signal["quality"]) not in capability["qualities"]:
            raise ContractError("Signal quality is unavailable")


def _operand(operand, catalog):
    _keys(operand, {"signal", "constant", "networks"})
    if (operand["signal"] is None) == (operand["constant"] is None):
        raise ContractError("Operand must choose exactly one signal or constant")
    if operand["signal"] is not None:
        _signal(operand["signal"], catalog)
    else:
        _integer(operand["constant"], -(2**31), 2**31 - 1)
    _keys(operand["networks"], {"red", "green"})
    if any(type(value) is not bool for value in operand["networks"].values()):
        raise ContractError("Invalid operand circuit network")


def _item_filter(name, catalog):
    name = _name(name)
    capability = catalog["signals"].get(name)
    if capability is None or capability["type"] != "item":
        raise ContractError("Filter item absent from capabilities")


def validate_entity_configuration(configuration, prototype, catalog, *, previous_prototype=None):
    """Validate requested full settings; geometry belongs to the draft editor.

    A validated intent is not yet a native blueprint import or built receipt.
    Workstation replacement, entity quality, recipe quality and module quality
    are distinct choices; modules consume declared slots of the chosen quality.
    """
    _name(prototype)
    if previous_prototype is not None:
        _name(previous_prototype)
    assert_no_credentials(configuration)
    if len(canonical_bytes(configuration)) > 16_000:
        raise ContractError("Entity configuration exceeds bound")
    _keys(configuration, {"quality", "recipe_quality", "modules", "settings"})
    spec = catalog["entities"].get(prototype)
    if spec is None:
        raise ContractError("Entity has no configuration capabilities")
    quality = _name(configuration["quality"])
    if quality not in spec["qualities"]:
        raise ContractError("Entity quality is unavailable")
    recipe_quality = configuration["recipe_quality"]
    if recipe_quality is not None and _name(recipe_quality) not in spec["recipe_qualities"]:
        raise ContractError("Recipe quality is unavailable")
    if previous_prototype is not None and previous_prototype != prototype:
        previous = catalog["entities"].get(previous_prototype)
        if previous is None or prototype not in previous["upgrade_targets"]:
            raise ContractError("Workstation upgrade is unavailable")
    modules = configuration["modules"]
    if type(modules) is not list or len(modules) > 64:
        raise ContractError("Invalid module configuration")
    count, seen = 0, set()
    for row in modules:
        _keys(row, {"item", "quality", "count"})
        item, module_quality = _name(row["item"]), _name(row["quality"])
        if item not in spec["allowed_modules"] or module_quality not in catalog["modules"].get(item, []):
            raise ContractError("Module item or quality is unavailable")
        identity = (item, module_quality)
        if identity in seen:
            raise ContractError("Duplicate module configuration")
        seen.add(identity)
        count += _integer(row["count"], 1, 64)
    if count > spec["module_slots"][quality]:
        raise ContractError("Module capacity exceeded")
    settings = configuration["settings"]
    if settings is not None:
        if type(settings) is dict and settings.get("kind") == "bundle":
            _keys(settings, {"kind", "values"})
            values = settings["values"]
            if type(values) is not list or not 1 <= len(values) <= 4:
                raise ContractError("Invalid configuration settings bundle")
            kinds = []
            for value in values:
                if type(value) is not dict or value.get("kind") not in spec["settings_kinds"]:
                    raise ContractError("Settings are unsupported by this entity")
                kinds.append(value["kind"])
                child = copy.deepcopy(configuration)
                child["settings"] = value
                validate_entity_configuration(child, prototype, catalog, previous_prototype=previous_prototype)
            if len(kinds) != len(set(kinds)):
                raise ContractError("Duplicate configuration settings kind")
            return copy.deepcopy(configuration)
        if type(settings) is not dict or settings.get("kind") not in spec["settings_kinds"]:
            raise ContractError("Settings are unsupported by this entity")
        kind = settings["kind"]
        if kind == "arithmetic":
            _keys(settings, {"kind", "first", "second", "operation", "output_signal"})
            _operand(settings["first"], catalog)
            _operand(settings["second"], catalog)
            if type(settings["operation"]) is not str or settings["operation"] not in _OPERATIONS:
                raise ContractError("Unsupported arithmetic operation")
            _signal(settings["output_signal"], catalog)
        elif kind == "splitter":
            _keys(settings, {"kind", "input_priority", "output_priority", "filter"})
            if any(settings[key] not in ("none", "left", "right") for key in ("input_priority", "output_priority")):
                raise ContractError("Invalid splitter priority")
            if settings["filter"] is not None:
                _item_filter(settings["filter"], catalog)
        elif kind == "inserter":
            _keys(settings, {"kind", "filter_mode", "filters", "stack_size"})
            if settings["filter_mode"] not in ("whitelist", "blacklist"):
                raise ContractError("Invalid inserter filter mode")
            for item in _names(settings["filters"]):
                _item_filter(item, catalog)
            if "filter_slots" in spec and len(settings["filters"]) > spec["filter_slots"]:
                raise ContractError("Inserter filter capacity exceeded")
            if settings["stack_size"] is not None:
                _integer(settings["stack_size"], 1, 255)
                if "max_stack_size" in spec and settings["stack_size"] > spec["max_stack_size"]:
                    raise ContractError("Inserter stack size capability exceeded")
        elif kind == "enable_condition":
            _keys(settings, {"kind", "first", "second", "comparator"})
            _operand(settings["first"], catalog)
            _operand(settings["second"], catalog)
            if type(settings["comparator"]) is not str or settings["comparator"] not in _COMPARATORS:
                raise ContractError("Invalid circuit comparator")
    return copy.deepcopy(configuration)


def legacy_geometry_compatible(configuration):
    """Never silently strip rich settings to pass the v1 exporter."""
    return (configuration["quality"] == "normal" and configuration["recipe_quality"] in (None, "normal")
            and configuration["modules"] == [] and configuration["settings"] is None)


is_legacy_configuration = legacy_geometry_compatible
