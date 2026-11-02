"""Declared feasibility checks, not proof of sustained native factory output."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from functools import lru_cache

from ._reference_contract import ContractError, Entity, _entity_box, _overlap, _FLOW, power_only_flow


class FunctionalValidationError(ContractError):
    def __init__(self, issues):
        self.issues = issues[:16]
        super().__init__(self.issues[0]["code"])


def known(value):
    """Unknown observations never imply empty inventories or usable geometry."""
    if type(value) is dict and "knowledge" in value:
        return known(value["value"]) if value["knowledge"] == "known" else None
    return value


def rotate_vector(vector, direction):
    x, y = vector
    return {0: (x, y), 4: (-y, x), 8: (-x, -y), 12: (y, -x)}[direction]


def mirrored(vector, entity):
    """A north-facing vector as a mirrored entity has it: flipped left to right in its own frame."""
    return (-vector[0], vector[1]) if getattr(entity, "mirror", False) else tuple(vector)


def mirrored_direction(direction, entity):
    """A north-facing connection's direction on a mirrored entity: east and west swap."""
    return (16 - direction) % 16 if getattr(entity, "mirror", False) else direction


def port_point(entity, vector):
    x, y = rotate_vector(mirrored(vector, entity), entity.direction)
    return entity.x + x, entity.y + y


def contains(box, point):
    # Half-open footprints avoid accepting a point on two adjacent entities.
    return box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3]


def prototype_facts(spec, name):
    """Require the facts needed by each entity type; never infer them from names."""
    required = {"type", "energy_source"}
    if spec.get("energy_source") == "burner":
        required.add("fuel_categories")
    if spec.get("type") == "mining-drill":
        required.update(("resource_categories", "mining_area_radius", "drop_vector"))
    if spec.get("type") == "inserter":
        required.update(("pickup_vector", "drop_vector"))
    if spec.get("type") == "electric-pole":
        required.update(("supply_area_distance", "max_wire_distance"))
    if spec.get("type") in ("furnace", "assembling-machine"):
        required.add("crafting_categories")
    if spec.get("type") in ("generator", "solar-panel"):
        required.add("produces_power")
    if spec.get("type") in ("boiler", "offshore-pump", "pipe", "pipe-to-ground") or name == "steam-engine":
        required.add("fluid_boxes")
    if spec.get("type") == "offshore-pump":
        required.add("requires_water")
    if spec.get("type") == "loader":
        required.add("container_distance")
    return None if required <= spec.keys() else "prototype_facts_missing:" + name


def resource_outputs(entity, spec, terrain):
    patches = known(terrain.get("resource_patches")) if type(terrain) is dict else None
    if type(patches) is not list:
        return set()
    radius = spec["mining_area_radius"]
    if radius <= 0:
        return set()
    area = (entity.x - radius, entity.y - radius, entity.x + radius, entity.y + radius)
    result = set()
    for patch in patches:
        patch = known(patch)
        if type(patch) is not dict:
            continue
        resource, bounds = known(patch.get("resource")), known(patch.get("bounds"))
        category = known(patch.get("category", patch.get("resource_category")))
        if category is None and resource in ("iron-ore", "copper-ore", "coal", "stone"):
            category = "basic-solid"
        if category not in spec["resource_categories"] or type(bounds) is not list or len(bounds) != 4:
            continue
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
            continue
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            continue
        if _overlap(area, bounds) and type(resource) is str:
            result.add(resource)
    return result


def accepts_drill_output(target, spec, item, recipes):
    if spec["type"] in ("container", "transport-belt"):
        return True
    if item == "coal" and spec["energy_source"] == "burner":
        return "chemical" in spec["fuel_categories"]
    if spec["type"] != "furnace":
        return False
    vanilla = {"iron-ore": "iron-plate", "copper-ore": "copper-plate", "stone": "stone-brick"}
    if target.recipe is None:
        # Furnaces choose their recipe from input; category must still fit.
        return item in vanilla and "smelting" in spec["crafting_categories"]
    if vanilla.get(item) == target.recipe:
        return "smelting" in spec["crafting_categories"]
    recipe = known(recipes.get(target.recipe)) if type(recipes) is dict else None
    ingredients = known(recipe.get("ingredients")) if type(recipe) is dict else None
    if type(ingredients) is dict:
        return item in ingredients
    return type(ingredients) is list and any(type(entry) is dict and entry.get("item", entry.get("name")) == item
                                             for entry in ingredients)


def _recipe_ingredients(recipe):
    """Return known ingredient names, or None when ingredient facts are unknown."""
    recipe = known(recipe)
    ingredients = known(recipe.get("ingredients")) if type(recipe) is dict else None
    if type(ingredients) is dict:
        return set(ingredients)
    if type(ingredients) is not list:
        return None
    names = set()
    for entry in ingredients:
        entry = known(entry)
        name = known(entry.get("item", entry.get("name"))) if type(entry) is dict else None
        if type(name) is not str:
            return None
        names.add(name)
    return names


def crafting_input_accepts(target, spec, item, recipes):
    """Check crafting ingredients separately from fuel destinations.

    None means unknown recipe ingredients: manual delivery remains allowed,
    but a drill drop needs positively known ingredient compatibility.
    """
    if spec["type"] not in ("furnace", "assembling-machine", "lab"):
        return False
    vanilla = {"iron-ore": "iron-plate", "copper-ore": "copper-plate", "stone": "stone-brick"}
    recipes = recipes if type(recipes) is dict else {}
    if target.recipe is not None:
        ingredients = _recipe_ingredients(recipes.get(target.recipe))
        if ingredients is not None and item in ingredients:
            return True
        if spec["type"] == "furnace" and target.recipe in vanilla.values():
            return vanilla.get(item) == target.recipe
        return None if ingredients is None else False
    if spec["type"] == "furnace":
        if item in vanilla:
            return True
        for recipe in recipes.values():
            recipe = known(recipe)
            if type(recipe) is dict and known(recipe.get("category")) == "smelting":
                ingredients = _recipe_ingredients(recipe)
                if ingredients is not None and item in ingredients:
                    return True
        return False
    return None


def powered_poles(poles, producers, specs, boxes, existing_rows):
    def supplies(pole, entity):
        radius = specs[pole.id]["supply_area_distance"]
        if radius <= 0:
            return False
        area = (pole.x - radius, pole.y - radius, pole.x + radius, pole.y + radius)
        return _overlap(area, boxes[entity.id])

    powered = {p.id for p in poles if known(existing_rows.get(p.id, {}).get("powered")) is True
               or any(supplies(p, producer) for producer in producers)}
    changed = True
    while changed:
        changed = False
        for pole in poles:
            if pole.id in powered:
                continue
            for other in poles:
                if other.id in powered and math.hypot(pole.x - other.x, pole.y - other.y) <= min(
                        specs[pole.id]["max_wire_distance"], specs[other.id]["max_wire_distance"]):
                    powered.add(pole.id)
                    changed = True
                    break
    return [pole for pole in poles if pole.id in powered], supplies


_DIRECTIONS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def fluid_components(active, specs):
    """Connect rotated ports, retaining distinct boxes within each entity."""
    nodes, ports = {}, []
    for eid, entity in active.items():
        for box in specs[eid].get("fluid_boxes", []):
            node = (eid, box["index"])
            nodes[node] = box
            if box["production"] == "none":
                continue
            for connection in box["connections"]:
                point = port_point(entity, connection["position"])
                direction = (mirrored_direction(connection["direction"], entity) + entity.direction) % 16
                dx, dy = _DIRECTIONS[direction]
                ports.append((node, point, (point[0] + dx, point[1] + dy), direction, connection))
    edges = {node: set() for node in nodes}

    def close(first, second):
        return all(abs(a - b) <= 0.01 for a, b in zip(first, second))

    def link(first, second):
        edges[first].add(second)
        edges[second].add(first)

    normal = [port for port in ports if port[4]["type"] == "normal"]
    # A 0.02-wide bucket plus its neighbours includes every point within 0.01,
    # even across grid boundaries. The exact tests still decide connectivity.
    def bucket(value):
        return math.floor(value * 50)

    normal_by_point = defaultdict(list)
    for index, port in enumerate(normal):
        normal_by_point[(bucket(port[1][0]), bucket(port[1][1]))].append((index, port))
    for index, first in enumerate(normal):
        bx, by = bucket(first[2][0]), bucket(first[2][1])
        for x in (bx - 1, bx, bx + 1):
            for y in (by - 1, by, by + 1):
                for other_index, second in normal_by_point.get((x, y), ()):
                    if other_index > index and first[0][0] != second[0][0] \
                            and close(first[2], second[1]) and close(second[2], first[1]):
                        link(first[0], second[0])
    underground = [port for port in ports if port[4]["type"] == "underground"]
    # Sort opposite-facing ports along each prototype's axis line. A bisect
    # starts at the first point ahead, avoiding scans over the rest of the base.
    underground_lines = defaultdict(list)
    for index, port in enumerate(underground):
        axis = 1 if port[3] in (0, 8) else 0
        key = (active[port[0][0]].prototype, port[3], bucket(port[1][1 - axis]))
        underground_lines[key].append((port[1][axis], index))
    for line in underground_lines.values():
        line.sort()
    nearest = {}
    for index, first in enumerate(underground):
        limit = first[4]["max_underground_distance"]
        if limit is None:
            continue
        axis = 1 if first[3] in (0, 8) else 0
        sign = 1 if first[3] in (4, 8) else -1
        along, across = first[1][axis], first[1][1 - axis]
        line_bucket = bucket(across)
        prototype = active[first[0][0]].prototype
        best = None
        for offset in (-1, 0, 1):
            line = underground_lines.get((prototype, (first[3] + 8) % 16, line_bucket + offset), ())
            start = bisect_right(line, (along, len(underground))) if sign > 0 else bisect_left(line, (along, -1)) - 1
            stop = len(line) if sign > 0 else -1
            for cursor in range(start, stop, sign):
                coordinate, other_index = line[cursor]
                distance = (coordinate - along) * sign
                if distance > limit + 0.01 or (best is not None and distance > best[0]):
                    break
                second = underground[other_index]
                if first[0][0] != second[0][0] and abs(second[1][1 - axis] - across) <= 0.01:
                    candidate = (distance, other_index)
                    if best is None or candidate < best:
                        best = candidate
        if best is not None:
            nearest[index] = best[1]
    for first, second in nearest.items():
        if nearest.get(second) == first:
            link(underground[first][0], underground[second][0])
    components = {}
    for node in nodes:
        if node in components:
            continue
        component, pending = set(), [node]
        while pending:
            current = pending.pop()
            if current not in component:
                component.add(current)
                pending.extend(edges[current] - component)
        for member in component:
            components[member] = component
    return nodes, components


def pump_on_water(entity, spec, footprint, terrain):
    """Accept known water overlapping the footprint or behind an output port."""
    water = known(terrain.get("water")) if type(terrain) is dict else None
    if type(water) is not list:
        return False
    behind = []
    for box in spec.get("fluid_boxes", []):
        if box["production"] not in ("output", "input-output"):
            continue
        for connection in box["connections"]:
            x, y = port_point(entity, connection["position"])
            dx, dy = _DIRECTIONS[(mirrored_direction(connection["direction"], entity) + entity.direction) % 16]
            behind.append((x - dx, y - dy))
    for bounds in water:
        bounds = known(bounds)
        if type(bounds) is not list or len(bounds) != 4 or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in bounds):
            continue
        if bounds[0] < bounds[2] and bounds[1] < bounds[3] and (
                _overlap(footprint, bounds) or any(contains(bounds, point) for point in behind)):
            return True
    return False


def fluid_power_sources(active, specs, boxes, terrain):
    """Return steam engines with a water-fed boiler, and fluid failures by entity."""
    nodes, components = fluid_components(active, specs)
    mixed, seen_components = set(), set()
    for component in components.values():
        component_id = id(component)
        if component_id in seen_components:
            continue
        seen_components.add(component_id)
        filters = {nodes[member]["filter"] for member in component if nodes[member]["filter"] is not None}
        if len(filters) > 1:
            mixed.update(component)
    off_water = {eid for eid, entity in active.items() if specs[eid].get("requires_water") is True
                 and not pump_on_water(entity, specs[eid], boxes[eid], terrain)}
    pumps = {node for node, box in nodes.items() if specs[node[0]]["type"] == "offshore-pump"
             and node[0] not in off_water and specs[node[0]].get("requires_water") is True
             and box["production"] in ("output", "input-output") and box["filter"] == "water"}

    def inputs(eid, fluid):
        return [node for node, box in nodes.items() if node[0] == eid and box["filter"] == fluid
                and box["production"] in ("input", "input-output")]

    boilers = {eid for eid in active if specs[eid]["type"] == "boiler"}
    valid_boilers = {eid for eid in boilers if inputs(eid, "water") and all(
        node not in mixed and components[node] & pumps for node in inputs(eid, "water"))}
    steam = {node for node, box in nodes.items() if node[0] in valid_boilers
             and box["production"] == "output" and box["filter"] == "steam" and node not in mixed}
    engines = {eid for eid, entity in active.items() if entity.prototype == "steam-engine"}
    valid_engines = {eid for eid in engines if inputs(eid, "steam") and all(
        node not in mixed and components[node] & steam for node in inputs(eid, "steam"))}
    return valid_engines, (boilers - valid_boilers) | (engines - valid_engines), off_water, mixed


# What a loader can fill or empty (as the Vanilla Loaders mod snaps them), and what carries items to or from
# its belt end.
LOADER_TARGETS = frozenset(("container", "logistic-container", "infinity-container", "linked-container",
                            "furnace", "assembling-machine", "lab", "boiler", "reactor", "rocket-silo",
                            "ammo-turret", "artillery-turret", "mining-drill"))
BELT_CARRIERS = frozenset(("transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "linked-belt"))


def loader_ends(entities, catalog):
    """Which end each loader is, read from what stands at its two ends, and what is wrong with the rest.

    A loader moves items the way it faces. With a container or machine ahead (container_distance from
    its centre) it is an "input" loader, filling it from the belt behind it, which must face the same
    way; with one behind it is an "output" loader, emptying it onto the belt ahead, which must not face
    back into it. A container at both ends or at neither leaves the end unknown.
    `entities` maps id to Entity (the plan's own and the built ones known to the catalog).
    Returns ({loader id: "input" | "output"}, [(loader id, issue code)]).
    """
    boxes = {eid: _entity_box(e, catalog[e.prototype]) for eid, e in entities.items() if e.prototype in catalog}

    def at(point, exclude):
        return [entities[eid] for eid, box in boxes.items() if eid != exclude and contains(box, point)]

    ends, issues = {}, []
    for eid, loader in entities.items():
        spec = catalog.get(loader.prototype) or {}
        if spec.get("type") != "loader":
            continue
        if type(spec.get("container_distance")) not in (int, float):
            issues.append((eid, "prototype_facts_missing:" + loader.prototype))
            continue
        dx, dy = _DIRECTIONS[loader.direction]
        reach = spec["container_distance"]
        ahead = at((loader.x + dx * reach, loader.y + dy * reach), eid)
        behind = at((loader.x - dx * reach, loader.y - dy * reach), eid)

        def kinds(found):
            return {catalog[e.prototype].get("type") for e in found}
        filled, emptied = kinds(ahead) & LOADER_TARGETS, kinds(behind) & LOADER_TARGETS
        if bool(filled) == bool(emptied):
            issues.append((eid, "loader_container_end_ambiguous" if filled else "loader_container_end_missing"))
            continue
        end, belt = ("input", behind) if filled else ("output", ahead)
        reverse = (loader.direction + 8) % 16
        feeding = [e for e in belt if catalog[e.prototype].get("type") in BELT_CARRIERS
                   and (e.direction == loader.direction if end == "input" else e.direction != reverse)]
        if not feeding:
            issues.append((eid, "loader_belt_end_unconnected"))
            continue
        ends[eid] = end
    return ends, issues


def existing_geometry(context):
    """The actor force's built entities on its surface, as ({id: Entity}, {id: row}), ids "existing:<n>"."""
    existing = known(context["facts"]["built_machines"])
    entities, rows = {}, {}
    for index, row in enumerate(existing if type(existing) is list else []):
        if type(row) is not dict or type(row.get("position")) is not dict:
            continue
        if row.get("owner_force_id", context["identity"]["force_id"]) != context["identity"]["force_id"] \
                or row.get("surface_id", context["identity"]["surface_id"]) != context["identity"]["surface_id"]:
            continue
        position, direction = row["position"], row.get("direction")
        if any(type(position.get(k)) not in (int, float) for k in ("x", "y")) or direction not in (0, 4, 8, 12):
            continue
        entity_id = "existing:" + str(index)
        entities[entity_id] = Entity(entity_id, row.get("prototype"), position["x"], position["y"],
                                     direction, known(row.get("recipe")), row.get("mirror") is True)
        rows[entity_id] = row
    return entities, rows


# Loaders are for the few places an inserter cannot keep up: many of them weigh on a big game's update time
# (the user, 2026-10-24), so a design has at most this many; past it the repair round is told to use inserters.
MAX_DESIGN_LOADERS = 4


def loaders_over_cap(entities, catalog, cap=MAX_DESIGN_LOADERS):
    """The loaders of a design past the cap, in its own order."""
    found = [eid for eid, e in entities.items() if (catalog.get(e.prototype) or {}).get("type") == "loader"]
    return found[cap:]


def validate_functional_plan(proposal, context):
    """Validate flow stages against same-stage/dependency geometry and observations.

    Issues are collected so a tool turn can repair power and input together.
    A later or independent stage cannot provide an earlier stage's prerequisites.
    """
    catalog = proposal.snapshot.prototypes
    entities = {e.id: e for e in proposal.entities}
    stage_by_id = {s.id: s for s in proposal.stages}
    issues = []

    def add(code, path):
        issue = {"code": code, "path": path}
        if issue not in issues:
            issues.append(issue)

    @lru_cache(maxsize=64)
    def ancestors(stage_id):
        stage = stage_by_id[stage_id]
        result = {stage_id}
        for dependency in stage.depends_on:
            result.update(ancestors(dependency))
        return result

    facts = context["facts"]
    terrain, recipes = known(facts["terrain"]), known(facts["recipes"])
    if type(recipes) is list:
        recipes = {row["name"]: row for row in recipes
                   if type(row) is dict and type(row.get("name")) is str}
    existing_entities, existing_rows = existing_geometry(context)

    # Loaders, in whatever stage: exactly one container end, and a belt that carries to or from the other.
    known_existing = {eid: e for eid, e in existing_entities.items() if e.prototype in catalog}
    _, loader_issues = loader_ends({**entities, **known_existing}, catalog)
    order = {e.id: index for index, e in enumerate(proposal.entities)}
    for eid, code in loader_issues:
        if eid in order:
            add(code, "entities[" + str(order[eid]) + "]")
    for eid in loaders_over_cap(entities, catalog):
        add("too_many_loaders_use_inserters", "entities[" + str(order[eid]) + "]")

    for index, stage in enumerate(proposal.stages):
        if not any(w["kind"] in _FLOW for w in stage.witnesses) and not power_only_flow(
                stage.entity_ids, stage.witnesses, entities, catalog) and not any(
                catalog[entities[eid].prototype].get("type") == "boiler"
                or entities[eid].prototype == "steam-engine" for eid in stage.entity_ids):
            continue
        path = "stages[" + str(index) + "]"
        available_stages = ancestors(stage.id)
        available = {eid: entities[eid] for s in proposal.stages if s.id in available_stages for eid in s.entity_ids}
        missing = False
        for entity in available.values():
            code = prototype_facts(catalog[entity.prototype], entity.prototype)
            if code:
                add(code, path)
                missing = True
        if missing:
            continue
        # Only catalog-backed existing machines can supply ports or power.
        # Unclassified existing geometry near a port is diagnosed below.
        usable_existing = {eid: e for eid, e in existing_entities.items()
                           if e.prototype in catalog and prototype_facts(catalog[e.prototype], e.prototype) is None}
        active = {**available, **usable_existing}
        specs = {eid: catalog[e.prototype] for eid, e in active.items()}
        boxes = {eid: _entity_box(e, specs[eid]) for eid, e in active.items()}
        supplies = [supply for s in proposal.stages if s.id in available_stages for supply in s.manual_supplies]
        manual = {(s["entity_id"], s["slot"]) for s in supplies}
        drills = {eid: resource_outputs(e, specs[eid], terrain) for eid, e in active.items()
                  if specs[eid]["type"] == "mining-drill"}
        poles = [e for eid, e in active.items() if specs[eid]["type"] == "electric-pole"]
        valid_engines, unconnected, off_water, mixed = fluid_power_sources(active, specs, boxes, terrain)
        if unconnected & available.keys():
            add("fluid_input_unconnected", path)
        if off_water & available.keys():
            add("pump_not_on_water", path)
        if any(node[0] in available for node in mixed):
            add("fluid_mixed", path)
        producers = [e for eid, e in active.items() if specs[eid].get("produces_power") is True
                     and (eid in available or known(existing_rows[eid].get("powered")) is True)
                     and (e.prototype != "steam-engine" or eid in valid_engines)]
        powered, in_supply = powered_poles(poles, producers, specs, boxes, existing_rows)

        def at_port(point, exclude):
            for eid, e in existing_entities.items():
                if eid in usable_existing or e.prototype not in catalog:
                    continue
                if contains(_entity_box(e, catalog[e.prototype]), point):
                    add("prototype_facts_missing:" + e.prototype, path)
            return [e for eid, e in active.items() if eid != exclude and contains(boxes[eid], point)]

        def fuel_source(source):
            if "coal" in drills.get(source.id, set()):
                return True
            if any(s["entity_id"] == source.id and s["item"] == "coal" for s in supplies):
                return True
            row = existing_rows.get(source.id, {})
            for field in ("contents", "output_inventory", "fuel_inventory", "input_inventory"):
                contents = known(row.get(field))
                if type(contents) is dict and type(contents.get("coal")) in (int, float) and contents["coal"] > 0:
                    return True
            return any(w.get("item") == "coal" and w["kind"] in _FLOW
                       for s in proposal.stages if s.id in available_stages for w in s.witnesses
                       if w["entity_id"] == source.id)

        def inserter_sources(target):
            sources = []
            for eid, inserter in available.items():
                if specs[eid]["type"] != "inserter":
                    continue
                if contains(boxes[target.id], port_point(inserter, specs[eid]["drop_vector"])):
                    sources.extend(source for source in at_port(port_point(inserter, specs[eid]["pickup_vector"]), target.id)
                                   if source.id != eid)
            return sources

        for eid in stage.entity_ids:
            entity, spec = entities[eid], specs[eid]
            if spec["energy_source"] == "burner" and (eid, "fuel") not in manual \
                    and not any(fuel_source(source) for source in inserter_sources(entity)):
                add("burner_fuel_unplanned", path)
            if spec["energy_source"] == "electric" and not spec.get("produces_power", False) \
                    and not any(in_supply(pole, entity) for pole in powered):
                add("electric_consumer_unpowered", path)
                for existing_id, existing_entity in existing_entities.items():
                    existing_spec = catalog.get(existing_entity.prototype, {})
                    if existing_id not in usable_existing and existing_spec.get("type") == "electric-pole":
                        add("prototype_facts_missing:" + existing_entity.prototype, path)
            if spec["type"] == "mining-drill":
                outputs = drills[eid]
                claimed = {w["item"] for w in stage.witnesses
                           if w["kind"] == "resource_extraction_delta" and w["entity_id"] == eid}
                if not outputs or not claimed <= outputs:
                    add("drill_not_on_resource", path)
                else:
                    targets = at_port(port_point(entity, spec["drop_vector"]), eid)
                    if not any(accepts_drill_output(target, specs[target.id], item, recipes)
                               for target in targets for item in (claimed or outputs)):
                        add("drill_output_unconnected", path)
            if spec["type"] in ("furnace", "assembling-machine", "lab"):
                if spec["type"] == "lab" and any(w["kind"] == "research_progress_delta"
                                                  and w["entity_id"] == eid for w in stage.witnesses):
                    science_input = any(s["entity_id"] == eid and s["slot"] == "input"
                                        and s["item"].endswith("science-pack") for s in supplies)
                    feeder = any(specs[iid]["type"] == "inserter" and contains(
                        boxes[eid], port_point(inserter, specs[iid]["drop_vector"]))
                        for iid, inserter in active.items())
                    if not science_input and not feeder:
                        add("crafting_input_unplanned", path)
                if any(w["kind"] == "product_counter_delta" and w["entity_id"] == eid for w in stage.witnesses):
                    drill_input = any(contains(boxes[eid], port_point(active[did], specs[did]["drop_vector"]))
                                      and any(crafting_input_accepts(entity, spec, item, recipes) is True
                                              for item in outputs)
                                      for did, outputs in drills.items())
                    manual_input = any(s["entity_id"] == eid and s["slot"] == "input"
                                       and crafting_input_accepts(entity, spec, s["item"], recipes) is not False
                                       for s in supplies)
                    if not manual_input and not drill_input and not inserter_sources(entity):
                        add("crafting_input_unplanned", path)
                recipe = known(recipes.get(entity.recipe)) if type(recipes) is dict else None
                category = known(recipe.get("category")) if type(recipe) is dict else None
                if category is not None and "crafting_categories" in spec and category not in spec["crafting_categories"]:
                    add("recipe_category_mismatch", path)
    if issues:
        raise FunctionalValidationError(issues)
