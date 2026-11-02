"""Ratio calculator: how many machines a production goal needs, like a player's calculator.

Recipe times, products and machine speeds come from the game (the site
export) or, offline, from `data/factorio-2.0.77.json`. A goal such as
"30 blue science per minute" becomes machine counts per recipe, the raw
resources it eats, and the belt lanes it needs; `audit` compares that with
what an engineer actually built.

    book = Book.vanilla()
    plan = solve({"chemical-science-pack": 0.5}, book)
    print(plan.summary())
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

DATA = Path(__file__).with_name("data") / "factorio-2.0.77.json"
RAW = frozenset({"iron-ore", "copper-ore", "coal", "stone", "crude-oil", "water", "wood", "uranium-ore"})
BELT_SPEED = {"transport-belt": 15.0, "fast-transport-belt": 30.0, "express-transport-belt": 45.0}
# Which machine a player would use for each crafting category, best first.
PREFERRED = {
    "smelting": ("electric-furnace", "steel-furnace", "stone-furnace"),
    "chemistry": ("chemical-plant",), "chemistry-or-cryogenics": ("chemical-plant",),
    "organic-or-chemistry": ("chemical-plant",), "oil-processing": ("oil-refinery",),
    "rocket-building": ("rocket-silo",),
}
ASSEMBLERS = ("assembling-machine-3", "assembling-machine-2", "assembling-machine-1")
# Recipes a planner should not pick to make an item (filling and emptying barrels, recycling). Not
# "empty-": Bob's Warfare makes its empty magazine with "empty-recipe".
SKIP = ("barrel", "recycling")
DRILL_RATE = {"electric-mining-drill": 0.5, "burner-mining-drill": 0.25}


@dataclass(frozen=True)
class Recipe:
    name: str
    category: str
    energy: float
    ingredients: tuple[tuple[str, float], ...]
    products: tuple[tuple[str, float], ...]

    def makes(self, item: str) -> float:
        return sum(amount for name, amount in self.products if name == item)

    def net(self, item: str) -> float:
        """What one craft adds of `item`, less what it uses of it (a loop like kovarex nets little)."""
        return self.makes(item) - sum(amount for name, amount in self.ingredients if name == item)


@dataclass(frozen=True)
class Machine:
    name: str
    speed: float
    categories: frozenset[str]


class Book:
    """Recipes and machines, with a player's choice of recipe and machine."""

    def __init__(self, recipes: Mapping[str, Recipe], machines: Mapping[str, Machine]):
        self.recipes, self.machines = dict(recipes), dict(machines)

    @staticmethod
    def _recipes(raw: Mapping[str, Any]) -> dict[str, Recipe]:
        out = {}
        for name, r in raw.items():
            if not isinstance(r, Mapping) or r.get("energy") is None:
                continue
            ing = tuple((i["name"], float(i["amount"])) for i in r.get("ingredients", []))
            pro = tuple((p["name"], float(p.get("amount") or 0) * float(p.get("probability") or 1))
                        for p in r.get("products", []))
            out[name] = Recipe(name, r.get("category", "crafting"), float(r["energy"]), ing, pro)
        return out

    @staticmethod
    def _machines(raw: Mapping[str, Any]) -> dict[str, Machine]:
        return {name: Machine(name, float(m["speed"]), frozenset(m.get("categories", [])))
                for name, m in raw.items() if isinstance(m, Mapping) and m.get("speed")}

    @classmethod
    def vanilla(cls, space_age: bool = False) -> "Book":
        data = json.loads(DATA.read_text(encoding="utf-8"))["space_age" if space_age else "base"]
        return cls(cls._recipes(data["recipes"]), cls._machines(data["machines"]))

    @classmethod
    def from_site(cls, site: Mapping[str, Any], space_age: bool | None = None) -> "Book":
        """The game's own recipe list when the export carries it, filled in from the offline data (Space
        Age's when the game runs it)."""
        base = cls.vanilla(bool(site.get("space_age")) if space_age is None else space_age)
        # Space Age's own recipes (the silo, platform foundations) fill in whatever the base data and
        # the export leave out: a plan may name them before the export lists them.
        extra = cls.vanilla(True)
        rows = site.get("recipes") if isinstance(site.get("recipes"), list) else []
        live = cls._recipes({r["name"]: r for r in rows if isinstance(r, Mapping) and "name" in r})
        machines = site.get("machines") if isinstance(site.get("machines"), Mapping) else {}
        return cls({**extra.recipes, **base.recipes, **live},
                   {**extra.machines, **base.machines, **cls._machines(machines)})

    def recipe_for(self, item: str) -> Recipe | None:
        """The recipe a player would use to make `item`: the one named after it, else one that makes
        it (mods name recipes freely), fewest by-products and ingredients first."""
        own = self.recipes.get(item)
        if own and own.net(item) > 0:
            return own
        if item == "petroleum-gas" and "basic-oil-processing" in self.recipes:
            return self.recipes["basic-oil-processing"]
        options = [r for r in self.recipes.values()
                   if r.net(item) > 0 and not any(s in r.name for s in SKIP)]
        options.sort(key=lambda r: (len(r.products), len(r.ingredients), r.name))
        return options[0] if options else None

    def machine_for(self, category: str, allowed: Iterable[str] | None = None) -> Machine | None:
        allowed = set(allowed) if allowed is not None else None
        order = PREFERRED.get(category) or ASSEMBLERS
        for name in order:
            m = self.machines.get(name)
            if m and category in m.categories and (allowed is None or name in allowed):
                return m
        for m in sorted(self.machines.values(), key=lambda m: -m.speed):
            if category in m.categories and (allowed is None or m.name in allowed):
                return m
        return None


@dataclass
class Step:
    recipe: str
    item: str
    rate: float          # items of `item` per second
    machine: str
    machines: float      # exact machine count at full speed
    count: int           # machines to build (rounded up)

    @property
    def utilization(self) -> float:
        return self.machines / self.count if self.count else 0.0


@dataclass
class Plan:
    targets: dict[str, float]
    steps: list[Step] = field(default_factory=list)
    raw: dict[str, float] = field(default_factory=dict)       # mined/pumped per second
    inputs: dict[str, float] = field(default_factory=dict)    # supplied from outside (e.g. the bus)

    def step(self, recipe: str) -> Step | None:
        return next((s for s in self.steps if s.recipe == recipe), None)

    def belts(self, belt: str = "transport-belt") -> dict[str, float]:
        """Belt lanes (full belts) each bus input needs."""
        speed = BELT_SPEED[belt]
        return {item: rate / speed for item, rate in {**self.inputs, **self.raw}.items()
                if item not in ("water", "crude-oil", "petroleum-gas")}

    def drills(self, drill: str = "electric-mining-drill") -> dict[str, int]:
        return {item: math.ceil(rate / DRILL_RATE[drill] - 1e-9) for item, rate in self.raw.items()
                if item in ("iron-ore", "copper-ore", "coal", "stone")}

    def summary(self) -> str:
        lines = [", ".join(f"{rate * 60:g}/min {item}" for item, rate in self.targets.items())]
        for s in self.steps:
            lines.append(f"  {s.count:>3} x {s.machine:<22} {s.recipe:<28} {s.rate * 60:8.1f}/min"
                         f"  ({s.utilization:.0%} busy)")
        for label, values in (("from outside", self.inputs), ("raw", self.raw)):
            if values:
                lines.append(f"  {label}: " + ", ".join(f"{k} {v * 60:.1f}/min" for k, v in sorted(values.items())))
        return "\n".join(lines)


def solve(targets: Mapping[str, float], book: Book, *, inputs: Iterable[str] = (),
          allowed: Iterable[str] | None = None) -> Plan:
    """Machines needed to make `targets` (items per second). Items in `inputs` are
    supplied from outside (a bus lane) instead of being made here."""
    inputs, plan = set(inputs), Plan(dict(targets))
    # Order items so every consumer is handled before its producers.
    order, seen = [], set()

    def visit(item: str) -> None:
        if item in seen:
            return
        seen.add(item)
        recipe = None if item in RAW or item in inputs else book.recipe_for(item)
        if recipe:
            for ingredient, _ in recipe.ingredients:
                visit(ingredient)
        order.append(item)

    for item in targets:
        visit(item)
    demand = {item: float(rate) for item, rate in targets.items()}
    for item in reversed(order):
        rate = demand.get(item, 0.0)
        if rate <= 0:
            continue
        if item in RAW:
            plan.raw[item] = plan.raw.get(item, 0.0) + rate
            continue
        if item in inputs:
            plan.inputs[item] = plan.inputs.get(item, 0.0) + rate
            continue
        recipe = book.recipe_for(item)
        machine = recipe and book.machine_for(recipe.category, allowed)
        if not recipe or not machine:
            plan.inputs[item] = plan.inputs.get(item, 0.0) + rate
            continue
        crafts = rate / recipe.net(item)
        exact = crafts * recipe.energy / machine.speed
        plan.steps.append(Step(recipe.name, item, rate, machine.name, exact, max(1, math.ceil(exact - 1e-9))))
        for ingredient, amount in recipe.ingredients:
            demand[ingredient] = demand.get(ingredient, 0.0) + crafts * amount
    plan.steps.reverse()  # producers first, final products last
    return plan


def unlocked(site: Mapping[str, Any]) -> set[str]:
    """What the engineer can make from its export's unlocked recipes: every recipe by name, and every
    item one makes (Bob's empty magazine comes from "empty-recipe")."""
    out: set[str] = set()
    for r in site.get("recipes") or []:
        if not isinstance(r, Mapping) or r.get("name") is None:
            continue
        out.add(str(r["name"]))
        out.update(str(p["name"]) for p in r.get("products") or [] if isinstance(p, Mapping) and "name" in p)
    return out


def science(per_minute: float, packs: Iterable[str]) -> dict[str, float]:
    return {pack: per_minute / 60 for pack in packs}


# --- audit of a built base -------------------------------------------------

SCIENCE = ("automation-science-pack", "logistic-science-pack", "chemical-science-pack",
           "military-science-pack", "production-science-pack", "utility-science-pack")


@dataclass
class AuditRow:
    recipe: str
    machines: int
    capacity: float     # items/s the built machines can make
    needed: float       # items/s the base's science output actually uses

    @property
    def utilization(self) -> float:
        return self.needed / self.capacity if self.capacity else 0.0


def audit(dump: Mapping[str, Any], book: Book) -> tuple[list[AuditRow], dict[str, float]]:
    """Compare what an engineer built (an npc-map-v1 dump) with what its science output needs.
    Returns per-recipe rows and the science packs per second the base can make."""
    built: dict[str, list[str]] = {}
    for e in dump.get("entities", []):
        if e.get("f") == dump.get("force") and e.get("r") and e.get("t") in ("assembling-machine", "furnace"):
            built.setdefault(e["r"], []).append(e["n"])
    capacity: dict[str, float] = {}
    for recipe_name, machines in built.items():
        recipe = book.recipes.get(recipe_name)
        if not recipe:
            continue
        for item, amount in recipe.products:
            capacity[item] = capacity.get(item, 0.0) + sum(
                (book.machines[m].speed if m in book.machines else 1.0) / recipe.energy * amount for m in machines)
    packs = {p: capacity[p] for p in SCIENCE if p in capacity}
    # Labs use every pack of a research together, so the slowest pack sets the pace.
    pace = min(packs.values()) if packs else 0.0
    needed = solve({p: pace for p in packs}, book).steps if pace else []
    need_by_recipe = {s.recipe: s.rate for s in needed}
    rows = []
    for recipe_name, machines in sorted(built.items()):
        recipe = book.recipes.get(recipe_name)
        if not recipe:
            continue
        main = recipe.products[0][0]
        rows.append(AuditRow(recipe_name, len(machines), capacity.get(main, 0.0), need_by_recipe.get(recipe_name, 0.0)))
    return rows, packs
