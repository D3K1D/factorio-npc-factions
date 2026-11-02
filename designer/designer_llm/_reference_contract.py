"""Offline, structural contract for proposed Factorio construction plans.

The v1 schema has additive optional functional prototype facts and manual stage
supplies. Validation here never qualifies native flow, reach, payment or success;
shared acceptance also checks declared feasibility in functional_validation.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping
import zlib


SNAPSHOT_SCHEMA = "designer-snapshot-v1"
DESIGN_SCHEMA = "designer-proposal-v1"
SUPPORTED_GAME_VERSION = "2.0.77"
SUPPORTED_BLUEPRINT_VERSION = 2 << 48 | 77 << 16
MAX_ENTITIES = 128
MAX_STAGES = 64
MAX_BLUEPRINT_BYTES = 1_000_000
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\Z")
# The game versions the contract checks: 2.0.77, which it was written against, and Factorio 2.1
# (2026-10-23). Its geometry checks use the prototype facts each snapshot carries (sizes, vectors,
# fluid boxes), which the game exports itself.
_SUPPORTED_VERSION = re.compile(r"2\.0\.77|2\.1\.\d{1,5}\Z")


def supported_game_version(value) -> bool:
    return isinstance(value, str) and _SUPPORTED_VERSION.fullmatch(value) is not None

_FLOW = {"product_counter_delta", "resource_extraction_delta", "energy_generated_delta",
         "research_progress_delta"}
_WITNESSES = _FLOW | {"entity_built", "power_connected", "inventory_delta"}


class ContractError(ValueError):
    pass


def _bad(message: str) -> None:
    raise ContractError(message)


def _keys(row: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(row, dict) or set(row) - (required | optional) or required - set(row):
        _bad("Missing or unknown contract field")
    return row


def _text(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _bad(f"Invalid {label}")
    return value


def _integer(value: Any, label: str, lower: int = 0, upper: int = 1_000_000_000) -> int:
    if type(value) is not int or not lower <= value <= upper:
        _bad(f"Invalid {label}")
    return value


def _number(value: Any, label: str, lower: float = -1_000_000, upper: float = 1_000_000) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        _bad(f"Invalid {label}")
    return float(value)


def _unique_names(values: Any, label: str, pattern: re.Pattern[str] = _NAME) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 4096:
        _bad(f"Invalid {label}")
    out = tuple(_text(x, pattern, label) for x in values)
    if len(out) != len(set(out)):
        _bad(f"Duplicate {label}")
    return out


def _rect(raw: Any) -> tuple[float, float, float, float]:
    if not isinstance(raw, list) or len(raw) != 4:
        _bad("Invalid bounding box")
    left, top, right, bottom = (_number(x, "bounding box") for x in raw)
    if not left < right or not top < bottom:
        _bad("Empty or reversed bounding box")
    return left, top, right, bottom


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _bad("Duplicate JSON object key")
        result[key] = value
    return result


def load_json(value: str) -> dict:
    if not isinstance(value, str) or len(value) > MAX_BLUEPRINT_BYTES:
        _bad("JSON input is too large")
    try:
        result = json.loads(value, object_pairs_hook=_json_pairs,
                            parse_constant=lambda _: _bad("Nonfinite JSON number"))
    except (ValueError, TypeError) as exc:
        raise ContractError("Malformed JSON") from exc
    if not isinstance(result, dict):
        _bad("JSON root must be an object")
    return result


def canonical_bytes(row: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("Unserializable contract") from exc


def snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return parse_snapshot(copy.deepcopy(dict(snapshot))).sha256


@dataclass(frozen=True)
class Snapshot:
    raw: dict
    sha256: str
    bounds: tuple[float, float, float, float]
    occupied: tuple[tuple[float, float, float, float], ...]
    prototypes: dict[str, dict]
    recipes: frozenset[str]
    items: frozenset[str]


def parse_snapshot(row: dict) -> Snapshot:
    _keys(row, {"schema", "game_version", "source_hash", "world_id", "force_id",
                "observed_tick", "quality", "researched_technologies", "unlocked_recipes",
                "items", "prototypes", "survey_bounds", "occupied_boxes"})
    if row["schema"] != SNAPSHOT_SCHEMA or row["quality"] != "normal" \
            or not supported_game_version(row["game_version"]):
        _bad("Unsupported snapshot schema, quality or game version")
    _text(row["source_hash"], _SHA, "source hash")
    _text(row["world_id"], _ID, "world ID")
    _text(row["force_id"], _ID, "force ID")
    _integer(row["observed_tick"], "observed tick")
    _unique_names(row["researched_technologies"], "research")
    recipes = frozenset(_unique_names(row["unlocked_recipes"], "recipe"))
    items = frozenset(_unique_names(row["items"], "item"))
    bounds = _rect(row["survey_bounds"])
    if not isinstance(row["occupied_boxes"], list) or len(row["occupied_boxes"]) > 4096:
        _bad("Invalid occupied boxes")
    occupied = tuple(_rect(x) for x in row["occupied_boxes"])
    prototypes = row["prototypes"]
    if not isinstance(prototypes, dict) or not 1 <= len(prototypes) <= 256:
        _bad("Invalid prototype catalog")
    for name, spec in prototypes.items():
        _text(name, _NAME, "prototype")
        _keys(spec, {"placement_item", "width", "height"}, {
            "type", "energy_source", "fuel_categories", "crafting_categories", "resource_categories",
            "mining_area_radius", "drop_vector", "pickup_vector", "supply_area_distance",
            "max_wire_distance", "produces_power", "fluid_boxes", "requires_water", "container_distance"})
        if "type" in spec:
            _text(spec["type"], _NAME, "prototype type")
        if "energy_source" in spec and spec["energy_source"] not in (
                "burner", "electric", "void", "heat", "fluid", "none"):
            _bad("Invalid prototype energy source")
        for field in ("fuel_categories", "crafting_categories", "resource_categories"):
            if field in spec:
                _unique_names(spec[field], field)
        for field in ("mining_area_radius", "supply_area_distance", "max_wire_distance", "container_distance"):
            if field in spec:
                _number(spec[field], field, 0, 1000000)
        for field in ("drop_vector", "pickup_vector"):
            if field in spec:
                if type(spec[field]) is not list or len(spec[field]) != 2:
                    _bad("Invalid prototype vector")
                for component in spec[field]:
                    _number(component, field)
        if "produces_power" in spec and type(spec["produces_power"]) is not bool:
            _bad("Invalid prototype power production flag")
        if "requires_water" in spec and type(spec["requires_water"]) is not bool:
            _bad("Invalid prototype water requirement flag")
        if "fluid_boxes" in spec:
            fluid_boxes = spec["fluid_boxes"]
            if type(fluid_boxes) is not list or len(fluid_boxes) > 256:
                _bad("Invalid prototype fluid boxes")
            indices = set()
            for box in fluid_boxes:
                _keys(box, {"index", "production", "filter", "connections"})
                box_index = _integer(box["index"], "fluid box index", 1)
                if box_index in indices:
                    _bad("Duplicate fluid box index")
                indices.add(box_index)
                if box["production"] not in ("input", "output", "input-output", "none"):
                    _bad("Invalid fluid box production")
                if box["filter"] is not None:
                    _text(box["filter"], _NAME, "fluid filter")
                if type(box["connections"]) is not list or len(box["connections"]) > 256:
                    _bad("Invalid fluid connections")
                for connection in box["connections"]:
                    _keys(connection, {"position", "direction", "type", "max_underground_distance"})
                    position = connection["position"]
                    if type(position) is not list or len(position) != 2:
                        _bad("Invalid fluid connection position")
                    for component in position:
                        _number(component, "fluid connection position")
                    if _integer(connection["direction"], "fluid direction", 0, 15) not in (0, 4, 8, 12):
                        _bad("Invalid fluid direction")
                    if connection["type"] not in ("normal", "underground"):
                        _bad("Invalid fluid connection type")
                    if connection["max_underground_distance"] is not None:
                        _number(connection["max_underground_distance"], "underground distance", 0)
        if _text(spec["placement_item"], _NAME, "placement item") not in items:
            _bad("Prototype placement item absent from snapshot")
        _number(spec["width"], "prototype width", 0.01, 256)
        _number(spec["height"], "prototype height", 0.01, 256)
    owned = copy.deepcopy(row)
    return Snapshot(owned, hashlib.sha256(canonical_bytes(owned)).hexdigest(), bounds,
                    occupied, owned["prototypes"], recipes, items)


def _assert_snapshot_integrity(snapshot: Snapshot) -> None:
    if not isinstance(snapshot, Snapshot):
        _bad("Expected a parsed snapshot")
    rebuilt = parse_snapshot(copy.deepcopy(snapshot.raw))
    if rebuilt.sha256 != snapshot.sha256 or rebuilt.bounds != snapshot.bounds \
            or rebuilt.occupied != snapshot.occupied or rebuilt.prototypes != snapshot.prototypes \
            or rebuilt.recipes != snapshot.recipes or rebuilt.items != snapshot.items:
        _bad("Parsed snapshot changed after validation")


@dataclass(frozen=True)
class Entity:
    id: str
    prototype: str
    x: float
    y: float
    direction: int
    recipe: str | None
    mirror: bool = False   # built mirrored (2.x): its connections flip left to right in its own frame


def power_only_flow(entity_ids, witnesses, entities, catalog):
    """A pole/steam-engine connection can witness a stage devoted to power support."""
    if any(catalog[entities[eid].prototype].get("type") in (
            "lab", "furnace", "assembling-machine", "mining-drill") for eid in entity_ids):
        return False
    return any(w["kind"] == "power_connected" and (
        entities[w["entity_id"]].prototype == "steam-engine"
        or catalog[entities[w["entity_id"]].prototype].get("type") == "electric-pole")
        for w in witnesses)


@dataclass(frozen=True)
class Stage:
    id: str
    depends_on: tuple[str, ...]
    entity_ids: tuple[str, ...]
    planned_bill: dict[str, int]
    witnesses: tuple[dict, ...]
    manual_supplies: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Proposal:
    raw: dict
    snapshot_sha256: str
    entities: tuple[Entity, ...]
    stages: tuple[Stage, ...]
    snapshot: Snapshot
    manifest_sha256: str


def _entity_box(entity: Entity, spec: dict) -> tuple[float, float, float, float]:
    width, height = float(spec["width"]), float(spec["height"])
    if entity.direction in (4, 12):
        width, height = height, width
    return (entity.x - width / 2, entity.y - height / 2,
            entity.x + width / 2, entity.y + height / 2)


def parse_proposal(row: dict, snapshot: Snapshot) -> Proposal:
    _assert_snapshot_integrity(snapshot)
    _keys(row, {"schema", "snapshot_sha256", "game_version", "source_hash", "world_id",
                "force_id", "observed_tick", "quality", "plan_id", "entities", "stages"})
    if row["schema"] != DESIGN_SCHEMA or row["snapshot_sha256"] != snapshot.sha256:
        _bad("Stale or unsupported proposal")
    for key in ("game_version", "source_hash", "world_id", "force_id", "observed_tick", "quality"):
        if type(row[key]) is not type(snapshot.raw[key]) or row[key] != snapshot.raw[key]:
            _bad(f"Proposal identity mismatch: {key}")
    _text(row["plan_id"], _ID, "plan ID")
    if not isinstance(row["entities"], list) or not 1 <= len(row["entities"]) <= MAX_ENTITIES:
        _bad("Invalid entity count")
    entities: list[Entity] = []
    ids: set[str] = set()
    boxes: list[tuple[float, float, float, float]] = []
    for raw in row["entities"]:
        _keys(raw, {"id", "prototype", "position", "direction"}, {"recipe"})
        entity_id = _text(raw["id"], _ID, "entity ID")
        if entity_id in ids:
            _bad("Duplicate entity ID")
        ids.add(entity_id)
        name = _text(raw["prototype"], _NAME, "prototype")
        if name not in snapshot.prototypes:
            _bad("Entity prototype is not allowed by snapshot")
        position = raw["position"]
        if not isinstance(position, dict):
            _bad("Invalid entity position")
        _keys(position, {"x", "y"})
        recipe = raw.get("recipe")
        if recipe is not None and _text(recipe, _NAME, "recipe") not in snapshot.recipes:
            _bad("Recipe is not unlocked in snapshot")
        direction = _integer(raw["direction"], "direction", 0, 15)
        if direction not in (0, 4, 8, 12):
            _bad("Only cardinal orientation is supported by this contract")
        entity = Entity(entity_id, name, _number(position["x"], "x"),
                        _number(position["y"], "y"), direction, recipe)
        box = _entity_box(entity, snapshot.prototypes[name])
        if box[0] < snapshot.bounds[0] or box[1] < snapshot.bounds[1] \
                or box[2] > snapshot.bounds[2] or box[3] > snapshot.bounds[3]:
            _bad("Entity extends beyond surveyed bounds")
        if any(_overlap(box, old) for old in boxes + list(snapshot.occupied)):
            _bad("Declared collision")
        boxes.append(box)
        entities.append(entity)
    if not isinstance(row["stages"], list) or not 1 <= len(row["stages"]) <= MAX_STAGES:
        _bad("Invalid stage count")
    stages: list[Stage] = []
    stage_ids: set[str] = set()
    assigned: set[str] = set()
    for raw in row["stages"]:
        _keys(raw, {"id", "depends_on", "entity_ids", "planned_bill", "witnesses"}, {"manual_supplies"})
        stage_id = _text(raw["id"], _ID, "stage ID")
        if stage_id in stage_ids:
            _bad("Duplicate stage ID")
        stage_ids.add(stage_id)
        deps = _unique_names(raw["depends_on"], "dependency", _ID)
        entity_ids = _unique_names(raw["entity_ids"], "stage entity", _ID)
        if not entity_ids or any(x not in ids or x in assigned for x in entity_ids):
            _bad("Stage entity missing or assigned twice")
        assigned.update(entity_ids)
        bill = raw["planned_bill"]
        if not isinstance(bill, dict) or not bill or len(bill) > 128:
            _bad("Invalid planned bill")
        for item, amount in bill.items():
            if _text(item, _NAME, "bill item") not in snapshot.items:
                _bad("Unknown planned bill item")
            _integer(amount, "planned bill count", 1, 100_000)
        for entity in entities:
            if entity.id in entity_ids:
                placement = snapshot.prototypes[entity.prototype]["placement_item"]
                if bill.get(placement, 0) < sum(
                    snapshot.prototypes[e.prototype]["placement_item"] == placement
                    for e in entities if e.id in entity_ids):
                    _bad("Planned bill omits placement items")
        witnesses = raw["witnesses"]
        if not isinstance(witnesses, list) or not witnesses or len(witnesses) > 32:
            _bad("Invalid stage witnesses")
        checked = []
        entity_by_id = {entity.id: entity for entity in entities}
        for witness in witnesses:
            _keys(witness, {"kind", "entity_id"}, {"item", "min_delta"})
            if witness["kind"] not in _WITNESSES or witness["entity_id"] not in entity_ids:
                _bad("Invalid stage witness")
            if witness["kind"] in ("entity_built", "power_connected"):
                if "item" in witness or "min_delta" in witness:
                    _bad("Unexpected witness quantity")
            else:
                _integer(witness.get("min_delta"), "witness minimum", 1, 1_000_000)
                if witness["kind"] == "research_progress_delta" and snapshot.prototypes[
                        entity_by_id[witness["entity_id"]].prototype].get("type") != "lab":
                    _bad("Research witness must target a lab")
                if witness["kind"] not in ("energy_generated_delta", "research_progress_delta"):
                    if _text(witness.get("item"), _NAME, "witness item") not in snapshot.items:
                        _bad("Unknown witness item")
                elif "item" in witness:
                    _bad("Research witness must not claim an item" if witness["kind"] == "research_progress_delta"
                         else "Energy witness must not claim an item")
            checked.append(dict(witness))
        if not any(w["kind"] in _FLOW for w in checked) and not power_only_flow(
                entity_ids, checked, entity_by_id, snapshot.prototypes):
            _bad("Functional stage lacks a planned native flow witness")
        supplies = raw.get("manual_supplies", [])
        if type(supplies) is not list or len(supplies) > 256:
            _bad("Invalid manual supplies")
        seen_supplies = set()
        for supply in supplies:
            _keys(supply, {"entity_id", "item", "slot"})
            _text(supply["entity_id"], _ID, "supply entity")
            if supply["entity_id"] not in assigned:
                _bad("Manual supply entity is not in this or an earlier stage")
            if _text(supply["item"], _NAME, "supply item") not in snapshot.items:
                _bad("Unknown manual supply item")
            if supply["slot"] not in ("fuel", "input"):
                _bad("Invalid manual supply slot")
            key = (supply["entity_id"], supply["item"], supply["slot"])
            if key in seen_supplies:
                _bad("Duplicate manual supply")
            seen_supplies.add(key)
        stages.append(Stage(stage_id, deps, entity_ids, dict(bill), tuple(checked),
                            tuple(copy.deepcopy(supplies))))
    if assigned != ids:
        _bad("Every entity must belong to exactly one stage")
    stage_by_id = {stage.id: stage for stage in stages}
    for stage in stages:
        if stage.id in stage.depends_on or any(d not in stage_by_id for d in stage.depends_on):
            _bad("Unknown or self-dependent stage")
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(stage_id: str) -> None:
        if stage_id in visiting:
            _bad("Stage dependency cycle")
        if stage_id in done:
            return
        visiting.add(stage_id)
        for dependency in stage_by_id[stage_id].depends_on:
            visit(dependency)
        visiting.remove(stage_id)
        done.add(stage_id)

    for stage in stages:
        visit(stage.id)
    owned = copy.deepcopy(row)
    return Proposal(owned, snapshot.sha256, tuple(entities), tuple(stages), snapshot,
                    hashlib.sha256(canonical_bytes(owned)).hexdigest())


def _assert_proposal_integrity(proposal: Proposal) -> None:
    if not isinstance(proposal, Proposal):
        _bad("Expected a parsed proposal")
    _assert_snapshot_integrity(proposal.snapshot)
    if proposal.snapshot_sha256 != proposal.snapshot.sha256 \
            or hashlib.sha256(canonical_bytes(proposal.raw)).hexdigest() != proposal.manifest_sha256:
        _bad("Parsed proposal changed after validation")
    rebuilt = parse_proposal(copy.deepcopy(proposal.raw), proposal.snapshot)
    if rebuilt.entities != proposal.entities or rebuilt.stages != proposal.stages:
        _bad("Parsed proposal geometry or stages changed after validation")


def incremental_frontier(proposal: Proposal, built_entity_ids: set[str],
                         verified_stage_ids: set[str]) -> tuple[str, tuple[str, ...]] | None:
    """Offer only the first unfinished, dependency-qualified prefix; reserve no items."""
    _assert_proposal_integrity(proposal)
    if not isinstance(built_entity_ids, set) or not isinstance(verified_stage_ids, set):
        _bad("Native completion identifiers must be sets")
    all_entities = {e.id for e in proposal.entities}
    all_stages = {s.id for s in proposal.stages}
    if not built_entity_ids <= all_entities or not verified_stage_ids <= all_stages:
        _bad("Unknown native completion identifier")
    stage_by_id = {s.id: s for s in proposal.stages}
    for stage_id in verified_stage_ids:
        stage = stage_by_id[stage_id]
        if not set(stage.entity_ids) <= built_entity_ids or not set(stage.depends_on) <= verified_stage_ids:
            _bad("Unwitnessed or out-of-order stage completion")
    for stage in proposal.stages:
        if stage.id in verified_stage_ids:
            continue
        if not set(stage.depends_on) <= verified_stage_ids:
            continue
        remaining = tuple(x for x in stage.entity_ids if x not in built_entity_ids)
        return stage.id, remaining  # Empty remaining means wait for the stage's native witness.
    return None


def encode_blueprint(proposal: Proposal) -> str:
    """Serialize geometry only; stages and payment remain in the separate manifest."""
    _assert_proposal_integrity(proposal)
    version = _VERSION.fullmatch(proposal.raw["game_version"])
    assert version is not None
    major, minor, patch = (int(x) for x in version.groups())
    if any(x > 65535 for x in (major, minor, patch)):
        _bad("Game version exceeds blueprint encoding bounds")
    from .functional_validation import loader_ends   # (which builds on this module)
    ends = loader_ends({e.id: e for e in proposal.entities}, proposal.snapshot.prototypes)[0]
    entries = []
    for number, entity in enumerate(proposal.entities, 1):
        row = {"entity_number": number, "name": entity.prototype,
               "position": {"x": entity.x, "y": entity.y}, "direction": entity.direction}
        if entity.recipe is not None:
            row["recipe"] = entity.recipe
        if entity.id in ends:   # a loader's end, as Factorio's blueprints say it
            row["type"] = ends[entity.id]
        entries.append(row)
    blueprint = {"blueprint": {"item": "blueprint", "label": proposal.raw["plan_id"],
                               "version": major << 48 | minor << 32 | patch << 16,
                               "entities": entries}}
    payload = canonical_bytes(blueprint)
    if len(payload) > MAX_BLUEPRINT_BYTES:
        _bad("Blueprint exceeds byte limit")
    return "0" + base64.b64encode(zlib.compress(payload, 9)).decode("ascii")


def decode_blueprint(value: str) -> dict:
    if not isinstance(value, str) or not value.startswith("0") or len(value) > MAX_BLUEPRINT_BYTES:
        _bad("Invalid blueprint string")
    try:
        packed = base64.b64decode(value[1:], validate=True)
        decompressor = zlib.decompressobj()
        payload = decompressor.decompress(packed, MAX_BLUEPRINT_BYTES + 1)
        if len(payload) > MAX_BLUEPRINT_BYTES or not decompressor.eof or decompressor.unused_data \
                or decompressor.unconsumed_tail:
            _bad("Incomplete or oversized blueprint payload")
        result = load_json(payload.decode("utf-8"))
    except (ValueError, UnicodeError, zlib.error) as exc:
        raise ContractError("Malformed blueprint string") from exc
    _keys(result, {"blueprint"})
    blueprint = _keys(result["blueprint"], {"item", "label", "version", "entities"})
    if blueprint["item"] != "blueprint" or not isinstance(blueprint["entities"], list) \
            or not 1 <= len(blueprint["entities"]) <= MAX_ENTITIES:
        _bad("Unsupported blueprint payload")
    _text(blueprint["label"], _ID, "blueprint label")
    encoded = _integer(blueprint["version"], "blueprint version", 0, 2**64 - 1)
    if encoded & 0xFFFF or not supported_game_version(
            f"{encoded >> 48 & 0xFFFF}.{encoded >> 32 & 0xFFFF}.{encoded >> 16 & 0xFFFF}"):
        _bad("Unsupported blueprint game version")
    seen = set()
    for row in blueprint["entities"]:
        _keys(row, {"entity_number", "name", "position", "direction"}, {"recipe", "type"})
        number = _integer(row["entity_number"], "entity number", 1, MAX_ENTITIES)
        if number in seen:
            _bad("Duplicate blueprint entity number")
        seen.add(number)
        _text(row["name"], _NAME, "blueprint prototype")
        _keys(row["position"], {"x", "y"})
        _number(row["position"]["x"], "blueprint x")
        _number(row["position"]["y"], "blueprint y")
        if _integer(row["direction"], "blueprint direction", 0, 15) not in (0, 4, 8, 12):
            _bad("Unsupported blueprint direction")
        if "recipe" in row:
            _text(row["recipe"], _NAME, "blueprint recipe")
        if "type" in row and row["type"] not in ("input", "output"):
            _bad("Unsupported blueprint loader end")
    return result


def blueprint_matches_proposal(encoded: str, proposal: Proposal) -> bool:
    """Check geometry against the bound manifest; a blueprint alone is never authority."""
    return decode_blueprint(encoded) == decode_blueprint(encode_blueprint(proposal))
