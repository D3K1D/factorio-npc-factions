"""The nuclear plant's fuel: uranium mined with sulfuric acid, fuel cells made from it, and the cells carried by belt
to each reactor's fuel chest (companion/nuclear_plant.py).

- A drill field on the uranium ore (smelting.plan_field), as many drills as the fuel cell block takes ore (sized by
  nuclear.mining from the game's facts), its collector belt to the fuel cell block's ore port.
- The fuel cell block and a sulfuric acid block from the library (ready blocks with sidecar ports: the optimizer's).
  Their iron and the acid block's sulfur are handed into a chest at each port, an inserter putting them on its belt
  (a few a minute: 1 acid a uranium ore at 2.1's rate); the U-238 the cells do not use goes into a chest.
- The acid block's water from an offshore pump (or the water already piped nearest), its acid piped to every drill's
  outer fluid connection (from the game's facts).
- The cells' belt runs past each reactor's fuel chest, an inserter putting them in: the chest keeps a stock, so
  the reactors never run out between cells.
Every piece's size and connections come from the game's facts and the blocks' own sidecars.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import layout_planner, nuclear, nuclear_cell, rates, team_area
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

ORE, ACID, CELL, U238 = "uranium-ore", "sulfuric-acid", nuclear.FUEL_CELL, nuclear.U238
STEP = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
FACING = {(0, -1): NORTH, (1, 0): EAST, (0, 1): SOUTH, (-1, 0): WEST}
DIRECTION_STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
SUPPLY = {"small-electric-pole": 2.5, "medium-electric-pole": 3.5, "substation": 9.0, "big-electric-pole": 2.0}
CHEST, INSERTER = "iron-chest", "inserter"
DRILL_WIDTH = 3        # the field layout's drills (smelting.plan_field)
BLOCK_MARGIN = 3       # free tiles round a ready block: its port chests and inserters, a walk round it
BELT_LIMIT = 4000


def _makes(build: Mapping[str, Any]) -> dict[str, float]:
    return {p["item"]: float(p.get("rate") or 0) for p in (build.get("import") or {}).get("ports") or []
            if isinstance(p, Mapping) and p.get("way") == "out"}


# The ports a block needs here (item: kind), in and out; any other input must come by belt (handed into a chest).
NEEDS = {CELL: ({ORE: "belt"}, {CELL: "belt"}), ACID: ({"water": "fluid"}, {ACID: "fluid"})}


def _fits(build: Mapping[str, Any], item: str) -> bool:
    ports = [p for p in (build.get("import") or {}).get("ports") or [] if isinstance(p, Mapping)]
    ins = {p.get("item"): p.get("kind") for p in ports if p.get("way") == "in"}
    outs = {p.get("item"): p.get("kind") for p in ports if p.get("way") == "out"}
    need_in, need_out = NEEDS[item]
    return (all(ins.get(i) == k for i, k in need_in.items()) and all(outs.get(i) == k for i, k in need_out.items())
            and all(k == "belt" for i, k in ins.items() if i not in need_in)
            and all(k == "belt" or i in need_out for i, k in outs.items())
            and all(p.get("outside") and p.get("edge") in STEP for p in ports))


def ready(builds: list[Mapping[str, Any]], item: str, site: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
    """The library block with sidecar ports that makes `item` with the ports the chain needs (NEEDS); with `site`, its
    recipes unlocked and every piece to be had (oil_plant's rule). The fewest buildings first."""
    from companion.oil_plant import _at_hand
    unlocked = {r.get("name") for r in (site or {}).get("recipes") or [] if isinstance(r, Mapping)}
    have, protos = (_at_hand(site), site.get("prototypes") or {}) if site is not None else (set(), {})

    def buildable(b):
        return site is None or (all(r in unlocked for r in b.get("recipes_needed") or [])
                                and all((protos.get(e["prototype"]) or {}).get("placement_item", e["prototype"]) in have
                                        for e in b.get("entities") or []))
    found = [b for b in builds if (b.get("import") or {}).get("ports_from") == "sidecar" and _makes(b).get(item, 0) > 0
             and _fits(b, item) and buildable(b)]
    return min(found, key=lambda b: (len(b.get("entities") or []), str(b.get("id"))), default=None)


def _size(facts: rates.Facts | None, name: str) -> tuple[int, int]:
    e = (facts.entities.get(name) if facts else None) or {}
    if e.get("width"):
        return int(e["width"]), int(e.get("height") or e["width"])
    from companion.smelting import footprint
    side = int(math.isqrt(len(footprint([Placed(name, 0.5, 0.5)]))) or 1)
    return side, side


def tiles(facts: rates.Facts | None, e: Placed) -> set[tuple[int, int]]:
    w, h = _size(facts, e.name)
    return nuclear_cell.tiles_of((e.x, e.y), w, h, e.direction)


def port(build: Mapping[str, Any], p: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """A port's own tile (the block's belt or pipe at its edge), the tile beyond it where the outside joins, and the
    step out. A pipe-to-ground joins on the side it faces, whatever edge the sidecar names (the acid block's water)."""
    own = (int(p["outside"][0]), int(p["outside"][1]))
    step = STEP[str(p.get("edge"))]
    if p.get("kind") == "fluid":
        e = next((e for e in build.get("entities") or [] if e["prototype"] == "pipe-to-ground"
                  and math.floor(e["position"]["x"]) == own[0] and math.floor(e["position"]["y"]) == own[1]), None)
        if e is not None:
            step = DIRECTION_STEP[int(e.get("direction") or 0) % 16]
    return own, (own[0] + step[0], own[1] + step[1]), step


def _box(build: Mapping[str, Any]) -> set[tuple[int, int]]:
    w, h = (build.get("size") or [0, 0])[:2]
    return {(x, y) for x in range(-BLOCK_MARGIN, w + BLOCK_MARGIN) for y in range(-BLOCK_MARGIN, h + BLOCK_MARGIN)}


def feeds(world: Mapping[str, Any], facts: rates.Facts | None) -> list[dict]:
    """Where the cells' belt goes by each of the engineer's reactors: its fuel chest (nuclear_plant.fuel_side, the row
    above the middle on its outer side), the inserter tile beyond it and the belt tile beyond that."""
    from companion.nuclear_plant import CHEST as FUEL_CHEST
    mine = world.get("force")
    ents = world.get("entities", [])

    def reactor(name):
        return ((facts.entities.get(name) or {}).get("kind") == "reactor") if facts else name == "nuclear-reactor"
    reactors = [e for e in ents if e.get("f") == mine and reactor(e.get("n"))]
    chests = {(math.floor(e["x"]), math.floor(e["y"])) for e in ents if e.get("n") == FUEL_CHEST and e.get("f") == mine}
    out = []
    for r in reactors:
        w, _ = _size(facts, r["n"])
        for side in (-1, 1):
            chest = (math.floor(r["x"] + side * (w / 2 + 1.5)), math.floor(r["y"] - 1))
            if chest in chests:
                out.append({"chest": chest, "inserter": (chest[0] + side, chest[1]),
                            "belt": (chest[0] + 2 * side, chest[1]), "side": side})
    return sorted(out, key=lambda f: f["chest"])


MAX_TRACE = 200   # belts followed down from a machine's output inserter, at most


def made_lines(world: Mapping[str, Any], site: Mapping[str, Any], item: str) -> list:
    """The standing belts that carry `item`: the lines the game recorded with it (not the main bus: its lanes are the
    bus planner's to tap), and the belts the engineer's own machines making it put it on (an inserter from the
    machine onto a belt, followed down to its end): the smelting's plates, the oil plant's sulfur."""
    from companion import belt_lines
    out = [t for t in belt_lines.lines(world, item) if t.kind != "bus"]
    makes = {r.get("name") for r in site.get("recipes") or [] if isinstance(r, Mapping)
             and any(isinstance(p, Mapping) and p.get("name") == item for p in r.get("products") or [])} | {item}
    mine = world.get("force")
    ents = [e for e in world.get("entities", []) if e.get("f") == mine]
    belts = {(math.floor(e["x"]), math.floor(e["y"])): int(e.get("d") or 0) for e in ents if e.get("t") == "transport-belt"}
    machines = {}
    for e in ents:
        if e.get("r") in makes:
            w, h = int(e.get("w") or 1), int(e.get("h") or 1)
            for t in nuclear_cell.tiles_of((e["x"], e["y"]), w, h, int(e.get("d") or 0)):
                machines[t] = e
    heads = set()
    for e in ents:
        if e.get("t") != "inserter" or "long" in str(e.get("n")):
            continue
        at, (dx, dy) = (math.floor(e["x"]), math.floor(e["y"])), DIRECTION_STEP.get(int(e.get("d") or 0) % 16, (0, 0))
        pick, drop = (at[0] + dx, at[1] + dy), (at[0] - dx, at[1] - dy)   # (an inserter faces where it picks up)
        if pick in machines and drop in belts:
            heads.add(drop)
    for head in sorted(heads):
        run, t = [], head
        while t in belts and len(run) < MAX_TRACE and t not in {(x, y) for x, y, _ in run}:
            run.append((t[0], t[1], belts[t]))
            sx, sy = DIRECTION_STEP.get(belts[t] % 16, (0, 0))
            t = (t[0] + sx, t[1] + sy)
        if len(run) >= 3:
            out.append(belt_lines.Line(f"made-{item}@{head[0]},{head[1]}", item, run, belt_lines.LANE))
    return out


def plan(site: Mapping[str, Any], world: Mapping[str, Any], builds: list[Mapping[str, Any]],
         book: Mapping[str, dict] | None = None) -> dict:
    """The fuel chain as a plan for /npc-plan submit. Raises PlanError when it cannot be had. Its belts hop under
    what is in the way only once underground belts are unlocked; its iron and sulfur are tapped by splitter."""
    from companion import belt_lines
    from companion.smelting import undergrounds
    unlocked = {r.get("name") for r in site.get("recipes") or [] if isinstance(r, Mapping)}
    with undergrounds("underground-belt" in unlocked), belt_lines.taps("splitter" in unlocked):
        return _plan(site, world, builds, book)


def _plan(site: Mapping[str, Any], world: Mapping[str, Any], builds: list[Mapping[str, Any]],
          book: Mapping[str, dict] | None = None) -> dict:
    from companion.base_plan import blocked_tiles, find_fit
    from companion.oil_plant import cliff_tiles
    from companion.power import connect, live_poles
    from companion.rocket_plan import _pump, _tiles as world_tiles, pipe_map
    from companion.smelting import foreign_tiles, ore_tiles, plan_field, route
    from companion import land
    facts = rates.of(site)
    cells, acid = ready(builds, CELL, site), ready(builds, ACID, site)
    if cells is None or acid is None:
        raise layout_planner.PlanError("no ready fuel cell block and sulfuric acid block in the library")
    from companion import nuclear_plant
    lines = nuclear_plant.feeds(site, world, book)   # (the book's plant: its fuel belts' feeds, in turn)
    to = [] if lines else feeds(world, facts)  # (else the generated plant's fuel chests)
    if not lines and not to:
        raise layout_planner.PlanError("no nuclear plant with a fuel belt or fuel chests to bring the cells to")
    near_to = lines[0]["feed"] if lines else to[0]["belt"]
    ore_rate = next((float(p.get("rate") or 0) for p in cells["import"]["ports"]
                     if p.get("way") == "in" and p.get("item") == ORE), 0.0)
    mined = nuclear.mining(site, ore_rate, ORE, width=DRILL_WIDTH)
    drill_facts = (facts.entities.get(mined["drill"]) or {}) if mined and facts else {}
    drill_fluid = [c for b in drill_facts.get("fluid") or [] for c in b.get("c") or []]
    if not mined or mined.get("fluid") != ACID or not drill_fluid:
        raise layout_planner.PlanError("the game's facts do not say how uranium ore is mined with acid")
    ore = ore_tiles(world.get("resources", {}), ORE)
    if not ore:
        raise layout_planner.PlanError("no uranium ore known on the map")
    cliffs = cliff_tiles(world)
    water = {(x, y) for y, a, b in world.get("water", []) for x in range(a, b + 1)}
    taken = set(blocked_tiles(world)) | team_area.forbidden(world) | cliffs | water
    for e in world.get("entities", []):   # (every building by its size in the game's facts: a turbine is 3x5)
        if e.get("t") not in ("tree", "simple-entity", "cliff") and facts and e.get("n") in facts.entities:
            taken |= tiles(facts, Placed(e["n"], e["x"], e["y"], int(e.get("d") or 0)))
    bare = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles")}
    kept = land.reserved_tiles(world) - set(blocked_tiles(bare))   # (kept ground with nothing on it)
    area = tuple(world["area"])
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    for f in to:
        taken |= {f["inserter"], f["belt"]}   # (kept for the cells' belt and its inserters)
    for f in lines:
        taken |= {f["feed"], f["on"]}         # (kept for the cells' belt)
    # The drills: on their ore even in ground kept for another section (an ore cannot move).
    other = foreign_tiles(world.get("resources", {}), ORE)
    room = taken - (kept & ore)
    # (what is in the way widened a tile east and west: the drills' backs, where their acid comes in, stay free)
    room |= {(x + dx, y) for x, y in room for dx in (-1, 1)}
    field = (plan_field(ore, room, mined["drills"], ORE, near=near_to, foreign=other)
             or plan_field(ore, room, mined["drills"], ORE, min_ore=6, near=near_to, foreign=other))
    if field is None:
        raise layout_planner.PlanError(f"no room for {mined['drills']} drills on the uranium ore")
    out: list[Placed] = [Placed(mined["drill"] if e.name == "electric-mining-drill" else e.name, e.x, e.y, e.direction)
                         for e in field.entities()]
    used = set(taken)
    for e in out:
        used |= tiles(facts, e)
    used.add(field.exit)
    # Each drill's acid inlet: its fluid connection reaching a free tile outside the field, kept for its pipe (a
    # belt laid along the drills took them all).
    body = set()
    for e in out:
        if e.name == mined["drill"]:
            body |= tiles(facts, e)
    inlets, touching = [], set()
    for cx, cy, d in field.drills:
        reach = [nuclear_cell.connection((cx, cy), d, c)[1] for c in drill_fluid]
        touching |= {t for t in reach if t not in body}
        outer = [t for t in reach if t not in body and t not in used]
        if not outer:
            raise layout_planner.PlanError("a uranium drill with no free side for its acid")
        inlets.append(outer[0])
    used |= set(inlets)
    wanted: list[tuple[str, tuple[int, int], tuple[int, int], float]] = []   # (item, outside tile, step out, rate)

    def place(build: Mapping[str, Any], anchor: tuple[float, float]) -> tuple[int, int]:
        fit = find_fit(used, _box(build), home, area, anchor=anchor)
        if fit is None:
            raise layout_planner.PlanError(f"no room for the {'/'.join(_makes(build))} block near the uranium")
        ox, oy = fit
        for e in build["entities"]:
            p = Placed(e["prototype"], e["position"]["x"] + ox, e["position"]["y"] + oy, int(e.get("direction") or 0),
                       e.get("recipe"), e.get("belt_to_ground_type"), mirror=bool(e.get("mirror")))
            out.append(p)
            used.update(tiles(facts, p))
        return ox, oy

    def ports_of(build, at):
        return {p["item"]: (p, *[(t[0] + at[0], t[1] + at[1]) for t in port(build, p)[:2]], port(build, p)[2])
                for p in build["import"]["ports"]}

    def by_belt(beyond, step, item, rate):
        """The port's item by belt from the base, tapped once the blocks stand (its outside tile kept till then: a
        chest filled by hand is no automation, the user's rule)."""
        wanted.append((item, beyond, step, rate))
        used.add(beyond)

    def into_chest(beyond, step):
        """An inserter taking what the port's belt brings out, a chest beyond it."""
        chest = (beyond[0] + step[0], beyond[1] + step[1])
        out.append(Placed(INSERTER, beyond[0] + 0.5, beyond[1] + 0.5, FACING[(-step[0], -step[1])]))
        out.append(Placed(CHEST, chest[0] + 0.5, chest[1] + 0.5))
        used.update({beyond, chest})

    # The fuel cell block below the field's exit, its ore belt from the field.
    at = place(cells, field.exit)
    cell_ports = ports_of(cells, at)
    for item, (p, own, beyond, step) in cell_ports.items():
        if p.get("way") == "in" and item != ORE:
            by_belt(beyond, step, item, float(p.get("rate") or 0))
        elif p.get("way") == "out" and item != CELL:
            into_chest(beyond, step)
    _, ore_own, ore_beyond, ore_step = cell_ports[ORE]
    path = route(field.exit, ore_beyond, used - {field.exit, ore_beyond}, bounds=area, limit=BELT_LIMIT)
    if path is None:
        raise layout_planner.PlanError("no belt from the uranium drills to the fuel cell block")
    path[-1] = Placed(path[-1].name, path[-1].x, path[-1].y, FACING[(-ore_step[0], -ore_step[1])])
    out += path
    used |= {(math.floor(b.x), math.floor(b.y)) for b in path}
    # The cells' belt into each of the book plant's fuel belts in turn (sideloading its head; from the tile past its
    # spent-cell sink on to the next), or past every fuel chest of the generated plant, an inserter putting them in.
    _, _, start, cell_step = cell_ports[CELL]
    for k, f in enumerate(lines):
        leg = route(start, f["feed"], used - {start, f["feed"]}, bounds=area, limit=BELT_LIMIT)
        if leg is None:
            raise layout_planner.PlanError("no belt from the fuel cell block to the nuclear plant's fuel belt")
        leg[-1] = Placed(leg[-1].name, leg[-1].x, leg[-1].y, f["face"])
        out += leg
        used |= {(math.floor(b.x), math.floor(b.y)) for b in leg}
        start = f["on"]
    belts: list[Placed] = []
    for k, f in enumerate(to):
        free = used - {start, f["belt"]}
        leg = route(start, f["belt"], free, bounds=area, limit=BELT_LIMIT)
        if leg is None:
            raise layout_planner.PlanError("no belt from the fuel cell block to a reactor's fuel chest")
        if belts:   # (the belt so far turned onto this leg; the leg's first tile is the last one's end)
            nxt = (math.floor(leg[1].x), math.floor(leg[1].y)) if len(leg) > 1 else f["belt"]
            last = belts[-1]
            d = (nxt[0] - math.floor(last.x), nxt[1] - math.floor(last.y))
            belts[-1] = Placed(last.name, last.x, last.y, FACING[(int(math.copysign(1, d[0])) if d[0] else 0,
                                                                    int(math.copysign(1, d[1])) if d[1] else 0)])
            leg = leg[1:]
        belts += leg
        used |= {(math.floor(b.x), math.floor(b.y)) for b in leg}
        start = f["belt"]
        side = f["side"]
        out.append(Placed(INSERTER, f["inserter"][0] + 0.5, f["inserter"][1] + 0.5, EAST if side > 0 else WEST))
    out += belts
    # The acid block near the field, its sulfur and iron by hand, its water from the shore, its acid to the drills.
    xs = [cx for cx, _, _ in field.drills]
    ys = [cy for _, cy, _ in field.drills]
    at = place(acid, ((min(xs) + max(xs)) / 2, min(ys) - 8))
    acid_ports = ports_of(acid, at)
    for item, (p, own, beyond, step) in acid_ports.items():
        if p.get("way") == "in" and p.get("kind") != "fluid":
            by_belt(beyond, step, item, float(p.get("rate") or 0))
    fluids = {own: item for item, (p, own, _, _) in acid_ports.items() if p.get("kind") == "fluid"}
    pipes = pipe_map(world, out, fluids)
    # (off what stands, and off ground kept for others where nothing stands; the pumps' shore may be kept ground)
    pipes.blocked |= (taken - kept) | cliffs
    for e in out:
        if e.name not in ("pipe", "pipe-to-ground"):
            pipes.blocked |= tiles(facts, e)
    pipes.blocked |= {t for t in used if t not in taken} - set(pipes.pipes) - set(pipes.ends)
    pipes.blocked -= {beyond for item, (p, _, beyond, _) in acid_ports.items() if p.get("kind") == "fluid"}
    for item, (p, own, beyond, _) in acid_ports.items():
        if p.get("kind") == "fluid":
            pipes.reserved[beyond] = item
    for t in touching:   # (every drill's fluid connections outside the field: acid's only)
        pipes.reserved[t] = ACID
    for _, beyond, step, _ in wanted:   # (the belt-fed ports' ways in: their branches come later)
        pipes.blocked |= {beyond, (beyond[0] + step[0], beyond[1] + step[1])}
    pipes.blocked -= set(inlets)
    _, _, acid_out, _ = acid_ports[ACID]
    network: set[tuple[int, int]] = set()
    for t in sorted(inlets, key=lambda t: math.dist(t, acid_out)):
        if t in network:
            continue
        path = pipes.route(ACID, acid_out, t, joins=network) if network else pipes.route(ACID, acid_out, t)
        if path is None:
            raise layout_planner.PlanError("no acid pipe to every uranium drill")
        out += path
        network |= {acid_out, t} | {(math.floor(q.x), math.floor(q.y)) for q in path if q.name == "pipe"}
    used |= set(pipes.pipes) | set(pipes.ends)
    _, _, water_in, _ = acid_ports["water"]
    path, tried = None, set()
    from companion.nuclear_plant import PUMP_TRIES
    for _ in range(PUMP_TRIES):   # (the nearest shore spots, one after another, until one has a pipe here)
        pump = _pump(world, (used - kept) | tried, water_in)   # (a shore cannot move: kept ground is no bar)
        if pump is None:
            break
        entity, outlet = pump
        spot = world_tiles(entity.name, entity.x, entity.y, entity.direction)
        tried |= spot | {outlet}
        pipes.blocked |= spot
        path = pipes.route("water", water_in, outlet)
        if path is not None:
            pipes.reserved[outlet] = "water"
            out += [entity] + path
            break
        pipes.blocked -= spot - taken
    if path is None:   # (no shore of its own within reach: the water already piped nearest)
        theirs = {t for t, f in pipes.pipes.items() if f == "water"}
        near = min(theirs, key=lambda t: math.dist(t, water_in), default=None)
        path = pipes.route("water", water_in, near) if near else None
        if path is None:
            raise layout_planner.PlanError("no shore or water pipe for the acid block's water")
        out += path
    used |= set(pipes.pipes) | set(pipes.ends) | {(math.floor(e.x), math.floor(e.y)) for e in out}
    # Their iron and sulfur by belt: a splitter on a standing belt of each (one carrying it from what makes it: the
    # smelting's plates, the oil plant's sulfur), its branch into the port. After the pipes: a belt hops under a pipe,
    # and a branch laid first walled the acid block's water inlet in.
    from companion import belt_lines
    from companion.smelting import footprint
    # (each port's way in kept clear while the others' branches are laid: the acid block's iron branch, laid first
    # along its edge, walled its sulfur port in)
    ways = {k: {beyond, (beyond[0] + step[0], beyond[1] + step[1])} for k, (_, beyond, step, _) in enumerate(wanted)}
    for k, (item, beyond, step, rate) in enumerate(wanted):   # (each port's own rate: the acid block's iron is 50x)
        others = set().union(*(w for j, w in ways.items() if j != k))
        blocked = (used | others) - {beyond}
        last, key = FACING[(-step[0], -step[1])], f"nuclear-fuel-{item}-{k}"
        # (the lines the game recorded first, tapped with a claim on them so later plans see what is left; then the
        # belts traced from the engineer's own machines, which have no record to claim on)
        branch = belt_lines.tap(world, item, beyond, rate, blocked, last=last, key=key)
        traced = [t for t in made_lines(world, site, item) if t.id.startswith("made-")]
        if branch is None and traced:
            branch = belt_lines.tap(world, item, beyond, rate, blocked, last=last, key=key, among=traced)
        if branch is None:
            raise layout_planner.PlanError(f"no standing belt of {item} to tap for the nuclear fuel")
        out += branch
        used |= footprint(branch)
    # Power: the blocks and the field have poles of their own; the inserters added round them get a pole if none
    # reaches them; then every group joined to the grid a generator feeds.
    poles = [(e.x, e.y, SUPPLY.get(e.name, 2.5)) for e in out if e.name in SUPPLY]
    live = set(live_poles(world))   # (a pole on a network no generator feeds powers nothing)
    poles += [(e["x"], e["y"], SUPPLY.get(e["n"], 2.5)) for e in world.get("entities", [])
              if e.get("n") in SUPPLY and (e["x"], e["y"]) in live]
    from companion.nuclear_plant import _poles
    taken_now = set(used)
    for e in [e for e in out if e.name == INSERTER]:
        t = (math.floor(e.x), math.floor(e.y))
        if any(abs(t[0] + 0.5 - x) < s + 0.5 and abs(t[1] + 0.5 - y) < s + 0.5 for x, y, s in poles):
            continue
        box = (t[0] - 2, t[1] - 2, t[0] + 3, t[1] + 3)
        got = _poles([({t}, e)], taken_now, box)
        out += got
        taken_now |= {(math.floor(p.x), math.floor(p.y)) for p in got}
        poles += [(p.x, p.y, SUPPLY[p.name]) for p in got]
    hard = (taken_now - kept) | cliffs
    extra, problems = connect(out, live_poles(world), hard | kept)
    if problems:
        extra, problems = connect(out, live_poles(world), hard)
    if problems:
        raise layout_planner.PlanError(f"nuclear fuel: {problems[0]}")
    out += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    dx, dy = -float(origin["x"]), -float(origin["y"])
    entities = [e.to_plan(f"fuel-{n}", dx, dy) for n, e in enumerate(out, start=1)]
    first_drill = next(entities[i]["id"] for i, e in enumerate(out) if e.name == mined["drill"])
    centrifuge = next((entities[i]["id"] for i, e in enumerate(out) if e.recipe == nuclear.PROCESSING), None)
    witnesses = [{"kind": "entity_built", "entity_id": first_drill}]
    if centrifuge:
        witnesses.append({"kind": "product_counter_delta", "entity_id": centrifuge, "item": U238, "min_delta": 1})
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"nuclear-fuel-{safe}", "source": "uranium-plant", "purpose": "nuclear-fuel",
            "entities": entities, "drills": mined["drills"], "reactor_feeds": len(lines) or len(to),
            "taps": [item for item, _, _, _ in wanted],
            "notes": [f"{mined['drills']} drills for {ore_rate:.2f} uranium ore/s on {mined['fluid_per_second']:g} "
                      f"acid/s; " + (f"cells into {len(lines)} fuel belt(s)" if lines else
                                     f"cells to {len(to)} fuel chest(s)")
                      + "; " + ", ".join(f"{item} tapped by belt" for item, _, _, _ in wanted)],
            "stages": [{"id": "nuclear-fuel", "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": [], "witnesses": witnesses,
                        # (its taps' claims on the lines the game recorded: later plans see what is left)
                        **({"line_ops": line_ops} if (line_ops := belt_lines.ops(out)) else {})}]}
