"""Deterministic starter layout planner (no LLM).

Reads an ``npc-site-v1`` export from the game and proposes burner drill ->
stone furnace pairs on the nearest iron ore. Each drill sits fully on ore and
its output point lands inside its furnace. Stage 1 uses the starter kit; later
stages add pairs the NPC must pay for from its own production.

This is the fallback when no designer LLM is configured, and the test double
for the native designed-start test.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from companion import avoid, team_area

DRILL = "burner-mining-drill"
FURNACE = "stone-furnace"
CHEST = "wooden-chest"
PRODUCTS = {"iron-ore": "iron-plate", "copper-ore": "copper-plate", "stone": "stone-brick"}
DIRECTIONS = (0, 4, 8, 12)


class PlanError(ValueError):
    """No acceptable layout exists for this site."""


def rotate(vector: Iterable[float], direction: int) -> tuple[float, float]:
    x, y = vector
    if direction == 4:
        return -y, x
    if direction == 8:
        return -x, -y
    if direction == 12:
        return y, -x
    return x, y


def tiles_from_rows(rows: Iterable[Iterable[int]]) -> set[tuple[int, int]]:
    tiles = set()
    for y, x1, x2 in rows:
        for x in range(int(x1), int(x2) + 1):
            tiles.add((x, int(y)))
    return tiles


def box_tiles(box: Iterable[float]) -> set[tuple[int, int]]:
    left, top, right, bottom = box
    return {(x, y)
            for x in range(math.floor(left), math.ceil(right))
            for y in range(math.floor(top), math.ceil(bottom))}


def square_tiles(cx: float, cy: float, size: int) -> set[tuple[int, int]]:
    half = size / 2
    return box_tiles((cx - half, cy - half, cx + half, cy + half))


def rich_of(patch: Mapping[str, Any], ore: set) -> set:
    """Tiles in the rich part of the patch (all of it when the game did not mark one)."""
    rows = patch.get("rich_rows")
    return tiles_from_rows(rows) if rows is not None else ore


def _pair_candidates(ore: set, blocked: set, bounds, drop_vector, home, target_size: int = 2,
                     rich: set | None = None) -> list[dict[str, Any]]:
    _, _, width, height = bounds
    pairs = []
    for cx in range(1, int(width)):
        for cy in range(1, int(height)):
            drill_tiles = square_tiles(cx, cy, 2)
            if not drill_tiles <= ore or drill_tiles & blocked:
                continue
            for direction in DIRECTIONS:
                dx, dy = rotate(drop_vector, direction)
                px, py = cx + dx, cy + dy
                if target_size == 1:
                    centers = [(math.floor(px) + 0.5, math.floor(py) + 0.5)]
                else:
                    centers = [(fx, fy) for fx in (math.floor(px), math.floor(px) + 1)
                               for fy in (math.floor(py), math.floor(py) + 1)]
                half = target_size / 2
                for fx, fy in centers:
                        if not (fx - half <= px < fx + half and fy - half <= py < fy + half):
                            continue
                        furnace_tiles = square_tiles(fx, fy, target_size)
                        if furnace_tiles & drill_tiles or furnace_tiles & blocked:
                            continue
                        if not all(0 <= x < width and 0 <= y < height for x, y in furnace_tiles):
                            continue
                        distance = math.hypot(cx - home[0], cy - home[1])
                        pairs.append({"drill": (cx, cy), "direction": direction, "furnace": (fx, fy),
                                      "tiles": drill_tiles | furnace_tiles,
                                      "thin": len(drill_tiles - rich) if rich is not None else 0,
                                      "furnace_on_ore": len(furnace_tiles & ore), "distance": distance})
    return pairs


# Burner drills that feed each other: each drops its coal into the next one's fuel slot, so the loop
# never needs refueling and keeps the spare coal for the engineer. (offset from the first drill, direction)
LOOPS = {
    4: [[((0, 0), 4), ((2, 0), 8), ((2, 2), 12), ((0, 2), 0)],     # around a square, clockwise
        [((0, 0), 8), ((0, 2), 4), ((2, 2), 0), ((2, 0), 12)]],    # counter-clockwise
    2: [[((0, 0), 8), ((0, 2), 0)],                                # facing each other
        [((0, 0), 4), ((2, 0), 12)]],
}


def _covers(center: tuple[float, float], point: tuple[float, float], size: int = 2) -> bool:
    half = size / 2
    return center[0] - half <= point[0] < center[0] + half and center[1] - half <= point[1] < center[1] + half


def plan_loop(site: Mapping[str, Any], *, drills: int = 4, resource: str = "coal",
              name: str = "coal-loop") -> dict[str, Any]:
    """A self-feeding loop of burner drills on `resource` (coal): fewer drills if a big loop does not fit."""
    drill_facts = site.get("prototypes", {}).get(DRILL)
    if not drill_facts or not drill_facts.get("drop_vector"):
        raise PlanError("burner drill not available")
    drop_vector = drill_facts["drop_vector"]
    patch = next((r for r in site.get("resources", []) if r.get("resource") == resource), None)
    if not patch:
        raise PlanError(f"no {resource} within the surveyed area")
    ore = tiles_from_rows(patch["rows"])
    rich = rich_of(patch, ore)
    blocked = set()
    for obstacle in site.get("obstacles", []):
        if not obstacle.get("mineable", False) or obstacle.get("type") == "cliff":
            blocked |= box_tiles(obstacle["box"])
    blocked |= tiles_from_rows(site.get("water_rows", []))
    blocked |= team_area.site_forbidden(site)   # the team world: its own plot only, clear of players' builds
    blocked |= avoid.tiles(site)                # ground the game handed back as unbuildable
    for entity in site.get("built", []):
        blocked |= square_tiles(entity["position"]["x"], entity["position"]["y"], 2)
    home = site.get("home", {"x": 0, "y": 0})
    _, _, width, height = site["bounds"]
    for size in sorted((n for n in LOOPS if n <= drills), reverse=True):
        # Only shapes where every drill really drops into the next (checked with the drill's drop point).
        shapes = [shape for shape in LOOPS[size]
                  if all(_covers(shape[(i + 1) % size][0], (shape[i][0][0] + rotate(drop_vector, shape[i][1])[0],
                                                             shape[i][0][1] + rotate(drop_vector, shape[i][1])[1]))
                         for i in range(size))]
        best = None
        for cx in range(1, int(width)):
            for cy in range(1, int(height)):
                for shape in shapes:
                    tiles = set()
                    for (ox, oy), _ in shape:
                        tiles |= square_tiles(cx + ox, cy + oy, 2)
                    if not tiles <= ore or tiles & blocked:
                        continue
                    if not all(0 <= x < width and 0 <= y < height for x, y in tiles):
                        continue
                    key = (len(tiles - rich), math.hypot(cx - home["x"], cy - home["y"]), cx, cy)
                    if best is None or key < best[0]:
                        best = (key, cx, cy, shape)
        if best:
            _, cx, cy, shape = best
            entities = [{"id": f"drill-{i}", "prototype": DRILL, "position": {"x": cx + ox, "y": cy + oy},
                         "direction": direction} for i, ((ox, oy), direction) in enumerate(shape, start=1)]
            ids = [e["id"] for e in entities]
            request = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
            return {"plan_id": f"{name}-{request}", "source": "starter-layout-planner", "entities": entities,
                    "stages": [{"id": "loop", "depends_on": [], "entity_ids": ids,
                                "manual_supplies": [{"entity_id": i, "item": "coal", "slot": "fuel"} for i in ids],
                                "witnesses": [{"kind": "entity_built", "entity_id": i} for i in ids]
                                + [{"kind": "resource_extraction_delta", "entity_id": ids[0], "item": resource,
                                    "min_delta": 2}]}]}
    raise PlanError(f"no room on {resource} for drills that feed each other")


def plan(site: Mapping[str, Any], *, pairs: int = 2, resource: str = "iron-ore",
         output: str = "furnace", name: str = "starter-iron") -> dict[str, Any]:
    """Burner drill pairs on `resource`; each drill drops into a furnace or a wooden chest (or, with
    output "loop", into the next drill: see plan_loop)."""
    if output == "loop":
        return plan_loop(site, drills=pairs, resource=resource, name=name)
    facts = site.get("prototypes", {})
    drill_facts = facts.get(DRILL)
    target = FURNACE if output == "furnace" else CHEST
    if not drill_facts or target not in facts:
        raise PlanError(f"burner drill or {target} not available")
    if output == "furnace" and resource not in PRODUCTS:
        raise PlanError(f"{resource} cannot be smelted")
    drop_vector = drill_facts.get("drop_vector")
    if not drop_vector:
        raise PlanError("burner drill has no drop vector")
    patch = next((r for r in site.get("resources", []) if r.get("resource") == resource), None)
    if not patch:
        raise PlanError(f"no {resource} within the surveyed area")
    ore = tiles_from_rows(patch["rows"])
    blocked = set()
    for obstacle in site.get("obstacles", []):
        if not obstacle.get("mineable", False) or obstacle.get("type") == "cliff":
            blocked |= box_tiles(obstacle["box"])
    blocked |= tiles_from_rows(site.get("water_rows", []))
    blocked |= team_area.site_forbidden(site)   # the team world: its own plot only, clear of players' builds
    blocked |= avoid.tiles(site)                # ground the game handed back as unbuildable
    for entity in site.get("built", []):
        blocked |= square_tiles(entity["position"]["x"], entity["position"]["y"], 2)
    home = site.get("home", {"x": 0, "y": 0})
    candidates = _pair_candidates(ore, blocked, site["bounds"], drop_vector, (home["x"], home["y"]),
                                  1 if output == "chest" else 2, rich_of(patch, ore))
    if not candidates:
        raise PlanError("no drill position fully on ore with room for a furnace")

    chosen: list[dict[str, Any]] = []
    used: set = set()
    anchor = None
    for _ in range(pairs):
        options = [c for c in candidates if not (c["tiles"] & used)]
        if not options:
            break
        # Deep in the patch first (the thin edge runs dry), then close to home and to each other.
        if anchor is None:
            key = lambda c: (c["thin"], c["furnace_on_ore"], c["distance"], c["drill"], c["direction"])
        else:
            key = lambda c: (c["thin"], math.hypot(c["drill"][0] - anchor[0], c["drill"][1] - anchor[1]),
                             c["furnace_on_ore"], c["drill"], c["direction"])
        best = min(options, key=key)
        chosen.append(best)
        used |= best["tiles"]
        anchor = best["drill"]

    entities, stages = [], []
    for index, pair in enumerate(chosen, start=1):
        drill_id = f"drill-{index}"
        target_id = f"{'furnace' if output == 'furnace' else 'chest'}-{index}"
        entities.append({"id": drill_id, "prototype": DRILL,
                         "position": {"x": pair["drill"][0], "y": pair["drill"][1]},
                         "direction": pair["direction"]})
        entities.append({"id": target_id, "prototype": target,
                         "position": {"x": pair["furnace"][0], "y": pair["furnace"][1]}, "direction": 0})
        supplies = [{"entity_id": drill_id, "item": "coal", "slot": "fuel"}]
        witnesses = [{"kind": "entity_built", "entity_id": drill_id},
                     {"kind": "entity_built", "entity_id": target_id},
                     {"kind": "resource_extraction_delta", "entity_id": drill_id, "item": resource, "min_delta": 2}]
        if output == "furnace":
            supplies.append({"entity_id": target_id, "item": "coal", "slot": "fuel"})
            witnesses.append({"kind": "product_counter_delta", "entity_id": target_id,
                              "item": PRODUCTS[resource], "min_delta": 5 if index == 1 else 3})
        stages.append({"id": f"pair-{index}", "depends_on": [f"pair-{index - 1}"] if index > 1 else [],
                       "entity_ids": [drill_id, target_id], "manual_supplies": supplies, "witnesses": witnesses})
    request = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{name}-{request}", "source": "starter-layout-planner",
            "entities": entities, "stages": stages}
