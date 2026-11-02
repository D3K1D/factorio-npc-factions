"""Rates in this game, from what its machines do: the game's own data (/npc-plan facts, mods included,
normal quality) with the engineer's research bonuses. The designer's brief and System One's options take
their numbers from here, so a plan is sized on what this game's machines really make, burn and carry.

    facts = rates.of(site)                       # the export the bridge attached to the request, or None
    facts.per_machine(recipe_row)                # who can make it and how fast: [(machine, items/s), ...]
    facts.inserter_rate("fast-inserter")         # items/s chest to chest, with the stack bonus researched
    facts.fuel_per_second("stone-furnace")       # coal a second at full burn
    facts.steam()                                # pumps : boilers : engines
    facts.brief(["stone-furnace", "inserter"])   # a few plain lines of the facts a request needs
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

FUEL = "coal"                 # what burner machines are fed, unless the game has none of it
WATER = (0.2, 15.0)           # kJ per degree and unit, starting temperature: water's, when the game says nothing
BRIEF_CHARS = 1400            # the facts lines a brief carries at most


def _rows(value: Any) -> list:
    return list(value.values()) if isinstance(value, Mapping) else list(value or [])


class Facts:
    def __init__(self, raw: Mapping[str, Any]):
        self.raw = raw
        self.entities: dict[str, dict] = {k: dict(v) for k, v in (raw.get("entities") or {}).items()
                                          if isinstance(v, Mapping)}
        self.fuels: dict[str, dict] = dict(raw.get("fuels") or {})
        self.modules: dict[str, dict] = dict(raw.get("modules") or {})
        self.qualities: dict[str, dict] = dict(raw.get("qualities") or {})
        self.fluids: dict[str, dict] = dict(raw.get("fluids") or {})
        self.bonuses: dict[str, float] = {k: float(v) for k, v in (raw.get("bonuses") or {}).items()
                                          if isinstance(v, (int, float))}

    @property
    def key(self) -> str:
        """What the facts depend on: the game, its mods and the research bonuses."""
        basis = {"game": self.raw.get("game_version"), "mods": sorted((self.raw.get("mods") or {}).items()),
                 "bonuses": sorted(self.bonuses.items()), "ready": sorted(n for n, e in self.entities.items()
                                                                          if e.get("ready"))}
        return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:16]

    def of_kind(self, kind: str, ready: bool | None = None) -> list[str]:
        return sorted(n for n, e in self.entities.items()
                      if e.get("kind") == kind and (ready is None or bool(e.get("ready")) == ready))

    # --- crafting ---------------------------------------------------------

    def makers(self, categories: Iterable[str]) -> list[str]:
        """Machines that can make a recipe of any of `categories`: buildable now first, fastest first."""
        wanted = set(categories)
        found = [n for n, e in self.entities.items()
                 if e.get("kind") == "crafter" and wanted & set(_rows(e.get("categories")))]
        return sorted(found, key=lambda n: (not self.entities[n].get("ready"), -float(self.entities[n].get("speed") or 0), n))

    def per_machine(self, recipe: Mapping[str, Any], limit: int = 3) -> list[tuple[str, float]]:
        """For a recipe row (the site export's): its main product per second in each machine that can
        make it, with the machine's own productivity."""
        seconds = float(recipe.get("energy") or recipe.get("seconds") or 0)
        products = _rows(recipe.get("products") or recipe.get("results"))
        if seconds <= 0 or not products:
            return []
        amount = float(products[0].get("amount") or 1)
        categories = _rows(recipe.get("categories")) or [recipe.get("category") or "crafting"]
        out = []
        for name in self.makers(categories)[:limit]:
            e = self.entities[name]
            rate = float(e.get("speed") or 0) / seconds * amount * (1 + float(e.get("productivity") or 0))
            out.append((name, round(rate, 4)))
        return out

    # --- moving things ----------------------------------------------------

    def inserter_rate(self, name: str) -> float | None:
        """Items a second chest to chest: one swing there and back a full turn, its hand stack the
        inserter's own bonus plus the force's (bulk inserters carry the bulk bonus)."""
        e = self.entities.get(name)
        if not e or e.get("kind") != "inserter" or not e.get("rotation"):
            return None
        stack = 1 + float(e.get("stack_bonus") or 0)
        if e.get("bulk"):
            stack += self.bonuses.get("bulk_inserter_capacity", 0)
        elif e.get("force_bonus", True):
            stack += self.bonuses.get("inserter_stack", 0)
        return round(float(e["rotation"]) * 60 * stack, 3)

    def belt_rate(self, name: str) -> float | None:
        e = self.entities.get(name)
        rate = e and e.get("items_per_second")
        return float(rate) * (1 + self.bonuses.get("belt_stack", 0)) if rate else None

    # --- energy -----------------------------------------------------------

    def fuel_per_second(self, name: str, fuel: str = FUEL) -> float | None:
        """Fuel a burner machine burns a second at full work."""
        e, f = self.entities.get(name), self.fuels.get(fuel)
        if not e or e.get("source") != "burner" or not e.get("kw") or not f or not f.get("mj"):
            return None
        return round(float(e["kw"]) / (float(f["mj"]) * 1000) / float(e.get("effectivity") or 1), 4)

    def kw(self, name: str) -> float | None:
        e = self.entities.get(name)
        if not e or e.get("source") != "electric" or e.get("kw") is None:
            return None
        return round(float(e["kw"]) + float(e.get("drain_kw") or 0), 3)

    def _first(self, kind: str, by: str) -> str | None:
        """The building of a kind a player starts with: one buildable now, else the first tier (the
        lowest `by`: a boiler before a heat exchanger, a steam engine before a modded turbine)."""
        names = self.of_kind(kind)
        names.sort(key=lambda n: (not self.entities[n].get("ready"), float(self.entities[n].get(by) or 0), n))
        return names[0] if names else None

    def steam(self, boiler: str | None = None, engine: str | None = None, pump: str | None = None) -> dict | None:
        """How a steam plant fits together: steam a boiler makes, engines per boiler, boilers per pump."""
        boiler = boiler or self._first("boiler", "target_temperature")
        engine = engine or self._first("generator", "max_kw")
        pump = pump or self._first("pump", "per_second")
        b, g, p = self.entities.get(boiler or ""), self.entities.get(engine or ""), self.entities.get(pump or "")
        if not (b and g and b.get("kw") and b.get("target_temperature") and g.get("fluid_per_second")):
            return None
        # water in, steam out: the energy a unit takes is steam's heat capacity over the rise from water's
        # temperature (2.1: steam 0.2 kJ a degree)
        steam_fluid, water = self.fluids.get("steam") or {}, self.fluids.get("water") or {}
        heat = float(steam_fluid.get("heat_capacity_kj") or WATER[0])
        start = float(water.get("default_temperature") or WATER[1])
        steam = float(b["kw"]) * float(b.get("effectivity") or 1) / (heat * (float(b["target_temperature"]) - start))
        out = {"boiler": boiler, "engine": engine, "steam_per_boiler": round(steam, 2),
               "engines_per_boiler": round(steam / float(g["fluid_per_second"]), 2),
               "engine_kw": g.get("max_kw"), "boiler_fuel": self.fuel_per_second(boiler)}
        if p and p.get("per_second"):
            out.update(pump=pump, boilers_per_pump=round(float(p["per_second"]) / steam, 1))
        return out

    # --- the brief --------------------------------------------------------

    def brief(self, buildings: Iterable[str] = (), limit: int = BRIEF_CHARS) -> str:
        """The facts a request needs, in a few plain lines: the buildings it may place (what each makes,
        burns or draws), the inserters and belts it can use, and how steam power fits together."""
        lines = []
        machines = []
        for name in sorted(set(buildings)):
            e = self.entities.get(name)
            if not e:
                continue
            parts = []
            if e.get("kind") in ("crafter", "drill", "lab") and e.get("speed"):
                parts.append(("mines " if e["kind"] == "drill" else "speed ") + f"{e['speed']:g}"
                             + (" a second" if e["kind"] == "drill" else ""))
            burn = self.fuel_per_second(name)
            if burn:
                parts.append(f"burns {burn:g} {FUEL} a second")
            draw = self.kw(name)
            if draw:
                parts.append(f"draws {draw:g} kW")
            if e.get("max_kw"):
                parts.append(f"gives up to {e['max_kw']:g} kW")
            if e.get("per_second"):
                parts.append(f"pumps {e['per_second']:g} a second")
            if e.get("modules"):
                parts.append(f"{e['modules']} module slots")
            if parts:
                machines.append(f"{name} " + ", ".join(parts))
        if machines:
            lines.append("Buildings: " + "; ".join(machines) + ".")
        moving = []
        for name in self.of_kind("inserter"):
            rate = self.inserter_rate(name)
            if rate and (self.entities[name].get("ready") or name in buildings):
                moving.append(f"{name} {rate:g} a second")
        for name in self.of_kind("belt"):
            rate = self.belt_rate(name)
            if rate and (self.entities[name].get("ready") or name in buildings):
                moving.append(f"{name} {rate:g} a second (both lanes)")
        if moving:
            lines.append("Moving items (chest to chest, research included): " + "; ".join(moving) + ".")
        plant = self.steam()
        if plant and (set(buildings) & {plant["boiler"], plant["engine"]} or self.entities[plant["boiler"]].get("ready")):
            text = (f"Steam: a {plant['boiler']} makes {plant['steam_per_boiler']:g} steam a second, enough for "
                    f"{plant['engines_per_boiler']:g} {plant['engine']} ({plant['engine_kw']:g} kW each)")
            if plant.get("boilers_per_pump"):
                text += f"; one {plant['pump']} feeds {plant['boilers_per_pump']:g} boilers"
            lines.append(text + ".")
        out = "\n".join(lines)
        return out[:limit]


def layout_note(counts: Mapping[str, int], facts: Facts, recipes: Iterable[Mapping[str, Any]] = (),
                resource: str | None = None) -> str:
    """What a layout does at full speed, in one plain sentence, for System One to weigh layouts on: what
    its furnaces smelt (from `resource`), what its drills mine, what it burns and draws."""
    smelt = next((r for r in recipes if (r.get("category") == "smelting" or "smelting" in _rows(r.get("categories")))
                  and resource and any(i.get("name") == resource for i in _rows(r.get("ingredients")))), None)
    made, mined, burned, drawn = 0.0, 0.0, 0.0, 0.0
    for name, n in counts.items():
        e = facts.entities.get(name)
        if not e:
            continue
        if e.get("kind") == "crafter" and smelt and "smelting" in _rows(e.get("categories")) and smelt.get("energy"):
            made += n * float(e.get("speed") or 0) / float(smelt["energy"])
        elif e.get("kind") == "drill":
            mined += n * float(e.get("speed") or 0)
        burned += n * (facts.fuel_per_second(name) or 0)
        drawn += n * (facts.kw(name) or 0)
    parts = []
    if made:
        product = _rows(smelt.get("products"))[0].get("name") if _rows(smelt.get("products")) else "plates"
        parts.append(f"smelts up to {made:.2f} {product} a second")
    if mined:
        parts.append(f"mines {mined:.2f} {resource or 'ore'} a second")
    if burned:
        parts.append(f"burns {burned:.3f} {FUEL} a second")
    if drawn:
        parts.append(f"draws {drawn:.0f} kW")
    return ("At full speed it " + ", ".join(parts) + ".") if parts else ""


_cache: dict[int, Facts] = {}


def of(site: Mapping[str, Any]) -> Facts | None:
    """The facts the bridge attached to this request (site["game_facts"]), or None without them."""
    raw = site.get("game_facts") if isinstance(site, Mapping) else None
    if not isinstance(raw, Mapping) or not raw.get("entities"):
        return None
    found = _cache.get(id(raw))
    if found is None or found.raw is not raw:
        if len(_cache) > 64:
            _cache.clear()
        found = _cache[id(raw)] = Facts(raw)
    return found
