"""A power plant when the engineer reports a power shortage (its corrector saw many buildings on
low power): sized from what the base draws at full work against what its engines make, laid
like the bus's own plant, by the nearest shore, with coal by belt and hand fuel as a backup."""

from __future__ import annotations

import re
from typing import Any, Mapping

from companion import belt_lines, layout_planner, power_plant
from companion import ratios
from companion.base_plan import _coal_line, all_ore, blocked_tiles
from companion.power import connect, live_poles
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST
from companion.smelting import footprint
import math

PURPOSE = "power-plant"
# Behind a boiler (away from its steam output): where its fuel inserter and the coal belt go, as in
# the plant template (inserter 1.5 tiles behind the boiler's centre, belt 2.5).
BEHIND = {NORTH: (0, 1), EAST: (-1, 0), SOUTH: (0, -1), WEST: (1, 0)}
ALONG = {NORTH: EAST, SOUTH: EAST, EAST: SOUTH, WEST: SOUTH}   # how a row of such boilers runs


def plan_power(site: Mapping[str, Any], world: Mapping[str, Any], purpose: str = PURPOSE) -> dict:
    """`purpose` "steam-power": the engineer's first power, one boiler at the nearest shore (which may be
    far outside its site), with a pole line home so its first electric buildings can join it."""
    first = purpose == "steam-power"
    # More power doubles what stands at the least (power_plant.GROWTH), with coal of its own: a row whose coal belt
    # was laid for its boilers is not stretched, the new boilers get a row and a coal line sized for them.
    boilers = 1 if first else power_plant.boilers_to_add(world, ())
    blocked = blocked_tiles(world)
    ore = all_ore(world)
    home = (float(world["home"]["x"]), float(world["home"]["y"]))
    unlocked = ratios.unlocked(site)
    # A coal belt only with drills to fill it (electric drills); the first small plant is fed by hand.
    feed = not first and (not unlocked or "electric-mining-drill" in unlocked)
    rows = [r for r in site.get("power_rows") or [] if isinstance(r, Mapping)]
    found = power_plant.plan_row(world, boilers, blocked | ore, near=home, rows=rows, belt=feed, extend_fed=first)
    if found is None:
        raise layout_planner.PlanError(f"no shore with room for a {boilers}-boiler power plant")
    entities, coal_in, coal_dir, row, had_belt = found
    used = {t for e in entities for t in power_plant.tiles(e)}
    coal = []
    if row.get("belt") and not had_belt:
        coal = _coal_line(world, blocked, used, coal_in, rate=row["boilers"] * power_plant.COAL_PER_BOILER,
                          last=coal_dir) or []
        if not coal:   # no coal route: no belt that nothing fills; the boilers are fed by hand
            entities = [e for e in entities if e.name not in ("transport-belt", "burner-inserter")]
            row = {**row, "belt": False}
    placed = entities + coal
    powered = live_poles(world)   # (only poles a generator feeds)
    if not powered:
        # No network yet: a pole line from the plant to home, where the first electric buildings go.
        hx, hy = math.floor(float(world["home"]["x"])), math.floor(float(world["home"]["y"]))
        taken = blocked | used | footprint(coal)
        spot = min(((hx + dx, hy + dy) for dx in range(-8, 9) for dy in range(-8, 9)
                    if (hx + dx, hy + dy) not in taken and max(abs(dx), abs(dy)) >= 3),
                   key=lambda t: abs(t[0] - hx) + abs(t[1] - hy), default=None)
        if spot:
            placed.append(Placed("small-electric-pole", spot[0] + 0.5, spot[1] + 0.5))
    extra, problems = connect(placed, powered, blocked | used | footprint(coal))
    if problems:
        raise layout_planner.PlanError(f"power plant: {problems[0]}")
    placed += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    out = [e.to_plan(f"power-{n}", -ox, -oy) for n, e in enumerate(placed, start=1)]
    boiler_ids = [e["id"] for e in out if e["prototype"] == "boiler"]
    engine = next(e["id"] for e in out if e["prototype"] == "steam-engine")
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{purpose}-{safe}", "source": "power-planner", "purpose": purpose, "entities": out,
            "power_row": row,
            "stages": [{"id": "power-plant", "depends_on": [], "entity_ids": [e["id"] for e in out],
                        "manual_supplies": [{"entity_id": b, "item": "coal", "slot": "fuel"} for b in boiler_ids],
                        "witnesses": [{"kind": "entity_built", "entity_id": engine},
                                      {"kind": "power_connected", "entity_id": engine}],
                        **({"line_ops": line_ops} if (line_ops := belt_lines.ops(placed)) else {})}]}


def _feed_row(world: Mapping[str, Any], site: Mapping[str, Any], taken: set):
    """The engineer's newest hand-fed boiler row (from its saved rows), given its coal belt and inserters
    exactly as the plant template lays them, so a later extension of the row continues the belt."""
    rows = [r for r in site.get("power_rows") or [] if isinstance(r, Mapping) and not r.get("belt")]
    for row in reversed(rows):
        found = power_plant.plan_row(world, 0, taken, near=(0, 0), rows=[row], belt=True)
        if found and found[4] is False and (found[3].get("wx"), found[3].get("wy")) == (row.get("wx"), row.get("wy")):
            entities, coal_in, coal_dir, new_row, _ = found
            return entities, coal_in, coal_dir, new_row, int(row["boilers"])
    return None


def _feed_boilers(world: Mapping[str, Any], taken: set):
    """Hand-fed boilers not from a saved row (a native starter plant): the longest line of them whose backs
    share a belt line (one row, or two rows facing away from the belt between them), the belt and a burner
    inserter behind each. Coal enters at the line's start."""
    force = world.get("force")
    mine = [e for e in world.get("entities", []) if e.get("f") == force]
    inserters = {(math.floor(e["x"]), math.floor(e["y"])) for e in mine if e.get("t") == "inserter"}
    lines: dict = {}
    for b in (e for e in mine if e.get("n") == "boiler"):
        d = int(b.get("d") or 0)
        bx, by = BEHIND.get(d, (0, 1))
        ins = (math.floor(b["x"] + 1.5 * bx), math.floor(b["y"] + 1.5 * by))
        belt = (math.floor(b["x"] + 2.5 * bx), math.floor(b["y"] + 2.5 * by))
        if ins in inserters or ins in taken or belt in taken:
            continue   # already fed, or no room behind it
        key = (ALONG.get(d, EAST), belt[1] if bx == 0 else belt[0])
        lines.setdefault(key, []).append((ins, belt, d))
    best = None
    for (along, _), found in lines.items():
        sx, sy = (1, 0) if along == EAST else (0, 1)
        found.sort(key=lambda f: f[1][0] * sx + f[1][1] * sy)
        # runs of boilers whose backs a straight belt can join (free tiles between, no long gaps)
        run = [found[0]]
        runs = [run]
        for prev, cur in zip(found, found[1:]):
            gap = [(prev[1][0] + k * sx, prev[1][1] + k * sy) for k in range(1, (cur[1][0] - prev[1][0]) * sx
                                                                             + (cur[1][1] - prev[1][1]) * sy)]
            if len(gap) <= 8 and not any(t in taken for t in gap):
                run.append(cur)
            else:
                run = [cur]
                runs.append(run)
        for r in runs:
            if best is None or len(r) > len(best[2]):
                best = (along, (sx, sy), r)
    if best is None:
        return None
    along, (sx, sy), run = best
    first, last = run[0][1], run[-1][1]
    out = []
    for k in range(0, (last[0] - first[0]) * sx + (last[1] - first[1]) * sy + 1):
        out.append(Placed("transport-belt", first[0] + k * sx + 0.5, first[1] + k * sy + 0.5, along))
    for ins, _, d in run:
        out.append(Placed("burner-inserter", ins[0] + 0.5, ins[1] + 0.5, (d + 8) % 16))
    coal_in = (first[0] - sx, first[1] - sy)
    if coal_in in taken:
        return None
    return out, coal_in, along, None, len(run)


def plan_boiler_coal(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """Once electric drills are researched, the boilers stop needing hand-fed coal, as a player does it
    first thing: electric drills on the nearest coal, a belt to the boilers' backs, burner inserters
    (no power needed, so a plant that ran dry restarts)."""
    blocked = blocked_tiles(world)
    ore = all_ore(world)
    # belts and inserters may stand on ore; only buildings, trees and rocks are in the way
    found = _feed_row(world, site, blocked) or _feed_boilers(world, blocked)
    if found is None:
        raise layout_planner.PlanError("no hand-fed boilers with room behind them for a coal belt")
    entities, coal_in, coal_dir, row, boilers = found
    used = {t for e in entities for t in power_plant.tiles(e)}
    coal = _coal_line(world, blocked, used, coal_in, rate=boilers * power_plant.COAL_PER_BOILER, last=coal_dir)
    if not coal:
        raise layout_planner.PlanError("no coal field with a belt route to the boilers")
    placed = entities + coal
    powered = live_poles(world)   # (only poles a generator feeds)
    extra, problems = connect(placed, powered, blocked | used | footprint(coal))
    if problems:
        raise layout_planner.PlanError(f"coal belt: {problems[0]}")
    placed += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    out = [e.to_plan(f"coal-{n}", -ox, -oy) for n, e in enumerate(placed, start=1)]
    first = next(e["id"] for e in out if e["prototype"] == "burner-inserter")
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    plan = {"plan_id": f"boiler-coal-{safe}", "source": "power-planner", "purpose": "boiler-coal",
            "entities": out, "boilers": boilers,
            "stages": [{"id": "boiler-coal", "depends_on": [], "entity_ids": [e["id"] for e in out],
                        "manual_supplies": [], "witnesses": [{"kind": "entity_built", "entity_id": first}],
                        **({"line_ops": line_ops} if (line_ops := belt_lines.ops(placed)) else {})}]}
    if row:
        plan["power_row"] = row
    return plan
