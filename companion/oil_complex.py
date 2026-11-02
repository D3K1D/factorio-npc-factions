"""The oil complex: crude oil and water in; rocket fuel, lubricant and sulfuric acid out.

One advanced refinery splits crude into heavy oil, light oil and petroleum gas.
Every one of the three always has somewhere to go, so the refinery never stops
while anything downstream is short (a refinery halts when any output is full):

    heavy oil  -> lubricant (out to the electric engines), solid fuel
    light oil  -> rocket fuel assemblers, solid fuel
    gas        -> sulfur (for the acid plant beside it), solid fuel

Solid fuel goes onto a belt past the rocket fuel assemblers and ends in a
buffer chest, which the engineer empties when it fills. Sulfur goes straight
from its plant to the acid plant; the engineer hands the acid plant its iron.

Chemical plants face south (fluid in on their south side, item inserters on
the north side), the rocket fuel assemblers face west (light oil in on their
west side). Pipes inside the block are laid by the pipe router, which keeps the
fluids apart. Local layout (before shifting to positive coordinates):

    row -7       rocket fuel belt, west to the item port
    rows -5..-3  rocket fuel assemblers, x 0-2 and 4-6
    row -1       solid fuel belt, west, into the buffer chest at x -3
    rows 1..3    plants: lubricant -4..-2, solid fuel (heavy 0-2, light 4-6, gas 8-10), sulfur 12-14, acid 16-18
    row 4        plant inlets
    rows 7..11   refinery x 3-7, outlets on row 6, inlets on row 12 (the crude and water ports)
"""

from __future__ import annotations

from companion.modules import EAST, Module, NORTH, Placed, Port, SOUTH, WEST, _drop_overlaps
from companion.pipes import PipeMap

# plant: (name, recipe, top-left x)
PLANTS = (("lubricant", "lubricant", -4), ("fuel-heavy", "solid-fuel-from-heavy-oil", 0),
          ("fuel-light", "solid-fuel-from-light-oil", 4), ("fuel-gas", "solid-fuel-from-petroleum-gas", 8),
          ("sulfur", "sulfur", 12), ("acid", "sulfuric-acid", 16))
FUEL_PLANTS = ("fuel-heavy", "fuel-light", "fuel-gas")
REFINERY_X, REFINERY_Y = 3, 7
RF_X = (0, 4)


def _tiles(x: int, y: int, w: int, h: int) -> set[tuple[int, int]]:
    return {(x + i, y + j) for i in range(w) for j in range(h)}


def oil_complex(rate: float = 0.1, book=None) -> Module:
    """`rate`: rocket fuel per second the block is meant for (one refinery makes about 0.15/s)."""
    m = Module("oil-complex")
    m.makes = {"rocket-fuel": rate}
    blocked: set[tuple[int, int]] = set()

    def put(name, x, y, direction=NORTH, recipe=None, w=1, h=1):
        m.entities.append(Placed(name, x + w / 2, y + h / 2, direction, recipe))
        blocked.update(_tiles(x, y, w, h))

    # Refinery (north): outlets heavy/light/gas on row 6 at x 3/5/7, inlets water/crude on row 12 at x 4/6.
    put("oil-refinery", REFINERY_X, REFINERY_Y, NORTH, "advanced-oil-processing", 5, 5)
    rx = REFINERY_X + 2
    pipes = PipeMap()
    sources = {"heavy-oil": (rx - 2, 6), "light-oil": (rx, 6), "petroleum-gas": (rx + 2, 6), "water": (rx - 1, 12),
               "crude-oil": (rx + 1, 12)}
    sinks: dict[str, list[tuple[int, int]]] = {f: [] for f in sources}   # one connection tile per machine
    # Plants face south: inlets on row 4 at x and x+2 (the first fluid ingredient on the east one).
    fluids_in = {"lubricant": ("heavy-oil", "heavy-oil"), "fuel-heavy": ("heavy-oil", "heavy-oil"),
                 "fuel-light": ("light-oil", "light-oil"), "fuel-gas": ("petroleum-gas", "petroleum-gas"),
                 "sulfur": ("petroleum-gas", "water"), "acid": ("water", "water")}
    for name, recipe, x in PLANTS:
        put("chemical-plant", x, 1, SOUTH, recipe, 3, 3)
        west, east = fluids_in[name]
        pipes.reserve((x, 4), west)
        pipes.reserve((x + 2, 4), east)
        if west == east:
            sinks[west].append((x + 2, 4) if x + 2 <= rx else (x, 4))   # the inlet nearer the refinery
        else:
            sinks[west].append((x, 4))
            sinks[east].append((x + 2, 4))
    # Plant outlets (north side, row 0): lubricant and acid; the solid fuel and sulfur plants have none.
    lube_out, acid_out = (-4, 0), (18, 0)
    pipes.reserve((-2, 0), "lubricant")
    pipes.reserve((16, 0), "sulfuric-acid")
    # Rocket fuel assemblers (west-facing): light oil in at x-1, middle row.
    for x in RF_X:
        put("assembling-machine-2", x, -5, WEST, "rocket-fuel", 3, 3)
        sinks["light-oil"].append((x - 1, -4))

    # Items. Solid fuel: plant -> inserter (row 0) -> belt (row -1, west) -> rocket fuel inserters (row -2)
    # and on into the buffer chest.
    for name, _, x in PLANTS:
        if name in FUEL_PLANTS:
            put("inserter", x + 1, 0, SOUTH)          # picks from the plant (south), drops on the belt
    for x in range(10, -2, -1):
        put("transport-belt", x, -1, WEST)
    put("inserter", -2, -1, EAST)                     # belt end -> chest
    put("steel-chest", -3, -1)
    for x in RF_X:
        put("inserter", x + 1, -2, SOUTH)             # belt -> rocket fuel assembler
        put("inserter", x + 1, -6, SOUTH)             # assembler -> rocket fuel belt
    for x in range(6, -6, -1):
        put("transport-belt", x, -7, WEST)
    m.ports.append(Port("rocket-fuel", "out", -5, -7, WEST, 0.1))
    # Sulfur plant -> acid plant, through the gap between them.
    put("inserter", 15, 2, WEST)                      # picks west (sulfur), drops east (acid)
    # Power: poles in the gaps of row 0, over the rocket fuel row, and beside the refinery.
    for x, y in ((-1, 0), (3, 0), (7, 0), (11, 0), (15, 0), (3, -6), (8, 6), (19, 2)):
        put("small-electric-pole", x, y)
    pipes.blocked |= blocked

    # Every connection tile in use gets its pipe first, so no route ends beside another fluid.
    fixed: list[Placed] = []
    ends = [(f, t) for f, t in sources.items()] + [(f, t) for f, ts in sinks.items() for t in ts]         + [("lubricant", lube_out), ("sulfuric-acid", acid_out)]
    for fluid, (x, y) in ends:
        pipe = [Placed("pipe", x + 0.5, y + 0.5)]
        pipes.add_pipes(pipe, fluid)
        fixed.extend(pipe)
    # Then each fluid from its source to every consumer, branching off what is connected so far.
    laid: list[Placed] = []
    for fluid, source in sources.items():
        joined = {source}
        for sink in sinks[fluid]:
            route = pipes.route(fluid, source, sink, joins=joined)
            if route is None:
                raise ValueError(f"oil complex: no pipe for {fluid} to {sink}")
            laid.extend(route)
            joined |= {(int(p.x - 0.5), int(p.y - 0.5)) for p in route if p.name == "pipe"} | {sink}
    m.entities.extend(fixed + laid)
    m.pipe_fluids = {**{t: f for t, f in pipes.pipes.items()}, **{t: f for t, (f, _) in pipes.ends.items()}}
    m.ports.append(Port("crude-oil", "fluid", rx + 1, 12, SOUTH, 20.0))
    m.ports.append(Port("water", "fluid", rx - 1, 12, SOUTH, 15.0))
    m.ports.append(Port("lubricant", "fluid-out", lube_out[0], lube_out[1], WEST, 0.0))
    m.ports.append(Port("sulfuric-acid", "fluid-out", acid_out[0], acid_out[1], EAST, 0.0))
    m.supplies = [{"entity": ("chemical-plant", 17.5, 2.5), "item": "iron-plate", "slot": "input"}]
    return _shift(_drop_overlaps(m))


def _shift(m: Module) -> Module:
    """Move the block so its top-left tile is (0, 0)."""
    xs = [e.x - 0.5 for e in m.entities] + [p.x for p in m.ports]
    ys = [e.y - 0.5 for e in m.entities] + [p.y for p in m.ports]
    big = {"oil-refinery": 2, "chemical-plant": 1, "assembling-machine-2": 1}
    x0 = min(min(e.x - 0.5 - big.get(e.name, 0) for e in m.entities), min(xs))
    y0 = min(min(e.y - 0.5 - big.get(e.name, 0) for e in m.entities), min(ys))
    dx, dy = -int(x0), -int(y0)
    for e in m.entities:
        e.x += dx
        e.y += dy
    for p in m.ports:
        p.x += dx
        p.y += dy
    for s in getattr(m, "supplies", []):
        name, x, y = s["entity"]
        s["entity"] = (name, x + dx, y + dy)
    m.pipe_fluids = {(x + dx, y + dy): f for (x, y), f in getattr(m, "pipe_fluids", {}).items()}
    m.width = int(max(e.x + 0.5 + big.get(e.name, 0) for e in m.entities))
    m.height = int(max(e.y + 0.5 + big.get(e.name, 0) for e in m.entities))
    return m
