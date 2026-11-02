"""Walls made by machines (the team world; the user's call, 2026-10-24: walls are an automated system, put
up by hand only in an emergency). Two builds:

    brick smelting   burner drills on stone dropping into stone furnaces (the built-in layout planner),
                     more of them when the ledger finds bricks short;
    the wall row     an assembler row making the best wall the engineer can make, from what its recipe
                     takes (stone bricks in vanilla; the game's own recipe, mods included), fed from a
                     chest the engineer keeps filled from the furnaces, the walls into a chest.

The walls stack up in that chest until there are enough for a long stretch of the wall line
(perimeter.wall_stock), and the wall plans take them from there.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from companion import layout_planner, perimeter, ratios
from companion.blue_modules import _count, row_module
from companion.bus import END_CHEST
from companion.military_modules import _with_chests
from companion.modules import Placed, loader_limit
from companion.ratios import Book

PURPOSE = "wall-automation"
BRICKS = "brick-smelting"
WALL_RATE = 0.2   # walls a second the row is laid out for (12 a minute); the bricks set the real pace


# What a family falls back to, when a better member cannot be had yet: the row's rate is far below what even the
# plain ones take (its assembler 2 needed steel the base did not make yet, run 25, 2026-10-25).
PLAIN = {"inserter": "inserter", "transport-belt": "transport-belt", "underground-belt": "underground-belt",
         "splitter": "splitter", "assembling-machine": "assembling-machine-1"}


def _makeable(parts: list[Placed], site: Mapping[str, Any]) -> list[Placed]:
    """The row with what the engineer can make: a faster inserter or belt it has not researched becomes the
    plain one (the row's rate is far below what the machine could take: the row laid out for an
    assembler's full speed asked for fast inserters, blocking the build, one-engineer hour 2026-10-24);
    anything else it cannot make turns the plan down here rather than in the game."""
    from companion import handcraft
    unlocked = ratios.unlocked(site)
    can: dict[str, bool] = {}

    def ok(name: str) -> bool:   # researched, and made from what it can have now
        if name not in can:
            can[name] = name in unlocked and (not site.get("recipes") or handcraft.obtainable(site, name))
        return can[name]
    out, missing = [], set()
    for p in parts:
        name = p.name
        if not ok(name):
            name = next((plain for family, plain in PLAIN.items() if family in name and ok(plain)), name)
        if not ok(name):
            missing.add(name)
        out.append(Placed(name, p.x, p.y, p.direction, p.recipe, p.kind, p.bar) if name != p.name else p)
    if missing:
        raise layout_planner.PlanError("the wall row needs what cannot be made yet: " + ", ".join(sorted(missing)))
    return out


def plan_block(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """The wall row near home, powered from the engineer's network: its inputs from chests it fills by
    hand, the walls into a chest it (and its wall plans) take them from."""
    from companion.base_plan import all_ore, blocked_tiles, find_fit
    from companion.power import connect
    from companion.smelting import footprint
    kit = perimeter.arms(site)
    if not kit["walls"]:
        raise layout_planner.PlanError("no wall it can make yet")
    wall = kit["walls"][0]
    book = Book.from_site(site)
    recipe = book.recipe_for(wall)
    if recipe is None:
        raise layout_planner.PlanError(f"no recipe makes {wall}")
    if any(n not in book.recipes and book.recipe_for(n) is None for n, _ in recipe.ingredients):
        raise layout_planner.PlanError(f"{wall} takes what nothing here makes")
    inputs = tuple(n for n, _ in recipe.ingredients)
    if len(inputs) > 4:
        raise layout_planner.PlanError(f"too many inputs for one row: {', '.join(inputs)}")
    machines = site.get("machines") or {}
    machine = "assembling-machine-2" if "assembling-machine-2" in machines else "assembling-machine-1"
    if machine not in book.machines:
        raise layout_planner.PlanError(f"no {machine} to make walls in")
    n = _count(WALL_RATE, book, recipe.name, wall, machine)
    module = row_module("walls", book, [recipe.name] * n, a=inputs[:2], b=inputs[2:], product=wall,
                        rate=WALL_RATE, machine=machine)
    parts = _makeable(_with_chests(module, loader_limit(site)), site)
    taken = blocked_tiles(world) | all_ore(world)
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    shape = footprint(parts) | {(x, y) for x in range(-3, module.width + 1) for y in range(-1, module.height + 1)}
    fit = find_fit(taken, shape, home, tuple(world["area"]), anchor=home)
    if not fit:
        raise layout_planner.PlanError(f"no room near home for the wall row ({module.width + 3}x{module.height})")
    ox, oy = fit
    placed = [Placed(e.name, e.x + ox, e.y + oy, e.direction, e.recipe, e.kind, e.bar) for e in parts]
    taken |= {(x + ox, y + oy) for x, y in shape}
    live = set(world.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in world.get("entities", [])
               if e.get("t") == "electric-pole" and e.get("f") == world.get("force")
               and (not live or e.get("net") is None or e.get("net") in live)]
    if not powered:
        raise layout_planner.PlanError("no electric network to power the wall row")
    extra, problems = connect(placed, powered, taken)
    if problems:
        raise layout_planner.PlanError("; ".join(problems)[:160])
    placed += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    entities = [e.to_plan(f"walls-{k}", -float(origin["x"]), -float(origin["y"]))
                for k, e in enumerate(placed, start=1)]
    chests = list(zip([e for e in entities if e["prototype"] == END_CHEST], module.ports))
    maker = next(e["id"] for e in entities if e.get("recipe") == recipe.name)
    stage = {"id": "wall-row", "depends_on": [], "entity_ids": [e["id"] for e in entities],
             "manual_supplies": [{"entity_id": e["id"], "item": port.item, "slot": "input"}
                                 for e, port in chests if port.kind == "in"],
             "witnesses": [{"kind": "product_counter_delta", "entity_id": maker, "item": wall, "min_delta": 2}]}
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{PURPOSE}-{safe}", "source": "wall-block", "purpose": PURPOSE,
            "entities": entities, "stages": [stage]}
