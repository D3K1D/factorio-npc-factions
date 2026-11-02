"""Upgrading in place: a bottleneck module gets better machines on the same spots.

When one of the bus's modules is flat out (nearly all its machines working) while a module that
consumes its product is starved, the engineer upgrades the busy module where it stands: the next
assembler tier, fast inserters, steel furnaces, whatever its research allows, rather than
building a second module beside it. The game replaces a building standing where a plan wants a
different one, and points the module's records at the new machines when the upgrade is done.

Smelting blocks are upgraded the same way, as the guides build them (stone, then steel furnaces in
the same 2x2 spots): a busy block whose plates the bus's modules are starved of gets steel
furnaces, twice as fast, and the rest of its mining field (the drills planned for its later
furnaces), so the faster furnaces have the ore to run on.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from companion import bus_planner, layout_planner, ledger
from companion import ratios
from companion.modules import Placed

PURPOSE = "upgrade"
TIERS = {"assembling-machine-1": "assembling-machine-2", "assembling-machine-2": "assembling-machine-3",
         "inserter": "fast-inserter", "stone-furnace": "steel-furnace"}
BUSY = 0.75      # share of a module's machines working right now that makes it flat out
STARVED = 0.5    # share of a consumer's machines short of inputs right now that makes it starved
# Measured over the last 30 minutes (the factory ledger), the bar is the ledger's: 90% of the time
# working, 30% of it short of inputs. A snapshot is judged by the looser bars above.


def _busy(block: Mapping[str, Any]) -> bool:
    share, measured = ledger.busy_share(block, "working")
    return share >= (ledger.BUSY if measured else BUSY)


def _starved(block: Mapping[str, Any]) -> float | None:
    """How starved a consumer is, if starved at all."""
    share, measured = ledger.busy_share(block, "starved")
    return share if share >= (ledger.STARVED if measured else STARVED) else None


def upgradable(site: Mapping[str, Any]) -> bool:
    """Cheap check for the goal list: a flat-out bus block with a better tier unlocked for it."""
    recipes = ratios.unlocked(site)
    for b in site.get("base") or []:
        stage = str(b.get("stage", "")) if isinstance(b, Mapping) else ""
        if stage.startswith(("bus-", "smelting-")) and _busy(b):
            if any(TIERS.get(m) in recipes for m in (b.get("machines") or {})):
                return True
    return False


def bottlenecks(site: Mapping[str, Any], layout) -> list[tuple[str, str, str]]:
    """(busy module's stage, item, starved consumer's stage), worst first."""
    blocks = {str(b.get("stage")): b for b in site.get("base") or [] if isinstance(b, Mapping) and b.get("stage")}
    makes, needs = {}, {}
    for placed in layout.bus.attached:
        stage = f"bus-{placed.module.name}"
        makes[stage] = {p.item for p in placed.module.ports if p.kind == "out"}
        needs[stage] = {p.item for p in placed.module.ports if p.kind == "in"}
    for s in layout.stages:   # smelting blocks make their plates
        plate = _smelted(s.name)
        if plate:
            makes[s.name] = {plate}
    found = []
    for producer, items in makes.items():
        busy = blocks.get(producer)
        if not busy or not _busy(busy):
            continue
        for consumer, inputs in needs.items():
            hungry = blocks.get(consumer)
            starved = _starved(hungry) if hungry else None
            for item in items & inputs:
                if starved is not None:
                    found.append((starved, producer, item, consumer))
    return [(p, i, c) for _, p, i, c in sorted(found, reverse=True)]


def _smelted(stage: str) -> str | None:
    """The plate a smelting block's stage makes ("smelting-iron-plate", "smelting-iron-plate-more")."""
    m = re.fullmatch(r"smelting-(.+?)(-more)?", stage)
    return m.group(1) if m else None


def plan_upgrade(site: Mapping[str, Any], world: Mapping[str, Any], book=None, library=None) -> dict:
    layout = bus_planner.replay(site, world, book, library)
    if layout is None:
        raise layout_planner.PlanError("no bus to upgrade")
    found = bottlenecks(site, layout)
    if not found:
        raise layout_planner.PlanError("no bottleneck to upgrade")
    producer, item, consumer = found[0]
    stage = next((s for s in layout.stages if s.name == producer), None)
    recipes = ratios.unlocked(site)
    better = [Placed(TIERS[e.name], e.x, e.y, e.direction, e.recipe, e.kind) for e in stage.entities
              if TIERS.get(e.name) in recipes]
    more = next((s for s in layout.stages if s.name == f"{producer}-more"), None) if _smelted(producer) else None
    if better and more:   # faster furnaces need the ore the block's later drills would mine
        better += [Placed(e.name, e.x, e.y, e.direction, e.recipe, e.kind) for e in more.entities
                   if e.name == "electric-mining-drill"]
    if not better:
        raise layout_planner.PlanError(f"nothing in {producer} has a better tier unlocked")
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", producer)
    entities = [e.to_plan(f"upgrade-{n}", -ox, -oy) for n, e in enumerate(better, start=1)]
    machine = next((e for e in entities if e.get("recipe")), entities[0])
    witnesses = [{"kind": "entity_built", "entity_id": machine["id"]}]
    if machine.get("recipe"):
        witnesses.append({"kind": "product_counter_delta", "entity_id": machine["id"], "item": machine["recipe"],
                          "min_delta": 1})
    return {"plan_id": f"{PURPOSE}-{safe}-{re.sub(r'[^A-Za-z0-9_-]', '-', str(site.get('request_id', 'x')))}",
            "source": "upgrade-planner", "purpose": PURPOSE, "entities": entities, "upgrades": producer,
            "stages": [{"id": f"upgrade-{safe}", "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": [], "witnesses": witnesses}]}
