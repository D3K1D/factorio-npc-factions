"""A production block an engineer builds for someone else, at their base (the strategist's build_at).

The block is one of the bus modules (green circuits, gears, science...) sized for the asked rate,
placed on free ground as near the asked spot as it fits. Its input and output belts run a few tiles
out of the block and end there: the player connects them (an engineer cannot tap someone else's bus
yet), and the ends are marked on their map. The engineer pays for it; once built it is theirs.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import blue_modules, layout_planner
from companion import modules as mods
from companion.base_plan import all_ore, blocked_tiles, find_fit
from companion.modules import EAST, NORTH, SOUTH, WEST, Placed
from companion.ratios import Book
from companion.smelting import footprint

BLOCKS = {"electronic-circuit": (mods, "green_circuits"), "iron-gear-wheel": (mods, "gears"),
          "automation-science-pack": (mods, "red_science"), "logistic-science-pack": (mods, "green_science"),
          "advanced-circuit": (blue_modules, "red_circuits"), "engine-unit": (blue_modules, "engines"),
          "chemical-science-pack": (blue_modules, "blue_science")}
STEP = {EAST: (1, 0), WEST: (-1, 0), SOUTH: (0, 1), NORTH: (0, -1)}
STUB = 3   # belt tiles out of the block at each input and output, for the player to connect


def plan_module(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    order = site.get("order") or {}
    item = str(order.get("item") or "")
    if item not in BLOCKS:
        raise layout_planner.PlanError(f"no production block for {item} yet")
    rate = max(0.1, min(60.0, float(order.get("rate") or 1)))
    machines = site.get("machines") or {}
    machine = "assembling-machine-2" if "assembling-machine-2" in machines else "assembling-machine-1"
    lib, name = BLOCKS[item]
    module = getattr(lib, name)(rate, Book.from_site(site), machine=machine)
    entities = list(module.entities)
    ends = []
    for port in module.ports:
        dx, dy = STEP.get(port.direction, (0, 0))
        sign = -1 if port.kind == "in" else 1      # inputs arrive from upstream, the output leaves downstream
        for k in range(1, STUB + 1):
            entities.append(Placed("transport-belt", port.x + 0.5 + sign * dx * k, port.y + 0.5 + sign * dy * k,
                                   port.direction))
        ends.append(((port.x + sign * dx * STUB, port.y + sign * dy * STUB),
                     f"{port.item.replace('-', ' ')} {'in' if port.kind == 'in' else 'out'}"))
    at = order.get("at") or {}
    anchor = (float(at.get("x", world["home"]["x"])), float(at.get("y", world["home"]["y"])))
    taken = blocked_tiles(world) | all_ore(world)
    shape = footprint(entities)
    fit = find_fit(taken, shape, (int(anchor[0]), int(anchor[1])), tuple(world["area"]), anchor=anchor)
    if not fit:
        raise layout_planner.PlanError(f"no free ground near {anchor[0]:.0f},{anchor[1]:.0f} for the "
                                       f"{item} block ({module.width}x{module.height})")
    ox, oy = fit
    origin = site.get("origin") or {"x": 0, "y": 0}
    dx0, dy0 = ox - float(origin["x"]), oy - float(origin["y"])
    names: dict[str, int] = {}
    out = []
    for e in entities:
        names[e.name] = names.get(e.name, 0) + 1
        out.append(e.to_plan(f"block-{e.name}-{names[e.name]}", dx0, dy0))
    machine_id = next((e["id"] for e in out if e["prototype"].startswith("assembling-machine")), out[0]["id"])
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"block-{item}-{safe}", "source": "module-order", "purpose": "module",
            "item": item, "rate": rate, "at": (round(ox), round(oy)), "entities": out,
            "markers": [{"x": x + dx0 + 0.5, "y": y + dy0 + 0.5, "text": text} for (x, y), text in ends],
            "stages": [{"id": "block", "depends_on": [], "entity_ids": [e["id"] for e in out],
                        "manual_supplies": [], "witnesses": [{"kind": "entity_built", "entity_id": machine_id}]}]}
