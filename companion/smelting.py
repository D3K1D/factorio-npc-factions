"""Feeding the bus: mining fields on ore patches, smelting columns, and belt routes.

A mining field is a line of electric drills on both sides of a collector belt
(pairs of drills, then a gap row for a pole). A smelting column is the classic
ore belt | inserters | furnaces | inserters | plate belt stack, flowing south.
`route` finds a belt path between them around obstacles, hopping over belts and
other buildings with underground belts where it must, and the plate belt
continues into a bus lane.
"""

from __future__ import annotations

import contextlib
import contextvars
import heapq
import math
from dataclasses import dataclass, field, replace
from typing import Iterable

from companion.modules import EAST, LOADERS, Module, NORTH, Placed, Port, SOUTH, WEST, loader_tiles

DRILL_DROP = 1.85   # electric mining drill: drop point this far in front of its centre
DRILL_REACH = 2     # its 5x5 mining area reaches two tiles beyond its 3x3 body
FURNACE_RATE = {"stone-furnace": 1 / 3.2, "steel-furnace": 2 / 3.2}
STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
UNDERGROUND_SPAN = 5    # yellow underground belts: the exit at most 5 tiles past the entrance
UNDERGROUND_COST = 8    # extra cost of a hop, so belts go around when that is not much longer
TURN_COST = 0.6         # belts run straight where they can: fewer bends, no staircases
MAX_EXPANSIONS = 300_000   # a route search gives up here: no route, or none worth building
SETTLE_AFTER = 20_000      # states a route search has looked at before it floods the whole window once (route)
POCKET = 20_000            # tiles flooded from either end of a route first (_apart): a smaller side walled in is no way through
GREED = 1.3                # weighted A*: heads for the goal, paths at most a few percent longer (long
                           # routes on a cluttered map took seconds and hit the cap; now milliseconds)


def smelting_column(ore: str, plate: str, furnaces: int, *, furnace: str = "stone-furnace",
                    inserter: str = "inserter", mirrored: bool = False) -> Module:
    """Ore belt (x=0) | inserters | furnaces (2x2) | inserters | plate belt (x=5), all flowing south.
    `mirrored`: the east column of a full-belt block, sharing that plate belt (not included, nor the poles
    beside it, which the west column's poles cover): plate belt (x=5) | inserters | furnaces | inserters |
    ore belt (x=10). Its inserters drop on the plate belt's other lane, so the two columns fill it."""
    m = Module(f"smelting-{plate}")
    for i in range(furnaces):
        top = 2 * i
        m.entities.append(Placed(furnace, 3.0, top + 1.0, NORTH))
        m.entities.append(Placed(inserter, 1.5, top + 0.5, WEST))   # picks the ore belt (west), drops east
        m.entities.append(Placed(inserter, 4.5, top + 0.5, WEST))   # picks the furnace (west), drops east
        if i % 2 == 0:
            m.entities.append(Placed("small-electric-pole", 1.5, top + 1.5))
            m.entities.append(Placed("small-electric-pole", 4.5, top + 1.5))
        for row in (top, top + 1):
            m.entities.append(Placed("transport-belt", 0.5, row + 0.5, SOUTH))
            m.entities.append(Placed("transport-belt", 5.5, row + 0.5, SOUTH))
    rate = furnaces * FURNACE_RATE[furnace]
    m.ports = [Port(ore, "in", 0, 0, SOUTH, rate), Port(plate, "out", 5, 2 * furnaces - 1, SOUTH, rate)]
    m.width, m.height, m.makes = 6, 2 * furnaces, {plate: rate}
    if mirrored:
        flip = {WEST: EAST, EAST: WEST}
        m.entities = [replace(e, x=11 - e.x, direction=flip.get(e.direction, e.direction)) for e in m.entities
                      if not (e.x == 5.5 and e.name == "transport-belt") and not (e.x == 4.5 and e.name.endswith("pole"))]
        m.ports = [Port(ore, "in", 10, 0, SOUTH, rate), Port(plate, "out", 5, 2 * furnaces - 1, SOUTH, rate)]
        m.width = 11
    return m


STEEL_PITCH = 3                      # rows per furnace pair: two for the furnaces, one for poles
STEEL_PER_PAIR = 1 / 16              # steel/s from one pair of stone furnaces (iron furnace into steel furnace)
ORE_PER_PAIR = 1 / 3.2               # what the pair's iron furnace smelts, and its steel furnace uses


def steel_pairs(rate: float) -> int:
    return max(1, math.ceil(rate / STEEL_PER_PAIR - 1e-9))


def steel_column(pairs: int) -> Module:
    """Steel by direct insertion, as the guides build it: iron furnaces hand their plates straight to
    steel furnaces beside them, no belt between (one stone furnace smelts exactly what one steel
    furnace takes). All flowing south: ore and coal belt A (x=0) | inserters | iron furnaces |
    inserters | steel furnaces | inserters | steel belt B (x=8) | coal belt C (x=9). Long-handed
    inserters reach over B for the steel furnaces' coal, so only steel leaves on B.

    Above the first pair: ore comes in from the west at (-1, -1) and coal from the north at (9, -3);
    a splitter sends coal down C and, along row -1, onto A's other lane (both side-loaded, so neither
    can crowd the other off A). Steel leaves below B's last belt."""
    m = Module("steel-direct")
    m.entities.append(Placed("splitter", 9.0, -1.5, SOUTH))           # coal: right half down C, left to A
    m.entities.append(Placed("transport-belt", 9.5, -0.5, SOUTH))
    for x in range(1, 9):
        m.entities.append(Placed("transport-belt", x + 0.5, -0.5, WEST))   # onto A's east lane
    m.entities.append(Placed("transport-belt", 0.5, -0.5, SOUTH))          # A's head: ore from the west
    for i in range(pairs):
        top = STEEL_PITCH * i
        m.entities.append(Placed("stone-furnace", 3.0, top + 1.0, NORTH))           # iron
        m.entities.append(Placed("stone-furnace", 6.0, top + 1.0, NORTH))           # steel
        m.entities.append(Placed("inserter", 1.5, top + 0.5, WEST))                 # A into the iron furnace
        m.entities.append(Placed("inserter", 4.5, top + 0.5, WEST))                 # iron plates straight across
        m.entities.append(Placed("inserter", 7.5, top + 0.5, WEST))                 # steel onto B
        m.entities.append(Placed("long-handed-inserter", 7.5, top + 1.5, EAST))     # coal from C over B
        if i % 2 == 0:   # the gap row: poles reach this pair and the next
            for px in (1.5, 4.5, 7.5):
                m.entities.append(Placed("small-electric-pole", px, top + 2.5))
        for row in range(top, top + STEEL_PITCH):
            for bx in (0, 8, 9):
                m.entities.append(Placed("transport-belt", bx + 0.5, row + 0.5, SOUTH))
    bottom = STEEL_PITCH * pairs
    steel, ore = pairs * STEEL_PER_PAIR, pairs * ORE_PER_PAIR
    m.ports = [Port("iron-ore", "in", -1, -1, EAST, ore), Port("coal", "in", 9, -3, SOUTH, pairs * 0.045),
               Port("steel-plate", "out", 8, bottom, SOUTH, steel)]
    m.width, m.height, m.makes = 10, bottom, {"steel-plate": steel}
    return m


def furnaces_for(rate: float, furnace: str = "stone-furnace") -> int:
    return max(1, math.ceil(rate / FURNACE_RATE[furnace] - 1e-9))


@dataclass
class Field:
    """Drills on an ore patch along a south-flowing collector belt at column `belt_x`; with `lines` 2, a
    second line LINE_PITCH east whose collector turns west below the field and sideloads the first."""
    resource: str
    belt_x: int
    top: int
    drills: list[tuple[float, float, int]] = field(default_factory=list)   # (cx, cy, direction)
    rows: int = 0
    lines: int = 1

    @property
    def exit(self) -> tuple[int, int]:
        """The tile just below the last collector belt tile, where the ore leaves the field."""
        return self.belt_x, self.top + self.rows + (2 if self.lines > 1 else 0)

    def entities(self) -> list[Placed]:
        out = [Placed("electric-mining-drill", cx, cy, d) for cx, cy, d in self.drills]
        tail = 2 if self.lines > 1 else 0   # the first line's collector runs on to take the others
        for j in range(self.lines):
            bx = self.belt_x + LINE_PITCH * j
            for row in range(self.top, self.top + self.rows + (tail if j == 0 else 1 if self.lines > 1 else 0)):
                out.append(Placed("transport-belt", bx + 0.5, row + 0.5, SOUTH))
            if j > 0:   # below the field: west along the gap row, onto the first collector's side
                joint = self.top + self.rows + 1
                for x in range(self.belt_x + 1, bx + 1):
                    out.append(Placed("transport-belt", x + 0.5, joint + 0.5, WEST))
            # A pole reaches two rows past its own, so each drill pair needs a pole row right beside
            # it: one above the first pair, then one in every gap row (below a unit's second pair and
            # above the next unit's first), one per side, seven rows apart.
            for row in range(self.top - 1, self.top + self.rows + 1, 7):
                out.append(Placed("small-electric-pole", bx - 1.5, row + 0.5))
                out.append(Placed("small-electric-pole", bx + 2.5, row + 0.5))
        return out


def ore_tiles(resources: dict[str, list], resource: str) -> set[tuple[int, int]]:
    tiles = set()
    for y, a, b in resources.get(resource, []):
        for x in range(a, b + 1):
            tiles.add((x, y))
    return tiles


NEAR_WEIGHT = 0.6   # ore tiles under a drill a player gives up per tile of belt saved
LINE_PITCH = 7      # a second drill line's collector, this far east of the first
FLUIDS = ("crude-oil",)   # resources a mining drill does not mine


def foreign_tiles(resources: dict[str, list], resource: str) -> set[tuple[int, int]]:
    """Tiles of every other ore a mining drill would mine: where two patches touch, a drill over both
    puts both on its belt (copper plates on the iron lanes, stone in the coal)."""
    out: set[tuple[int, int]] = set()
    for name in resources:
        if name != resource and name not in FLUIDS:
            out |= ore_tiles(resources, name)
    return out


def _line_drills(belt_x: int, top: int, count: int) -> list[tuple[float, float, int]]:
    placed = []
    for p in range(math.ceil(count / 2)):
        unit, k = divmod(p, 2)
        y0 = top + unit * 7 + k * 3
        for cx, d in ((belt_x - 1.5, EAST), (belt_x + 2.5, WEST)):
            if len(placed) < count:
                placed.append((cx, y0 + 1.5, d))
    return placed


def field_at(resource: str, belt_x: int, top: int, drills: int, lines: int = 1) -> Field:
    """The field plan_field laid out at (belt_x, top) with `drills` drills: a plan made again later (its
    own drills now stand there) extends the field it built instead of starting one somewhere else."""
    per = math.ceil(drills / lines)
    rows = math.ceil(math.ceil(per / 2) / 2) * 7 - 1
    placed = []
    for j in range(lines):
        placed += _line_drills(belt_x + LINE_PITCH * j, top, min(per, drills - len(placed)))
    return Field(resource, belt_x, top, placed, rows, lines)


def plan_field(ore: set[tuple[int, int]], blocked: set[tuple[int, int]], drills: int, resource: str,
               *, min_ore: int = 15, near: tuple[float, float] | None = None,
               foreign: set[tuple[int, int]] = frozenset(), lines: int = 1) -> Field | None:
    """The best vertical drill line for `drills` drills: every drill mines at least `min_ore` tiles.
    With `near` (where the ore is going), a line close to it beats a slightly richer one far away:
    players mine the part of the patch next to their furnaces, not across it. No drill's 5x5 mining area
    may reach a `foreign` tile (another ore, see foreign_tiles): one ore per belt."""
    if not ore:
        return None
    xs = [x for x, _ in ore]
    ys = [y for _, y in ore]
    per = math.ceil(drills / lines)
    pairs = math.ceil(per / 2)
    units = math.ceil(pairs / 2)
    rows = units * 7 - 1
    # Ore under any 7x7 mining area in O(1): a summed-area table over the patch (with a margin).
    gx0, gy0 = min(xs) - 8, min(ys) - 8
    w, h = max(xs) - gx0 + 9, max(ys) - gy0 + 9
    table = [[0] * (w + 1) for _ in range(h + 1)]
    for x, y in ore:
        table[y - gy0 + 1][x - gx0 + 1] += 1
    for j in range(1, h + 1):
        row, above, run = table[j], table[j - 1], 0
        for i in range(1, w + 1):
            run += row[i]
            row[i] = above[i] + run

    def mined(x0: int, y0: int, grid=table, size: int = 7) -> int:
        a, b = min(max(x0 - gx0, 0), w), min(max(y0 - gy0, 0), h)
        c, d = min(max(x0 + size - gx0, 0), w), min(max(y0 + size - gy0, 0), h)
        return grid[d][c] - grid[b][c] - grid[d][a] + grid[b][a]

    # What is in the way over the same ground, summed the same way: a drill's 3x3 body is free in O(1) (tile by tile it
    # was 130 million lookups in one outpost search on run 33's map, 2026-10-25).
    taken = [[0] * (w + 1) for _ in range(h + 1)]
    for j in range(1, h + 1):
        row, above, run, ty = taken[j], taken[j - 1], 0, gy0 + j - 1
        for i in range(1, w + 1):
            run += (gx0 + i - 1, ty) in blocked
            row[i] = above[i] + run

    other = None
    if foreign:
        other = [[0] * (w + 1) for _ in range(h + 1)]
        for x, y in foreign:
            if 0 <= x - gx0 < w and 0 <= y - gy0 < h:
                other[y - gy0 + 1][x - gx0 + 1] += 1
        for j in range(1, h + 1):
            row, above, run = other[j], other[j - 1], 0
            for i in range(1, w + 1):
                run += row[i]
                row[i] = above[i] + run

    best, best_score = None, None
    width = LINE_PITCH * (lines - 1)
    for bx in range(min(xs), max(xs) + 1 - width):
        for top in range(min(ys) - 2, max(ys) - rows + 3):
            placed, score, ok = [], 0, True
            for j in range(lines):
                lx = bx + LINE_PITCH * j
                count = min(per, drills - len(placed))
                in_line = 0
                for p in range(math.ceil(count / 2)):
                    unit, k = divmod(p, 2)
                    y0 = top + unit * 7 + k * 3          # the drill bodies' top row
                    for x0, cx, d in ((lx - 3, lx - 1.5, EAST), (lx + 1, lx + 2.5, WEST)):
                        if in_line >= count:
                            break
                        if mined(x0, y0, taken, 3):   # (its body on something in the way)
                            ok = False
                            break
                        here = mined(x0 - DRILL_REACH, y0 - DRILL_REACH)
                        if here < min_ore or other is not None and mined(x0 - 1, y0 - 1, other, 5):
                            ok = False   # too little ore, or another ore in its mining area
                            break
                        score += here
                        placed.append((cx, y0 + 1.5, d))
                        in_line += 1
                    if not ok:
                        break
                if not ok:
                    break
            if near is not None:
                score -= NEAR_WEIGHT * drills * math.dist((bx + width / 2, top + rows / 2), near)
            if not ok or len(placed) != drills or (best_score is not None and score <= best_score):
                continue
            tail = 2 if lines > 1 else 0
            if any((bx + LINE_PITCH * j, r) in blocked for j in range(lines)
                   for r in range(top, top + rows + 1 + tail)):
                continue
            if any((px, r) in blocked for j in range(lines) for r in range(top - 1, top + rows + 1, 7)
                   for px in (bx + LINE_PITCH * j - 2, bx + LINE_PITCH * j + 2)):
                continue
            if lines > 1 and any((x, top + rows + 1) in blocked for x in range(bx, bx + width + 1)):
                continue
            best, best_score = Field(resource, bx, top, placed, rows, lines), score
    return best


# Whether the belts being planned may hop under what is in the way: only once underground belts are researched (the
# electric smelting waited on logistics research for its two crossings, run 37, 2026-10-25). Per thread of planning.
_UNDERGROUNDS = contextvars.ContextVar("undergrounds", default=True)


@contextlib.contextmanager
def undergrounds(allowed: bool):
    """Plan the belts inside with (or without) underground hops."""
    token = _UNDERGROUNDS.set(bool(allowed))
    try:
        yield
    finally:
        _UNDERGROUNDS.reset(token)


def _apart(start: tuple[int, int], goal: tuple[int, int], free, hops: bool, cap: float = None) -> bool:
    """Whether `start` and `goal` lie in parts of the ground no belt joins: proven cheaply, before the search, by
    flooding from both ends at once (the smaller side first) with every move a route can make in either direction. A
    side that runs out without meeting the other is walled in: no route. A side past POCKET tiles is open; both open,
    or the floods meeting, settles nothing (the search decides). `cap`: POCKET unless given (math.inf: the whole
    window, see route).
    The moves are a route's own, loosened, never tightened (so no route the search would find is ever turned down):
    a step to a free tile; a hop to a free tile 3..UNDERGROUND_SPAN+1 tiles on in a straight line whose entrance (the
    tile beside one end, toward the other) is free; the goal only by a step (a route ends on an ordinary belt). The
    hop's entrance is what walls thicker than a hop rest on: a flood that hopped from any tile went straight through a
    5-thick ring round the goal, and the search then took 6 s to give up (300,000 states, every time a belt was
    planned to an enclosed goal; OP-4, 2026-10-27)."""
    if start == goal:
        return False
    cap = POCKET if cap is None else cap

    def moves(tile):
        x, y = tile
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            yield x + dx, y + dy
            if not hops or tile == goal:
                continue
            for reach in range(3, UNDERGROUND_SPAN + 2):
                far = (x + dx * reach, y + dy * reach)
                if far != goal and (free((x + dx, y + dy)) or free((far[0] - dx, far[1] - dy))):
                    yield far

    seen = ({start}, {goal})
    todo = ([start], [goal])
    while True:
        open_ = [len(s) > cap for s in seen]
        if all(open_):
            return False
        side = min((i for i in (0, 1) if not open_[i]), key=lambda i: len(todo[i]))
        if not todo[side]:
            return True   # that side ran out without meeting the other: walled in
        tile = todo[side].pop()
        mine, other = seen[side], seen[1 - side]
        for near in moves(tile):
            if near in other:
                return False
            if near in mine or not free(near):
                continue
            mine.add(near)
            todo[side].append(near)


DEAD_END = 64   # tiles followed back from a route's goal at most, belt by belt, for a dead end (_dead_end)


def _dead_end(goal: tuple[int, int], start: tuple[int, int], free, hops: bool) -> bool:
    """Whether no belt can arrive at `goal` at all, as the search moves: the ways in (tile, heading, just out of an
    underground) followed back from it run out within DEAD_END tiles without meeting `start`. The tile flood misses
    this: a coal entry whose one free neighbour could only be reached out of an underground heading across the
    entry's way in (an exit goes straight on) passed the flood, and four coal lines to it searched 300,000 states
    each, 3.5 s apiece in every replay of the bus (run 33's map, 2026-10-26)."""
    todo = [(goal, d, False) for d in STEP.values()]   # (the goal is reached by an ordinary belt, any heading)
    seen, tiles = set(todo), {goal}
    while todo:
        (x, y), (dx, dy), exited = todo.pop()
        if exited:   # it came out of an underground here, heading (dx, dy): its entrance `span` back, a belt before it
            if not hops:
                continue
            befores = []
            for span in range(2, UNDERGROUND_SPAN + 1):
                entrance = (x - dx * span, y - dy * span)
                ex, ey = goal[0] - entrance[0], goal[1] - entrance[1]
                if entrance == goal or not free(entrance) or ex * dy == ey * dx and 0 < ex * dx + ey * dy < span:
                    continue   # (never hopping onto or over the goal)
                befores.append((entrance[0] - dx, entrance[1] - dy))
        else:
            befores = [(x - dx, y - dy)]
        for before in befores:
            if before == start:
                return False
            if before == goal or not free(before):
                continue
            for heading in STEP.values():   # how it may have been heading there: never back, straight on out of a hop
                if heading == (-dx, -dy):
                    continue
                for out_of_hop in ((False, True) if heading == (dx, dy) else (False,)):
                    state = (before, heading, out_of_hop)
                    if state not in seen:
                        seen.add(state)
                        tiles.add(before)
                        if len(tiles) > DEAD_END:
                            return False
                        todo.append(state)
    return True


def route(start: tuple[int, int], goal: tuple[int, int], blocked: set[tuple[int, int]], *,
          bounds: tuple[int, int, int, int], limit: int = 400, expansions: int | None = None,
          stats: dict | None = None) -> list[Placed] | None:
    """Belts from `start` to `goal` (both tiles), avoiding `blocked`; an underground pair hops over up to
    four blocked tiles in a straight line. The goal is reached by an ordinary belt, which keeps the
    direction of travel (callers may turn it). Returns None when no path exists. `expansions`: states searched at most
    (default MAX_EXPANSIONS); `stats["searched"]` is set to how many were."""
    x0, y0, x1, y1 = bounds

    def free(tile):
        return (tile == goal or tile not in blocked) and x0 <= tile[0] <= x1 and y0 <= tile[1] <= y1

    # A route that does not exist searched the whole reachable map before giving up: 20 of run 33's 59 coal routes,
    # 62 of the planner's 64 seconds of routing (2026-10-25). It most often fails because one end stands in a small
    # pocket, walled in: flooded first round either end, with every move a route may make (a step, a hop under), a
    # pocket that fills up is no way through, and nothing more is searched. (The flood is looser than the search, so
    # it never turns down a route the search would find.)
    hops = _UNDERGROUNDS.get()
    if _dead_end(goal, start, free, hops) or _apart(start, goal, free, hops):
        return None
    # A state is (tile, heading, after an underground exit): an exit must be followed straight on.
    gx, gy = goal

    def h(tile):   # A*: every tile of belt costs at least one, so the tile distance never overestimates
        return abs(tile[0] - gx) + abs(tile[1] - gy)

    first = (start, None, False)
    settled = False   # (both ends flooded to the window's edges: a cut across the whole window)
    best, came = {first: 0}, {first: None}
    heap = [(GREED * h(start), 0, 0, first)]
    count, end, popped = 0, None, 0
    while heap:
        _, cost, _, state = heapq.heappop(heap)
        if cost > best.get(state, math.inf):
            continue
        popped += 1
        if stats is not None:
            stats["searched"] = popped
        if popped > (expansions or MAX_EXPANSIONS):
            return None   # a route that does not exist would otherwise search the whole map
        (x, y), heading, after_exit = state
        if (x, y) == goal and not after_exit:
            end = state
            break
        budget = expansions or MAX_EXPANSIONS
        if popped == SETTLE_AFTER and not settled and budget >= 2 * SETTLE_AFTER:
            # A long search: a cut across the whole window (a river, a wall from edge to edge) leaves both ends more
            # ground than the first flood looks at. Flooded once more, with what the search had left to spend (half
            # each side), it settles it: about 1.4 s at +-256 tiles where the search's give-up took 7 s. A window
            # too big to flood in that stays the search's to decide; a route that does exist goes on searching.
            settled = True
            if _apart(start, goal, free, hops, cap=(budget - SETTLE_AFTER) // 2):
                return None
        for d, (dx, dy) in STEP.items():
            if after_exit and d != heading or heading is not None and d == (heading + 8) % 16:
                continue   # straight on after an exit; never back the way it came
            turn = TURN_COST if heading is not None and d != heading else 0
            moves = [((x + dx, y + dy), False, 1 + turn, None)]
            if hops:   # after an exit only straight on (checked above): exit then entrance weaves across a bus
                # Entrance on the next tile (fed from behind), exit 2..5 tiles beyond it.
                entrance = (x + dx, y + dy)
                if entrance != goal and free(entrance):
                    for span in range(2, UNDERGROUND_SPAN + 1):
                        exit_ = (entrance[0] + dx * span, entrance[1] + dy * span)
                        if exit_ == goal or (goal[0] - entrance[0]) * dy == (goal[1] - entrance[1]) * dx and                                 0 < (goal[0] - entrance[0]) * dx + (goal[1] - entrance[1]) * dy < span:
                            break   # never hop onto or over the goal
                        if free(exit_):
                            # (a shorter hop wins a tie: the tidier crossing)
                            moves.append((exit_, True, span + 1 + UNDERGROUND_COST + 0.01 * span + turn, entrance))
            for tile, exited, step, entrance in moves:
                if not free(tile):
                    continue
                nxt = (tile, d, exited)
                if cost + step + h(tile) > 2 * limit:
                    continue   # could only end longer than `limit` tiles (each tile costs at least one)
                if cost + step < best.get(nxt, math.inf):
                    best[nxt] = cost + step
                    came[nxt] = (state, entrance)
                    count += 1
                    heapq.heappush(heap, (cost + step + GREED * h(tile), cost + step, count, nxt))
    if end is None:
        return None
    # Walk back: tiles in order, with the underground pairs marked.
    tiles: list[tuple[tuple[int, int], str | None]] = []
    state = end
    while state is not None:
        link = came[state]
        if link and link[1] is not None:
            tiles.append((state[0], "output"))
            tiles.append((link[1], "input"))
        else:
            tiles.append((state[0], None))
        state = link[0] if link else None
    tiles.reverse()
    if len(tiles) > limit or len({tile for tile, _ in tiles}) < len(tiles):
        return None   # too long, or it would reuse a tile
    belts = []
    for i, ((x, y), kind) in enumerate(tiles):
        if kind is not None:   # entrance and exit both face the way the hop goes
            belts.append(Placed("underground-belt", x + 0.5, y + 0.5, _towards(tiles[i - 1][0], (x, y)), kind=kind))
            continue
        nxt = tiles[i + 1][0] if i + 1 < len(tiles) else None
        direction = _towards((x, y), nxt) if nxt else (belts[-1].direction if belts else SOUTH)
        belts.append(Placed("transport-belt", x + 0.5, y + 0.5, direction))
    return belts


def _towards(a: tuple[int, int], b: tuple[int, int]) -> int:
    dx, dy = b[0] - a[0], b[1] - a[1]
    unit = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
    return next(d for d, v in STEP.items() if v == unit)


def outlets(entities: Iterable[Placed]) -> set[tuple[int, int]]:
    """The tiles open belt ends put what they carry on: a belt (or an underground's exit) whose next tile holds none
    of these buildings. A route laid there takes the overflow: a smelting column's feed tail spilled its spare coal
    onto the copper ore route, which carried it round into the head, and the ore lane clogged with coal (run 28,
    Codex's replay, 2026-10-25). Kept from later routes, as an existing belt's are (base_plan.blocked_tiles)."""
    ents = list(entities)
    taken = footprint(ents)
    out = set()
    for e in ents:
        belt = "transport-belt" in e.name or "underground-belt" in e.name
        if not belt or ("underground-belt" in e.name and e.kind == "input"):
            continue   # (an underground's entrance feeds its exit)
        dx, dy = STEP.get(e.direction, (0, 0))
        ahead = (math.floor(e.x) + dx, math.floor(e.y) + dy)
        if ahead not in taken:
            out.add(ahead)
    return out


# Buildings longer one way than the other, (width, height) facing north or south: a power plant's.
RECTS = {"boiler": (3, 2), "steam-engine": (3, 5), "offshore-pump": (1, 2)}


def footprint(entities: Iterable[Placed]) -> set[tuple[int, int]]:
    """Tiles covered by `entities` (3x3 drills and assemblers, 2x2 furnaces, splitters and loaders, a power plant's
    boilers, engines and pumps by RECTS, turned with them; 1x1 rest). (A pole chain once took a steam engine's tiles
    for free: its last pole gave way to the engine and left a coal field off the network, run 59, 2026-10-26.)"""
    sizes = {"electric-mining-drill": 3, "assembling-machine-1": 3, "assembling-machine-2": 3,
             "assembling-machine-3": 3, "stone-furnace": 2, "steel-furnace": 2, "electric-furnace": 3, "lab": 3,
             "chemical-plant": 3, "oil-refinery": 5, "pumpjack": 3, "storage-tank": 3, "rocket-silo": 9,
             "gun-turret": 2, "radar": 3, "roboport": 4, "big-electric-pole": 2, "substation": 2}
    tiles = set()
    for e in entities:
        n = sizes.get(e.name, 1)
        if e.name == "splitter":
            if e.direction in (NORTH, SOUTH):
                tiles.update({(math.floor(e.x - 0.5), math.floor(e.y)), (math.floor(e.x + 0.5), math.floor(e.y))})
            else:
                tiles.update({(math.floor(e.x), math.floor(e.y - 0.5)), (math.floor(e.x), math.floor(e.y + 0.5))})
            continue
        if e.name in LOADERS:
            tiles |= loader_tiles(e.x, e.y, e.direction)
            continue
        if e.name in RECTS:
            w, h = RECTS[e.name]
            w, h = (h, w) if e.direction in (EAST, WEST) else (w, h)
            x0, y0 = math.floor(e.x - w / 2 + 0.5), math.floor(e.y - h / 2 + 0.5)
            tiles.update((x0 + i, y0 + j) for i in range(w) for j in range(h))
            continue
        for i in range(n):
            for j in range(n):
                tiles.add((math.floor(e.x - n / 2 + 0.5) + i, math.floor(e.y - n / 2 + 0.5) + j))
    return tiles
