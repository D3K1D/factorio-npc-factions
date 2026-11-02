"""Blueprints players teach in game (`/npc teach`) become library entries.

The goal a blueprint serves is read from its label when it names one (for example
"copper-smelting-start", or just "copper"), otherwise from what is in it: drills
with furnaces smelt, drills with chests supply, assemblers make their main
recipe. Drills must stand on ore of their goal's resource when the layout is
reused, so the ore under each drill is recorded as a requirement.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from companion import blueprints as bp

WORDS = {"iron": "iron-smelting-expansion", "copper": "copper-smelting-start", "coal": "coal-supply",
         "stone": "stone-supply"}
RESOURCE = {"iron-smelting-expansion": "iron-ore", "copper-smelting-start": "copper-ore",
            "coal-supply": "coal", "stone-supply": "stone", "coal-loop": "coal"}
SCIENCE = {"automation-science-pack": "red-science-automation", "logistic-science-pack": "green-science-automation",
           "chemical-science-pack": "blue-science-automation"}
DRILL_RADIUS = {"burner-mining-drill": 0.99, "electric-mining-drill": 2.49}


def purpose_of(entry: Mapping[str, Any], known: set[str]) -> str | None:
    label = str(entry.get("label") or "").lower()
    for purpose in sorted(known, key=len, reverse=True):
        if purpose in label:
            return purpose
    entities = entry.get("entities") or []
    kinds = {e.get("kind") for e in entities}
    if "mining-drill" in kinds:
        if kinds == {"mining-drill"} and len(entities) >= 2:
            return "coal-loop"      # drills and nothing else: they feed each other
        for word, purpose in WORDS.items():
            if word in label:
                return purpose
        return "coal-supply" if "container" in kinds and "furnace" not in kinds else "iron-smelting-expansion"
    recipes = Counter(e["recipe"] for e in entities if e.get("recipe"))
    if not recipes:
        return None
    main = recipes.most_common(1)[0][0]
    return SCIENCE.get(main, f"module:{main}")


def to_library(entry: Mapping[str, Any]) -> dict | None:
    from companion.catalog_planner import PURPOSES
    entities = [e for e in entry.get("entities") or [] if isinstance(e, Mapping) and e.get("name")]
    if not entities:
        return None
    purpose = purpose_of(entry, set(PURPOSES))
    if purpose is None:
        return None
    prototypes, plan_entities = {}, []
    for n, e in enumerate(entities, start=1):
        prototypes[e["name"]] = {"type": e.get("kind"), "width": e.get("w", 1), "height": e.get("h", 1),
                                 "placement_item": e.get("item") or e["name"],
                                 "mining_area_radius": DRILL_RADIUS.get(e["name"], 0.99)}
        entity = {"id": f"taught-{n}", "prototype": e["name"], "direction": int(e.get("direction") or 0),
                  "position": {"x": float(e["x"]), "y": float(e["y"])}}
        if e.get("recipe"):
            entity["recipe"] = e["recipe"]
        if e.get("belt_to_ground_type"):
            entity["belt_to_ground_type"] = e["belt_to_ground_type"]
        plan_entities.append(entity)
    # The ore a reused layout must stand on: everything each drill would mine.
    rows: dict[int, set[int]] = {}
    resource = RESOURCE.get(purpose)
    for e in plan_entities:
        radius = DRILL_RADIUS.get(e["prototype"]) if resource else None
        if radius:
            x, y = e["position"]["x"], e["position"]["y"]
            for tx, ty in bp.box_tiles(x - radius, y - radius, x + radius, y + radius):
                rows.setdefault(ty, set()).add(tx)
    site = {"prototypes": prototypes, "resources": [
        {"resource": resource, "rows": [[y, x, x] for y, xs in rows.items() for x in sorted(xs)]}] if rows else []}
    plan = {"plan_id": f"taught-{entry.get('id')}", "entities": plan_entities,
            "stages": [{"id": "taught", "depends_on": [], "entity_ids": [e["id"] for e in plan_entities],
                        "manual_supplies": [], "witnesses": []}]}
    blueprint = bp.from_plan(plan, site, source=f"player:{entry.get('player', 'unknown')}", purpose=purpose)
    blueprint["label"] = str(entry.get("label") or "")[:80]
    return blueprint
