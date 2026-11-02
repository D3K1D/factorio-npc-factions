"""The nuclear unit cell, laid from the game's own connection points.

Each prototype's heat and fluid connections (north-facing: x and y of the connection's edge tile from the
piece's centre, and the way it reaches) say where the next piece must stand: every piece goes where its
connection meets the last one's, so the layout follows the game's data rather than fixed offsets.

The cell: two reactors touching (their facing heat connections meet: the neighbour bonus); a heat-pipe spine out
of each, the first's north side and the second's south; heat exchangers on both sides of each spine facing away
from it, their heat input on the spine and their water passing through the exchanger next along; and a turbine
(two where the ratio needs) in line behind each one's steam. Sized by companion/nuclear.py; fuel, water, power
and placement on the map come with the builder.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

STEPS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
NAMES = {"reactor": "nuclear-reactor", "pipe": "heat-pipe", "exchanger": "heat-exchanger", "turbine": "steam-turbine"}


def turn(vector, direction: int) -> tuple[float, float]:
    x, y = vector
    return {0: (x, y), 4: (-y, x), 8: (-x, -y), 12: (y, -x)}[direction % 16]


def connection(center, direction: int, conn) -> tuple[tuple[int, int], tuple[int, int], int]:
    """A connection of a piece at `center` facing `direction`: (its own tile, the tile it reaches, its facing)."""
    px, py = turn((conn[0], conn[1]), direction)
    facing = (int(conn[2]) + direction) % 16
    dx, dy = STEPS[facing]
    tile = (math.floor(center[0] + px), math.floor(center[1] + py))
    return tile, (tile[0] + dx, tile[1] + dy), facing


def meeting(own: tuple[int, int], conn, direction: int) -> tuple[float, float]:
    """The centre of a piece facing `direction` whose connection `conn` has its own tile at `own`."""
    px, py = turn((conn[0], conn[1]), direction)
    return (own[0] + 0.5 - px, own[1] + 0.5 - py)


def tiles_of(center, w, h, direction) -> set[tuple[int, int]]:
    """Tiles of a w x h piece (its north-facing size) at `center` facing `direction`."""
    if direction in (4, 12):
        w, h = h, w
    x0, y0 = center[0] - w / 2, center[1] - h / 2
    return {(math.floor(x0 + 0.5) + i, math.floor(y0 + 0.5) + j) for i in range(int(w)) for j in range(int(h))}


def turbine_box(T) -> list:
    """The connections of the turbine's one fluid box the steam passes through (steam's own first; a generator's
    separate output box is not the way on)."""
    boxes = [b for b in T.get("fluid") or [] if len(b.get("c") or []) >= 2 and b.get("filter") in ("steam", None)]
    boxes.sort(key=lambda b: b.get("filter") != "steam")
    return list(boxes[0]["c"]) if boxes else []


def _parts(protos, names):
    R, X, T = protos[names["reactor"]], protos[names["exchanger"]], protos[names["turbine"]]
    heat_in = ((X.get("heat_src") or X.get("heat") or [None]))[0]
    water = next((b["c"] for b in X.get("fluid") or [] if b.get("filter") == "water" and len(b.get("c") or []) >= 2), None)
    steam = next((b["c"] for b in X.get("fluid") or [] if b.get("filter") == "steam" and b.get("c")), None)
    through = turbine_box(T)
    if heat_in is None or water is None or steam is None or len(through) < 2 or not R.get("heat"):
        # (a mod's piece joined otherwise: a heat exchanger without a water box passing through, a one-ended turbine)
        raise ValueError("the pieces' connections are not a reactor, a heat exchanger with water through it and "
                         "steam out, and a turbine with steam through it")
    t_in, t_out = sorted(through, key=lambda c: -c[1])[:2]   # (the south end takes the steam in)
    return R, X, T, heat_in, water, steam[0], t_in, t_out


def unit_cell(protos: Mapping[str, Any], exchangers: int, turbines: int, names: Mapping[str, str] = NAMES,
              gap: int = 1) -> dict:
    """The cell for `exchangers` heat exchangers and `turbines` turbines, `gap` free tiles between rows of them (a
    pole's place among the turbines, the water passing on by a pipe there). Returns {"entities": [(name, x, y,
    direction)], "water": [the tile each exchanger chain takes its water at], "reactors": [their centres],
    "tiles": every tile used, "gaps": the free tiles between turbine rows}."""
    R, X, T, heat_in, water_c, steam_out, t_in, t_out = _parts(protos, names)
    out: list[tuple[str, float, float, int]] = []
    taken: set[tuple[int, int]] = set()

    def put(name, center, direction, size):
        tiles = tiles_of(center, size["w"], size["h"], direction)
        if tiles & taken:
            raise ValueError(f"{name} would overlap at {center}")
        taken.update(tiles)
        out.append((name, center[0], center[1], direction))
    r1, r2 = (0.5, 0.5), (0.5 + R["w"], 0.5)
    put(names["reactor"], r1, 0, R)
    put(names["reactor"], r2, 0, R)
    counts = [exchangers - exchangers // 2, exchangers // 2]
    second = turbines - exchangers   # (how many exchangers get a second turbine)
    water: list[tuple[int, int]] = []
    for (reactor, side), count in zip(((r1, 0), (r2, 8)), counts):
        if count <= 0:
            continue
        conn = next((c for c in R["heat"] if int(c[2]) == side and abs(c[0]) < 0.01), None)
        if conn is None:
            raise ValueError("the reactor has no heat connection in the middle of its side")
        _, start, facing = connection(reactor, 0, conn)
        dx, dy = STEPS[facing]
        span = X["w"]   # an exchanger turned side-on is this many tiles along the spine
        pitch = span + gap
        spine = [(start[0] + dx * k, start[1] + dy * k) for k in range(math.ceil(count / 2) * pitch - gap)]
        for t in spine:
            put(names["pipe"], (t[0] + 0.5, t[1] + 0.5), 0, protos[names["pipe"]])
        chains: dict[int, list] = {12: [], 4: []}
        for k in range(count):
            row, direction = k // 2, (12 if k % 2 == 0 else 4)   # west of the spine, then east
            at = spine[row * pitch + span // 2]
            _, _, hfacing = connection((0.5, 0.5), direction, heat_in)
            hdx, hdy = STEPS[hfacing]
            center = meeting((at[0] - hdx, at[1] - hdy), heat_in, direction)
            put(names["exchanger"], center, direction, X)
            if chains[direction] and gap:
                # the water on from the exchanger before: a pipe where their water connections meet across the gap
                before = {connection(chains[direction][-1], direction, c)[1] for c in water_c}
                for c in water_c:
                    meet = connection(center, direction, c)[1]
                    if meet in before:
                        put(names.get("water_pipe", "pipe"), (meet[0] + 0.5, meet[1] + 0.5), 0, {"w": 1, "h": 1})
            chains[direction].append(center)
            _, reach, sfacing = connection(center, direction, steam_out)
            for _ in range(2 if second > 0 else 1):
                tcenter = meeting(reach, t_in, sfacing)   # (a turbine facing the way the steam goes)
                put(names["turbine"], tcenter, sfacing, T)
                _, reach, sfacing = connection(tcenter, sfacing, t_out)
            second -= 1
        # each chain takes its water at its far end: the connection reaching away from the reactor
        for direction, centers in chains.items():
            if centers:
                ends = [connection(centers[-1], direction, c)[1] for c in water_c]
                water.append(max(ends, key=lambda t: abs(t[1] - start[1])))
    xs, ys = [x for x, _ in taken], [y for _, y in taken]
    gaps = {(x, y) for x in range(min(xs), max(xs) + 1) for y in range(min(ys), max(ys) + 1)} - taken if gap else set()
    return {"entities": out, "water": water, "reactors": [r1, r2], "tiles": taken, "gaps": gaps}


def check(cell: Mapping[str, Any], protos: Mapping[str, Any], names: Mapping[str, str] = NAMES) -> list[str]:
    """What does not meet in a cell: an exchanger's heat input not on a heat pipe; its steam not reaching a turbine
    whose end faces back; exchangers of a chain whose water does not pass from one to the next; reactors not
    touching at facing heat connections. Empty when all meet."""
    R, X, T, heat_in, water_c, steam_out, _, _ = _parts(protos, names)
    at: dict[tuple[int, int], tuple] = {}
    for e in cell["entities"]:
        size = protos.get(e[0]) or {"w": 1, "h": 1}   # (a water pipe)
        for t in tiles_of((e[1], e[2]), size["w"], size["h"], e[3]):
            at[t] = e
    problems = []

    def meets(e, conn, kind):
        """Whether `e`'s connection reaches a piece of `kind` whose own connection reaches back to it."""
        own, reach, facing = connection((e[1], e[2]), e[3], conn)
        other = at.get(reach)
        if kind == "exchanger" and other and other[0] == names.get("water_pipe", "pipe"):
            # (the water on through a pipe across the gap between rows)
            own, reach = reach, (reach[0] + STEPS[facing][0], reach[1] + STEPS[facing][1])
            other = at.get(reach)
        if not other or other[0] != names[kind]:
            return False
        if kind == "pipe":
            return True
        conns = turbine_box(T) if kind == "turbine" else water_c
        return any(connection((other[1], other[2]), other[3], c)[1] == own for c in conns)
    for e in cell["entities"]:
        if e[0] != names["exchanger"]:
            continue
        if not meets(e, heat_in, "pipe"):
            problems.append(f"exchanger at {e[1:3]}: its heat input reaches no heat pipe")
        if not meets(e, steam_out, "turbine"):
            problems.append(f"exchanger at {e[1:3]}: its steam reaches no turbine facing back")
        # (a chain is the exchangers in one line facing one way; a lone one has no neighbour to pass to)
        if not any(meets(e, c, "exchanger") for c in water_c) and sum(
                1 for x in cell["entities"] if x[0] == names["exchanger"] and x[3] == e[3]
                and (x[1] == e[1] if e[3] in (4, 12) else x[2] == e[2])) > 1:
            problems.append(f"exchanger at {e[1:3]}: its water passes to no exchanger beside it")
    reactors = [e for e in cell["entities"] if e[0] == names["reactor"]]
    touching = sum(1 for a in reactors for c in R["heat"]
                   if (at.get(connection((a[1], a[2]), a[3], c)[1]) or ("",))[0] == names["reactor"])
    if len(reactors) > 1 and touching < 2:
        problems.append("the reactors do not touch at facing heat connections")
    return problems
