"""The base's power ahead: what it draws at full work now and once its next planned sections stand, against what its
generators can give. The user plays ahead (never underproduce): new power is due before the base runs short, not after
a brownout (the coordinator, 2026-10-30: nuclear once the projected draw passes about 80% of capacity).

From the site export alone: the base's blocks and their machines (site["base"]), the factory plan's sections not yet
built and what each is to make (site["factory_plan"]), the game's facts for every machine's draw and every generator's
output (companion/rates.py), and the site's recipes for the machines a section's items take.

    outlook(site)   {"capacity_kw", "draw_kw", "planned_kw", "share"}, or None when no generator stands
    due(site)       the share past DUE
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from companion import rates

DUE = 0.8
AHEAD = ("planned", "waiting", "building")   # (sections whose machines are still to come, wholly or in part)


def _machines(site: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for block in site.get("base") or []:
        for name, n in ((block or {}).get("machines") or {}).items() if isinstance(block, Mapping) else ():
            if isinstance(n, (int, float)):
                out[str(name)] = out.get(str(name), 0) + int(n)
    return out


def capacity_kw(site: Mapping[str, Any], facts: rates.Facts) -> float:
    """What the generators standing can give: steam engines and turbines no more than their boilers and heat
    exchangers make steam for, solar panels at their peak."""
    gens = heat = solar = 0.0
    for name, n in _machines(site).items():
        e = facts.entities.get(name) or {}
        if e.get("kind") == "generator":
            gens += float(e.get("max_kw") or 0) * n
        elif e.get("kind") == "boiler":
            heat += float(e.get("kw") or 0) * n
        elif e.get("kind") == "solar":
            solar += float(e.get("max_kw") or 0) * n
    return min(gens, heat) + solar


def draw_kw(site: Mapping[str, Any], facts: rates.Facts) -> float:
    """What the electric machines standing draw at full work."""
    return sum((facts.kw(name) or 0.0) * n for name, n in _machines(site).items())


def planned_kw(site: Mapping[str, Any], facts: rates.Facts) -> float:
    """What the factory plan's next sections will draw at full work: each item they are to make, by the machines its
    recipe takes at that rate (the best maker for it), the share a building section has still to build."""
    from companion import factory_plan
    plan = factory_plan.plan_of(site) or {}
    recipes = [r for r in site.get("recipes") or [] if isinstance(r, Mapping)]
    from companion.catalog_planner import known_resources
    ores = known_resources(site) | set(facts.raw.get("resources") or {})   # (what a mining section can give)
    total = 0.0
    for s in plan.get("sections") or []:
        if s.get("status") not in AHEAD:
            continue
        left = 1.0 - min(1.0, max(0.0, float(s.get("progress") or 0))) if s.get("status") == "building" else 1.0
        for item, rate in (s.get("makes") or {}).items():
            recipe = next((r for r in recipes if any(isinstance(p, Mapping) and p.get("name") == item
                                                     for p in r.get("products") or [])), None)
            # (no recipe: an ore it is to mine, by drills; else a product whose recipe is locked yet, not counted)
            makers = facts.per_machine(recipe, limit=1) if recipe else _drills(facts, item) if item in ores else []
            if not makers or makers[0][1] <= 0:
                continue
            maker, each = makers[0]
            total += (facts.kw(maker) or 0.0) * math.ceil(float(rate) / each - 1e-9) * left
    return total


MINING_TIME = 1.0   # seconds a mining cycle of an ore takes when the facts do not say (the vanilla ores')


def _drills(facts: rates.Facts, item: str) -> list[tuple[str, float]]:
    """An item no recipe makes (an ore a mining section is to give): the best electric drill that mines it, and what
    one gives a second (its speed over the ore's mining time, with the force's mining productivity)."""
    from companion.factory_plan import FLUIDS
    if item in FLUIDS:
        return []   # (crude oil and the like: pumpjacks, not counted here)
    raw = (facts.raw.get("resources") or {}).get(item) or {}
    category = raw.get("category") or "basic-solid"
    found = [(n, e) for n, e in facts.entities.items() if e.get("kind") == "drill" and e.get("source") == "electric"
             and (not e.get("categories") or category in e["categories"]) and float(e.get("speed") or 0) > 0]
    if not found:
        return []
    name, e = max(found, key=lambda ne: (bool(ne[1].get("ready")), float(ne[1]["speed"]), ne[0]))
    each = float(e["speed"]) / float(raw.get("mining_time") or MINING_TIME) * (1 + facts.bonuses.get("mining_productivity", 0))
    return [(name, each)]


def outlook(site: Mapping[str, Any]) -> dict | None:
    facts = rates.of(site)
    if facts is None:
        return None
    capacity = capacity_kw(site, facts)
    if capacity <= 0:
        return None
    draw, planned = draw_kw(site, facts), planned_kw(site, facts)
    return {"capacity_kw": round(capacity, 1), "draw_kw": round(draw, 1), "planned_kw": round(planned, 1),
            "share": round((draw + planned) / capacity, 3)}


def due(site: Mapping[str, Any]) -> bool:
    """New power due: the draw at full work, now and with the next planned sections, past DUE of what the
    generators can give."""
    o = outlook(site)
    return o is not None and o["share"] >= DUE
