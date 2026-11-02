"""The base's belt lines and what each carries, line by line: what goes on at its head, what each connection along it
takes, and so what is left further down (the user, 2026-10-25: the ledger tracks each belt line, coal trunks and the
main bus's belts alike, so the engineer and its strategist know how much is used and how much is spare).

A line is recorded by the game once it stands: its plain belts' tiles in order, the item, what feeds its head (coal
drills, a smelting column), and the claims of what it feeds (items a second at full work). Coal is the first: not a
drill field and a belt for every consumer, but a few shared trunks. A consumer's coal (base_plan._coal_line) taps the
nearest standing coal line with the spare for it, a splitter on a straight stretch with its other output belted to
the consumer; else a new trunk is laid from a field of its own, sized with room for the next ones (TRUNK_RATE). Only
lines that stand are tapped: a consumer is never joined to one planned but not built.

The main bus's belts are lines too (kind "bus"): each bus stage reports every lane built so far, its group (belts of
one item side by side, balanced together), its share of what goes on at the head, and the taps on it as claims. They
are never tapped from outside: the bus planner taps its own lanes.

Every line has probe tiles where the game samples how full the belt is (mod/scripts/base_memory.lua), averaged over
the last 30 and 5 minutes: on a trunk along its length and before each tap, on the bus just above each tap and
balancer and at the tail, the same rows for every belt of a group so they compare. From those, the lane ledger
(companion/lane_ledger.py) finds belts of one item that drain unevenly and lines that run short.

    with taps(allowed):          # (splitters researched)
        branch = tap(dump, "coal", entry, rate, blocked)
    ops(entities)                # what a stage tells the game of its lines and taps
    bus_lanes(bus, built)        # what a bus stage tells it of the bus's lanes
"""

from __future__ import annotations

import contextlib
import contextvars
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

DRILL_RATE = 0.5       # items a second an electric drill mines
LANE = 7.5             # items a second on a lane of yellow belt
# Coal a second a new trunk is sized for at least: room for the consumers after the first. One a second is two electric
# drills, enough for 44 stone furnaces (the opening's smelting is about 24); sized for three, an electric brick block of
# four furnaces got six coal drills, 140 iron hand-built for coal it would not use (run 49, 2026-10-26).
TRUNK_RATE = 1.0
TAP_TRIES = 24         # straight tiles of a line tried for a tap, nearest the consumer first
# A tap's route search gives up after TAP_SEARCH states (one found takes a few thousand: run 33's map, 168 to 270
# belts in 0.03 s), and a line is given up once its spots' searches came to LINE_SEARCH states and found no way: its
# other spots are farther from the same entry (a spot walled in close by costs next to nothing, and the next is tried).
# (Run 55's map: a line about 420 belts from the entry, past the router's 400, was searched to the end from 86 spots
# in turn, 5 s each, 570 s for one bus layout, 2026-10-26.)
TAP_SEARCH = 30_000
LINE_SEARCH = 4 * TAP_SEARCH
MAX_PROBES = 48        # tiles a line is sampled at (the game keeps at most this many): one before every tap
TRUNK_PROBES = 6       # a trunk's own probes, evenly along it (its taps add one each)
# Reading measured fill (items on a tile over what it holds, both lanes; a moving belt this full carries that share of
# what it can, a backed-up one is full whatever goes on).
MEASURED_MINUTES = 10  # sampled this long before a line's fill is judged
DRY = 0.1              # a belt this empty on average carries next to nothing there
FULL = 0.5             # a belt of the same group this full has plenty to give
BACKED = 0.9           # this full: backed up, so it carries what is taken, not what goes on
HUNGRY = 0.1           # a module measurably short of inputs this share of the time is short of what the bus brings
BELT_RATE = 2 * LANE   # a yellow belt, both lanes
_VEC = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
_TAPS = contextvars.ContextVar("belt_taps", default=False)


@contextlib.contextmanager
def taps(allowed: bool):
    """Plan what is inside as taps on standing lines (splitters researched), or not. Per thread of planning."""
    token = _TAPS.set(bool(allowed))
    try:
        yield
    finally:
        _TAPS.reset(token)


def tapping() -> bool:
    return _TAPS.get()


@dataclass
class Line:
    id: str
    item: str
    tiles: list[tuple[int, int, int]]          # (x, y, direction) of its plain belts, from its head on (a bus lane,
    #                                            straight down its column: its two ends)
    supply: float                              # items a second onto it, as designed (at most both lanes)
    claims: dict[str, float] = field(default_factory=dict)
    kind: str = "trunk"                        # "trunk": laid for its consumers; "bus": a lane of the main bus
    group: str | None = None                   # a bus lane's group: belts of one item side by side
    fill: dict[tuple[int, int], tuple[float, float | None, float, float | None]] = field(default_factory=dict)
    #                                            measured at each probe tile, average fill of both lanes over 30 and 5
    #                                            minutes, then of its fuller lane alike (a belt fed from one side
    #                                            carries on one lane: backed up, it is half full)
    minutes: int = 0                           # how long the game has sampled it
    unread: int = 0                            # taps with no probe before them (past MAX_PROBES): not judged there
    sides: dict[str, str] | None = None        # a belt with an item a side: {"left": item, "right": item} (its way)
    side_supply: dict[str, float] | None = None   # what goes on each side, as designed
    side_fill: dict[tuple[int, int], tuple[float | None, float | None]] = field(default_factory=dict)
    #                                            measured at each probe: each lane's own 30-minute fill (left, right)
    rate: float | None = None                  # items a second the belt carries full and moving (both lanes)

    @property
    def spare(self) -> float:
        return min(self.supply_now, 2 * LANE) - sum(self.claims.values())

    @property
    def measured(self) -> bool:
        return self.minutes >= MEASURED_MINUTES and bool(self.fill) and bool(self.rate)

    def along(self, at: tuple[int, int]) -> int:
        """How far down the line a tile is: its place among the line's tiles (a trunk winds), else its distance from
        the head (a bus lane runs straight from it)."""
        if not hasattr(self, "_order"):
            self._order = {(x, y): i for i, (x, y, _) in enumerate(self.tiles)}
        if at in self._order and len(self.tiles) > 2:
            return self._order[at]
        hx, hy = self.tiles[0][0], self.tiles[0][1]
        return abs(at[0] - hx) + abs(at[1] - hy)

    def probes(self) -> list[tuple[tuple[int, int], float]]:
        """Its measured probes, head first: (tile, 30-minute average fill of both lanes)."""
        return sorted(((at, v[0]) for at, v in self.fill.items()), key=lambda p: self.along(p[0]))

    @property
    def backed_up(self) -> bool:
        """A lane full somewhere, over the half hour or lately: less is taken there than comes (it has spare). A belt
        backs up from its end as soon as more goes on than is taken, so one nowhere backed up gives all it carries."""
        return self.measured and any(max(m, m5 or 0.0) >= BACKED for _, _, m, m5 in self.fill.values())

    @property
    def flowing(self) -> float | None:
        """Items a second measurably going on: the most it carries at any probe (a moving belt this full carries that
        share of what it can), while it is nowhere backed up; else None (backed up, it carries what is taken)."""
        if not self.measured or self.backed_up:
            return None
        return max(f for _, f in self.probes()) * float(self.rate)

    @property
    def supply_now(self) -> float:
        """What goes on: as designed, or less where the game measured less (drills run out, or unfuelled)."""
        flowing = self.flowing
        return self.supply if flowing is None else min(self.supply, flowing)


def lines(dump: Mapping[str, Any], item: str | None = None) -> list[Line]:
    """The standing lines the game exported with the map (of `item`), those whose belt still stands."""
    out = []
    for t in dump.get("belt_lines") or []:
        if not isinstance(t, Mapping) or t.get("ok") is False:
            continue
        on = {t.get("item"), *((t.get("sides") or {}).values() if isinstance(t.get("sides"), Mapping) else ())}
        if item and item not in on:   # (a belt with an item a side carries both)
            continue
        try:
            # (the site export gives a line's two ends only: the map export has every tile)
            raw = t.get("tiles") or [e for e in (t.get("head"), t.get("tail")) if e]
            tiles = [(int(e[0]), int(e[1]), int(e[2]) if len(e) > 2 and e[2] is not None else SOUTH) for e in raw]
            supply = float(t.get("supply") or 0)
        except (TypeError, ValueError):
            continue
        claims = {str(k): float(v) for k, v in (t.get("claims") or {}).items() if isinstance(v, (int, float))}
        if tiles:
            line = Line(str(t.get("id")), str(t.get("item")), tiles, supply, claims,
                        kind=str(t.get("kind") or "trunk"), group=str(t["group"]) if t.get("group") else None,
                        unread=int(t.get("unread") or 0) if isinstance(t.get("unread"), (int, float)) else 0,
                        sides=_sides(t.get("sides"), str), side_supply=_sides(t.get("side_supply"), float))
            _measured(line, t.get("measured"))
            out.append(line)
    return out


def _sides(raw: Any, kind) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    out = {}
    for side in ("left", "right"):
        try:
            if raw.get(side) is not None:
                out[side] = kind(raw[side])
        except (TypeError, ValueError):
            continue
    return out or None


def _measured(line: Line, raw: Any) -> None:
    """What the game measured of a line: {minutes, rate, probes: [{x, y, f (30-minute), f5, m (its fuller lane,
    30-minute), m5}]}."""
    if not isinstance(raw, Mapping):
        return
    try:
        line.minutes = int(raw.get("minutes") or 0)
        rate = float(raw.get("rate") or 0)
    except (TypeError, ValueError):
        return
    line.rate = rate if rate > 0 else None
    probes = raw.get("probes")
    for p in (probes.values() if isinstance(probes, Mapping) else probes or []):
        if not isinstance(p, Mapping) or not isinstance(p.get("f"), (int, float)):
            continue
        try:
            at = (int(p["x"]), int(p["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        # A probe read for less than MEASURED_MINUTES says too little yet, however old its line (one added when a lane
        # grew on: Codex, L-1); a probe exported without its own age is as old as its line.
        if isinstance(p.get("min"), (int, float)) and p["min"] < MEASURED_MINUTES:
            continue
        f, f5, m, m5 = (p.get(k) for k in ("f", "f5", "m", "m5"))
        m = float(m) if isinstance(m, (int, float)) else float(f)
        line.fill[at] = (float(f), float(f5) if isinstance(f5, (int, float)) else None, m,
                         float(m5) if isinstance(m5, (int, float)) else None)
        lane = [p.get(k) for k in ("l", "r")]
        if any(isinstance(v, (int, float)) for v in lane):
            line.side_fill[at] = tuple(float(v) if isinstance(v, (int, float)) else None for v in lane)


def straight(line: Line) -> list[tuple[int, int, int]]:
    """Its tiles a splitter can go on: a plain belt with plain belts before and after it, all one way."""
    out = []
    for i in range(1, len(line.tiles) - 1):
        (px, py, pd), (x, y, d), (nx, ny, nd) = line.tiles[i - 1], line.tiles[i], line.tiles[i + 1]
        dx, dy = _VEC.get(d, (0, 0))
        if pd == d == nd and (px + dx, py + dy) == (x, y) and (x + dx, y + dy) == (nx, ny):
            out.append((x, y, d))
    return out


def tap(dump: Mapping[str, Any], item: str, entry: tuple[int, int], rate: float, blocked: set, *, last: int = WEST,
        key: str | None = None, among: list[Line] | None = None, budget: tuple[int, int] | None = None,
        stats: dict | None = None, mixed: Mapping[str, float] | None = None) -> list[Placed] | None:
    """A branch off the nearest standing line of `item` with `rate` a second spare, into `entry` (its last belt
    pointing `last`): the splitter that takes the line's tile and the one beside it, and the belts on; or None.
    `mixed`: the consumer takes both lanes of a belt with an item a side, the other item's rate here
    ({item: items/s}); without it such a belt is not tapped (a splitter sends both lanes down the branch).
    `among`: these lines rather than those the game recorded (a belt traced on the map: no claim is recorded on it).
    `budget`: (states one route search may take, states a line's searches may take), for a branch worth a long
    search (default TAP_SEARCH, LINE_SEARCH); `stats["searched"]` is set to the states searched in all."""
    from companion.smelting import footprint, route
    if not tapping():
        return None
    per_route, per_line = budget or (TAP_SEARCH, LINE_SEARCH)
    total = 0
    if stats is not None:
        stats["searched"] = 0
    key = key or f"{entry[0]},{entry[1]}"
    # (a tap already there, or right before or after: not again)
    split = footprint([Placed("splitter", float(e["x"]), float(e["y"]), int(e.get("d") or 0))
                       for e in dump.get("entities") or [] if e.get("n") == "splitter"])
    bounds = tuple(dump.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6))
    ex, ey = entry

    def spare(t: Line) -> float:
        """What it spares of `item`: a belt with an item a side, that side's (its key "...#side")."""
        if t.sides and len(set(t.sides.values())) > 1 and item in t.sides.values():
            from companion import lane_ledger
            which = next(s for s, v in t.sides.items() if v == item)
            one = lane_ledger.side(t, which)
            return one.spare + one.claims.get(f"{key}#{which}", 0.0)
        return t.spare + t.claims.get(key, 0.0)
    def two(t: Line) -> bool:
        return bool(t.sides) and len(set(t.sides.values())) > 1

    def both(t: Line) -> bool:
        """A belt with an item a side, for a consumer that takes both: the other item named with a rate, and its
        side with room for it as well."""
        if not two(t):
            return True
        others = [v for v in t.sides.values() if v != item]
        if not others or float((mixed or {}).get(others[0], 0.0)) <= 0:
            return False
        from companion import lane_ledger
        which = next(s for s, v in t.sides.items() if v == others[0])
        return lane_ledger.side(t, which).spare >= float(mixed[others[0]])
    # (a splitter sends both lanes down the branch: a belt with an item a side only for a consumer that takes both)
    found = [t for t in (lines(dump, item) if among is None else among)
             if t.kind != "bus" and both(t) and spare(t) >= rate]
    found.sort(key=lambda t: min(abs(x - ex) + abs(y - ey) for x, y, _ in t.tiles))
    for line in found:
        along = {(x, y) for x, y, _ in line.tiles}
        spots = sorted(straight(line), key=lambda t: abs(t[0] - ex) + abs(t[1] - ey))[:TAP_TRIES]
        spent = 0
        for x, y, d in spots:
            if spent >= per_line:
                break
            dx, dy = _VEC[d]
            if {(x, y), (x - dx, y - dy), (x + dx, y + dy)} & split:
                continue
            for sx, sy in ((-dy, dx), (dy, -dx)):   # either side of the line
                side = (x + sx, y + sy)
                out = (side[0] + dx, side[1] + dy)      # the splitter's other output: the branch's first belt
                fed = (side[0] - dx, side[1] - dy)      # its other input: kept clear, nothing feeds it
                if any(t in blocked or t in along for t in (side, out, fed)):
                    continue
                searched: dict = {}
                path = route(out, entry, (set(blocked) | along | {side, fed, (x, y)}) - {entry}, bounds=bounds,
                             expansions=min(per_route, per_line - spent), stats=searched)
                total += searched.get("searched", 0)
                if stats is not None:
                    stats["searched"] = total
                if not path:
                    spent += searched.get("searched", 0)
                    if spent >= per_line:
                        break
                    continue
                path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, last)
                splitter = Placed("splitter", x + 0.5 + sx * 0.5, y + 0.5 + sy * 0.5, d)
                if among is None:   # (and the tile before it a probe: how full the line comes to the tap)
                    # (a belt with an item a side: the claim is on the side that carries it; the branch takes both)
                    which = next((s for s, v in (line.sides or {}).items() if v == item), None) \
                        if line.sides and len(set(line.sides.values())) > 1 else None
                    splitter.line_op = {"op": "tap", "line": line.id, "key": f"{key}#{which}" if which else key,
                                        "rate": round(float(rate), 4), "at": [x, y], "probe": [x - dx, y - dy, d]}
                    # (the other side's item goes down the branch too: its claim rides on the branch's first belt)
                    other = next((s for s, v in (line.sides or {}).items() if v != item), None) if which else None
                    if other and len(path) and float((mixed or {}).get(line.sides[other], 0.0)) > 0:
                        path[0].line_op = {"op": "tap", "line": line.id, "key": f"{key}#{other}",
                                           "rate": round(float(mixed[line.sides[other]]), 4)}
                return [splitter] + path
    return None


def trunk_rate(rate: float) -> float:
    """What a new coal trunk for a consumer taking `rate` is sized for: room for the next."""
    return max(float(rate), TRUNK_RATE)


def record_line(built: list[Placed], item: str, supply: float, rate: float, entry: tuple[int, int],
                key: str | None = None, sides: Mapping[str, str] | None = None,
                side_supply: Mapping[str, float] | None = None, claim_side: str | None = None) -> None:
    """Marks a new line on its first building, for the game to record once it stands: its plain belts in order,
    its item and supply, and the claim of the consumer it was laid for."""
    belts = [e for e in built if e.name == "transport-belt"]
    if not built or not belts:
        return
    tiles = [[math.floor(e.x), math.floor(e.y), int(e.direction)] for e in belts]
    head = tiles[0]
    # (on a belt with an item a side, the consumer it was laid for claims the side it takes: `claim_side`)
    claim = key or f"{entry[0]},{entry[1]}"
    built[0].line_op = {"op": "line", "id": f"{item}@{head[0]},{head[1]}", "item": item, "tiles": tiles,
                        "supply": round(float(supply), 4), "key": f"{claim}#{claim_side}" if claim_side else claim,
                        "rate": round(float(rate), 4), "kind": "trunk",
                        "probes": [tiles[i] for i in _spread(len(tiles), TRUNK_PROBES)],
                        **({"sides": dict(sides)} if sides else {}),
                        **({"side_supply": {k: round(float(v), 4) for k, v in side_supply.items()}} if side_supply
                           else {})}


def _spread(n: int, k: int) -> list[int]:
    """At most `k` of `n` indices, evenly spread, the first and last among them."""
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def ops(entities: Iterable[Placed]) -> list[dict]:
    """What a stage's buildings tell the game of the belt lines: lines laid and taps made."""
    return [dict(e.line_op) for e in entities if isinstance(getattr(e, "line_op", None), Mapping)]


def bus_lanes(bus: Any, built: Iterable[Placed]) -> list[dict]:
    """The main bus's lanes as lines, for the game to record (and sample) once the stage stands: of each lane, the
    rows of its column built so far (`built`: the buildings of the bus's stages built and this one), its group, its
    share of what goes on at the head (as designed: the head balancer spreads the smelting's belt evenly; a module's
    output onto it adds what the module makes), and each built tap on it as a claim, keyed by where its splitter
    stands. Probes go just above every tap and balancer of the group, and at its first and last rows, the same rows
    on every belt of the group (where each has a plain belt), so they can be compared. Data only: the bus is laid
    out as before. `bus` is a companion.bus.Bus."""
    built = list(built)
    at_x = {bus.lane_x(c): c for c in bus.lanes}
    rows: dict[int, set[int]] = {}
    splits: set[tuple[int, int]] = set()     # tiles a splitter takes (a tap's or a balancer's)
    here: set[tuple[str, int, int]] = set()  # (name, tile) of everything built
    for e in built:
        here.add((e.name, math.floor(e.x), math.floor(e.y)))
        if e.name == "splitter":
            splits |= {(math.floor(e.x - 0.5), math.floor(e.y)), (math.floor(e.x + 0.5), math.floor(e.y))}
        if e.name == "transport-belt" and int(e.direction) == SOUTH:
            c, y = at_x.get(math.floor(e.x)), math.floor(e.y)
            if c is not None and bus.lanes[c].start <= y < bus.bottom:
                rows.setdefault(c, set()).add(y)

    def standing(a) -> bool:
        """Whether a module is built: its buildings stand among `built` (not merely its rows of the lane)."""
        own = [(e.name, math.floor(e.x + a.x), math.floor(e.y + a.y)) for e in a.module.entities]
        return bool(own) and all(t in here for t in own[:3])

    taps: dict[int, list[tuple[int, str, float]]] = {}      # column -> (splitter row, key, items/s)
    fed: dict[int, list[tuple[int, float]]] = {}            # column -> (row, items/s) a module's output adds
    for a in bus.attached:
        up = standing(a)
        for p in a.module.ports:
            if p.kind == "in" and (c := bus.outer(p.item, a.side)) is not None:
                y = a.y + p.y - 1
                if (bus.lane_x(c), y) in splits:   # (a tap counts once its splitter is built on the lane)
                    taps.setdefault(c, []).append((y, f"{a.module.name}@{bus.lane_x(c)},{y}", float(p.rate)))
            elif p.kind == "out" and a.output_lane is not None and up:
                fed.setdefault(a.output_lane, []).append((a.y + p.y, float(a.module.makes.get(p.item, 0.0))))
    # A lane is the stretch joined up to its start (every row a belt or a splitter): a stretch built further down
    # before the rows above it carries nothing yet and is left out until joined; the joined part keeps its own
    # supply, taps and probes (Codex, L-2: a cut further down erased the whole lane's supply).
    reach: dict[int, int] = {}   # column -> the first row not joined up
    for c in list(rows):
        x = bus.lane_x(c)
        have = rows[c] | {y for sx, y in splits if sx == x}
        reach[c] = next(y for y in range(bus.lanes[c].start, bus.bottom + 1) if y not in have)
        rows[c] = {y for y in rows[c] if y < reach[c]}
        if not rows[c]:
            del rows[c]
    groups: dict[int, list[int]] = {}
    for c in sorted(rows):
        groups.setdefault(bus.group_of(c)[0], []).append(c)
    out = []
    for first, columns in groups.items():
        lane = bus.lanes[first]
        size = bus.group_of(first)[1]
        top, end = max(min(rows[c]) for c in columns), min(max(rows[c]) for c in columns)
        # Where to read: just before every tap first (a belt running dry before a tap is what a balancer is for),
        # then before every balancer, the head and the end; the rest spread evenly when there are more than the game
        # keeps (a dropped row before a tap could hide where it runs dry: Codex, L-2).
        def common(marks) -> list[int]:
            return sorted(r for r in marks if top <= r <= end and all(r in rows[c] for c in columns))

        def above(y: int) -> int | None:   # (the nearest row above a tap plain on every belt: a splitter may be)
            return next((r for r in range(y - 1, y - 4, -1) if common({r})), None)
        before_taps = sorted({r for c in columns for y, _, _ in taps.get(c, ()) if (r := above(y)) is not None})
        others = [r for r in common({top, end} | {row - 1 for f, _, row in bus.balancers
                                                   if f == first and row > lane.start}) if r not in before_taps]
        if len(before_taps) >= MAX_PROBES:   # (rare: past MAX_PROBES taps; the lane says how many go unread)
            probe_rows = [before_taps[i] for i in _spread(len(before_taps), MAX_PROBES)]
        else:
            room = MAX_PROBES - len(before_taps)
            probe_rows = sorted(before_taps + [others[i] for i in _spread(len(others), room)])
        group = f"{lane.item}@{bus.lane_x(first)},{lane.start}"
        for c in columns:
            x, ys, own = bus.lane_x(c), rows[c], bus.lanes[c]
            head = min(BELT_RATE, bus.supply.get(own.item, 0.0) / size) if own.source == "head" else 0.0
            supply = head + sum(rate for y, rate in fed.get(c, ()) if y < reach[c])
            claims = {key: round(rate, 4) for y, key, rate in taps.get(c, ()) if y < reach[c]}
            unread = sum(1 for y, _, _ in taps.get(c, ())
                         if y < reach[c] and not {y - 1, y - 2, y - 3} & set(probe_rows))
            out.append({"op": "line", "id": f"{own.item}@{x},{own.start}", "item": own.item, "kind": "bus",
                        "group": group, "tiles": [[x, min(ys), SOUTH], [x, max(ys), SOUTH]],
                        "supply": round(min(supply, BELT_RATE), 4), "claims": claims,
                        "probes": [[x, r, SOUTH] for r in probe_rows], **({"unread": unread} if unread else {})})
    return out


def summary(dump: Mapping[str, Any]) -> list[str]:
    """Each line laid for its consumers, for the strategist: its item, what goes on, what its connections take, what
    is left (and how full its end runs, once measured). The bus's lanes are the bus planner's."""
    out = []
    for t in lines(dump):
        if t.kind == "bus":
            continue
        text = (f"{t.item} line {t.id}: {min(t.supply_now, 2 * LANE):.2f}/s on, {sum(t.claims.values()):.2f}/s taken "
                f"by {len(t.claims)} connections, {t.spare:.2f}/s spare")
        if t.measured:
            text += ", backed up" if t.backed_up else f", all taken, its end {t.probes()[-1][1]:.0%} full"
        out.append(text)
    return out
