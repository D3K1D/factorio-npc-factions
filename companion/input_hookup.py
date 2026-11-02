"""A line's inputs by belt from what makes them, never only by hand (F-20, F-18b).

A science row placed near home took every input from a chest the engineer filled: once it went on to other goals,
nothing fed the line (oil practice, 2026-10-29: blue at 12-20 a minute, 0 by minute 37, its engines, red circuits,
sulfur and plastic all carried). Here an input port is fed the way a player feeds it: an inserter at the edge of a
machine that makes the item (standing, or a row placed earlier in the same plan) lifts it onto a belt routed to the
port. More machines join the same belt (side-loaded) until they cover what the port takes; one inserter lifts what a
machine makes, up to what an inserter moves.

    out = feed(item, rate, goal, EAST, sources=sources_of(item, world, placed, book), taken=..., bounds=...)
    out.entities   # inserters and belts (world tiles); empty when nothing could be routed
    out.rate       # what the sources joined can give (inserters and machines), items/s
    out.why        # what it connected, or why not: said, never silent
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from companion.modules import EAST, NORTH, SOUTH, WEST, Placed
from companion.ratios import Book

INSERTER = "inserter"
LONG = "long-handed-inserter"
LIFTS = (INSERTER, LONG)
INSERTER_RATE = 0.83       # items/s a basic inserter moves from a machine onto a belt
MAX_SOURCES = 4            # machines joined onto one input's belt at most
MAX_TRIES = 12             # sources tried for an input at most (nearest first)
ROUTE_LIMIT = 300          # belt tiles a feed may run at most (longer: the row is placed nearer, or by hand)
SIZES = {"assembling-machine-1": 3, "assembling-machine-2": 3, "assembling-machine-3": 3, "chemical-plant": 3,
         "oil-refinery": 5, "electric-furnace": 3, "stone-furnace": 2, "steel-furnace": 2}
POLE = "small-electric-pole"
POLE_SUPPLY = 2.5          # tiles a small pole powers round it
SUPPLY = {"small-electric-pole": 2.5, "medium-electric-pole": 3.5, "big-electric-pole": 2.0, "substation": 9.0}
END_REACH = 3              # tiles past a bus lane's last belt its end chest may stand
STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
TOWARD = {v: k for k, v in STEP.items()}


# Smelters of its own for a line whose plate no bus lane carries yet (D-19, the user: "or just have some smelters dedicated
# to it"): its defense rows, set by tier_builds round their hookup. The base's furnaces of that plate (and their blocks'
# end chests) then feed it by belt, not the hand's chest; with a bus lane of it, its lane end as any line.
import contextvars as _contextvars
DEDICATED: _contextvars.ContextVar[bool] = _contextvars.ContextVar("dedicated_smelters", default=False)


@dataclass
class Source:
    """A machine whose recipe makes the item: its tile box (x0, y0, x1, y1 inclusive) and what it makes a second; or
    a row's output port in the same plan (`start`: the tile its belt runs on to, its whole rate, no inserter), whose
    chest and inserter (`remove`) give way to the belt."""
    label: str
    box: tuple[int, int, int, int]
    rate: float
    start: tuple[int, int] | None = None
    grant: str | None = None   # (a bus lane's end: the lane the feed is granted on)
    block: tuple | None = None  # (a block's end chest: the block's box, whose machines it stands for)
    row: int = -1
    port: int = -1
    remove: list = field(default_factory=list)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2] + 1) / 2, (self.box[1] + self.box[3] + 1) / 2


@dataclass
class Fed:
    item: str
    asked: float
    entities: list[Placed] = field(default_factory=list)
    rate: float = 0.0
    sources: list[str] = field(default_factory=list)
    used: list[Source] = field(default_factory=list)
    lifts: list[Placed | None] = field(default_factory=list)   # (each used source's inserter, None for a row's belt)
    why: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.entities)

    @property
    def enough(self) -> bool:
        return self.ok and self.rate + 1e-9 >= self.asked


def machine_rate(book: Book, name: str, recipe: str, item: str) -> float:
    """What a machine running `recipe` makes of `item` a second at full speed (0 when unknown)."""
    r, m = book.recipes.get(recipe), book.machines.get(name)
    if r is None or m is None or r.energy <= 0:
        return 0.0
    return r.makes(item) * m.speed / r.energy


def _box(x: float, y: float, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0 = math.floor(x - w / 2 + 1e-6), math.floor(y - h / 2 + 1e-6)
    return x0, y0, x0 + w - 1, y0 + h - 1


def sources_of(item: str, world: Mapping[str, Any], placed: Iterable[Placed], book: Book) -> list[Source]:
    """Every machine of the engineer's force whose recipe makes `item`: standing ones in the map, and those placed by
    the plan so far (`placed`)."""
    out = []
    for e in world.get("entities") or []:
        if e.get("f") != world.get("force") or not e.get("r"):
            continue
        rate = machine_rate(book, str(e.get("n")), str(e["r"]), item)
        if rate > 0:
            w, h = int(e.get("w") or 3), int(e.get("h") or 3)
            out.append(Source(f"{e['n']}@{e['x']},{e['y']}", _box(float(e["x"]), float(e["y"]), w, h), rate))
    for p in placed:
        size = SIZES.get(p.name)
        if size and p.recipe and (rate := machine_rate(book, p.name, p.recipe, item)) > 0:
            out.append(Source(f"{p.name}@{p.x},{p.y} (planned)", _box(p.x, p.y, size, size), rate))
    return out


def _edges(box: tuple[int, int, int, int], reach: int = 1):
    """(inserter tile, its direction (toward the machine: where it picks up), the tile it drops on) round a box;
    `reach` 2: a long-handed inserter's, a tile further out (over what stands beside the box)."""
    x0, y0, x1, y1 = box
    r = reach
    for x in range(x0, x1 + 1):
        yield (x, y0 - r), SOUTH, (x, y0 - 2 * r)
        yield (x, y1 + r), NORTH, (x, y1 + 2 * r)
    for y in range(y0, y1 + 1):
        yield (x0 - r, y), EAST, (x0 - 2 * r, y)
        yield (x1 + r, y), WEST, (x1 + 2 * r, y)


def feed(item: str, rate: float, goal: tuple[int, int], goal_dir: int, *, sources: list[Source],
         taken: set[tuple[int, int]], bounds: tuple[int, int, int, int]) -> Fed:
    """Belts to `goal` (a tile; its belt turned `goal_dir`, into the port) from the nearest machines that make `item`,
    joined until they give `rate`. `taken`: tiles that cannot be used (it is not changed). Returns what was connected
    and what it gives, or why nothing was."""
    from companion.smelting import route
    out = Fed(item, rate)
    if not sources:
        out.why = f"nothing here makes {item}"
        return out
    used = set(taken) - {goal}
    gx, gy = goal
    # (a row's output port first: its whole rate on one belt; then machines, nearest first)
    order = sorted(sources, key=lambda s: (s.start is None, abs(s.centre[0] - gx) + abs(s.centre[1] - gy),
                                           s.label))[:MAX_TRIES]
    trunk: list[Placed] = []
    failed = 0
    for src in order:
        if len(out.sources) >= MAX_SOURCES or (out.sources and out.rate + 1e-9 >= rate):
            break
        joined = _join(src, trunk, goal, goal_dir, used, bounds, route)
        if joined is None:
            failed += 1
            continue
        belts, ins = joined
        trunk += belts
        laid = [ins, *belts] if ins is not None else belts
        out.entities += laid
        used |= {(math.floor(e.x), math.floor(e.y)) for e in laid}
        out.rate += src.rate if src.start is not None else min(src.rate, INSERTER_RATE)
        out.sources.append(src.label)
        out.used.append(src)
        out.lifts.append(ins)
    if not out.entities:
        out.why = f"no belt route to the {item} input from any of {len(order)} machines that make it"
    else:
        short = "" if out.enough else f", short of the {rate:.2f}/s it takes"
        out.why = (f"{item} by belt from {len(out.sources)} machine(s), {out.rate:.2f}/s{short}"
                   + (f" ({failed} had no route)" if failed else ""))
    return out


def _join(src: Source, trunk: list[Placed], goal, goal_dir, used, bounds, route):
    """An inserter at the machine's edge and belts from where it drops: to the goal (the first), or onto the belt
    already laid (side-loaded onto it, never head-on)."""
    from companion.smelting import footprint
    at = {(math.floor(b.x), math.floor(b.y)): b for b in trunk}
    if src.start is not None:   # (a row's output belt runs on: its chest and inserter give way)
        options = [(None, None, None, src.start, used - footprint(src.remove) - {src.start})]
    else:
        for name, reach in ((INSERTER, 1), (LONG, 2)):   # (the belt laid already runs past it: dropped onto it)
            for ins, way, drop in _edges(src.box, reach):
                if ins not in used and drop in at and drop != goal and at[drop].name == "transport-belt":
                    return [], Placed(name, ins[0] + 0.5, ins[1] + 0.5, way)
        # (a long-handed inserter where nothing beside it is free: over a belt or pole standing there)
        options = [(name, ins, way, drop, used | {ins}) for name, reach in ((INSERTER, 1), (LONG, 2))
                   for ins, way, drop in _edges(src.box, reach) if ins not in used and drop not in used]
    for name, ins, way, drop, free in options:
        lift = Placed(name, ins[0] + 0.5, ins[1] + 0.5, way) if ins is not None else None
        if not trunk:
            path = route(drop, goal, free, bounds=bounds, limit=ROUTE_LIMIT)
            if not path:
                continue
            path[-1].direction = goal_dir
            return path, lift
        for tile, belt in sorted(at.items(), key=lambda kv: abs(kv[0][0] - drop[0]) + abs(kv[0][1] - drop[1])):
            if tile == goal:
                continue
            path = route(drop, tile, free - {tile}, bounds=bounds, limit=ROUTE_LIMIT)
            if not path or len(path) < 2:
                continue
            path = path[:-1]   # (the belt there stands: the last of ours turns into its side)
            last = path[-1]
            if last.name != "transport-belt":
                continue
            into = TOWARD.get((tile[0] - math.floor(last.x), tile[1] - math.floor(last.y)))
            if into is None or STEP.get(into) == tuple(-v for v in STEP[belt.direction]):
                continue   # (head-on into it: no way on)
            last.direction = into
            return path, lift
    return None


def _skip(item: str, book: Book, ends: Mapping[str, list] | None = None) -> str | None:
    """Why an input is left to the hand: an item no recipe makes is mined or carried."""
    if book.recipe_for(item) is None:
        return f"{item}: no recipe makes it"
    return None


def from_bus(item: str, book: Book, lanes: Any = None) -> bool:
    """Whether `item` comes by belt from the bus only: plates and steel (smelted) do, through the lane ledger's grants
    at a lane's end (the user's picture, F-20 B: science taps the bus); never from furnaces by the side, nor belted
    across the base from the arrays. With no lane of it yet, the bus's lane for it comes first (tier_builds.lane_first)
    and the hand's chest bridges until then."""
    return (book.recipe_for(item) or SimpleNamespace(category="")).category == "smelting"


def sources_for(item: str, site: Mapping[str, Any], world: Mapping[str, Any], placed: list[Placed], book: Book,
                ends: Mapping[str, list], lanes: Any, drawn: Mapping) -> list[Source]:
    """What may feed `item`: a smelted item the bus's lane ends only; anything else the machines and output chests
    that make it (standing, or placed by this plan)."""
    if from_bus(item, book):
        bus = _ends(item, ends, lanes, drawn)
        if bus or not DEDICATED.get():
            return bus
        from companion import tier_builds
        if tier_builds.bus_carries(site, item):   # (its furnaces feed that lane: a tap there shorts it unseen, Factory)
            return bus
    # (a block's end chest stands for its machines: each producer counted once)
    chests = chest_sources(item, site, world, book)
    blocks = [c.block for c in chests if c.block]
    machines = [m for m in sources_of(item, world, placed, book)
                if not any(x0 <= m.centre[0] <= x1 and y0 <= m.centre[1] <= y1 for x0, y0, x1, y1 in blocks)]
    return machines + chests


def lane_ends(world: Mapping[str, Any], lanes: Any) -> dict[str, list[tuple[str, tuple[int, int], float]]]:
    """Per item, the bus lanes' end chests: (lane id, the chest's tile, the lane's spare items/s, by the ledger). A lane
    ends in a chest the engineer takes from (bus.END_CHEST): what comes there is what no tap took."""
    if lanes is None:
        return {}
    chests = {(math.floor(e["x"]), math.floor(e["y"])) for e in world.get("entities") or []
              if e.get("f") == world.get("force") and str(e.get("n", "")).endswith("chest")}
    out: dict[str, list] = {}
    for lane in lanes.lanes():
        tiles = lane.line.tiles
        if lane.line.kind != "bus" or not tiles or lane.headroom <= 0:
            continue
        x, y, d = tiles[-1]
        dx, dy = STEP.get(int(d), (0, 1))
        at = next(((x + dx * n, y + dy * n) for n in range(1, END_REACH + 1) if (x + dx * n, y + dy * n) in chests),
                  None)
        if at is not None:
            out.setdefault(lane.item, []).append((lane.id, at, float(lane.headroom)))
    return out


def _ends(item: str, ends: Mapping[str, list], lanes: Any, drawn: Mapping[tuple[int, int], float]) -> list[Source]:
    """The lane ends of `item` as sources: an inserter unloads the end chest onto the feed's belt, within the lane's
    spare and what the one inserter filling that chest brings (`drawn`: taken from each chest by feeds so far)."""
    out = []
    for lid, (x, y), spare in ends.get(item) or ():
        lane = lanes.lane(lid) if lanes is not None else None
        left = min(float(lane.headroom) if lane is not None else spare, INSERTER_RATE - drawn.get((x, y), 0.0))
        if left > 1e-9:
            out.append(Source(f"the {item} lane {lid}'s end", (x, y, x, y), left, grant=lid))
    return out


def poles_for(lifts: list[Placed], powered: list[tuple[float, float]], used: set[tuple[int, int]]) -> list[Placed]:
    """A small pole beside each inserter no pole powers (power.connect joins poles, it does not check what they
    power): on a free tile within reach, shared by the inserters it covers."""
    have = [tuple(p) if len(p) > 2 else (*p, POLE_SUPPLY) for p in powered]
    out = []
    for ins in lifts:
        if any(abs(ins.x - px) <= r and abs(ins.y - py) <= r for px, py, r in have):
            continue
        ix, iy = math.floor(ins.x), math.floor(ins.y)
        spot = next(((ix + dx, iy + dy) for r in (1, 2) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                     if max(abs(dx), abs(dy)) == r and (ix + dx, iy + dy) not in used), None)
        if spot is None:
            continue   # (the caller finds it unpowered: power.connect is told of none)
        pole = Placed(POLE, spot[0] + 0.5, spot[1] + 0.5)
        out.append(pole)
        used.add(spot)
        have.append((pole.x, pole.y, POLE_SUPPLY))
    return out


def unpowered(lifts: list[Placed], poles: list[tuple]) -> list[Placed]:
    """The inserters no pole's supply area covers (`poles`: (x, y) small poles, or (x, y, supply))."""
    have = [tuple(p) if len(p) > 2 else (*p, POLE_SUPPLY) for p in poles]
    return [i for i in lifts if not any(abs(i.x - px) <= r and abs(i.y - py) <= r for px, py, r in have)]


def supplies(world: Mapping[str, Any], entities: Iterable[Placed] = ()) -> list[tuple[float, float, float]]:
    """Every pole of the engineer's force with its supply reach: the map's and `entities`."""
    live = set(world.get("live_nets") or ())   # (a pole on no network a generator feeds powers nothing)
    out = [(float(e["x"]), float(e["y"]), SUPPLY.get(str(e.get("n")), POLE_SUPPLY))
           for e in world.get("entities") or [] if e.get("f") == world.get("force") and str(e.get("n")) in SUPPLY
           and (not live or e.get("net") is None or e.get("net") in live)]
    return out + [(e.x, e.y, SUPPLY[e.name]) for e in entities if e.name in SUPPLY]


@dataclass
class Hooked:
    """What hook_rows connected: per row, the ports fed by belt or feeding one (their indices), every feed's entities,
    and the hand's chests and inserters they replaced."""
    ports: dict[int, set[int]] = field(default_factory=dict)
    entities: dict[int, list[Placed]] = field(default_factory=dict)
    removed: dict[int, list[Placed]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    rates: dict[int, dict[str, float]] = field(default_factory=dict)   # row -> item -> items/s brought by belt

    def unfed_plates(self, rows: list, book: Book) -> list[str]:
        """The smelted items (plates, steel) some row's input port still takes by hand after the hookup, whatever
        stopped its belt: no lane end, a short feed, a refusal, no route or power."""
        out = []
        for k, (module, _, _) in enumerate(rows):
            if getattr(module, "feed_index", None) is not None:
                continue
            for i, p in enumerate(module.ports):
                if p.kind == "in" and (p.rate > 0 or module.name == "labs") and from_bus(p.item, book) \
                        and not self.fed(k, i) and p.item not in out:
                    out.append(p.item)
        return out

    @property
    def delivered(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for got in self.rates.values():
            for item, r in got.items():
                out[item] = out.get(item, 0.0) + r
        return out

    def fed(self, row: int, port: int) -> bool:
        return port in self.ports.get(row, ())


def _power(fed: Fed, power: tuple[list, list], used: set[tuple[int, int]]) -> list[Placed] | None:
    """Poles for a feed's inserters, joined to the network (`power`: its poles, and every pole's reach); None when
    one stays unpowered. Nothing is added to `power` here: _commit does, once the feed is made."""
    from companion.power import connect
    powered, reach = power
    lift = [e for e in fed.entities if e.name in LIFTS]
    tiles = used | {(math.floor(e.x), math.floor(e.y)) for e in fed.entities}
    own = poles_for(lift, reach, set(tiles))
    extra, problems = connect(own, powered, tiles)
    poles = own + extra
    if problems or unpowered(lift, reach + supplies({}, poles)):
        return None
    return poles


def _commit(fed: Fed, poles: list[Placed], power: tuple[list, list] | None) -> None:
    fed.entities += poles
    if power is not None:
        power[0].extend((e.x, e.y) for e in poles)
        power[1].extend(supplies({}, poles))


def _hand(part: list[Placed], tiles: set[tuple[int, int]]) -> list[Placed]:
    return [e for e in part if e.name in ("iron-chest", INSERTER, "loader") and (math.floor(e.x), math.floor(e.y)) in tiles]


def _outputs(rows) -> list[Source]:
    """Each row's output port as a source: its belt runs on from where its chest and inserter stood."""
    from companion.smelting import footprint
    out = []
    for k, (module, part, (ox, oy)) in enumerate(rows):
        if getattr(module, "feed_index", None) is not None:
            continue
        own = footprint([e for e in part if e.name not in ("iron-chest", INSERTER, "loader")])
        for i, p in enumerate(module.ports):
            if p.kind != "out" or p.rate <= 0:
                continue
            step = STEP.get(p.direction, (-1, 0))
            port = (p.x + ox, p.y + oy)
            start = (port[0] + step[0], port[1] + step[1]) if port in own else port
            remove = _hand(part, {(port[0] + step[0] * n, port[1] + step[1] * n) for n in range(4)})
            out.append(Source(f"{module.name}'s {p.item} output", (*start, *start), float(p.rate), start=start,
                              row=k, port=i, remove=remove))
    return out


def hook_rows(rows: list[tuple[Any, list[Placed], tuple[int, int]]], world: Mapping[str, Any], book: Book,
              taken: set[tuple[int, int]], bounds: tuple[int, int, int, int], *, lanes: Any = None,
              site: Mapping[str, Any] | None = None, power: tuple[list, list] | None = None,
              avoid: set[tuple[int, int]] | None = None) -> Hooked:
    """Every input port of the placed rows ((module, its placed entities, its offset)) fed by belt where something
    reaches it: another of these rows' output (its belt run on), or machines that make the item (standing, or in
    these rows). The chests and inserters (or loaders) the hand would have used give way. Ports that share a tile with
    another item's are left as they are (one belt carries one item here). Smelted items come from the bus's lane
    ends (`lanes`: the lane ledger, companion/lane_ledger.py; each feed granted on its lane, within its spare).
    `power`: (the network's poles, every pole's (x, y, supply)): each feed's inserters get poles joined to it, or the
    feed is not made. Returns what was connected, and a note for each port left to the hand."""
    from companion.smelting import footprint
    out = Hooked()
    placed = [e for _, part, _ in rows for e in part]
    # (the rows' buildings, not the room kept round them: tightly placed rows leave a feed no way through else)
    used = set(taken) | footprint(placed)
    outputs = _outputs(rows)
    ends = lane_ends(world, lanes)
    drawn: dict[tuple[int, int], float] = {}   # (from each lane end's chest so far)
    spent: dict[str, float] = {}   # (from each machine or chest so far, D-19's own smelters: no furnace given twice)
    for k, (module, part, (ox, oy)) in enumerate(rows):
        if getattr(module, "feed_index", None) is not None:   # (a library block from its own chests)
            continue
        # (labs' pack ports carry no rate: any feed serves them)
        ins = [(i, p) for i, p in enumerate(module.ports) if p.kind == "in" and (p.rate > 0 or module.name == "labs")]
        tiles: dict[tuple[int, int], set[str]] = {}
        for _, p in ins:
            tiles.setdefault((p.x, p.y), set()).add(p.item)
        for i, p in ins:
            why = _skip(p.item, book, ends)
            shared = len(tiles[(p.x, p.y)]) > 1
            lane = getattr(p, "lane", None)
            if why is None and shared and (len(tiles[(p.x, p.y)]) > 2 or lane not in ("left", "right")):
                why = f"{p.item}: its entry takes {len(tiles[(p.x, p.y)])} items on one belt, no lane each"
            if why is not None:
                out.notes.append(f"{module.name}: {why}, by hand")
                continue
            step = STEP.get(p.direction, (1, 0))
            port = (p.x + ox, p.y + oy)
            goal, into = (port[0] - step[0], port[1] - step[1]), p.direction
            if shared:   # (two items on one entry: each side-loaded onto its own lane, from that side; never mixed)
                side = (step[1], -step[0]) if lane == "left" else (-step[1], step[0])
                goal, into = (port[0] + side[0], port[1] + side[1]), TOWARD[(-side[0], -side[1])]
            # (the hand's chest, inserter or loader in front of it: out, its tiles free for the belt)
            gone = [] if shared else _hand(part, {(port[0] - step[0] * n, port[1] - step[1] * n) for n in (1, 2, 3)})
            free = used - footprint(gone)
            others = [e for e in placed if e not in part]
            mine = [o for o in outputs if o.row != k and o.port not in out.ports.get(o.row, set())
                    and rows[o.row][0].ports[o.port].item == p.item and not from_bus(p.item, book)]
            sources = mine + sources_for(p.item, site or {}, world, others, book, ends, lanes, drawn)
            if DEDICATED.get():   # (what earlier ports took of each furnace is not there for this one: Codex's review)
                from dataclasses import replace as _replace
                sources = [_replace(s, rate=s.rate - spent.get(s.label, 0.0)) if s.grant is None else s
                           for s in sources if s.grant is not None or s.rate - spent.get(s.label, 0.0) > 1e-6]
            if not sources and from_bus(p.item, book) and not DEDICATED.get():
                out.notes.append(f"{module.name}: no bus lane carries {p.item} yet, by hand until its lane stands")
                continue
            # (round the ground the factory plan keeps for its sections first; across it only when there is no way
            # round, said)
            fed = feed(p.item, p.rate, goal, into, sources=sources, taken=free | (avoid or set()), bounds=bounds)
            if not fed.enough and avoid:
                fed = feed(p.item, p.rate, goal, into, sources=sources, taken=free, bounds=bounds)
                crossed = sum(1 for e in fed.entities if (math.floor(e.x), math.floor(e.y)) in avoid)
                if fed.enough and crossed:
                    out.notes.append(f"{module.name}: the {p.item} feed crosses {crossed} tiles of ground kept for "
                                     f"the factory plan's sections")
            if not fed.enough:   # (one that brings too little leaves the hand's chest: it would starve the line)
                out.notes.append(f"{module.name}: {fed.why}, by hand")
                continue
            poles = _power(fed, power, used) if power is not None else []
            if poles is None:
                out.notes.append(f"{module.name}: no power reaches the {p.item} feed's inserters, by hand")
                continue
            key = f"{module.name}@{port[0]},{port[1]}"
            refused = _grant(fed, key, p.rate, lanes, drawn)
            if refused:
                out.notes.append(f"{module.name}: {p.item} from the bus refused ({refused}), by hand")
                continue
            _commit(fed, poles, power)
            left = p.rate
            for src in fed.used:   # (what this port takes of each, the first first: as _grant counts it)
                take = min(src.rate, max(left, 0.0))
                left -= take
                if src.grant is None:
                    spent[src.label] = spent.get(src.label, 0.0) + take
            for e in gone:
                part.remove(e)
            out.removed.setdefault(k, []).extend(gone)
            out.ports.setdefault(k, set()).add(i)
            for src in fed.used:
                if src.start is None:
                    continue
                for e in src.remove:
                    if e in rows[src.row][1]:
                        rows[src.row][1].remove(e)
                out.ports.setdefault(src.row, set()).add(src.port)
                used -= footprint(src.remove)
            out.entities.setdefault(k, []).extend(fed.entities)
            used |= {(math.floor(e.x), math.floor(e.y)) for e in fed.entities}
            got = out.rates.setdefault(k, {})
            got[p.item] = got.get(p.item, 0.0) + fed.rate
    return out


def _grant(fed: Fed, key: str, asked: float, lanes: Any, drawn: dict) -> str | None:
    """Each lane end a feed takes from, granted on its lane by the ledger (held for the rest of the plan) and told to
    the game as a claim on the lane (a tap op on the feed's inserter); None when all are granted, else the refusal.
    What is asked of each: the port's draw, from the first sources first."""
    left = asked
    wants = []
    for src, lift in zip(fed.used, fed.lifts):
        take = min(src.rate, max(left, 0.0)) if asked > 0 else min(src.rate, 0.1)   # (labs: any)
        left -= take
        if src.grant is not None:
            wants.append((src, lift, take))
    if not wants or lanes is None:
        return None
    for src, _, take in wants:   # (all or none: checked before any is held)
        lane = lanes.lane(src.grant)
        if lane is None or lane.headroom + 1e-9 < take:
            return f"the {src.grant} lane has {0 if lane is None else lane.headroom:.2f}/s spare, {take:.2f}/s asked"
    held = []
    for src, lift, take in wants:
        g = lanes.grant(take, key, line=src.grant)
        if not g.ok:   # (its group short, say: those held for this feed given back)
            for lane, t in held:
                lane.pending[key] = lane.pending.get(key, 0.0) - t
                if lane.pending[key] <= 1e-9:
                    lane.pending.pop(key, None)
            return g.why
        held.append((lanes.lane(src.grant), take))
    for src, lift, take in wants:
        x, y = src.box[0], src.box[1]
        drawn[(x, y)] = drawn.get((x, y), 0.0) + take
        if lift is not None:
            lift.line_op = {"op": "tap", "line": src.grant, "key": key, "rate": round(take, 4)}
    return None


def sink(port: Any, own: set[tuple[int, int]] = frozenset(), ox: int = 0, oy: int = 0) -> list[Placed]:
    """Where an output port's belt ends: an inserter on the port's tile lifting from the belt's last tile into a chest
    beyond it (the engineer takes what is not fed on: a belt-fed block's product), and a pole beside the inserter on
    whichever side is free of the block's own tiles (`own`)."""
    step = STEP.get(port.direction, (-1, 0))
    x, y = port.x + ox, port.y + oy
    chest = (x + step[0], y + step[1])
    side = next(((x + a, y + b) for r in (1, 2) for a in range(-r, r + 1) for b in range(-r, r + 1)
                 if max(abs(a), abs(b)) == r and (x + a, y + b) not in own and (x + a, y + b) != chest
                 and (x + a, y + b) != (x - step[0], y - step[1])), None)
    return [Placed(INSERTER, x + 0.5, y + 0.5, TOWARD[(-step[0], -step[1])]),
            Placed("iron-chest", chest[0] + 0.5, chest[1] + 0.5),
            *([Placed(POLE, side[0] + 0.5, side[1] + 0.5)] if side else [])]


def into_chest(item: str, rate: float, chest: tuple[int, int], *, sources: list[Source], taken: set[tuple[int, int]],
               bounds: tuple[int, int, int, int]) -> Fed:
    """A standing line's hand chest fed by belt (the retrofit): an inserter beside the chest lifts from the end of a
    belt brought from what makes the item. The chest stays; filled so, it is no longer the hand's (Research's
    hand_fed reading: a chest an inserter fills from a belt is fed)."""
    cx, cy = chest
    best = Fed(item, rate, why=f"no free side of the {item} chest at {cx},{cy} for an inserter and a belt end")
    for way, (dx, dy) in STEP.items():
        ins, end = (cx + dx, cy + dy), (cx + 2 * dx, cy + 2 * dy)
        if ins in taken or end in taken:
            continue
        fed = feed(item, rate, end, TOWARD[(-dx, -dy)], sources=sources, taken=taken | {ins}, bounds=bounds)
        if fed.enough:
            fed.entities.append(Placed(INSERTER, ins[0] + 0.5, ins[1] + 0.5, way))   # (it picks from the belt's end)
            return fed
        best = fed if fed.ok or not best.ok else best
    return best


_REPLAYED: dict = {}   # (npc, the saved bus) -> the bus's lanes laid out again, as line ops


def bus_book(site: Mapping[str, Any], world: Mapping[str, Any]) -> Any:
    """The lane ledger's book of the site's lanes; a bus built before its lanes were recorded (no bus lane in the
    game's record) has them laid out again from its saved plan (bus_planner.replay: its built stages' lanes, counted
    by design), so its lane ends can feed lines too."""
    import json
    from companion import belt_lines, lane_ledger
    book = lane_ledger.LaneBook.from_site(site, world)
    saved = site.get("base_plan") or {}
    done = saved.get("done") or {}
    lanes_built = any(str(d).startswith(("bus-", "smelting-", "feed-", "lane-")) for d in done)
    if not lanes_built or any(lane.line.kind == "bus" for lane in book.lanes()):
        return book   # (no lane built yet, its power only: nothing to lay out again, a costly replay spared)
    key = (site.get("npc_id"), json.dumps(saved, sort_keys=True, default=str))
    ops = _REPLAYED.get(key)
    if ops is None:
        from companion import bus_planner
        try:
            layout = bus_planner.replay(site, world)
        except Exception:   # (a bus that no longer lays out here: its lanes stay unknown, plates by hand)
            layout = None
        if layout is None:
            return book   # (tried again next time: the map may have changed, not the saved bus)
        built = [e for s in getattr(layout, "stages", None) or () if s.name in done for e in s.entities]
        ops = [{**op, "ok": True} for op in belt_lines.bus_lanes(layout.bus, built)]
        _REPLAYED.clear()   # (one bus at a time kept)
        _REPLAYED[key] = ops
    if not ops:
        return book
    return lane_ledger.LaneBook([lane.line for lane in book.lanes()] + belt_lines.lines({"belt_lines": ops}),
                                lane_ledger._starved(site))


def chest_sources(item: str, site: Mapping[str, Any], world: Mapping[str, Any], book: Book) -> list[Source]:
    """Output chests holding `item`: a chest an inserter fills from a machine whose recipe makes it, or from a belt in
    a block that makes it and nothing else (a smelting array's end chest). An inserter beside it unloads it onto the
    feed's belt, at most what the block makes (one inserter's worth: each chest one source)."""
    force = world.get("force")
    mine = [e for e in world.get("entities") or [] if e.get("f") == force]
    at: dict[tuple[int, int], Mapping[str, Any]] = {}
    for e in mine:
        w, h = int(e.get("w") or 1), int(e.get("h") or 1)
        x0, y0, x1, y1 = _box(float(e["x"]), float(e["y"]), w, h)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                at[(x, y)] = e
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    makes: list[tuple[tuple[float, float, float, float], float]] = []   # (a single-product block's box, its rate)
    for b in site.get("base") or ():
        if not isinstance(b, Mapping) or not isinstance(b.get("box"), (list, tuple)):
            continue
        recipes = [book.recipes[r] for r in b.get("recipes") or () if r in book.recipes]
        # (its end product: what it makes that none of its own machines take, cable for its circuits aside)
        final = {n for r in recipes for n, _ in r.products} - {n for r in recipes for n, _ in r.ingredients}
        if final == {item}:
            x0, y0, x1, y1 = b["box"]
            rate = float(((b.get("capacity") or {}).get("makes") or {}).get(item) or INSERTER_RATE)
            makes.append(((x0 + ox, y0 + oy, x1 + ox, y1 + oy), rate))
    out = []
    for ins in mine:
        if ins.get("t") != "inserter" or ins.get("d") is None:
            continue
        ix, iy = math.floor(float(ins["x"])), math.floor(float(ins["y"]))
        step = STEP.get(int(ins["d"]))
        if step is None:
            continue
        pick, drop = at.get((ix + step[0], iy + step[1])), at.get((ix - step[0], iy - step[1]))
        if drop is None or not str(drop.get("n", "")).endswith("chest") or pick is None:
            continue
        if pick.get("t") != "transport-belt":
            continue   # (a chest a machine fills by inserter: that machine is a source already)
        cx, cy = float(drop["x"]), float(drop["y"])
        box, rate = next(((b, r) for b, r in makes if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]), (None, 0.0))
        if rate > 0:
            c = (math.floor(cx), math.floor(cy))
            out.append(Source(f"the {item} chest at {c[0]},{c[1]}", (*c, *c), rate, block=box))
    # (a block's make shared among its end chests: two chests of one 0.4/s block are 0.2/s each, not 0.4)
    for s in out:
        share = sum(1 for t in out if t.block == s.block)
        s.rate = min(s.rate / share, INSERTER_RATE)
    return out


def soft_reserved(world: Mapping[str, Any]) -> set[tuple[int, int]]:
    """The ground the factory plan keeps for its sections (not the bus's planned ground): a feed goes round it first."""
    from companion import land
    return land.reserved_tiles({k: v for k, v in world.items() if k not in ("reserved_hard", "_reserved_tiles")})


def feed_ground(world: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Tiles a feed's belt may not use: the map's buildings and what they reach, ore, and the bus's planned ground;
    not the ground the factory plan keeps for its sections (`reserved`): the lines standing there send their output
    out across it, as a player's would."""
    from companion.base_plan import all_ore, blocked_tiles
    soft = {k: v for k, v in world.items() if k not in ("reserved", "_reserved_tiles")}
    return blocked_tiles(soft) | all_ore(world)


def inputs_anchor(module: Any, site: Mapping[str, Any], world: Mapping[str, Any], book: Book, placed: list[Placed],
                  home: tuple[int, int]) -> tuple[int, int] | None:
    """Where a row's inputs come from: the rate-weighted middle of each input's nearest source to home (a machine or
    output chest that makes it, a row placed before it, a bus lane's end), or None when none is known. Rows are
    placed by it first, so their feeds stay short (a row by home fed from arrays 150 tiles off took the whole run to
    belt, oil practice 2026-10-29)."""
    hx, hy = home
    lanes = None
    total, sx, sy = 0.0, 0.0, 0.0
    for p in getattr(module, "ports", ()):
        if p.kind != "in" or book.recipe_for(p.item) is None:
            continue
        if from_bus(p.item, book):   # (plates: the bus's lane ends: the line by the bus)
            lanes = lanes if lanes is not None else bus_book(site, world)
            found = _ends(p.item, lane_ends(world, lanes), lanes, {})
        else:
            found = sources_of(p.item, world, placed, book) + chest_sources(p.item, site, world, book)
        if not found:
            continue
        s = min(found, key=lambda q: (abs(q.centre[0] - hx) + abs(q.centre[1] - hy), q.label))
        w = max(float(p.rate), 0.05)
        total, sx, sy = total + w, sx + w * s.centre[0], sy + w * s.centre[1]
    if total <= 0:
        return None
    return int(sx / total), int(sy / total)
