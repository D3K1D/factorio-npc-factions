"""Blueprint library: position-independent layouts that NPCs can reuse.

A blueprint is saved from any plan an NPC accepted (designer LLM or the
deterministic planner). It stores entities relative to its own corner, the
resource tiles its drills need, its stages, and its track record. For a new
site the library finds where a blueprint fits (translated and rotated) and
turns it back into a plan in that site's local coordinates.

The library lives on the host (one JSON file per blueprint), so it grows
across games and is never part of a save or multiplayer sync. Each build's
lineage, uses and notes are kept by companion/build_history.py, and the
library is weighed for a job by companion/build_choice.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from companion import avoid, ratios, team_area

SCHEMA = "npc-blueprint-v1"
MAX_CANDIDATES = 6
# What a reused layout keeps of each entity besides its place and facing: an assembler's recipe, and
# which end an underground belt or a loader is (without it every one would go down as an "input").
# (and a blueprint's own settings: inserter pickup and drop, as bobinserters sets them; splitter priorities and
# filter; companion/blueprint_packs.py)
KEPT = ("recipe", "belt_to_ground_type", "mirror", "pickup_position", "drop_position", "input_priority", "output_priority",
        "filter", "filters", "use_filters")


def default_path() -> Path:
    base = os.environ.get("NPC_FACTIONS_DATA")
    if base:
        return Path(base) / "blueprints"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "npc-factions" / "blueprints"
    return Path.home() / ".local" / "share" / "npc-factions" / "blueprints"


def _as_list(value: Any) -> list:
    return [] if isinstance(value, dict) and not value else list(value or [])


def tiles_from_rows(rows: Iterable) -> set[tuple[int, int]]:
    return {(x, int(y)) for y, x1, x2 in rows for x in range(int(x1), int(x2) + 1)}


def box_tiles(left: float, top: float, right: float, bottom: float) -> set[tuple[int, int]]:
    return {(x, y) for x in range(math.floor(left), math.ceil(right)) for y in range(math.floor(top), math.ceil(bottom))}


def entity_size(spec: Mapping[str, Any], direction: int) -> tuple[int, int]:
    width, height = int(spec.get("width", 1)), int(spec.get("height", 1))
    return (height, width) if direction in (4, 12) else (width, height)


def entity_tiles(entity: Mapping[str, Any], spec: Mapping[str, Any]) -> set[tuple[int, int]]:
    w, h = entity_size(spec, entity.get("direction", 0))
    x, y = entity["position"]["x"], entity["position"]["y"]
    return box_tiles(x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def site_tiles(site: Mapping[str, Any]) -> tuple[dict[str, set], set, dict]:
    """Resource tiles, hard-blocked tiles, and the NPC's own movable buildings by tile.

    Trees and rocks are not obstacles: the NPC mines them out of the footprint.
    Its own buildings can be picked up and moved, so they only cost a relocation.
    Water, cliffs and anything unmineable block.
    """
    resources = {r["resource"]: tiles_from_rows(_as_list(r.get("rows"))) for r in _as_list(site.get("resources"))}
    blocked = set()
    for obstacle in _as_list(site.get("obstacles")):
        if not obstacle.get("mineable") or obstacle.get("type") == "cliff":
            blocked |= box_tiles(*obstacle["box"])
    water = tiles_from_rows(_as_list(site.get("water_rows")))
    resources["water"] = water
    blocked |= water
    blocked |= team_area.site_forbidden(site)   # the team world: not in another plot, not near players' builds
    blocked |= avoid.tiles(site)   # ground the game handed back as unbuildable
    prototypes = site.get("prototypes") or {}
    movable: dict[tuple[int, int], int] = {}
    for index, entity in enumerate(_as_list(site.get("built"))):
        for tile in entity_tiles({"position": entity["position"], "direction": entity.get("direction", 0)},
                                 prototypes.get(entity["name"], {"width": 1, "height": 1})):
            movable[tile] = index
    return resources, blocked, movable


def rich_tiles(site: Mapping[str, Any]) -> dict[str, set]:
    """The rich part of each patch (the game marks it), where drills last; patches without the mark
    count as rich everywhere."""
    return {r["resource"]: tiles_from_rows(_as_list(r.get("rich_rows")))
            for r in _as_list(site.get("resources")) if r.get("rich_rows") is not None}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def from_plan(plan: Mapping[str, Any], site: Mapping[str, Any], *, source: str, purpose: str | None = None) -> dict:
    """Normalize an accepted plan for `site` into a reusable blueprint."""
    prototypes = site.get("prototypes") or {}
    resources, _, _ = site_tiles(site)
    footprint: set = set()
    for entity in plan["entities"]:
        footprint |= entity_tiles(entity, prototypes.get(entity["prototype"], {}))
    dx, dy = -min(x for x, _ in footprint), -min(y for _, y in footprint)
    entities, requirements = [], []
    for entity in plan["entities"]:
        moved = {"id": entity["id"], "prototype": entity["prototype"], "direction": entity.get("direction", 0),
                 "position": {"x": entity["position"]["x"] + dx, "y": entity["position"]["y"] + dy}}
        for key in KEPT:   # (a setting that is off, false, is kept too)
            if entity.get(key) is not None and entity.get(key) != "":
                moved[key] = entity[key]
        entities.append(moved)
        spec = prototypes.get(entity["prototype"], {})
        if spec.get("type") == "offshore-pump" or spec.get("requires_water"):
            x, y = entity["position"]["x"], entity["position"]["y"]
            near = box_tiles(x - 3, y - 3, x + 3, y + 3)
            wet = near & resources.get("water", set())
            if wet:
                requirements.append({"entity_id": entity["id"], "resource": "water",
                                     "tiles": sorted([tx + dx, ty + dy] for tx, ty in wet)})
        if spec.get("type") == "mining-drill":
            r = spec.get("mining_area_radius", 0.99)
            x, y = entity["position"]["x"], entity["position"]["y"]
            area = box_tiles(x - r, y - r, x + r, y + r)
            best = max((name for name in resources if name != "water"),
                       key=lambda name: len(area & resources[name]), default=None)
            covered = area & resources.get(best, set())
            if covered:
                requirements.append({"entity_id": entity["id"], "resource": best,
                                     "tiles": sorted([tx + dx, ty + dy] for tx, ty in covered)})
    tiles = sorted([x + dx, y + dy] for x, y in footprint)
    bills = []
    for stage in plan["stages"]:
        bill: dict[str, int] = {}
        for entity_id in stage["entity_ids"]:
            entity = next(e for e in plan["entities"] if e["id"] == entity_id)
            item = prototypes.get(entity["prototype"], {}).get("placement_item", entity["prototype"])
            bill[item] = bill.get(item, 0) + 1
        bills.append(bill)
    stages = [{k: stage.get(k) or [] for k in ("id", "depends_on", "entity_ids", "manual_supplies", "witnesses")}
              for stage in plan["stages"]]
    outputs = sorted({w["item"] for s in stages for w in s["witnesses"]
                      if w.get("kind") == "product_counter_delta" and w.get("item")})
    layout = {"entities": entities, "stages": stages}
    blueprint_id = hashlib.sha256(_canonical(layout)).hexdigest()[:16]
    width = max(x for x, _ in tiles) + 1
    height = max(y for _, y in tiles) + 1
    return {"schema": SCHEMA, "id": blueprint_id, "purpose": purpose or site.get("purpose") or "unspecified",
            "source": source, "outputs": outputs, "size": [width, height], "footprint": tiles,
            "resource_requirements": requirements, "stage_bills": bills, **layout,
            "prototypes": {name: prototypes[name] for name in {e["prototype"] for e in entities} if name in prototypes},
            "stats": {"uses": 0, "successes": 0, "failures": 0, "first_output_ticks": [], "complete_ticks": [],
                      "last_failure": None}}


def rotate_tile(tile: Iterable[int], quarter_turns: int) -> tuple[int, int]:
    x, y = tile
    for _ in range(quarter_turns % 4):
        x, y = -y - 1, x
    return x, y


def rotate_point(x: float, y: float, quarter_turns: int) -> tuple[float, float]:
    for _ in range(quarter_turns % 4):
        x, y = -y, x
    return x, y


def rotated(blueprint: Mapping[str, Any], quarter_turns: int) -> dict:
    """Rotate clockwise by 90-degree steps and re-anchor at (0, 0)."""
    tiles = [rotate_tile(t, quarter_turns) for t in blueprint["footprint"]]
    dx, dy = -min(x for x, _ in tiles), -min(y for _, y in tiles)
    entities = []
    for e in blueprint["entities"]:
        x, y = rotate_point(e["position"]["x"], e["position"]["y"], quarter_turns)
        turned = {**e, "position": {"x": x + dx, "y": y + dy},
                  "direction": (e.get("direction", 0) + 4 * quarter_turns) % 16}
        for key in ("pickup_position", "drop_position"):   # (an inserter's own vectors turn with it)
            v = e.get(key)
            if isinstance(v, Mapping):
                vx, vy = float(v.get("x") or 0), float(v.get("y") or 0)
                for _ in range(quarter_turns % 4):
                    vx, vy = -vy, vx
                turned[key] = {"x": vx, "y": vy}
        entities.append(turned)
    requirements = [{**r, "tiles": [[x + dx, y + dy] for x, y in (rotate_tile(t, quarter_turns) for t in r["tiles"])]}
                    for r in blueprint["resource_requirements"]]
    return {**blueprint, "entities": entities, "resource_requirements": requirements,
            "footprint": [[x + dx, y + dy] for x, y in tiles], "rotation": quarter_turns % 4}


ANY_ORE = "*ore"   # (a mining layout for whichever ore patch it is placed on: resolved at placement)


def placements(blueprint: Mapping[str, Any], site: Mapping[str, Any], *, limit: int = 3) -> list[dict]:
    """Where `blueprint` fits on `site`: every drill on its resource (a layout for any ore: on one patch's ore, whichever,
    said in the placement), nothing on hard-blocked tiles."""
    reqs = blueprint.get("resource_requirements") or []
    if any(r.get("resource") == ANY_ORE for r in reqs):
        resources, _, _ = site_tiles(site)
        found = []
        for ore in sorted(r for r in resources if r not in ("water", "crude-oil")):
            one = {**blueprint, "resource_requirements": [{**r, "resource": ore} if r.get("resource") == ANY_ORE else r
                                                          for r in reqs]}
            found += [{**p, "resource": ore} for p in placements(one, site, limit=limit)]
        found.sort(key=lambda p: (p["thin"] > 0, p["relocations"], p["thin"], p["distance"], p["rotation"],
                                  p["offset"]))
        return found[:limit]
    resources, blocked, movable = site_tiles(site)
    rich = rich_tiles(site)
    _, _, width, height = _as_list(site["bounds"])
    home = site.get("home") or {"x": width / 2, "y": height / 2}
    found = []
    for turns in range(4):
        shape = rotated(blueprint, turns)
        footprint = [tuple(t) for t in shape["footprint"]]
        requirements = shape["resource_requirements"]
        wet = {tuple(t) for r in requirements if r["resource"] == "water" for t in r["tiles"]}
        if requirements:
            anchor = requirements[0]
            ore = resources.get(anchor["resource"], set())
            ax, ay = anchor["tiles"][0]
            offsets = {(ox - ax, oy - ay) for ox, oy in ore}
        else:
            offsets = {(x, y) for x in range(int(width)) for y in range(int(height))}
        for ox, oy in offsets:
            if any(not (0 <= x + ox < width and 0 <= y + oy < height)
                   or ((x + ox, y + oy) in blocked and (x, y) not in wet) for x, y in footprint):
                continue
            if not all((x + ox, y + oy) in resources.get(r["resource"], set())
                       for r in requirements for x, y in r["tiles"]):
                continue
            # Drills on the thin edge of a patch run dry in minutes: count the tiles that are not rich.
            thin = sum(1 for r in requirements if r["resource"] in rich for x, y in r["tiles"]
                       if (x + ox, y + oy) not in rich[r["resource"]])
            cx = ox + sum(x for x, _ in footprint) / len(footprint)
            cy = oy + sum(y for _, y in footprint) / len(footprint)
            moved = {movable[(x + ox, y + oy)] for x, y in footprint if (x + ox, y + oy) in movable}
            found.append({"rotation": turns, "offset": [ox, oy], "relocations": len(moved), "thin": thin,
                          "distance": math.hypot(cx - home["x"], cy - home["y"])})
    found.sort(key=lambda p: (p["thin"] > 0, p["relocations"], p["thin"], p["distance"], p["rotation"],
                              p["offset"]))
    return found[:limit]


def instantiate(blueprint: Mapping[str, Any], placement: Mapping[str, Any], request_id: str) -> dict:
    shape = rotated(blueprint, placement["rotation"])
    ox, oy = placement["offset"]
    entities = []
    for e in shape["entities"]:
        entity = {"id": e["id"], "prototype": e["prototype"], "direction": e["direction"],
                  "position": {"x": e["position"]["x"] + ox, "y": e["position"]["y"] + oy}}
        for key in KEPT:
            if e.get(key) is not None and e.get(key) != "":
                entity[key] = e[key]
        entities.append(entity)
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(request_id))
    return {"plan_id": f"bp-{blueprint['id']}-{safe}", "source": f"catalog:{blueprint['source']}",
            "blueprint_id": blueprint["id"], "entities": entities,
            "stages": json.loads(json.dumps(blueprint["stages"]))}


def locked_items(blueprint: Mapping[str, Any], site: Mapping[str, Any]) -> list[str]:
    """Items a layout places that the engineer neither carries enough of nor can craft yet (its recipe
    is still locked): a layout needing them stalls the build until research that may never come."""
    inventory = site.get("inventory") or {}
    recipes = ratios.unlocked(site)
    need: dict[str, int] = {}
    for bill in blueprint["stage_bills"]:
        for item, count in bill.items():
            need[item] = need.get(item, 0) + count
    # (and the recipes its machines are set to: an imported layout's science waits for its research,
    # companion/blueprint_packs.py)
    unset = [f"recipe:{r}" for r in blueprint.get("recipes_needed") or () if r not in recipes]
    return sorted(item for item, count in need.items() if inventory.get(item, 0) < count and item not in recipes)         + sorted(unset)


class Catalog:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self.path.mkdir(parents=True, exist_ok=True)
        self.blueprints: dict[str, dict] = {}
        # (several engineers' planners share one library and may write to it at once)
        self._lock = threading.RLock()
        for file in sorted(self.path.glob("*.json")):
            try:
                value = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if value.get("schema") == SCHEMA and isinstance(value.get("id"), str):
                self.blueprints[value["id"]] = value

    def __len__(self) -> int:
        return len(self.blueprints)

    def save(self, blueprint: dict) -> None:
        with self._lock:
            target = self.path / f"{blueprint['id']}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(blueprint, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(temp, target)
            self.blueprints[blueprint["id"]] = blueprint

    def add(self, blueprint: dict) -> dict:
        with self._lock:
            existing = self.blueprints.get(blueprint["id"])
            if existing:
                return existing
            self.save(blueprint)
            return blueprint

    def candidates(self, site: Mapping[str, Any], purpose: str, limit: int = MAX_CANDIDATES) -> list[dict]:
        """The saved layouts for `purpose` that can be built here as they are, best first (the weighing in
        companion/build_choice.py, which also reports on those that cannot)."""
        from companion import build_choice
        return [{"blueprint": r.build, "placement": r.placement, "score": r.score}
                for r in build_choice.weigh(self, site, purpose) if r.usable][:limit]

    def record(self, blueprint_id: str, *, success: bool, first_output_ticks: int | None = None,
               complete_ticks: int | None = None, reason: str | None = None,
               use: Mapping[str, Any] | None = None) -> None:
        """A build's outcome from the game: its counts, and with `use` (world, engineer, plan...) the use
        in its history (companion/build_history.py)."""
        with self._lock:
            blueprint = self.blueprints.get(blueprint_id)
            if not blueprint:
                return
            stats = blueprint["stats"]
            stats["uses"] += 1
            if success:
                stats["successes"] += 1
            else:
                stats["failures"] += 1
                stats["last_failure"] = reason
            for key, value in (("first_output_ticks", first_output_ticks), ("complete_ticks", complete_ticks)):
                if isinstance(value, int) and value >= 0:
                    stats[key] = (stats[key] + [value])[-20:]
            if use is not None:
                from companion import build_history
                build_history.note_outcome(blueprint, use)
            self.save(blueprint)


def score(blueprint: Mapping[str, Any], placement: Mapping[str, Any]) -> float:
    """Prefer proven, fast, nearby layouts that measured well where they were built; untried ones get a
    neutral prior (so a variant that runs better than its parent wins once its uses show it)."""
    from companion import build_history
    stats = blueprint["stats"]
    success_rate = (stats["successes"] + 1) / (stats["uses"] + 2)
    first = stats["first_output_ticks"]
    speed = 1.0 / (1.0 + (sum(first) / len(first)) / 3600.0) if first else 0.5
    return round(2.0 * success_rate + speed + 2.0 * build_history.performance(blueprint)
                 - placement["distance"] / 64.0 - 0.25 * placement.get("relocations", 0), 6)


def describe(candidate: Mapping[str, Any]) -> str:
    blueprint, placement = candidate["blueprint"], candidate["placement"]
    stats = blueprint["stats"]
    counts: dict[str, int] = {}
    for entity in blueprint["entities"]:
        counts[entity["prototype"]] = counts.get(entity["prototype"], 0) + 1
    parts = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    record = (f"built {stats['uses']} times, {stats['successes']} succeeded" if stats["uses"] else "never built yet")
    first = stats["first_output_ticks"]
    timing = f", first output after about {round(sum(first) / len(first) / 60)} s" if first else ""
    moves = placement.get("relocations", 0)
    moving = f"; requires moving {moves} of your own buildings" if moves else ""
    return (f"Saved layout {blueprint['id'][:8]}: {parts} in {len(blueprint['stages'])} stages, "
            f"{blueprint['size'][0]}x{blueprint['size'][1]} tiles, {round(placement['distance'])} tiles from home; "
            f"{record}{timing}{moving}.")
