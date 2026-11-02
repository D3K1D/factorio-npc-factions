"""Strict provider wire schema; local contract checks remain authoritative."""

import copy
import math
import re

from ._reference_contract import (
    MAX_ENTITIES, MAX_STAGES, SUPPORTED_GAME_VERSION, ContractError, canonical_bytes, supported_game_version,
)


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _array(items, *, minimum=0, maximum=4096, description=None):
    result = {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
    if description is not None:
        result["description"] = description
    return result


ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
SHA_PATTERN = r"^[0-9a-f]{64}$"
ID = {"type": "string", "pattern": ID_PATTERN}
NAME = {"type": "string", "pattern": NAME_PATTERN}
SHA = {"type": "string", "pattern": SHA_PATTERN}


def proposal_schema(game_version=SUPPORTED_GAME_VERSION):
    """Use arrays for bills, keeping every object closed and fully required.

    Do not claim every configurable model supports Structured Outputs. Schema
    limits and engine physics are checked locally after every provider result.
    """
    entity = _object({
        "id": ID, "prototype": NAME,
        "position": _object({
            "x": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
            "y": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
        }),
        "direction": {"type": "integer", "enum": [0, 4, 8, 12]},
        "recipe": {"type": ["string", "null"], "pattern": NAME_PATTERN},
    })
    witness = _object({
        "kind": {"type": "string", "enum": ["entity_built", "power_connected",
                 "inventory_delta", "product_counter_delta", "resource_extraction_delta",
                 "energy_generated_delta", "research_progress_delta"]},
        "entity_id": ID,
        "item": {"type": ["string", "null"], "pattern": NAME_PATTERN},
        "min_delta": {"type": ["integer", "null"], "minimum": 1, "maximum": 1_000_000},
    })
    witness["description"] = (
        "Target an entity owned by this same stage. entity_built/power_connected require null "
        "item and min_delta. Other kinds require positive min_delta and an allowed item, "
        "except energy_generated_delta and research_progress_delta require null item. "
        "research_progress_delta targets a lab and min_delta >= 1 counts research progress ticks/units. "
        "power_connected targeting a steam engine or pole also witnesses flow in a power-only stage."
    )
    stage = _object({
        "id": ID,
        "depends_on": _array(ID, description="Distinct existing stage IDs; no self-dependency or cycle."),
        "entity_ids": _array(ID, minimum=1, description=(
            "Distinct proposed entities owned by this stage. Every entity belongs to exactly one stage."
        )),
        "planned_bill": _array(_object({
            "item": NAME, "count": {"type": "integer", "minimum": 1, "maximum": 100_000},
        }), minimum=1, maximum=128, description=(
            "Distinct snapshot items; cover every placement item for the entities owned by this stage."
        )),
        "manual_supplies": {**_array(_object({
            "entity_id": ID, "item": NAME,
            "slot": {"type": "string", "enum": ["fuel", "input"]},
        }), maximum=256, description=(
            "NPC hand-carried fuel/input for an entity in this or an earlier stage. Null means none."
        )), "type": ["array", "null"]},
        "witnesses": _array(witness, minimum=1, maximum=32, description=(
            "At least one same-stage product_counter_delta, resource_extraction_delta, or "
            "energy_generated_delta, or research_progress_delta is required. A power-only stage may instead "
            "use power_connected on a steam engine or pole. Construction or inventory witnesses alone are insufficient."
        )),
    })
    stage["description"] = (
        "A functional construction stage owns nonempty geometry, its placement bill, and planned native "
        "flow witnesses together. Do not create a separate witness-only stage."
    )
    return copy.deepcopy(_object({
        "schema": {"type": "string", "enum": ["designer-proposal-v1"]},
        "snapshot_sha256": SHA, "game_version": {"type": "string", "enum": [game_version]},
        "source_hash": SHA, "world_id": ID, "force_id": ID,
        "observed_tick": {"type": "integer", "minimum": 0, "maximum": 1_000_000_000},
        "quality": {"type": "string", "enum": ["normal"]}, "plan_id": ID,
        "entities": _array(entity, minimum=1, maximum=MAX_ENTITIES),
        "stages": _array(stage, minimum=1, maximum=MAX_STAGES),
    }))


def validate_wire(value, schema=None, depth=0):
    """Small strict validator for precisely the generated schema subset."""
    if depth > 12:
        raise ContractError("Proposal nesting exceeds limit")
    schema = proposal_schema() if schema is None else schema
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if type(alternatives) is not list or not 1 <= len(alternatives) <= 8:
            raise ContractError("Invalid proposal schema alternatives")
        for alternative in alternatives:
            try:
                validate_wire(value, alternative, depth + 1)
                return
            except ContractError:
                pass
        raise ContractError("Proposal wire alternatives mismatch")
    types = schema["type"]
    types = types if isinstance(types, list) else [types]
    type_matches = {
        "null": value is None, "string": type(value) is str,
        "integer": type(value) is int, "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "object": type(value) is dict, "array": type(value) is list,
    }
    if not any(type_matches.get(t, False) for t in types):
        raise ContractError("Proposal wire type mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError("Proposal wire value unsupported")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) != set(properties):
            raise ContractError("Proposal wire fields mismatch")
        for key, child in value.items():
            validate_wire(child, properties[key], depth + 1)
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 4096):
            raise ContractError("Proposal array exceeds limit")
        for child in value:
            validate_wire(child, schema["items"], depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ContractError("Proposal string exceeds limit")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ContractError("Proposal string format unsupported")
    elif type(value) in (int, float):
        if (type(value) is float and not math.isfinite(value)) \
                or ("minimum" in schema and value < schema["minimum"]) \
                or ("maximum" in schema and value > schema["maximum"]):
            raise ContractError("Proposal number exceeds limit")


def version_of(value):
    """A proposal's game version when it is one the contract checks, else the one it was written against."""
    version = value.get("game_version") if type(value) is dict else None
    return version if supported_game_version(version) else SUPPORTED_GAME_VERSION


def from_wire(value):
    if len(canonical_bytes(value)) > 200_000:
        raise ContractError("Proposal exceeds byte limit")
    # Additive v1 extension: legacy proposals may omit the optional supplies.
    value = copy.deepcopy(value)
    stages = value.get("stages") if type(value) is dict else None
    if type(stages) is list:
        for stage in stages:
            if type(stage) is dict:
                stage.setdefault("manual_supplies", None)
    validate_wire(value, proposal_schema(version_of(value)))
    result = copy.deepcopy(value)
    for entity in result["entities"]:
        if entity["recipe"] is None:
            del entity["recipe"]
    for stage in result["stages"]:
        if stage["manual_supplies"] is None:
            del stage["manual_supplies"]
        bill = {}
        for entry in stage["planned_bill"]:
            if entry["item"] in bill:
                raise ContractError("Duplicate planned bill item")
            bill[entry["item"]] = entry["count"]
        stage["planned_bill"] = bill
        for witness in stage["witnesses"]:
            for field in ("item", "min_delta"):
                if witness[field] is None:
                    del witness[field]
    return result


def to_wire(value):
    result = copy.deepcopy(value)
    for entity in result["entities"]:
        entity.setdefault("recipe", None)
    for stage in result["stages"]:
        stage.setdefault("manual_supplies", None)
        stage["planned_bill"] = [{"item": item, "count": count}
                                 for item, count in sorted(stage["planned_bill"].items())]
        for witness in stage["witnesses"]:
            witness.setdefault("item", None)
            witness.setdefault("min_delta", None)
    validate_wire(result, proposal_schema(version_of(result)))
    return result
