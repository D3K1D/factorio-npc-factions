"""Building blocks a player would put beside a main bus.

A module is a stack of assembly rows. Each row has an input belt running away
from the bus, inserters into a line of assemblers, and inserters onto an output
belt. Rows chain: a row's output belt is the next row's input belt, carrying
the upstream product on its far lane and a second ingredient, side-loaded from
the bus, on its near lane. The last output belt runs back to the bus.

Ports sit on the module's west edge (x = 0), the side that faces the bus, so
the bus planner only has to bring each input belt to its port tile and take the
output belt away. Coordinates are module-local tile positions (entity centres),
x to the east, y to the south; directions are 16-way (north 0, east 4, south 8,
west 12). Machine counts come from the ratio calculator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from companion.ratios import Book, solve, unlocked

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12
ASSEMBLER = "assembling-machine-2"
# Loaders (the Vanilla Loaders mod): two tiles long, items moving the way one faces. An "input" loader
# takes from the belt behind it into the building ahead; an "output" loader takes from the building
# behind it onto the belt ahead. No power. Players use them rarely: only where an inserter cannot keep
# up (loader_limit), and so do the planners, with the yellow one, once its recipe is unlocked.
LOADER = "loader"
LOADERS = {"loader", "fast-loader", "express-loader", "turbo-loader"}


def loader_tiles(x: float, y: float, direction: int) -> set[tuple[int, int]]:
    """The two tiles of a loader centred at (x, y), one behind the other along its facing."""
    if direction in (NORTH, SOUTH):
        return {(math.floor(x), math.floor(y - 0.5)), (math.floor(x), math.floor(y + 0.5))}
    return {(math.floor(x - 0.5), math.floor(y)), (math.floor(x + 0.5), math.floor(y))}


@dataclass
class Placed:
    name: str
    x: float
    y: float
    direction: int = 0
    recipe: str | None = None
    kind: str | None = None  # underground belt or loader "input"/"output"
    bar: int | None = None   # a chest limited to this many slots
    mirror: bool = False     # built mirrored (2.x: a refinery's or a chemical plant's pipe connections swap sides)

    def to_plan(self, key: str, dx: float = 0, dy: float = 0) -> dict:
        entity = {"id": key, "prototype": self.name, "position": {"x": self.x + dx, "y": self.y + dy},
                  "direction": self.direction}
        if self.recipe:
            entity["recipe"] = self.recipe
        if self.kind:
            entity["belt_to_ground_type"] = self.kind
        if self.bar:
            entity["bar"] = self.bar
        if self.mirror:
            entity["mirror"] = True
        return entity


@dataclass
class Port:
    item: str
    kind: str          # "in" or "out"
    x: int             # tile on the module edge
    y: int
    direction: int     # belt direction on the port tile
    rate: float        # items per second
    lane: str | None = None   # "left" or "right" along `direction` where the item must come in on its own lane


@dataclass
class Module:
    name: str
    entities: list[Placed] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    width: int = 0
    height: int = 0
    makes: dict[str, float] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entities:
            out[e.name] = out.get(e.name, 0) + 1
        return out

    def plan(self, dx: float = 0, dy: float = 0, prefix: str = "") -> list[dict]:
        names: dict[str, int] = {}
        out = []
        for e in self.entities:
            names[e.name] = names.get(e.name, 0) + 1
            out.append(e.to_plan(f"{prefix}{e.name}_{names[e.name]}", dx, dy))
        return out


# Items per second one inserter moves between a belt and a machine (conservative,
# no capacity research); a machine side has three inserter slots.
INSERTER_RATE = {"inserter": 0.8, "fast-inserter": 2.2, "long-handed-inserter": 1.1}


def inserter_rate(name: str, site: Mapping[str, Any] | None = None) -> float:
    """Items per second one `name` inserter moves from a chest or machine: a swing there and back is one
    turn of its arm (the prototype's rotation speed, in turns per tick), and each swing moves a hand of
    one item plus the prototype's own bonus plus the force's researched stack bonus (bulk inserters
    count the bulk bonus). Without those facts, the conservative table above."""
    facts = ((site or {}).get("prototypes") or {}).get(name) or {}
    speed = facts.get("rotation_speed")
    if not speed:
        return INSERTER_RATE.get(name, 0.0)
    bonus = (site or {}).get("inserter_bonus") or {}
    researched = bonus.get("bulk", 0) if facts.get("bulk") else (
        bonus.get("stack", 0) if facts.get("stack_bonus", True) else 0)
    return float(speed) * 60 * (1 + (facts.get("hand_bonus") or 0) + (researched or 0))


def loader_limit(site: Mapping[str, Any]) -> float | None:
    """Above how many items per second a loader takes an inserter's place: the best inserter the
    engineer can make, measured by inserter_rate. None while the loader's recipe is locked."""
    have = unlocked(site)
    if LOADER not in have:
        return None
    try:   # (researched, but made from what it cannot have yet: a mod's loader needs steel, run 20, 2026-10-25)
        from companion import handcraft
        if site.get("recipes") and not handcraft.obtainable(site, LOADER):
            return None
    except Exception:
        pass
    facts = site.get("prototypes") or {}
    names = [n for n, f in facts.items() if isinstance(f, Mapping) and f.get("type") == "inserter" and n in have]
    names += [n for n in INSERTER_RATE if n in have]
    return max((inserter_rate(n, site) for n in names), default=INSERTER_RATE["inserter"])


@dataclass
class Row:
    recipe: str
    count: int
    near: str | None = None    # ingredient side-loaded from the bus onto the near lane
    far: str | None = None     # ingredient on the far lane: the upstream row's product, or a second bus input


def _belt(m: Module, x: int, y: int, direction: int, name: str = "transport-belt") -> None:
    m.entities.append(Placed(name, x + 0.5, y + 0.5, direction))


def _inserters(rate: float, kinds: tuple[str, ...]) -> tuple[str, int]:
    """The cheapest inserter type and how many (1-3) move `rate` items/s."""
    for kind in kinds:
        n = max(1, math.ceil(rate / INSERTER_RATE[kind] - 1e-9))
        if n <= 3:
            return kind, n
    return kinds[-1], 3


def _one(rate: float, kinds: tuple[str, ...]) -> str:
    """The cheapest inserter type that moves `rate` items/s on its own."""
    for kind in kinds:
        if INSERTER_RATE[kind] >= rate:
            return kind
    return kinds[-1]


def _slots(cx: float, n: int) -> list[float]:
    return [cx, cx - 1, cx + 1][:n]


def build(name: str, rows: list[Row], plan, book: Book, *, machine: str = ASSEMBLER,
          inserters: tuple[str, ...] = ("inserter", "fast-inserter")) -> Module:
    """Lay out `rows` top to bottom, each sized and supplied for its share of `plan`."""
    m = Module(name)
    widest = max(r.count for r in rows)
    width = 2 + 3 * widest + widest // 2 + 1
    y = 1  # the first input belt
    for index, row in enumerate(rows):
        step = plan.step(row.recipe)
        recipe = book.recipes[row.recipe]
        per_machine = step.rate / row.count / max(recipe.makes(step.item), 1e-9)   # crafts per second
        in_rate = per_machine * sum(a for _, a in recipe.ingredients)
        out_rate = per_machine * recipe.makes(step.item)
        in_kind, n_in = _inserters(in_rate, inserters)
        out_kind, n_out = _inserters(out_rate, inserters)
        for x in range(0, width):
            _belt(m, x, y, EAST)
        if index == 0:
            # Bus inputs arrive on the west edge one row above and below the belt's second tile
            # and side-load onto its north (near) and south (far) lanes.
            if row.near:
                _belt(m, 0, y - 1, EAST); _belt(m, 1, y - 1, SOUTH)
                m.ports.append(Port(row.near, "in", 0, y - 1, EAST, plan.inputs.get(row.near, 0.0)))
            if row.far:
                _belt(m, 0, y + 1, EAST); _belt(m, 1, y + 1, NORTH)
                m.ports.append(Port(row.far, "in", 0, y + 1, EAST, plan.inputs.get(row.far, 0.0)))
        elif row.near:
            # The upstream product is already on the far lane; side-load the bus input from the north.
            _belt(m, 0, y - 1, EAST); _belt(m, 1, y - 1, SOUTH)
            m.ports.append(Port(row.near, "in", 0, y - 1, EAST, plan.inputs.get(row.near, 0.0)))
        x = 3
        for k in range(row.count):
            cx, cy = x + 1.5, y + 3.5
            m.entities.append(Placed(machine, cx, cy, NORTH, row.recipe))
            for sx in _slots(cx, n_in):
                m.entities.append(Placed(in_kind, sx, y + 1.5, NORTH))     # belt (north) into the machine
            for sx in _slots(cx, n_out):
                m.entities.append(Placed(out_kind, sx, y + 5.5, NORTH))    # machine onto the belt (south)
            if k % 2 == 0:
                # Poles between a pair of assemblers on both inserter lines power both machines.
                px = x + 3 + 0.5 if k + 1 < row.count else x - 0.5
                m.entities.append(Placed("small-electric-pole", px, y + 1.5))
                m.entities.append(Placed("small-electric-pole", px, y + 5.5))
            x += 3 + (1 if k % 2 == 1 else 0)
        y += 6
    for x in range(0, width):
        _belt(m, x, y, WEST)
    last = plan.step(rows[-1].recipe)
    m.ports.append(Port(last.item, "out", 0, y, WEST, last.rate))
    m.width, m.height = width, y + 1
    m.makes = {last.item: last.rate}
    # An anchor pole near the bus edge links the module to the bus pole line.
    if not any(e.name == "small-electric-pole" and (e.x, e.y) == (2.5, 2.5) for e in m.entities):
        m.entities.append(Placed("small-electric-pole", 2.5, 2.5))
    return _drop_overlaps(m)


def _drop_overlaps(m: Module) -> Module:
    """Belts give way to anything else placed on the same tile."""
    taken = set()
    for e in m.entities:
        if e.name in LOADERS:
            taken |= loader_tiles(e.x, e.y, e.direction)
        elif e.name != "transport-belt":
            size = 3 if e.name.startswith("assembling") or e.name == "lab" else 1
            for dx in range(size):
                for dy in range(size):
                    taken.add((math.floor(e.x - size / 2 + dx + 0.5), math.floor(e.y - size / 2 + dy + 0.5)))
    seen, kept = set(), []
    for e in m.entities:
        tile = (math.floor(e.x), math.floor(e.y))
        if e.name == "transport-belt":
            if tile in taken or tile in seen:
                continue
            seen.add(tile)
        kept.append(e)
    m.entities = kept
    return m


def circuit_line(per_second: float, book: Book, *, machine: str = ASSEMBLER,
                 inserters: tuple[str, ...] = ("inserter", "fast-inserter")) -> Module:
    """The classic direct-insertion green circuit line: cable, circuit, cable, circuit, cable.

    One input belt above carries iron (north lane) and copper (south lane); cable machines
    hand cables straight to the circuit machines beside them; circuits leave on a belt below.
    Each unit of 3 cable and 2 circuit machines makes twice one circuit machine's rate."""
    circuit = book.recipes["electronic-circuit"]
    speed = book.machines[machine].speed
    per_circuit_machine = speed / circuit.energy * circuit.makes("electronic-circuit")
    units = max(1, math.ceil(per_second / (2 * per_circuit_machine) - 1e-9))
    rate = per_second / (2 * units)                      # circuits/s per circuit machine
    # One inserter per position: cables between machines (up to two cable machines feed a circuit
    # machine 3 cables per circuit), copper into a cable machine, iron into and circuits out of a
    # circuit machine.
    cable_kind = _one(rate * 3 / 2, inserters)
    iron_kind = _one(rate, inserters)
    copper_kind = _one(rate, inserters)
    out_kind = _one(rate, inserters)
    m = Module("green-circuits")
    width = 2 + units * 20
    y = 1
    for x in range(0, width):
        _belt(m, x, y, EAST)
    _belt(m, 0, y - 1, EAST); _belt(m, 1, y - 1, SOUTH)   # iron: north lane
    _belt(m, 0, y + 1, EAST); _belt(m, 1, y + 1, NORTH)   # copper: south lane
    m.ports.append(Port("iron-plate", "in", 0, y - 1, EAST, per_second))
    m.ports.append(Port("copper-plate", "in", 0, y + 1, EAST, per_second * 1.5))
    x = 2
    for u in range(units):
        if u > 0:
            x += 1  # a gap column between units
        for i, recipe in enumerate(["copper-cable", "electronic-circuit", "copper-cable", "electronic-circuit",
                                    "copper-cable"]):
            cx = x + 1.5
            m.entities.append(Placed(machine, cx, y + 3.5, NORTH, recipe))
            feed = copper_kind if recipe == "copper-cable" else iron_kind
            m.entities.append(Placed(feed, cx, y + 1.5, NORTH))                  # belt into the machine
            if recipe == "electronic-circuit":
                m.entities.append(Placed(out_kind, cx, y + 5.5, NORTH))          # circuits onto the belt
            if i < 4:
                gx = x + 3 + 0.5
                # Cables move from the cable machine to the circuit machine beside it:
                # facing west picks from the west, facing east picks from the east.
                # The pole sits mid-column so it reaches the inserter lines above and below the machines.
                m.entities.append(Placed(cable_kind, gx, y + 2.5, WEST if recipe == "copper-cable" else EAST))
                m.entities.append(Placed("small-electric-pole", gx, y + 3.5))
                x += 4
            else:
                x += 3
    y += 6
    for x2 in range(0, width):
        _belt(m, x2, y, WEST)
    m.ports.append(Port("electronic-circuit", "out", 0, y, WEST, per_second))
    m.width, m.height, m.makes = width, y + 1, {"electronic-circuit": per_second}
    m.entities.append(Placed("small-electric-pole", 2.5, 6.5))   # anchor to the bus pole line
    return _drop_overlaps(m)


def green_circuits(per_second: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return circuit_line(per_second, book, machine=machine)


def gears(per_second: float, book: Book, machine: str = ASSEMBLER) -> Module:
    plan = solve({"iron-gear-wheel": per_second}, book, inputs={"iron-plate"}, allowed={machine})
    return build("gears", [Row("iron-gear-wheel", plan.step("iron-gear-wheel").count, far="iron-plate")],
                 plan, book, machine=machine)


def red_science(per_second: float, book: Book, machine: str = ASSEMBLER) -> Module:
    """Gear row over a red science row: gears on the far lane, copper side-loaded on the near lane."""
    plan = solve({"automation-science-pack": per_second}, book, inputs={"iron-plate", "copper-plate"},
                 allowed={machine})
    return build("red-science", [Row("iron-gear-wheel", plan.step("iron-gear-wheel").count, far="iron-plate"),
                                 Row("automation-science-pack", plan.step("automation-science-pack").count,
                                     near="copper-plate")], plan, book, machine=machine)


def green_science(per_second: float, book: Book, machine: str = ASSEMBLER,
                  inserters: tuple[str, ...] = ("inserter", "fast-inserter")) -> Module:
    """Green science from bus iron, gears and circuits, with the intermediates made once.

    Inserter assemblers sit above a shared belt S (iron and gears from belt A, circuits by
    long-handed inserters from belt B) and drop inserters onto S's south lane; transport-belt
    assemblers sit below S (iron and gears from belt C) and drop belts onto S's north lane.
    Further east, green science assemblers above S take both, and their packs leave on a belt
    that loops round the east end and back west along the bottom to the bus."""
    speed = book.machines[machine].speed
    science, inserter, belt = (book.recipes[r] for r in
                               ("logistic-science-pack", "inserter", "transport-belt"))
    n_green = max(1, math.ceil(per_second / (speed / science.energy) - 1e-9))
    n_ins = max(1, math.ceil(per_second / (speed / inserter.energy) - 1e-9))
    n_belt = max(1, math.ceil(per_second / (speed / belt.energy * 2) - 1e-9))
    kinds = inserters
    m = Module("green-science")
    pitch = 4                       # machine (3) + gap column (1) for poles and spacing
    x_prod_end = 2 + pitch * max(n_ins, n_belt)       # last tile of the producer section
    x_green = x_prod_end + 2
    x_end = x_green + pitch * n_green + 1              # the output belt's east column

    def machines(count: int, x0: int, mid: float, recipe: str) -> list[float]:
        centres = []
        for k in range(count):
            left = x0 + pitch * k
            cx = left + 1.5
            m.entities.append(Placed(machine, cx, mid, NORTH, recipe))
            m.entities.append(Placed("small-electric-pole", left - 0.5, mid))
            centres.append(cx)
        return centres

    # Belt B (circuits) and belt A (iron north lane, gears south lane) above the inserter assemblers.
    # Circuits come in two rows up (bus taps need two rows between ports) and turn down into B.
    _belt(m, 0, -2, EAST); _belt(m, 1, -2, EAST); _belt(m, 2, -2, SOUTH); _belt(m, 2, -1, SOUTH)
    m.ports.append(Port("electronic-circuit", "in", 0, -2, EAST, per_second))
    for x in range(2, x_prod_end + 1):
        _belt(m, x, 0, EAST)
    for x in range(0, x_prod_end + 1):
        _belt(m, x, 1, EAST)
    _belt(m, 0, 0, EAST); _belt(m, 1, 0, SOUTH)
    _belt(m, 0, 2, EAST); _belt(m, 1, 2, NORTH)
    m.ports.append(Port("iron-plate", "in", 0, 0, EAST, per_second))
    m.ports.append(Port("iron-gear-wheel", "in", 0, 2, EAST, per_second * 1.5))
    ins_rate = per_second / n_ins
    for cx in machines(n_ins, 3, 4.5, "inserter"):
        m.entities.append(Placed(_one(ins_rate * 2, kinds), cx, 2.5, NORTH))                # iron and gears
        m.entities.append(Placed("long-handed-inserter", cx - 1, 2.5, NORTH))              # circuits from B
        m.entities.append(Placed(_one(ins_rate, kinds), cx, 6.5, NORTH))                   # onto S, south lane
    # Belt S runs east past the producers to the science assemblers.
    for x in range(2, x_end - 1):   # S stops short of the output belt's east column
        _belt(m, x, 7, EAST)
    belt_rate = per_second / n_belt
    for cx in machines(n_belt, 3, 10.5, "transport-belt"):
        m.entities.append(Placed(_one(belt_rate, kinds), cx, 8.5, SOUTH))     # picks the machine (south), drops on S
        m.entities.append(Placed(_one(belt_rate, kinds), cx, 12.5, SOUTH))    # picks belt C (south), drops in
    # Belt C (iron north lane, gears south lane) below the belt assemblers.
    for x in range(0, x_prod_end + 1):
        _belt(m, x, 13, EAST)
    _belt(m, 0, 12, EAST); _belt(m, 1, 12, SOUTH)
    _belt(m, 0, 14, EAST); _belt(m, 1, 14, NORTH)
    m.ports.append(Port("iron-plate", "in", 0, 12, EAST, per_second / 2))
    m.ports.append(Port("iron-gear-wheel", "in", 0, 14, EAST, per_second / 2))
    # Science assemblers above S; packs go up onto the output belt.
    green_rate = per_second / n_green
    for cx in machines(n_green, x_green, 4.5, "logistic-science-pack"):
        m.entities.append(Placed(_one(green_rate * 2, kinds), cx, 6.5, SOUTH))   # picks S (south), drops in
        m.entities.append(Placed(_one(green_rate, kinds), cx, 2.5, SOUTH))       # picks the machine, drops north
    # Output belt: east along y=1 over the science section, down the east end, back west along y=16.
    for x in range(x_green, x_end):   # a gap after belt A so its plates cannot run onto it
        _belt(m, x, 1, EAST)
    for y in range(1, 16):
        _belt(m, x_end, y, SOUTH)
    for x in range(0, x_end + 1):
        _belt(m, x, 16, WEST)
    m.ports.append(Port("logistic-science-pack", "out", 0, 16, WEST, per_second))
    # Poles along the producer section's middle row keep the science section wired to the rest.
    have = {(e.x, e.y) for e in m.entities if e.name == "small-electric-pole"}
    for k in range(max(n_ins, n_belt) + 1):
        spot = (2 + pitch * k + 0.5, 4.5)
        if spot not in have and spot[0] < x_green:
            m.entities.append(Placed("small-electric-pole", *spot))
    m.entities.append(Placed("small-electric-pole", 2.5, 9.5))   # anchor to the bus pole line
    shift = 2   # move everything down so the top port row is y >= 0
    for e in m.entities:
        e.y += shift
    for p in m.ports:
        p.y += shift
    m.width, m.height, m.makes = x_end + 1, 17 + shift, {"logistic-science-pack": per_second}
    return _drop_overlaps(m)


LAB_PACKS = 8   # four belts, two packs each


def labs(count: int, packs: tuple[str, ...]) -> Module:
    """A row of labs at the end of the bus, fed every pack the research needs (up to eight).

    Two belts run above the labs and two below, each carrying two packs, one per lane. Belt A (next
    to the labs, above) and belt C (next to them, below) are reached by inserters; belt B above A and
    belt D below C by long-handed inserters. A and C take their packs side-loaded straight from the
    west edge; B and D come in a few rows further out, are side-loaded there, and turn in beside A
    and C (a turn keeps both lanes)."""
    m = Module("labs")
    packs = tuple(packs)[:LAB_PACKS]
    pitch = 4
    width = 3 + pitch * count
    ya = 5 if len(packs) > 2 else 3       # belt A's row; belt B's head needs five rows above it
    yc = ya + 6                           # belt C, below the labs (rows ya+2..ya+4) and their inserters

    def straight(y: int, near: str | None, far: str | None) -> None:
        """A belt along row y from the west edge, its north lane fed from above, its south from below."""
        for x in range(0, width):
            _belt(m, x, y, EAST)
        if near:
            _belt(m, 0, y - 1, EAST); _belt(m, 1, y - 1, SOUTH)
            m.ports.append(Port(near, "in", 0, y - 1, EAST, 0.0))
        if far:
            _belt(m, 0, y + 1, EAST); _belt(m, 1, y + 1, NORTH)
            m.ports.append(Port(far, "in", 0, y + 1, EAST, 0.0))

    def turned(y: int, head: int, near: str | None, far: str | None) -> None:
        """A belt along row y from column 2, fed by a head belt along row `head` that turns at column 2."""
        for x in range(0, 2):
            _belt(m, x, head, EAST)
        if near:
            _belt(m, 0, head - 1, EAST); _belt(m, 1, head - 1, SOUTH)
            m.ports.append(Port(near, "in", 0, head - 1, EAST, 0.0))
        if far:
            _belt(m, 0, head + 1, EAST); _belt(m, 1, head + 1, NORTH)
            m.ports.append(Port(far, "in", 0, head + 1, EAST, 0.0))
        down = SOUTH if head < y else NORTH
        step = 1 if head < y else -1
        for yy in range(head, y, step):
            _belt(m, 2, yy, down)
        for x in range(2, width):
            _belt(m, x, y, EAST)

    lanes = list(packs) + [None] * (LAB_PACKS - len(packs))
    straight(ya, lanes[0], lanes[1])
    if lanes[2] or lanes[3]:
        turned(ya - 1, ya - 4, lanes[2], lanes[3])
    if lanes[4] or lanes[5]:
        straight(yc, lanes[4], lanes[5])
    if lanes[6] or lanes[7]:
        turned(yc + 1, yc + 4, lanes[6], lanes[7])
    for k in range(count):
        left = 3 + pitch * k
        cx = left + 1.5
        m.entities.append(Placed("lab", cx, ya + 3.5, NORTH))
        m.entities.append(Placed("inserter", cx, ya + 1.5, NORTH))                     # belt A (north) in
        if lanes[2] or lanes[3]:
            m.entities.append(Placed("long-handed-inserter", cx - 1, ya + 1.5, NORTH))  # belt B, two up
        if lanes[4] or lanes[5]:
            m.entities.append(Placed("inserter", cx, ya + 5.5, SOUTH))                 # belt C (south) in
        if lanes[6] or lanes[7]:
            m.entities.append(Placed("long-handed-inserter", cx - 1, ya + 5.5, SOUTH))  # belt D, two down
        m.entities.append(Placed("small-electric-pole", left - 0.5, ya + 3.5))
    m.entities.append(Placed("small-electric-pole", 2.5, ya + 5.5))   # anchor to the bus pole line
    m.width, m.height = width, yc + (6 if lanes[6] or lanes[7] else 2 if lanes[4] or lanes[5] else 0)
    return _drop_overlaps(m)
