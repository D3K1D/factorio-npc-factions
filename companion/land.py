"""The land round an engineer's base, square by square, for planning its factory's large sections.

The wide map (an npc-map-v1 dump: buildings, ore, water, trees, rocks and cliffs) is cut into cells of
CELL tiles, laid round the engineer's home. Each cell on the engineer's own ground (in the team world:
its plot; elsewhere: the map round home) says what it holds: free ground, ore, water, buildings and
which of the engineer's blocks stand there, and the trees, rocks and cliffs to clear before building.
The architect names cells for its sections instead of writing coordinates, and the code turns them
into world boxes; the same counts tell when a zone kept for later has been cleared.

    land = summarize(world, site)
    land.lines()                       # one plain line per cell, for the architect's brief
    land.box(["C4", "C5"])             # the world box the cells cover
    obstructions(world, (x0, y0, x1, y1))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from companion import ledger, team_area

CELL = 64            # tiles a side
MAX_CELLS = 36       # cells in a brief, nearest home first (a 6 x 6 square of own ground)
OWN_SHARE = 0.25     # a cell needs this much of its ground the engineer's own to be listed
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

Box = tuple[float, float, float, float]


@dataclass
class Cell:
    id: str
    box: tuple[int, int, int, int]       # world tiles: x0, y0, x1, y1 (edges)
    own: int = 0                         # tiles the engineer may build on
    water: int = 0
    built: int = 0                       # tiles under buildings (any force)
    ore: dict[str, int] = field(default_factory=dict)   # resource -> tiles
    trees: int = 0
    rocks: int = 0
    cliffs: int = 0
    nests: int = 0                       # biter nests and worms
    blocks: list[str] = field(default_factory=list)     # kinds of the engineer's blocks standing here
    home: bool = False

    @property
    def free(self) -> int:
        return max(0, self.own - self.water - self.built)

    def line(self) -> str:
        size = (self.box[2] - self.box[0]) * (self.box[3] - self.box[1])
        parts = [f"free {round(100 * self.free / size)}%"]
        if self.own < size:
            parts.append(f"own ground {round(100 * self.own / size)}%")
        if self.water:
            parts.append(f"water {round(100 * self.water / size)}%")
        if self.built:
            parts.append(f"built {round(100 * self.built / size)}%"
                         + (f" ({', '.join(self.blocks[:3])})" if self.blocks else ""))
        ores = ", ".join(f"{name} {n}" for name, n in sorted(self.ore.items(), key=lambda kv: -kv[1])[:3])
        if ores:
            parts.append("ore tiles " + ores)
        clear = ", ".join(f"{n} {what}" for what, n in (("trees", self.trees), ("rocks", self.rocks),
                                                       ("cliffs", self.cliffs)) if n)
        if clear:
            parts.append(clear)
        if self.nests:
            parts.append(f"{self.nests} biter nests and worms")
        return f"{self.id}{' (home)' if self.home else ''}: " + "; ".join(parts)


@dataclass
class Land:
    cells: list[Cell]
    size: int = CELL

    def cell(self, cell_id: str) -> Cell | None:
        return next((c for c in self.cells if c.id == str(cell_id).strip().upper()), None)

    def box(self, ids: Iterable[str]) -> tuple[int, int, int, int] | None:
        """The world box the named cells cover (their bounding box), or None when none is known."""
        found = [c for c in (self.cell(i) for i in ids) if c]
        if not found:
            return None
        return (min(c.box[0] for c in found), min(c.box[1] for c in found),
                max(c.box[2] for c in found), max(c.box[3] for c in found))

    def boxes(self, ids: Iterable[str]) -> list[tuple[int, int, int, int]]:
        """The world boxes that cover just the named cells, the biggest group of touching cells first, each as few
        rectangles as cover it. One box round cells that do not touch, or round an L of cells, covers the ground
        between them too: a defense section's cells along the plot's edges made a box the size of the plot,
        kept from every other planner (runs 25 and 26, 2026-10-25)."""
        found = list({c.id: c for c in (self.cell(i) for i in ids) if c}.values())
        groups: list[list[Cell]] = []
        left = list(found)
        while left:
            group, edge = [left.pop(0)], 0
            while edge < len(group):
                a = group[edge]
                edge += 1
                for c in list(left):
                    touching = ((a.box[2] == c.box[0] or c.box[2] == a.box[0]) and a.box[1] < c.box[3] and c.box[1] < a.box[3]) \
                        or ((a.box[3] == c.box[1] or c.box[3] == a.box[1]) and a.box[0] < c.box[2] and c.box[0] < a.box[2])
                    if touching:
                        group.append(c)
                        left.remove(c)
            groups.append(group)
        groups.sort(key=lambda g: -len(g))
        out: list[tuple[int, int, int, int]] = []
        for g in groups:   # each group as rectangles that cover just its cells (an L is two, not the box round it)
            rows: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for c in sorted(g, key=lambda c: (c.box[1], c.box[0])):
                runs = rows.setdefault((c.box[1], c.box[3]), [])
                if runs and runs[-1][1] == c.box[0]:
                    runs[-1] = (runs[-1][0], c.box[2])
                else:
                    runs.append((c.box[0], c.box[2]))
            rects: list[list[int]] = []
            for (y0, y1), runs in sorted(rows.items()):
                for x0, x1 in runs:
                    below = next((r for r in rects if r[0] == x0 and r[2] == x1 and r[3] == y0), None)
                    if below:
                        below[3] = y1
                    else:
                        rects.append([x0, y0, x1, y1])
            rects.sort(key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))
            out += [tuple(r) for r in rects]
        return out

    def cells_in(self, box: Box) -> list[Cell]:
        return [c for c in self.cells if c.box[0] < box[2] and c.box[2] > box[0] and c.box[1] < box[3]
                and c.box[3] > box[1]]

    def lines(self) -> list[str]:
        return [c.line() for c in self.cells]


def _grid(world: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """The grid's first cell corner and how many cells across and down: laid so home sits in the middle
    of a cell, over the whole map the dump covers."""
    home = world.get("home") or {"x": 0, "y": 0}
    hx, hy = float(home["x"]), float(home["y"])
    area = world.get("area") or (hx - 256, hy - 256, hx + 256, hy + 256)
    x0 = math.floor(hx - CELL / 2) - CELL * math.ceil((hx - CELL / 2 - area[0]) / CELL)
    y0 = math.floor(hy - CELL / 2) - CELL * math.ceil((hy - CELL / 2 - area[1]) / CELL)
    across = max(1, math.ceil((area[2] - x0) / CELL))
    down = max(1, math.ceil((area[3] - y0) / CELL))
    return x0, y0, min(across, len(LETTERS)), down


def summarize(world: Mapping[str, Any], site: Mapping[str, Any] | None = None, limit: int = MAX_CELLS) -> Land:
    """The cells of the engineer's own ground, nearest home first (at most `limit`)."""
    x0, y0, across, down = _grid(world)
    area = world.get("area") or (x0, y0, x0 + across * CELL, y0 + down * CELL)
    cells: dict[tuple[int, int], Cell] = {}
    for i in range(across):
        for j in range(down):
            box = (x0 + i * CELL, y0 + j * CELL, x0 + (i + 1) * CELL, y0 + (j + 1) * CELL)
            # (only what the map covers counts: beyond it nothing is known)
            seen = max(0, min(box[2], area[2]) - max(box[0], area[0])) * max(0, min(box[3], area[3]) - max(box[1], area[1]))
            cells[(i, j)] = Cell(f"{LETTERS[i]}{j + 1}", box, own=int(seen))

    def at(x: float, y: float) -> Cell | None:
        return cells.get((math.floor((x - x0) / CELL), math.floor((y - y0) / CELL)))

    def runs(rows: Iterable, add) -> None:
        """Run-length rows (y, x1, x2), split over the cells they cross."""
        for row in rows or ():
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            y, a, b = int(row[0]), int(row[1]), int(row[2])
            x = a
            while x <= b:
                c = at(x, y)
                end = min(b, (math.floor((x - x0) / CELL) + 1) * CELL + x0 - 1)
                if c:
                    add(c, end - x + 1)
                x = end + 1

    for tile in team_area.forbidden(world):
        c = at(tile[0], tile[1])
        if c:
            c.own -= 1
    runs(world.get("water"), lambda c, n: setattr(c, "water", c.water + n))
    for name, rows in (world.get("resources") or {}).items():
        runs(rows, lambda c, n, name=name: c.ore.__setitem__(name, c.ore.get(name, 0) + n))
    for t in world.get("trees") or ():
        c = at(t[0], t[1]) if isinstance(t, (list, tuple)) and len(t) >= 2 else None
        if c:
            c.trees += 1
    for e in world.get("entities") or ():
        c = at(e.get("x", 0), e.get("y", 0))
        if not c:
            continue
        kind = e.get("t")
        if kind == "cliff":
            c.cliffs += 1
        elif kind == "simple-entity":
            c.rocks += 1
        elif e.get("f") == "enemy":
            c.nests += 1 if kind in ("unit-spawner", "turret") else 0
        elif kind not in ("tree", "fish", "character"):
            c.built += int(e.get("w") or 1) * int(e.get("h") or 1)
    origin = (site or {}).get("origin") or {"x": 0, "y": 0}
    for block in (site or {}).get("base") or ():
        box = block.get("box") if isinstance(block, Mapping) else None
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        c = at((box[0] + box[2]) / 2 + float(origin["x"]), (box[1] + box[3]) / 2 + float(origin["y"]))
        kind = ledger.kind_of(block.get("purpose"), block.get("stage"))
        if c and kind not in c.blocks:
            c.blocks.append(kind)
    home = world.get("home") or {"x": 0, "y": 0}
    mine = at(float(home["x"]), float(home["y"]))
    if mine:
        mine.home = True
    hx, hy = float(home["x"]), float(home["y"])
    listed = [c for c in cells.values() if c.own >= OWN_SHARE * CELL * CELL and c.water < 0.9 * CELL * CELL]
    listed.sort(key=lambda c: ((c.box[0] + c.box[2]) / 2 - hx) ** 2 + ((c.box[1] + c.box[3]) / 2 - hy) ** 2)
    kept = listed[:limit]
    kept.sort(key=lambda c: (c.box[1], c.box[0]))
    return Land(kept)


def reserved_tiles(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Tiles of the ground the factory plan keeps for its other sections (the dump's `reserved` boxes, world
    tiles, put there for the planner of one goal): its planners build nothing there. Ore in that ground
    stays open, so mining fields and their belts still reach the ore. Worked out once per dump."""
    boxes = dump.get("reserved") if isinstance(dump, Mapping) else None
    hard = dump.get("reserved_hard") if isinstance(dump, Mapping) else None
    if not boxes and not hard:
        return set()
    cached = dump.get("_reserved_tiles") if isinstance(dump, dict) else None
    if cached is not None:
        return cached
    boxes = boxes or []
    out: set[tuple[int, int]] = set()
    rects = [tuple(math.floor(v) for v in b) for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4]
    for x0, y0, x1, y1 in rects:
        out.update((x, y) for x in range(x0, x1) for y in range(y0, y1))
    for rows in (dump.get("resources") or {}).values():
        for y, a, b in rows or ():
            for x0, y0, x1, y1 in rects:
                if y0 <= y < y1 and a < x1 and b >= x0:
                    out.difference_update((x, y) for x in range(max(a, x0), min(b, x1 - 1) + 1))
    # (the bus's own planned ground, ore and all: its fields are planned there; plan_keeper.bus_zones)
    for x0, y0, x1, y1 in (tuple(math.floor(v) for v in b) for b in hard or ()
                           if isinstance(b, (list, tuple)) and len(b) == 4):
        out.update((x, y) for x in range(x0, x1) for y in range(y0, y1))
    if isinstance(dump, dict):
        dump["_reserved_tiles"] = out
    return out


def obstructions(world: Mapping[str, Any], box: Box) -> dict[str, int]:
    """Trees, rocks and cliffs standing in a world box: what is left to clear there."""
    x0, y0, x1, y1 = box
    out = {"trees": 0, "rocks": 0, "cliffs": 0}
    for t in world.get("trees") or ():
        if isinstance(t, (list, tuple)) and len(t) >= 2 and x0 <= t[0] < x1 and y0 <= t[1] < y1:
            out["trees"] += 1
    for e in world.get("entities") or ():
        kind = e.get("t")
        if kind in ("simple-entity", "cliff") and x0 <= e.get("x", 0) < x1 and y0 <= e.get("y", 0) < y1:
            out["rocks" if kind == "simple-entity" else "cliffs"] += 1
    return out
