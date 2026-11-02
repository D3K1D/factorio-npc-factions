"""Pipe routing: carry one fluid from a machine's connection to another, the way a player would.

A route is a line of pipes, with underground pipe pairs to pass under
buildings, belts and other pipes. The router keeps fluids apart: a plain pipe
connects to every pipe beside it, so a plain pipe never sits next to another
fluid's plain pipe or on another machine's connection tile. An underground
pipe connects only on its open side and through the ground, so it may sit
beside anything; a pair must not have another pair's end between its ends on
the same line, or the ground connection would jump to it.

Tiles are (x, y) integers, x to the east, y to the south. Directions are
16-way as in modules.py; a pipe-to-ground faces its open side.

    pipes = PipeMap(blocked=buildings)
    pipes.reserve((10, 4), "crude-oil")         # a refinery's crude inlet
    placed = pipes.route("crude-oil", (40, 4), (10, 4))
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

SPAN = 10            # pipe-to-ground: the most tiles from one end to the other (max_underground_distance)
PIPE_COST = 1.0      # one pipe per tile
HOP_COST = 3.0       # two pipe-to-ground, any length: a building job saved per tile under ground
GREED = 1.2          # weighted A*: routes at most a fifth dearer, found in a fraction of the time (a
                     # remote power row's 370-tile water pipes hit the search cap one spot after another,
                     # 2 s each, run 55's map, 2026-10-26; now 0.01 to 0.3 s, and the nearest spot routes)
STEPS = {EAST: (1, 0), WEST: (-1, 0), SOUTH: (0, 1), NORTH: (0, -1)}
BACK = {EAST: WEST, WEST: EAST, NORTH: SOUTH, SOUTH: NORTH}

Tile = tuple[int, int]


@dataclass
class PipeMap:
    """What is on the ground, as far as fluids are concerned."""
    blocked: set[Tile] = field(default_factory=set)            # buildings, belts: pipes only pass under them
    pipes: dict[Tile, str] = field(default_factory=dict)        # plain pipes (and machine connection tiles in use)
    ends: dict[Tile, tuple[str, int]] = field(default_factory=dict)   # pipe-to-ground: (fluid, axis 0 = x, 1 = y)
    reserved: dict[Tile, str] = field(default_factory=dict)     # machine connection tiles; "" = keep clear
    hops: dict[tuple[int, int], list[tuple[int, int]]] = field(default_factory=dict)  # (axis, line) -> spans
    lines: dict[tuple[int, int], set[int]] = field(default_factory=dict)  # (axis, line) -> pipe-to-ground there
    faces: dict[Tile, int] = field(default_factory=dict)       # pipe-to-ground laid here: the side its pipe joins

    def reserve(self, tile: Tile, fluid: str) -> None:
        """A machine connects to `fluid` on this tile (or to nothing, with ""): no other pipe goes there."""
        self.reserved[tile] = fluid

    def add_pipes(self, placed: list[Placed], fluid: str) -> None:
        """Record pipes laid elsewhere (another route, a template)."""
        hop_start: dict[int, Tile] = {}
        for p in placed:
            t = (int(p.x - 0.5), int(p.y - 0.5))
            if p.name == "pipe":
                self.pipes[t] = fluid
            elif p.name == "pipe-to-ground":
                axis = 0 if p.direction in (EAST, WEST) else 1
                line = t[1] if axis == 0 else t[0]
                self.ends[t] = (fluid, axis)
                self.faces[t] = p.direction % 16
                self.lines.setdefault((axis, line), set()).add(t[axis])
                if axis in hop_start:
                    a = hop_start.pop(axis)
                    self.hops.setdefault((axis, line), []).append(tuple(sorted((a[axis], t[axis]))))
                else:
                    hop_start[axis] = t

    # -- rules ---------------------------------------------------------------

    def _free(self, t: Tile) -> bool:
        return t not in self.blocked and t not in self.pipes and t not in self.ends

    def _pipe_ok(self, t: Tile, fluid: str, own: set[Tile]) -> bool:
        """A plain pipe of `fluid` may go on `t`."""
        if t in own or not self._free(t):
            return False
        if self.reserved.get(t, fluid) != fluid:
            return False
        for d in STEPS.values():
            n = (t[0] + d[0], t[1] + d[1])
            if self.pipes.get(n, fluid) != fluid:
                return False
            # (a plain pipe joins every side: not beside a machine's connection kept for another fluid, which gets that
            # fluid's pipe later, nor at the open end of another fluid's pipe-to-ground on this line: the oil plant's
            # light oil pipe beside its heavy cracker's heavy oil inlet mixed the two, oil-practice-bus 2026-10-31)
            if self.reserved.get(n, "") not in ("", fluid):
                return False
            end = self.ends.get(n)
            if end is not None and end[0] != fluid and n in self.faces and STEPS[self.faces[n]] == (-d[0], -d[1]):
                return False
        return True

    def _end_ok(self, t: Tile, axis: int, own: set[Tile]) -> bool:
        if t in own or not self._free(t) or t in self.reserved:
            return False
        line, at = (t[1], t[0]) if axis == 0 else (t[0], t[1])
        return not any(lo < at < hi for lo, hi in self.hops.get((axis, line), ()))

    def _hop_ok(self, a: Tile, b: Tile, axis: int) -> bool:
        line = a[1] if axis == 0 else a[0]
        lo, hi = sorted((a[axis], b[axis]))
        return not any(lo < at < hi for at in self.lines.get((axis, line), ()))

    # -- search --------------------------------------------------------------

    def route(self, fluid: str, start: Tile, goal: Tile, *, limit: int = 200000,
              span: int = SPAN, joins: set[Tile] | None = None) -> list[Placed] | None:
        """Pipes from `start` to `goal` (both get a plain pipe: they are machine connection tiles or
        tiles beside one). `joins`: more tiles of the same fluid's pipes the route may start from
        instead (a branch). Records the route and returns it, or None."""
        sources = {start} | set(joins or ())
        if not self._pipe_ok(goal, fluid, set()) and self.pipes.get(goal) != fluid:
            return None
        h = lambda t: GREED * (abs(t[0] - goal[0]) + abs(t[1] - goal[1])) * HOP_COST / span
        # state: (tile, mode): mode None = plain pipe here; mode d = an exit facing d (must continue to t + d)
        heap: list = []
        order = itertools.count()
        came: dict = {}
        best: dict = {}
        for s in sources:
            if s == start and not self._pipe_ok(s, fluid, set()) and self.pipes.get(s) != fluid:
                continue
            key = (s, None)
            best[key] = 0.0
            came[key] = None
            heapq.heappush(heap, (h(s), 0.0, next(order), s, None))
        seen = 0
        while heap:
            _, cost, _, t, mode = heapq.heappop(heap)
            if cost > best.get((t, mode), float("inf")):
                continue
            if t == goal and mode is None:
                return self._commit(fluid, self._walk(came, (t, mode)))
            seen += 1
            if seen > limit:
                return None
            own = self._path_tiles(came, (t, mode))
            dirs = [mode] if mode is not None else list(STEPS)
            for d in dirs:
                dx, dy = STEPS[d]
                n = (t[0] + dx, t[1] + dy)
                # a plain pipe on the next tile
                if self._pipe_ok(n, fluid, own) or (n == goal and self.pipes.get(n) == fluid):
                    self._push(heap, best, came, (n, None), (t, mode, "pipe"), cost + PIPE_COST, h(n), order)
                # or an entrance there, and an exit k tiles further on
                axis = 0 if dx else 1
                if not self._end_ok(n, axis, own):
                    continue
                for k in range(2, span + 1):
                    e = (n[0] + dx * (k - 1), n[1] + dy * (k - 1))
                    if e in own or not self._end_ok(e, axis, own):
                        continue
                    if not self._hop_ok(n, e, axis):
                        break   # another pair's end lies on this line: no longer hop works either
                    self._push(heap, best, came, (e, d), (t, mode, ("hop", n, d)), cost + HOP_COST, h(e), order)
        return None

    @staticmethod
    def _push(heap, best, came, key, parent, cost, guess, order):
        if cost < best.get(key, float("inf")):
            best[key] = cost
            came[key] = parent
            heapq.heappush(heap, (cost + guess, cost, next(order), key[0], key[1]))

    @staticmethod
    def _path_tiles(came, key) -> set[Tile]:
        out: set[Tile] = set()
        while key is not None:
            out.add(key[0])
            parent = came.get(key)
            if parent is None:
                break
            t, mode, how = parent
            if isinstance(how, tuple):
                out.add(how[1])
            key = (t, mode)
        return out

    @staticmethod
    def _walk(came, key) -> list[tuple[str, Tile, int]]:
        """(kind, tile, direction) from start to goal: kind is pipe, enter or exit."""
        steps: list[tuple[str, Tile, int]] = []
        while True:
            parent = came.get(key)
            t, mode = key
            if parent is None:
                steps.append(("pipe", t, NORTH))
                break
            pt, pmode, how = parent
            if how == "pipe":
                steps.append(("pipe", t, NORTH))
            else:
                _, entrance, d = how
                steps.append(("exit", t, d))                 # open side faces on
                steps.append(("enter", entrance, BACK[d]))   # open side faces back
            key = (pt, pmode)
        steps.reverse()
        return steps

    def _commit(self, fluid: str, steps) -> list[Placed]:
        placed = []
        for kind, (x, y), d in steps:
            if kind == "pipe" and self.pipes.get((x, y)) == fluid:
                continue   # a branch starts on a pipe already laid
            if kind == "pipe":
                placed.append(Placed("pipe", x + 0.5, y + 0.5))
            else:
                placed.append(Placed("pipe-to-ground", x + 0.5, y + 0.5, d))
        self.add_pipes(placed, fluid)
        return placed
