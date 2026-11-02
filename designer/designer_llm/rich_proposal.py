"""Shared design acceptance: geometry, stages, declared feasibility and rich settings.

The projection is only a geometry check. Full settings and wires stay in the
v2 manifest, its hash, blueprint, and exact model-action replay.
"""

from __future__ import annotations

import copy
import math

from ._reference_contract import ContractError, canonical_bytes, parse_proposal, parse_snapshot
from .entity_configuration import validate_capability_catalog, validate_entity_configuration
from .private_config import assert_no_credentials
from .proposal_schema import ID, from_wire, validate_wire


def circuit_wire_schema():
    fields = {"from_id": ID, "from_connector": {"type": "integer", "minimum": 1, "maximum": 255},
              "to_id": ID, "to_connector": {"type": "integer", "minimum": 1, "maximum": 255},
              "color": {"type": "string", "enum": ["red", "green", "copper"]}}
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def wire_key(wire):
    endpoints = sorted(((wire["from_id"], wire["from_connector"]),
                        (wire["to_id"], wire["to_connector"])))
    return (wire["color"], *endpoints[0], *endpoints[1])


def canonical_wire(wire):
    color, first_id, first_connector, second_id, second_connector = wire_key(wire)
    return {"from_id": first_id, "from_connector": first_connector,
            "to_id": second_id, "to_connector": second_connector, "color": color}


def validate_circuit_wires(wires, entities, catalog):
    if type(wires) is not list or len(wires) > 128:
        raise ContractError("Circuit wire limit exceeded")
    if wires and catalog is None:
        raise ContractError("Circuit connector capabilities required")
    seen = set()
    for wire in wires:
        validate_wire(wire, circuit_wire_schema())
        if wire["from_id"] == wire["to_id"]:
            raise ContractError("Self circuit connection unsupported")
        key = wire_key(wire)
        if key in seen:
            raise ContractError("Duplicate circuit connection")
        seen.add(key)
        endpoints = []
        for prefix in ("from", "to"):
            entity = entities.get(wire[prefix + "_id"])
            if entity is None:
                raise ContractError("Circuit connection entity missing")
            spec = catalog["entities"].get(entity["prototype"], {})
            connector = spec.get("circuit_connectors", {}).get(str(wire[prefix + "_connector"]))
            if connector is None or connector["color"] != wire["color"]:
                raise ContractError("Circuit connector unavailable")
            endpoints.append((entity["position"], connector["max_distance"]))
        first, second = endpoints
        distance = math.hypot(first[0]["x"] - second[0]["x"], first[0]["y"] - second[0]["y"])
        if distance > min(first[1], second[1]):
            raise ContractError("Declared circuit wire reach exceeded")
    return [canonical_wire(wire) for wire in sorted(wires, key=wire_key)]


def capability_catalog(context):
    fact = context.get("entity_capabilities")
    if fact is None or fact["knowledge"] != "known":
        raise ContractError("Configuration capabilities required")
    identity = context["identity"]
    if fact["observed_tick"] != identity["observed_tick"] or fact["revision"] != identity["revision"]:
        raise ContractError("Capability observations are stale")
    return validate_capability_catalog(fact["value"], identity)


def validate_configured_entities(entities, configurations, catalog):
    if type(configurations) is not dict or set(configurations) != set(entities):
        raise ContractError("Entity configuration ownership mismatch")
    for entity_id, entity in entities.items():
        configuration = configurations[entity_id]
        validate_entity_configuration(configuration, entity["prototype"], catalog)
        spec = catalog["entities"][entity["prototype"]]
        if entity["direction"] not in spec["directions"]:
            raise ContractError("Orientation capability unavailable")
        recipe = entity.get("recipe")
        if configuration["recipe_quality"] is not None and recipe is None:
            raise ContractError("Recipe quality requires recipe")
        if recipe is not None and "allowed_recipes" in spec and recipe not in spec["allowed_recipes"]:
            raise ContractError("Recipe incompatible with declared entity")
        if configuration["modules"] and recipe is not None:
            allowed = spec.get("recipe_modules", {}).get(recipe)
            if allowed is None or any(module["item"] not in allowed for module in configuration["modules"]):
                raise ContractError("Recipe module compatibility unavailable")


def geometry_wire(wire):
    if type(wire) is not dict:
        raise ContractError("Invalid design wire")
    if wire.get("schema") == "designer-proposal-v1":
        return copy.deepcopy(wire)
    if wire.get("schema") != "designer-proposal-v2":
        raise ContractError("Unsupported design schema")
    result = copy.deepcopy(wire)
    for field in ("entity_configurations", "circuit_wires"):
        if field not in result:
            raise ContractError("Missing rich design fields")
        del result[field]
    result["schema"] = "designer-proposal-v1"
    # Reject any unrecognized fields in the unchanged v1 projection.
    from_wire(result)
    return result


def parse_design(wire, context):
    """Return checked geometry, complete manifest and optional rich catalog."""
    assert_no_credentials(wire)
    if len(canonical_bytes(wire)) > 200_000:
        raise ContractError("Rich design exceeds byte limit")
    geometry = parse_proposal(from_wire(geometry_wire(wire)), parse_snapshot(context["geometry_snapshot"]))
    from .functional_validation import validate_functional_plan
    validate_functional_plan(geometry, context)
    if wire["schema"] == "designer-proposal-v1":
        return geometry, geometry.raw, None
    catalog = capability_catalog(context)
    entities = {row["id"]: row for row in wire["entities"]}
    configurations = wire["entity_configurations"]
    validate_configured_entities(entities, configurations, catalog)
    wires = validate_circuit_wires(wire["circuit_wires"], entities, catalog)
    if canonical_bytes(wires) != canonical_bytes(wire["circuit_wires"]):
        raise ContractError("Circuit connections must use canonical order")
    manifest = copy.deepcopy(geometry.raw)
    manifest.update(schema="designer-proposal-v2", entity_configurations=copy.deepcopy(configurations),
                    circuit_wires=wires)
    # Derived configured placement requirements never claim inventory/payment.
    requirements = {}
    for stage in geometry.stages:
        counts = {}
        for entity_id in stage.entity_ids:
            entity, configuration = entities[entity_id], configurations[entity_id]
            placement = geometry.snapshot.prototypes[entity["prototype"]]["placement_item"]
            key = (placement, configuration["quality"], "placement")
            counts[key] = counts.get(key, 0) + 1
            for module in configuration["modules"]:
                key = (module["item"], module["quality"], "module")
                counts[key] = counts.get(key, 0) + module["count"]
        requirements[stage.id] = [{"item": item, "quality": quality, "purpose": purpose, "count": count}
                                  for (item, quality, purpose), count in sorted(counts.items())]
    manifest["configured_item_requirements"] = requirements
    return geometry, manifest, catalog
