"""The rocket's supply outside the bus: the oil complex, its pipes, and the rocket section's new lanes.

The rocket section of the bus (processing units, LDS, electric engines,
concrete, foundations, the silo) needs fluids and three items no bus lane
carries yet. Like a player, the engineer builds:

- the oil complex (companion/oil_complex.py) beside the bus, with an offshore
  pump at the nearest shore for its water and a pipe from the oil field's
  pumpjacks for its crude;
- pipes from the complex to the modules that use its fluids (sulfuric acid to
  processing units, lubricant to electric engines) and water to the concrete
  module, each branching off the line already laid where it can;
- a belt carrying rocket fuel from the complex into its new lane;
- a small stone smelting line (stone field, furnaces, coal) whose bricks start
  the stone-brick lane, and an iron ore field belted into the iron-ore lane
  (concrete takes a little ore).

All of it is built before the modules that use it. Pipes are laid by the pipe
router (companion/pipes.py), which keeps fluids apart; pipes already on the map
are joined or avoided by the fluid they hold (the map dump says which).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from companion.modules import EAST, NORTH, Placed, SOUTH, WEST
from companion.oil_complex import oil_complex
from companion import team_area
from companion.pipes import PipeMap
from companion.smelting import footprint, foreign_tiles, furnaces_for, ore_tiles, outlets, plan_field, route, smelting_column

ROCKET = "rocket-part"
SIDE_ITEMS = ("rocket-fuel", "stone-brick", "iron-ore")
FLUID_MACHINES = {"chemical-plant", "oil-refinery", "pumpjack", "offshore-pump", "boiler", "steam-engine",
                  "storage-tank", "pump", "assembling-machine-2", "assembling-machine-3"}
SIZES = {"oil-refinery": 5, "chemical-plant": 3, "assembling-machine-1": 3, "assembling-machine-2": 3,
         "assembling-machine-3": 3, "pumpjack": 3, "storage-tank": 3, "rocket-silo": 9, "lab": 3,
         "electric-mining-drill": 3, "stone-furnace": 2, "steel-furnace": 2}
STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
BRICK_RATE = 0.4          # bricks/s: concrete at 0.75/s takes 0.375
ORE_RATE = 0.1            # iron ore/s for concrete (0.075)


def _rot(v: tuple[float, float], d: int) -> tuple[float, float]:
    x, y = v
    for _ in range((d // 4) % 4):
        x, y = -y, x
    return x, y


def _tiles(name: str, x: float, y: float, direction: int = 0) -> set[tuple[int, int]]:
    if name == "offshore-pump":
        return {(math.floor(x), math.floor(y))}   # the land tile; its water comes out on the next one
    if name == "boiler":
        w, h = (2, 3) if direction in (EAST, WEST) else (3, 2)
    elif name == "steam-engine":
        w, h = (5, 3) if direction in (EAST, WEST) else (3, 5)
    else:
        w = h = SIZES.get(name, 1)
    x0, y0 = math.floor(x - w / 2 + 0.5), math.floor(y - h / 2 + 0.5)
    return {(x0 + i, y0 + j) for i in range(w) for j in range(h)}


def _ring(tiles: set[tuple[int, int]]) -> set[tuple[int, int]]:
    out = set()
    for x, y in tiles:
        for dx, dy in STEP.values():
            if (x + dx, y + dy) not in tiles:
                out.add((x + dx, y + dy))
    return out


def pipe_map(dump: Mapping[str, Any], planned: list[Placed], fluids: Mapping[tuple[int, int], str]) -> PipeMap:
    """What fluids meet on the ground: every building blocks a pipe tile, pipes already there (and the
    planned ones in `fluids`) carry their fluid, and the tiles around fluid machines stay clear of plain
    pipes (a pipe there could join a machine's connection)."""
    m = PipeMap()
    machines: list[set[tuple[int, int]]] = []
    for y, a, b in dump.get("water", []):
        m.blocked.update((x, y) for x in range(a, b + 1))
    m.blocked |= team_area.forbidden(dump)   # (the team world: only its own plot, or beyond the team area)
    existing = []
    for e in dump.get("entities", []):
        if e.get("t") in ("tree", "simple-entity"):
            continue
        t = (math.floor(e["x"]), math.floor(e["y"]))
        if e.get("t") == "pipe":
            m.pipes[t] = e.get("fl") or "?"
        elif e.get("t") == "pipe-to-ground":
            existing.append((t, e.get("d") or 0, e.get("fl") or "?"))
        else:
            tiles = _tiles(e["n"], e["x"], e["y"], e.get("d") or 0)
            m.blocked |= tiles
            if e["n"] in FLUID_MACHINES:
                machines.append(tiles)
    for e in planned:
        t = (math.floor(e.x), math.floor(e.y))
        if e.name == "pipe":
            m.pipes[t] = fluids.get(t, "?")
        elif e.name == "pipe-to-ground":
            existing.append((t, e.direction, fluids.get(t, "?")))
        else:
            tiles = _tiles(e.name, e.x, e.y, e.direction)
            m.blocked |= tiles
            if e.name in FLUID_MACHINES:
                machines.append(tiles)
    _pair_ends(m, existing)
    for tiles in machines:
        for t in _ring(tiles):
            if t not in m.pipes and t not in m.ends:
                m.reserved.setdefault(t, "")
    return m


def _pair_ends(m: PipeMap, ends: list[tuple[tuple[int, int], int, str]]) -> None:
    """Record pipe-to-ground ends with their partners (the nearest end facing back along its line)."""
    at = {t: (d, f) for t, d, f in ends}
    for t, d, f in ends:
        axis = 0 if d in (EAST, WEST) else 1
        line = t[1] if axis == 0 else t[0]
        m.ends[t] = (f, axis)
        m.faces[t] = d % 16   # (its open side: no other fluid's plain pipe there, pipes.PipeMap._pipe_ok)
        m.lines.setdefault((axis, line), set()).add(t[axis])
        dx, dy = STEP[d]
        for k in range(1, 11):   # the underground side is opposite the open side
            u = (t[0] - dx * k, t[1] - dy * k)
            if u in at and at[u][0] == (d + 8) % 16:
                if (t[axis] < u[axis]):
                    m.hops.setdefault((axis, line), []).append((t[axis], u[axis]))
                break


def crude_tiles(dump: Mapping[str, Any], m: PipeMap) -> set[tuple[int, int]]:
    """Plain pipe tiles on the crude network of the engineer's pumpjacks (or their free outlet tiles)."""
    out: set[tuple[int, int]] = set()
    for e in dump.get("entities", []):
        if e.get("n") != "pumpjack" or e.get("f") != dump.get("force"):
            continue
        ox, oy = _rot((1, -2), e.get("d") or 0)
        start = (math.floor(e["x"] + ox), math.floor(e["y"] + oy))
        if start not in m.pipes and start not in m.ends and start not in m.blocked:
            out.add(start)       # nothing there yet: a new pipe can start on the outlet
            m.reserved[start] = "crude-oil"
            continue
        seen, todo = {start}, [start]
        while todo:
            t = todo.pop()
            if t in m.pipes:
                m.pipes[t] = "crude-oil"
                out.add(t)
                nbrs = [(t[0] + dx, t[1] + dy) for dx, dy in STEP.values()]
            elif t in m.ends:
                f, axis = m.ends[t]
                m.ends[t] = ("crude-oil", axis)
                nbrs = [(t[0] + dx, t[1] + dy) for dx, dy in STEP.values()]
                line = t[1] if axis == 0 else t[0]
                for lo, hi in m.hops.get((axis, line), ()):
                    for a, b in ((lo, hi), (hi, lo)):
                        if a == t[axis]:
                            nbrs.append((b, line) if axis == 0 else (line, b))
            else:
                continue
            for n in nbrs:
                if n not in seen and (n in m.pipes or n in m.ends):
                    seen.add(n)
                    todo.append(n)
    return out


def _outside(port, dx: int = 0, dy: int = 0) -> tuple[int, int]:
    sx, sy = STEP[port.direction]
    return port.x + dx + sx, port.y + dy + sy


def plan_rocket(dump: Mapping[str, Any], bus, stages: list, notes: list[str], blocked: set[tuple[int, int]],
                taken: set[tuple[int, int]], lane_row: int, Stage, coal_line,
                sites: dict[str, str] | None = None) -> None:
    """Add the rocket section's outside supply to `stages`, before its first module. `blocked`: buildings
    and water on the map; `taken`: tiles no new building may use (those, ore, the bus area); `lane_row`:
    the row the new lanes start on; `coal_line(entry, rate, resource, used)`: base_plan's ore belt planner;
    `sites`: where the complex went when first planned ("oil-complex": "x,y"), kept so replays agree with
    what is built (a new spot is written into it)."""
    first = next((i for i, s in enumerate(stages) if s.name in ROCKET_STAGES), len(stages))
    planned = [e for s in stages for e in s.entities]
    built = footprint(e for e in planned if e.name not in ("pipe", "pipe-to-ground")) | outlets(planned)
    for e in planned:
        if e.name in SIZES or e.name in ("boiler", "steam-engine", "offshore-pump"):
            built |= _tiles(e.name, e.x, e.y, e.direction)
    used = taken | built
    # Fluid ports of the bus modules, in world tiles, and the fluid in each module's pipes.
    fluids: dict[tuple[int, int], str] = {}
    module_ports: dict[str, tuple[int, int]] = {}
    for placed in bus.attached:
        for port in placed.module.ports:
            if port.kind == "fluid":
                module_ports[port.item] = _outside(port, placed.x, placed.y)
                for e in placed.module.entities:
                    if e.name == "pipe":
                        fluids[(math.floor(e.x + placed.x), math.floor(e.y + placed.y))] = port.item
    entries = {item: (bus.lane_x(bus.lane_of(item)), lane_row - 1) for item in SIDE_ITEMS
               if bus.lane_of(item) is not None}
    used |= set(entries.values())
    out: list = []

    # 1. The complex, near the fluid modules, its port tiles free.
    complex_ = oil_complex()
    local = set()
    for e in complex_.entities:
        local |= _tiles(e.name, e.x, e.y, e.direction)
    port_tiles = {p.item: _outside(p) for p in complex_.ports}
    shape = local | set(port_tiles.values())
    targets = list(module_ports.values()) or [(bus.east, lane_row)]
    anchor = (sum(x for x, _ in targets) / len(targets) + complex_.width, sum(y for _, y in targets) / len(targets))
    from companion.base_plan import find_fit
    home = (int(dump["home"]["x"]), int(dump["home"]["y"]))
    sites = sites if sites is not None else {}
    if sites.get("oil-complex"):
        fit = tuple(int(v) for v in sites["oil-complex"].split(","))
    else:
        fit = _built_complex(dump, complex_) or find_fit(used, shape, home, tuple(dump["area"]), anchor=anchor)
    if fit is None:
        notes.append("no room for the oil complex: the rocket section has no fluids")
        return
    ox, oy = fit
    sites["oil-complex"] = f"{ox},{oy}"
    placed_complex = [Placed(e.name, e.x + ox, e.y + oy, e.direction, e.recipe) for e in complex_.entities]
    for t, f in getattr(complex_, "pipe_fluids", {}).items():
        fluids[(t[0] + ox, t[1] + oy)] = f
    ports = {p.item: p for p in complex_.ports}
    supplies = [{"x": s["entity"][1] + ox, "y": s["entity"][2] + oy, "item": s["item"], "slot": s["slot"]}
                for s in getattr(complex_, "supplies", [])]
    used |= {(x + ox, y + oy) for x, y in local}
    built |= {(x + ox, y + oy) for x, y in local}
    pipes = pipe_map(dump, planned + placed_complex, fluids)
    stage_entities = list(placed_complex)

    # Pipe tiles joined to each fluid's source so far: branches start only on these.
    network: dict[str, set[tuple[int, int]]] = {}
    for t, f in fluids.items():
        if (t[0] - ox, t[1] - oy) in getattr(complex_, "pipe_fluids", {}):
            network.setdefault(f, set()).add(t)

    def lay(fluid: str, start: tuple[int, int], goal: tuple[int, int], joins=None) -> bool:
        path = pipes.route(fluid, start, goal, joins=joins)
        if path is None:
            notes.append(f"no pipe route for {fluid} from {start} to {goal}")
            return False
        stage_entities.extend(path)
        network.setdefault(fluid, set()).update(
            {start, goal} | {(math.floor(e.x), math.floor(e.y)) for e in path if e.name == "pipe"})
        return True

    def at(item):   # a complex port's outside tile, in world tiles
        x, y = port_tiles[item]
        return x + ox, y + oy

    # 2. Water: an offshore pump at the nearest shore, piped to the complex and the concrete module.
    water_goal = at("water")
    pump = _pump(dump, used, water_goal)
    if pump is None:
        notes.append("no shore for the oil complex's water pump")
    else:
        pump_entity, outlet = pump
        stage_entities.append(pump_entity)
        pipes.blocked |= _tiles(pump_entity.name, pump_entity.x, pump_entity.y, pump_entity.direction)
        pipes.reserved[outlet] = "water"
        if lay("water", outlet, water_goal) and "water" in module_ports:
            lay("water", water_goal, module_ports["water"], joins=network["water"])
    # 3. Crude: a new pumpjack on a free well (its pipe starts on the pumpjack's outlet), else a branch off
    # a crude pipe already there.
    well = _new_pumpjack(dump, used, at("crude-oil"))
    if well is not None:
        jack, pole, outlet = well
        stage_entities += [jack, pole]
        pipes.blocked |= _tiles(jack.name, jack.x, jack.y) | {(math.floor(pole.x), math.floor(pole.y))}
        pipes.reserved[outlet] = "crude-oil"
        crude = {outlet}
    else:
        crude = crude_tiles(dump, pipes)
    if not crude:
        notes.append("no free oil well or crude pipe for the oil complex")
    else:
        start = min(crude, key=lambda t: abs(t[0] - at("crude-oil")[0]) + abs(t[1] - at("crude-oil")[1]))
        lay("crude-oil", start, at("crude-oil"), joins=crude)
    # 4. Fluids out to the modules.
    for fluid in ("sulfuric-acid", "lubricant"):
        if fluid in module_ports:
            lay(fluid, at(fluid), module_ports[fluid])
    out.append(Stage("oil-complex", stage_entities, supplies))

    # 5. Rocket fuel by belt into its lane. Belts may cross the bus area and ore, not buildings.
    taken_belts = blocked | built | footprint(stage_entities) | {t for t in pipes.pipes} | set(pipes.ends)
    for t in pipes.pipes:
        taken_belts.add(t)
    if pump is not None:
        taken_belts |= _tiles(pump[0].name, pump[0].x, pump[0].y, pump[0].direction)
    # Each new lane's entry, and the tile above it, is kept for its own belt.
    keep = {e: {e, (e[0], e[1] - 1)} for e in entries.values()}
    for tiles in keep.values():
        taken_belts |= tiles
    if "rocket-fuel" in entries:
        entry = entries["rocket-fuel"]
        path = route(at("rocket-fuel"), entry, taken_belts - keep[entry], bounds=tuple(dump["area"]), limit=2000)
        if path is None:
            notes.append("no belt brings rocket-fuel to the bus")
        else:
            path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, SOUTH)
            out.append(Stage("lane-rocket-fuel", path))
            taken_belts |= footprint(path) | outlets(path)
    # 6. Bricks and iron ore.
    if "stone-brick" in entries:
        entry = entries["stone-brick"]
        line = _brick_line(dump, entry, used | taken_belts, taken_belts - keep[entry], coal_line)
        if line is None:
            notes.append("no belt brings stone-brick to the bus")
        else:
            out.append(Stage("lane-stone-brick", line))
            taken_belts |= footprint(line) | outlets(line)
    if "iron-ore" in entries:
        entry = entries["iron-ore"]
        line = coal_line(entry, ORE_RATE, "iron-ore", taken_belts - keep[entry])
        if not line:
            notes.append("no belt brings iron-ore to the bus")
        else:
            out.append(Stage("lane-iron-ore", line))
    stages[first:first] = out


def _built_complex(dump: Mapping[str, Any], complex_) -> tuple[int, int] | None:
    """Where the engineer's oil complex already stands, from its refinery (a plan that lost its saved
    spot finds it again rather than building a second one)."""
    local = next((e for e in complex_.entities if e.name == "oil-refinery"), None)
    for e in dump.get("entities", []):
        if (local and e.get("n") == "oil-refinery" and e.get("r") == "advanced-oil-processing"
                and e.get("f") == dump.get("force")):
            return round(e["x"] - local.x), round(e["y"] - local.y)
    return None


def _new_pumpjack(dump: Mapping[str, Any], used: set, near: tuple[int, int]):
    """A pumpjack on the free oil well nearest `near`, a pole beside it, and its outlet tile."""
    oil = ore_tiles(dump.get("resources", {}), "crude-oil")
    used = (used - oil) | team_area.forbidden(dump)   # a pumpjack stands on its well (one it may use)
    wells = sorted(oil, key=lambda t: (t[0] - near[0]) ** 2 + (t[1] - near[1]) ** 2)
    for wx, wy in wells:
        body = {(wx - 1 + i, wy - 1 + j) for i in range(3) for j in range(3)}
        if body & used:
            continue
        for d in (NORTH, EAST, SOUTH, WEST):
            ox, oy = _rot((1, -2), d)
            outlet = (math.floor(wx + 0.5 + ox), math.floor(wy + 0.5 + oy))
            if outlet in used:
                continue
            for px, py in ((wx - 2, wy), (wx + 2, wy), (wx, wy - 2), (wx, wy + 2)):
                if (px, py) not in used and (px, py) != outlet:
                    return (Placed("pumpjack", wx + 0.5, wy + 0.5, d), Placed("small-electric-pole", px + 0.5, py + 0.5),
                            outlet)
    return None


def _pump(dump: Mapping[str, Any], used: set, near: tuple[int, int]):
    """An offshore pump at the shore nearest `near`, and the land tile its water comes out on."""
    from companion.power_plant import _turn
    water = {(x, y) for y, a, b in dump.get("water", []) for x in range(a, b + 1)}
    spots = sorted(team_area.pumps(dump), key=lambda s: (s["x"] - near[0]) ** 2 + (s["y"] - near[1]) ** 2)
    for spot in spots[:60]:
        wx, wy, turns = spot["wx"], spot["wy"], spot["turns"]
        px, py, pd = _turn(0, 1, NORTH, turns, 1, "offshore-pump")
        ex, ey, _ = _turn(0, 2, NORTH, turns, 1, "pipe")
        outlet = (math.floor(wx + 0.5 + ex), math.floor(wy + 0.5 + ey))
        pump = Placed("offshore-pump", wx + 0.5 + px, wy + 0.5 + py, pd)
        land = _tiles(pump.name, pump.x, pump.y, pump.direction) - water
        if outlet in used or outlet in water or land & used:
            continue
        return pump, outlet
    return None


def _brick_line(dump: Mapping[str, Any], entry: tuple[int, int], used: set, taken: set,
                coal_line) -> list[Placed] | None:
    """Stone drills, a short furnace column fed stone on one lane and coal on the other, and its brick
    belt to `entry`: the plate columns at the bus head, small. Buildings avoid `used`; belts `taken`."""
    stone = ore_tiles(dump["resources"], "stone")
    furnaces = furnaces_for(BRICK_RATE * 2 / 2)   # two stone per brick: one furnace makes 0.31 bricks/s
    furnaces = max(2, furnaces)
    drills = max(2, math.ceil(BRICK_RATE * 2 / 0.5))
    col = smelting_column("stone", "stone-brick", furnaces)
    room = (used - stone) | (taken & stone)   # drills stand on stone, not on what is built there
    other = foreign_tiles(dump["resources"], "stone")
    fld = plan_field(stone, room, drills, "stone", foreign=other) or         plan_field(stone, room, drills, "stone", min_ore=6, foreign=other)
    if fld is None:
        return None
    field_entities = fld.entities()
    base = taken | footprint(field_entities) | outlets(field_entities)
    # The column near the field: the nearest spots first, until its belts all find a way.
    from companion.base_plan import find_fit
    shape = footprint(col.entities) | outlets(col.entities) | {(-1, -1), (0, -1), (1, -1), (-1, -2), (1, -2),
                                       (5, col.height), (5, col.height + 1), (5, col.height + 2)}
    for skip in range(8):
        fit = find_fit(used | base, shape, fld.exit, tuple(dump["area"]), skip=skip * 3)
        if fit is None:
            return None
        cx, cy = fit
        entities = [Placed(e.name, e.x + cx, e.y + cy, e.direction, e.recipe) for e in col.entities]
        entities.append(Placed("transport-belt", cx + 0.5, cy - 0.5, SOUTH))   # the column head
        out_start = (cx + 5, cy + col.height)
        here = base | footprint(entities) | outlets(entities) | {out_start}
        ore_in, coal_in = (cx - 1, cy - 1), (cx + 1, cy - 1)
        brick = route(out_start, entry, here - {entry, out_start}, bounds=tuple(dump["area"]), limit=4000)
        if brick is None:
            continue
        here |= footprint(brick) | outlets(brick)
        path = route(fld.exit, ore_in, (here | {coal_in}) - {ore_in}, bounds=tuple(dump["area"]), limit=2000)
        if path is None:
            continue
        path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, EAST)    # sideloads the head
        here |= footprint(path) | outlets(path)
        coal = coal_line(coal_in, furnaces * 0.0225, "coal", here, WEST) or []
        brick[-1] = Placed("transport-belt", brick[-1].x, brick[-1].y, SOUTH)
        return field_entities + path + coal + entities + brick
    return None


# The rocket section's bus stages (its supply goes in before the first of them).
ROCKET_STAGES = {"bus-green-circuits-3", "bus-red-circuits-2", "bus-steel-2", "bus-engines-2",
                 "bus-processing-units", "bus-low-density-structures", "bus-electric-engines", "bus-concrete",
                 "bus-foundations", "bus-rocket-silo"}
