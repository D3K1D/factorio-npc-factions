"""Plates kept for building while the base grows.

A player growing a base builds smelting well past what its machines take, so there is always iron to grab for the
next belts, inserters and machines: over-producing iron, copper and the rest for a while, as long as growing is the
goal (the user's direction, 2026-10-25). Run 32 (2026-10-25) built its whole bus on nine iron furnaces sized for
science alone, 120 to 170 iron a minute, about what 20 science a minute takes: every belt and machine of the next
stage waited on plates. Here building is a consumer like any machine: its draw, the allowance, is what the stages
still to build take, spread over a while of building, and at least a floor and a share of the machines' draw. The
bus planner builds the rest of a smelting block next when what stands cannot cover both (bus_planner.plan_stage).
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping

FLOOR = {"iron-plate": 1.0, "copper-plate": 0.5}   # items/s at the least while the base grows (60 and 30 a minute)
SHARE = 0.5          # and at least half again what its machines take
HORIZON_S = 1800     # what the stages still to build take, spread over half an hour of building
CAP = 5.0            # items/s at most for one plate (a third of a belt)
HEADROOM = 0.85      # a supply counts as full at this share (the ledger's AVERAGE_LIMIT)
RAW = ("iron-plate", "copper-plate", "steel-plate", "stone-brick", "plastic-bar", "stone", "wood")


def expansion(site: Mapping[str, Any]) -> float:
    """How much growing the base is the goal now, 0 to 1: all of it, less as the threat calls for defense first
    (none in a full emergency: threat.urgency)."""
    try:
        from companion import threat
        return max(0.0, 1.0 - threat.urgency(threat.estimate(site)))
    except Exception:   # (no threat known: growing is the goal)
        return 1.0


_cache: dict[str, Any] = {"book": None, "costs": {}}   # the costs worked out with the book last asked about
_lock = threading.Lock()   # (planners run on more than one thread)


def _cost(item: str, book: Any) -> dict[str, float]:
    with _lock:
        if _cache["book"] is not book:
            _cache["book"], _cache["costs"] = book, {}
        return _cost_in(_cache["costs"], item, book)


def _cost_in(costs: dict, item: str, book: Any) -> dict[str, float]:
    if item not in costs:
        from companion.ratios import solve
        try:
            need = solve({item: 1.0}, book, inputs=set(RAW), allowed={"assembling-machine-2"})
            # (an item with no recipe comes back as its own input: only what smelting and the like make counts; stone
            # and wood come back as the solver's raw resources, and counted for nothing, a furnace cost no stone)
            parts: dict[str, float] = {}
            for k, v in [*need.inputs.items(), *need.raw.items()]:
                if v > 0 and k in RAW:
                    parts[k] = parts.get(k, 0.0) + float(v)
            costs[item] = parts
        except (KeyError, ValueError):
            costs[item] = {}
    return costs[item]


def cost(names: Iterable[str], book: Any) -> dict[str, float]:
    """What building these (one each, by entity name) takes, through their recipes down to plates, bricks and the
    like. A building with no known recipe costs nothing here."""
    out: dict[str, float] = {}
    for name in names:
        for item, amount in _cost(str(name), book).items():
            out[item] = out.get(item, 0.0) + amount
    return out


def allowance(site: Mapping[str, Any], demand: Mapping[str, float], upcoming: Mapping[str, float]) -> dict[str, float]:
    """Items/s of each plate kept for building on top of what the machines take (`demand`): what the stages still to
    build take (`upcoming`, items) over HORIZON_S, at least FLOOR and SHARE of the machines' draw, at most CAP; all
    of it while growing is the goal (expansion)."""
    level = expansion(site)
    out = {}
    for item in sorted(set(FLOOR) | set(demand) | set(upcoming)):
        want = max(FLOOR.get(item, 0.0), SHARE * float(demand.get(item, 0.0)),
                   float(upcoming.get(item, 0.0)) / HORIZON_S)
        value = round(level * min(CAP, want), 4)
        if value > 0:
            out[item] = value
    return out


def short(supply: float, demand: float, kept: float) -> bool:
    """Whether a supply (items/s at full speed) cannot cover its machines and building together."""
    return supply * HEADROOM < demand + kept
