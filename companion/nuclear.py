"""Nuclear power sized from the game's own numbers: how many heat exchangers and turbines a block of reactors
feeds, the fuel cells it burns, its water, and the uranium chain behind the cells.

Reactors stand in two rows (2xN): each one heats more for every working neighbour (the prototype's
neighbour_bonus), so a 2x2 block gives three times a lone reactor's heat each. Every rate comes from the
game's facts (companion/rates.py: reactors, heat exchangers as boilers on heat, steam turbines as generators,
fuels, steam's heat capacity) and the site's recipes, mods included. The layout and the build come later.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from companion import rates

FUEL_CELL = "uranium-fuel-cell"
PROCESSING = "uranium-processing"
U235, U238 = "uranium-235", "uranium-238"
WATER_TEMPERATURE = 15.0   # (when the game's facts do not say)


def neighbours(n: int, rows: int = 2) -> list[int]:
    """How many neighbours each of `n` reactors has, set out in `rows` rows side by side (2xN)."""
    if n <= 0:
        return []
    rows = max(1, min(rows, n))
    cols = math.ceil(n / rows)
    cells = [(i % cols, i // cols) for i in range(n)]
    taken = set(cells)
    return [sum((x + dx, y + dy) in taken for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))) for x, y in cells]


def _pick(facts: rates.Facts, kind: str, test=lambda e: True, by: str = "kw") -> tuple[str, dict] | None:
    found = [(n, e) for n, e in facts.entities.items() if e.get("kind") == kind and test(e)]
    if not found:
        return None
    return max(found, key=lambda ne: (bool(ne[1].get("ready")), float(ne[1].get(by) or 0), ne[0]))


def _recipe(site: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    return next((r for r in site.get("recipes") or [] if isinstance(r, Mapping) and r.get("name") == name), None)


def _made(recipe: Mapping[str, Any], item: str) -> float:
    """What a craft of `recipe` gives of `item` on average (its chance counted)."""
    return sum(float(p.get("amount") or 0) * float(p.get("probability") or 1)
               for p in recipe.get("products") or [] if isinstance(p, Mapping) and p.get("name") == item)


def _takes(recipe: Mapping[str, Any], item: str) -> float:
    return sum(float(i.get("amount") or 0) for i in recipe.get("ingredients") or []
               if isinstance(i, Mapping) and i.get("name") == item)


def sizing(site: Mapping[str, Any], reactors: int = 2) -> dict | None:
    """The plant for `reactors` reactors in a 2xN block: its heat, heat exchangers, turbines, megawatts, fuel
    cells and water a minute, offshore pumps, and the centrifuges and uranium ore behind the cells. None
    when the game's facts lack a reactor, a heat exchanger or a turbine."""
    facts = rates.of(site)
    if facts is None:
        return None
    # The pieces that join as a nuclear plant: a reactor burning the fuel cell, a heat-fed boiler making steam, a
    # turbine on steam (a mod set has more: a fluid-heated solar tower, a turbine on another steam, a moon's reactor).
    burns = set((facts.fuels.get(FUEL_CELL) or {}).get("categories") or ["nuclear"])

    def boxes(e):
        return {b.get("filter") for b in e.get("fluid") or [] if isinstance(b, Mapping)}
    reactor = _pick(facts, "reactor", lambda e: bool(set(e.get("fuel") or burns) & burns) and not boxes(e))
    exchanger = _pick(facts, "boiler", lambda e: e.get("source") == "heat" and (not boxes(e) or "steam" in boxes(e)))
    turbine = _pick(facts, "generator", lambda e: float(e.get("fluid_per_second") or 0) > 0
                    and (not boxes(e) or boxes(e) <= {"steam", None}), by="max_kw")
    if not (reactor and exchanger and turbine):
        return None
    r, x, t = reactor[1], exchanger[1], turbine[1]
    reactor_kw, bonus = float(r.get("kw") or 0), float(r.get("neighbour_bonus") or 0)
    if reactor_kw <= 0 or float(x.get("kw") or 0) <= 0 or float(t.get("fluid_per_second") or 0) <= 0:
        return None
    links = neighbours(reactors)
    heat_kw = sum(reactor_kw * (1 + bonus * k) for k in links)
    exchangers = math.ceil(heat_kw / float(x["kw"]) - 1e-6)
    steam = facts.fluids.get("steam") or {}
    water = facts.fluids.get("water") or {}
    capacity = float(steam.get("heat_capacity_kj") or 0.2)
    rise = float(x.get("target_temperature") or 500) - float(water.get("default_temperature") or WATER_TEMPERATURE)
    steam_each = float(x["kw"]) / (capacity * rise)               # steam a second from one exchanger at full heat
    turbines = math.ceil(exchangers * steam_each / float(t["fluid_per_second"]) - 1e-6)
    power_kw = min(heat_kw, turbines * float(t.get("max_kw") or 0))
    fuel_mj = float((facts.fuels.get(FUEL_CELL) or {}).get("mj") or 0)
    cells_min = reactors * 60 * reactor_kw / 1000 / fuel_mj if fuel_mj else None   # (a reactor burns at one rate)
    pump = _pick(facts, "pump", by="per_second")
    water_s = exchangers * steam_each
    out = {"reactor": reactor[0], "heat_exchanger": exchanger[0], "turbine": turbine[0], "reactors": reactors,
           "neighbours": links, "heat_mw": round(heat_kw / 1000, 3), "heat_exchangers": exchangers,
           "turbines": turbines, "power_mw": round(power_kw / 1000, 3), "steam_per_second": round(water_s, 2),
           "offshore_pumps": math.ceil(water_s / float(pump[1]["per_second"]) - 1e-6) if pump else None,
           "fuel_cells_per_minute": round(cells_min, 4) if cells_min is not None else None}
    out.update(_uranium(site, facts, cells_min))
    return out


def _uranium(site: Mapping[str, Any], facts: rates.Facts, cells_min: float | None) -> dict:
    """The centrifuges and ore behind `cells_min` fuel cells a minute (the fuel cell recipe's U-235 and U-238,
    uranium processing's chance of U-235)."""
    cell, processing = _recipe(site, FUEL_CELL), _recipe(site, PROCESSING)
    if not (cell and processing and cells_min):
        return {}
    per_cell = {u: _takes(cell, u) / max(_made(cell, FUEL_CELL), 1e-9) for u in (U235, U238)}
    need = {u: per_cell[u] * cells_min / 60 for u in (U235, U238)}   # a second
    maker = (facts.per_machine(processing, limit=1) or [(None, 0)])[0][0]
    speed = float((facts.entities.get(maker) or {}).get("speed") or 1) if maker else 1.0
    crafts = speed / max(float(processing.get("energy") or 1), 1e-9)   # crafts a second per centrifuge
    made = {u: _made(processing, u) * crafts for u in (U235, U238)}
    centrifuges = max(math.ceil(need[u] / made[u] - 1e-6) for u in (U235, U238) if made[u] > 0) \
        if any(made.values()) else None
    ore = _takes(processing, "uranium-ore") * crafts * (centrifuges or 0)
    return {"centrifuge": maker, "centrifuges": centrifuges, "uranium_ore_per_second": round(ore, 4),
            "u235_per_minute": round(need[U235] * 60, 4), "u238_per_minute": round(need[U238] * 60, 4)}


def mining(site: Mapping[str, Any], ore_per_second: float, resource: str = "uranium-ore",
           width: int | None = None) -> dict | None:
    """The drills for `ore_per_second` of a resource mined with a fluid, and the fluid they take: from the
    resource's mining time and fluid per cycle (the game's facts) and the best drill's speed (of those `width`
    tiles wide, when given: the field layout's). None when the facts do not say how it is mined."""
    facts = rates.of(site)
    raw = (facts.raw.get("resources") or {}).get(resource) if facts else None
    if not raw or ore_per_second <= 0:
        return None
    category = raw.get("category")

    # (facts that give drills' fluid boxes say which take the fluid: a dry drill does not; older facts say nothing)
    known = any(e.get("fluid") for e in facts.entities.values() if e.get("kind") == "drill")

    def mines(e):   # (its resource category, and a fluid in: a pumpjack is a drill too, a faster one)
        takes = [c for b in e.get("fluid") or [] if isinstance(b, Mapping) for c in b.get("c") or []
                 if len(c) < 4 or c[3] != "output"]
        return ((not category or not e.get("categories") or category in e["categories"])
                and (bool(takes) or not known))
    drill = _pick(facts, "drill", lambda e: float(e.get("speed") or 0) > 0 and mines(e)
                  and (width is None or e.get("width") == width), by="speed")
    if drill is None:
        return None
    speed, time = float(drill[1]["speed"]), float(raw.get("mining_time") or 1)
    productivity = 1 + float(facts.bonuses.get("mining_productivity") or 0)
    each = speed / time * productivity                      # ore a second per drill
    drills = math.ceil(ore_per_second / each - 1e-6)
    # fluid_amount is per ten mining cycles' worth in the prototype (per cycle it is fluid_amount / 10)
    fluid = drills * speed / time * float(raw.get("fluid_amount") or 0) / 10
    return {"drill": drill[0], "drills": drills, "ore_per_drill": round(each, 4), "fluid": raw.get("fluid"),
            "fluid_per_second": round(fluid, 4)}
