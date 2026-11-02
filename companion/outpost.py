"""Smelting outposts: a resource expansion by an ore patch, where the bus has no room for one.

The bus lays its expansions along its column. On a crowded plot cliffs or buildings stand there (run 33's, 2026-10-25),
and the plates the base grows on would wait. As players do, the smelting then goes where the ore and the room are:
plate modules (24 stone furnaces each, growth.plate_module) on free ground beside a patch, each fed by its own ore field
on that patch and its own coal belt, their plates into a chest store the engineer takes its building plates from (a
belt on to the bus is a later step). The spot is searched round the patches the map holds, nearest home first, and
kept once found (the bus's sites), so every replay of the bus lays the outpost out the same.

    stages, notes = plan(dump, "outpost-iron-plate-2-15", taken, blocked, found, coal_line, Stage)
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from companion import doorways, growth
from companion.modules import EAST, NORTH, Placed
from companion.smelting import footprint, ore_tiles, outlets

# What each product is smelted from, and how much of it a piece takes (the bus's plates, and bricks)
ORES = {**growth.PLATES, "stone-brick": "stone"}
RATIO = {"stone-brick": 2.0}
STORE = "iron-chest"
STORE_SLOTS = 8        # the store's bar: 800 plates for the engineer, then the line backs up into the furnaces
GAP = 2                # tiles between an outpost's modules
CLEAR = 3              # tiles kept between the outpost and its patch (the field's drills and belt)
REACH = (0, 8, 16, 24, 32, 40)   # tiles farther out from the patch, tried in turn
STEP = 8               # tiles between two spots tried along a side
PATCHES = 4            # patches tried, nearest home first
TRIES = 6              # spots by one patch whose feeds are worked out at most (each a few belt searches)
# Of the spots where it all fits, the one with the fewest belts (the user, 2026-10-25): the first that fitted took a
# 12-furnace electric iron block to 428 buildings, 334 of them belts carried by hand from far ore and coal (run 41).
BEST_OF = 4            # spots that fit weighed against each other at most, the likeliest few belts first (_ranked)
# An outpost beyond the team's wall holds its own (the user, 2026-10-25: outposts in this biter world need heavy
# defenses, thick walls and many turrets, laser turrets where they can be powered: no refilling by hand).
RING_MARGIN = 6        # tiles between what an outpost holds and its wall: a gap, a pole row, its turrets, a gap
RING_LAYERS = 2        # it starts two walls thick (the plot's wall gets thicker where it is hit, up to four)
RING_STEP = 20         # tiles between turret pairs along its wall
TURRET_IN = 3          # a turret's near side this many tiles inside the wall's inner layer
RING_REACH = 48        # an outpost's feed drills this near its furnaces stand inside its wall (a coal field far off
                       # keeps its belt in through a gap)
RING_WALLS = 100       # wall pieces a stage of its wall (a stack): the wall goes up as walls are made, never holding
                       # the plates up (bus_planner.walls_first)
DOOR = 4               # its door: gates side by side, a tank passes (as the plot's wall's, perimeter.DOOR)
DOOR_CLEAR = 2         # tiles either side of the door kept free inside: its way in
NEST_CLEAR = 48        # tiles kept between a new outpost and the nearest nest or worm
OWN_KINDS = ("wall", "gate", "ammo-turret", "electric-turret", "fluid-turret", "electric-pole")


def is_outpost(goal: str) -> bool:
    return str(goal).startswith("outpost-")


def name(plate: str, index: int, rate: float = growth.BELT) -> str:
    """The goal of outpost `index`: `rate` items/s more of `plate` (numbered with the bus's expansions)."""
    return f"outpost-{plate}-{index}-{rate:g}"


def parse(goal: str) -> tuple[str, int, float]:
    rest, _, rate = goal[len("outpost-"):].rpartition("-")
    plate, _, index = rest.rpartition("-")
    return plate, int(index), float(rate)


def tag(goal: str) -> str:
    return f"-x{parse(goal)[1]}"


def modules(goal: str) -> list:
    """Its plate modules: enough for its rate, a full lane's furnaces each, the last only what is left."""
    plate, index, rate = parse(goal)
    per = growth.LANE_FURNACES * growth.FURNACE_RATE["stone-furnace"]
    count = max(1, math.ceil(rate / per - 1e-9))
    out = [growth.plate_module(ORES[plate], plate, min(per, rate - k * per) if rate > k * per else per,
                               RATIO.get(plate, 1.0)) for k in range(count)]
    for k, m in enumerate(out, start=1):
        m.name = f"smelt-{plate}-x{index}" + (f"-{k}" if k > 1 else "")
    return out


def _laid(mods: list, x: int, y: int) -> tuple[list[Placed], list[tuple[Any, int, int]]]:
    """The modules stacked from (x, y) southward, each with its store at its output (an inserter lifting the plates
    off the belt's end into a limited chest, a pole for the inserter): the buildings, and where each module is."""
    out, at = [], []
    for k, m in enumerate(mods):
        mx, my = x, y + k * (m.height + GAP)
        at.append((m, mx, my))
        out += [Placed(e.name, e.x + mx, e.y + my, e.direction, e.recipe, e.kind, e.bar) for e in m.entities]
        port = next(p for p in m.ports if p.kind == "out")
        px, py = mx + port.x, my + port.y
        out += [Placed("inserter", px - 0.5, py + 0.5, EAST),                       # picks the belt's end, east of it
                Placed(STORE, px - 1.5, py + 0.5, EAST, bar=STORE_SLOTS),
                Placed("small-electric-pole", px - 0.5, py - 0.5)]
    return out, at


def _spots(dump: Mapping[str, Any], ore: str, width: int, height: int) -> list[tuple[int, int, tuple]]:
    """Corners to try for a block `width` by `height`, with the patch each is beside: round each patch of `ore`,
    nearest home first, out to REACH."""
    from companion import outline
    home = (float((dump.get("home") or {}).get("x") or 0), float((dump.get("home") or {}).get("y") or 0))
    out = []
    for p in [p for p in outline.survey_patches(dump, home) if p.resource == ore][:PATCHES]:
        x0, y0, x1, y1 = p.box
        for d in REACH:
            for y in range(y0 - height, y1 + 1, STEP):
                out += [(x0 - CLEAR - width - d, y, p.box), (x1 + CLEAR + d, y, p.box)]
            for x in range(x0 - width, x1 + 1, STEP):
                out += [(x, y0 - CLEAR - height - d, p.box), (x, y1 + CLEAR + d, p.box)]
    return out


def _ranked(spots: list, mods: list, dump: Mapping[str, Any], ore: str) -> list:
    """The spots, those likely to need the fewest belts first: the way from its first module's coal entry to the
    nearest coal, and from its ore entry to its patch (in patch order, the first four that fitted missed one with a
    third of the belts, run 33's map, 2026-10-25)."""
    if not mods:
        return spots
    ports = {p.item: p for p in mods[0].ports if p.kind == "ext"}
    coal = sorted(ore_tiles(dump.get("resources") or {}, "coal"))[::4]   # (every fourth tile: near enough)
    if "coal" not in ports or ore not in ports or not coal:
        return spots

    def estimate(spot):
        x, y, box = spot
        cx, cy = x + ports["coal"].x, y + ports["coal"].y
        ox, oy = x + ports[ore].x, y + ports[ore].y
        to_coal = min(abs(cx - tx) + abs(cy - ty) for tx, ty in coal)
        to_ore = 0 if box is None else max(box[0] - ox, 0, ox - box[2]) + max(box[1] - oy, 0, oy - box[3])
        return to_coal + to_ore
    # (within each patch, nearest home first as before: ranked across patches, far patches' spots by the coal came
    # first and failed, three times the searches before one fitted)
    order = {}
    for _, _, box in spots:
        order.setdefault(box, len(order))
    return sorted(spots, key=lambda spot: (order[spot[2]], estimate(spot)))


def _clash(placed: list[Placed]) -> str | None:
    """Two of its buildings on one tile, as "electric-mining-drill over transport-belt at (x, y)"; else None. The game
    keeps the stronger of two and drops the other: a coal belt under an ore field's drills lost six belts and the
    furnaces never got coal (run 45, 2026-10-26)."""
    at: dict[tuple[int, int], Placed] = {}
    for e in placed:
        for tile in footprint([e]):
            other = at.get(tile)
            if other is not None and other is not e:
                return f"{e.name} over {other.name} at {tile}"
            at[tile] = e
    return None


def needs_ring(dump: Mapping[str, Any], box: tuple[int, int, int, int]) -> bool:
    """Whether an outpost holding `box` needs a wall of its own: it stands (in part) beyond the team area, which the
    team's wall ring holds, or there is no team world."""
    team = dump.get("team") if isinstance(dump.get("team"), Mapping) else None
    area = team.get("area") if team else None
    if not (isinstance(area, (list, tuple)) and len(area) == 4):
        return True
    x0, y0, x1, y1 = box
    return not (area[0] <= x0 and area[1] <= y0 and x1 <= area[2] and y1 <= area[3])


def _ring_turrets(kit: Mapping[str, Any] | None) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """(the turret for its wall, the one by its door): an electric turret where it can make one, the door's a gun
    turret then (a mix: the door holds when the power fails), else gun turrets both."""
    turrets = [t for t in (kit or {}).get("turrets") or [] if isinstance(t, Mapping)]
    electric = next((t for t in turrets if t.get("kind") == "electric"), None)
    ammo = next((t for t in turrets if t.get("kind") == "ammo"), None)
    return (electric or ammo), (ammo or electric)


def own_at(dump: Mapping[str, Any]) -> dict[tuple[int, int], str]:
    """The tiles the engineer's walls, gates, turrets and poles stand on, and what stands there: a ring laid out
    again over its own built pieces finds them in their places (what stands is otherwise ground taken)."""
    out = {}
    for e in dump.get("entities", []) or []:
        if e.get("f") != dump.get("force") or e.get("t") not in OWN_KINDS:
            continue
        w, h = int(e.get("w") or 1), int(e.get("h") or 1)
        tx, ty = math.floor(float(e["x"]) - w / 2 + 0.01), math.floor(float(e["y"]) - h / 2 + 0.01)
        for i in range(w):
            for j in range(h):
                out[(tx + i, ty + j)] = str(e.get("n"))
    return out


def ring(box: tuple[int, int, int, int], home: tuple[float, float], kit: Mapping[str, Any] | None, taken: set,
         powered: list, standing: Mapping[tuple[int, int], str] | None = None,
         area: tuple | None = None) -> tuple[list[Placed], list[Placed], list[dict], list[str], bool]:
    """An outpost's defense round `box` (what it holds, end exclusive): RING_LAYERS walls RING_MARGIN out, a door
    DOOR gates wide through them on the side facing home, turret pairs inside every RING_STEP tiles (electric ones
    first, with a pole each and a line to the power network), and a pair either side of the door, one of them gun
    turrets where it has electric ones too (the door holds when the power fails). What `taken` holds keeps its gap
    (belts and pipes crossing), save its own pieces standing where they go (`standing`, from own_at). Returns (the
    guard: turrets, poles and line; the wall: gates first; the guard's ammo as {x, y, item, slot}; notes; whether
    the guard's line reaches the network)."""
    from companion.power import connect
    walls = list((kit or {}).get("walls") or [])
    gates = list((kit or {}).get("gates") or [])
    wall, gate = (walls[0] if walls else None), (gates[0] if gates else None)
    turret, door_turret = _ring_turrets(kit)
    ammo = {str(k): list(v) for k, v in ((kit or {}).get("ammo") or {}).items()}
    standing = standing or {}
    laid: set = set()
    notes: list[str] = []

    def free(tiles, name: str | None = None) -> bool:
        return all(t not in laid and (t not in taken or (name is not None and standing.get(t) == name))
                   and (area is None or area[0] <= t[0] <= area[2] and area[1] <= t[1] <= area[3]) for t in tiles)

    x0, y0, x1, y1 = box[0] - RING_MARGIN, box[1] - RING_MARGIN, box[2] + RING_MARGIN, box[3] + RING_MARGIN
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = home[0] - cx, home[1] - cy
    side = ("east" if dx > 0 else "west") if abs(dx) >= abs(dy) else ("south" if dy > 0 else "north")
    across = side in ("north", "south")   # (its door runs along x)
    mid = round(cx if across else cy)
    door = set(range(mid - DOOR // 2, mid - DOOR // 2 + DOOR))
    gate_out, wall_out = [], []
    for k in range(RING_LAYERS):
        a0, b0, a1, b1 = x0 - k, y0 - k, x1 + k, y1 + k
        tiles = [(x, b0, "north") for x in range(a0, a1)] + [(x, b1 - 1, "south") for x in range(a0, a1)] + \
            [(a0, y, "west") for y in range(b0 + 1, b1 - 1)] + [(a1 - 1, y, "east") for y in range(b0 + 1, b1 - 1)]
        for x, y, edge in tiles:
            if edge == side and (x if across else y) in door:
                if gate and free({(x, y)}, gate):
                    # (a gate across a line running east-west faces east, one across a north-south line north)
                    gate_out.append(Placed(gate, x + 0.5, y + 0.5, EAST if across else NORTH))
                laid.add((x, y))   # (no gate yet: the door stays open)
            elif wall and free({(x, y)}, wall):
                wall_out.append(Placed(wall, x + 0.5, y + 0.5))
                laid.add((x, y))
    # its way in kept free of turrets and poles, the wall's inner layer to what it holds
    for a in range(min(door) - DOOR_CLEAR, max(door) + DOOR_CLEAR + 1):
        for o in range(1, RING_MARGIN):
            laid.add({"north": (a, y0 + o), "south": (a, y1 - 1 - o), "west": (x0 + o, a), "east": (x1 - 1 - o, a)}[side])
    guard: list[Placed] = []
    supplies: list[dict] = []
    poles: list[Placed] = []
    if turret is None:
        notes.append("the outpost's wall has no turrets: it can make none yet")
        return guard, gate_out + wall_out, supplies, notes, True

    def pair(t: Mapping[str, Any], ax: int, ay: int, horizontal: bool) -> bool:
        """Two turrets side by side from (ax, ay), a tile apart, the pole for electric ones in the gap's line."""
        w, h = int(t.get("w") or 2), int(t.get("h") or 2)
        spots = [(ax + (i * (w + 1) if horizontal else 0), ay + (0 if horizontal else i * (h + 1))) for i in range(2)]
        placed = [Placed(t["name"], sx + w / 2, sy + h / 2) for sx, sy in spots]
        electric = t.get("kind") == "electric"
        pole_at = (ax + w, ay - 1) if horizontal else (ax - 1, ay + h)
        if not free(footprint(placed), t["name"]) or electric and not free({pole_at}, "small-electric-pole"):
            return False
        laid.update(footprint(placed))
        guard.extend(placed)
        if electric:
            pole = Placed("small-electric-pole", pole_at[0] + 0.5, pole_at[1] + 0.5)
            laid.add(pole_at)
            guard.append(pole)
            poles.append(pole)
        else:
            item = next((ammo[c][0] for c in t.get("ammo_categories") or [] if ammo.get(c)), "firearm-magazine")
            supplies.extend({"x": e.x, "y": e.y, "item": item, "slot": "ammo"} for e in placed)
        return True

    north, south, west, east = y0 + TURRET_IN, y1 - 1 - TURRET_IN - 1, x0 + TURRET_IN, x1 - 1 - TURRET_IN - 1
    # the door's pairs first, either side of its way in, then pairs round the wall
    if across:
        row = north if side == "north" else south
        pair(door_turret, min(door) - DOOR_CLEAR - 6, row, True)
        pair(turret, max(door) + DOOR_CLEAR + 1, row, True)
    else:
        col = west if side == "west" else east
        pair(door_turret, col, min(door) - DOOR_CLEAR - 6, False)
        pair(turret, col, max(door) + DOOR_CLEAR + 1, False)
    for x in range(west, east - 3, RING_STEP):
        pair(turret, x, north, True)
        pair(turret, x, south, True)
    for y in range(north + RING_STEP // 2, south - 3, RING_STEP):
        pair(turret, west, y, False)
        pair(turret, east, y, False)
    joined = True
    if poles:
        extra, problems = connect(poles, powered, set(taken) | laid)
        guard += extra
        if problems:
            joined = False
            notes.append(f"the outpost's turrets have no power line to the network ({problems[0]})")
    return guard, gate_out + wall_out, supplies, notes, joined


def _held(laid: list[Placed], feeds: list) -> tuple[int, int, int, int]:
    """What an outpost's wall goes round (end exclusive): its furnaces and stores, and its feeds' drills within
    RING_REACH of them."""
    tiles = footprint(laid)
    bx0, by0 = min(q[0] for q in tiles), min(q[1] for q in tiles)
    bx1, by1 = max(q[0] for q in tiles) + 1, max(q[1] for q in tiles) + 1
    near = [e for _, f in feeds for e in f if e.name == "electric-mining-drill"
            and bx0 - RING_REACH <= e.x <= bx1 + RING_REACH and by0 - RING_REACH <= e.y <= by1 + RING_REACH]
    tiles |= footprint(near)
    return (min(q[0] for q in tiles), min(q[1] for q in tiles),
            max(q[0] for q in tiles) + 1, max(q[1] for q in tiles) + 1)


def _nest_near(dump: Mapping[str, Any], tiles: set, nests: list) -> bool:
    if not nests or not tiles:
        return False
    bx0, by0 = min(q[0] for q in tiles), min(q[1] for q in tiles)
    bx1, by1 = max(q[0] for q in tiles), max(q[1] for q in tiles)
    return any(math.hypot(max(bx0 - nx, 0, nx - bx1), max(by0 - ny, 0, ny - by1)) < NEST_CLEAR for nx, ny in nests)


def plan(dump: Mapping[str, Any], goal: str, taken: set, blocked: set, found: dict, coal_line: Callable,
         Stage: Any, built: set | None = None, kit: Mapping[str, Any] | None = None) -> tuple[list, list[str]]:
    """The outpost's stages: beyond the team area its guard first (outpost-guard-<plate>-x<n>: turrets, of `kit`,
    companion/perimeter.arms), each module's feeds (supply-<plate>-x<n>[-k]), its furnaces and store
    (outpost-smelt-<plate>-x<n>), and then its wall (outpost-wall-<plate>-<n>-<k>, out of the expansion's
    section: built as walls are made), on the first spot round a patch, clear of nests, where they all fit and
    reach the power network; else none, and why. `taken`: tiles no new building may use (the bus's, ore, water,
    what stands); `built`: what stands or is planned (no ore, which drills stand on), `blocked` by default."""
    built0 = set(blocked if built is None else built)
    from companion.power import connect
    plate, index, rate = parse(goal)
    ore, t = ORES[plate], tag(goal)
    label = f"the {plate} expansion {index}:"
    mods = modules(goal)
    width = max(m.width for m in mods) + 3
    height = sum(m.height for m in mods) + GAP * (len(mods) - 1)
    key = f"outpost-{plate}{t}"
    spots = []
    if found.get(key):
        try:
            spots = [(*(int(v) for v in str(found[key]).split(",")[:2]), None)]
        except ValueError:
            spots = []
    spots = spots or _ranked(_spots(dump, ore, width, height), mods, dump, ore)
    x0, y0, x1, y1 = dump.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6)
    ores = {(tx, ty) for rows in (dump.get("resources") or {}).values() for ty, a, b in rows
            for tx in range(int(a), int(b) + 1)}
    forbidden = taken | ores   # (no building on ore: the drills will want it)
    patch = ore_tiles(dump["resources"], ore)
    own = growth.own_belts_of(dump)
    from companion.power import live_poles
    powered = live_poles(dump)
    nests = [(float(e["x"]), float(e["y"])) for e in dump.get("entities", []) if e.get("f") == "enemy"
             and e.get("t") in ("unit-spawner", "turret")]
    home = (float((dump.get("home") or {}).get("x") or 0), float((dump.get("home") or {}).get("y") or 0))
    area = tuple(dump["area"]) if dump.get("area") else None
    why, tried, holds = "no free ground by any of its patches", {}, {}
    from companion.smelting import foreign_tiles, plan_field
    other = foreign_tiles(dump["resources"], ore)   # (the fields kept off other ore, as plan_feeds keeps them)
    drills = max(2, math.ceil(mods[0].makes.get(plate, 0.0) * RATIO.get(plate, 1.0) / growth.DRILL_RATE))
    best = None   # ((belts, buildings), stages, notes, its fields kept, its spot)
    fitted = 0
    for x, y, box in spots:
        if fitted >= BEST_OF:
            break
        if box is not None:
            if box not in holds:   # (a patch too small, or taken by drills already, holds none of its fields)
                tiles_here = {q for q in patch if box[0] <= q[0] < box[2] and box[1] <= q[1] < box[3]}
                holds[box] = plan_field(tiles_here, built0, drills, ore, min_ore=6, foreign=other) is not None
                if not holds[box]:
                    why = f"no room for {drills} drills on {ore} by it"
            if not holds[box] or tried.get(box, 0) >= TRIES:
                continue
        laid, at = _laid(mods, x, y)
        tiles = footprint(laid)
        # (a spot kept from before is laid out where it was: its own buildings may stand there now)
        if box is not None and (any(not (x0 <= tx <= x1 and y0 <= ty <= y1) for tx, ty in tiles) or tiles & forbidden
                                or _nest_near(dump, tiles, nests)):
            continue
        tried[box] = tried.get(box, 0) + 1
        built = built0 | tiles | outlets(laid)
        feeds, ok = [], True
        for k, (m, mx, my) in enumerate(at):
            ports = {p.item: p for p in m.ports if p.kind == "ext"}
            ore_at = (mx + ports[ore].x, my + ports[ore].y)
            coal_at = (mx + ports["coal"].x, my + ports["coal"].y)
            suffix = f"-{k + 1}" if k else ""
            # (its fields on the patch it stands by: an outpost by one patch and its ore from another wants long belts)
            here_patch = patch if box is None else {q for q in patch if box[0] <= q[0] < box[2] and box[1] <= q[1] < box[3]}
            fed, _, why = growth.plan_feeds(dump, ore, ore_at, coal_at, ports[ore].rate, ports["coal"].rate, built,
                                            taken, blocked, coal_line, found=found, key=f"field-{plate}{t}{suffix}",
                                            own_belts=own, patch=here_patch)
            if fed is None:
                ok = False
                break
            built = fed[1]
            feeds.append((suffix, fed[0]))
        keys = [f"field-{plate}{t}{f'-{k + 1}' if k else ''}" for k in range(len(at))]
        if not ok:
            for k in keys:   # (the fields kept for this spot are no good for the next)
                found.pop(k, None)
            continue
        # Beyond the team's wall it holds its own (the user, 2026-10-25): its guard built first, before anything worth
        # taking stands there, its power line laid first too (the outpost's joins it), its wall as walls are made.
        guard, wall, supplies, notes, joined = [], [], [], [], True
        held = _held(laid, feeds)
        if kit and needs_ring(dump, held):
            guard, wall, supplies, notes, joined = ring(held, home, kit, (set(built) - set(doorways.wall_room(
                dump, dump.get("force")))) | footprint(laid), powered, own_at(dump), area)
        line = powered + ([(e.x, e.y) for e in guard if e.name == "small-electric-pole"] if joined else [])
        from companion.power import cover   # (every machine in some pole's area, then the poles joined, F-21)
        laid = laid + cover(laid + [e for _, f in feeds for e in f], dump, set(built) | footprint(guard + wall))
        poles, problems = connect(laid + [e for _, f in feeds for e in f], line, set(built) | footprint(guard + wall))
        if problems:
            why = f"no pole line reaches the power network ({problems[0]})"
            for k in keys:
                found.pop(k, None)
            continue
        stages = [Stage(f"outpost-guard-{plate}{t}", guard, supplies)] if guard else []
        stages += [Stage(f"supply-{plate}{t}{suffix}", f) for suffix, f in feeds]
        stages.append(Stage(f"outpost-smelt-{plate}{t}", laid + poles))
        stages += [Stage(f"outpost-wall-{plate}-{index}-{n // RING_WALLS + 1}", wall[n:n + RING_WALLS])
                   for n in range(0, len(wall), RING_WALLS)]
        placed = [e for st in stages for e in st.entities]
        clash = _clash(placed)
        if clash:
            why = f"its buildings would overlap ({clash})"
            for k in keys:
                found.pop(k, None)
            continue
        cost = (sum(1 for e in placed if e.name in ("transport-belt", "underground-belt")), len(placed))
        if best is None or cost < best[0]:
            best = (cost, stages, notes, {k: found[k] for k in keys if k in found}, f"{x},{y}")
        fitted += 1
        if box is None:
            break   # (a spot kept from before is laid out where it was: no other is weighed)
        for k in keys:   # (the next spot works out fields of its own)
            found.pop(k, None)
    if best is not None:
        found.update(best[3])
        found[key] = best[4]
        return best[1], best[2]
    found.pop(key, None)
    return [], [f"{label} no room for an outpost by any {ore} patch ({why})"]
