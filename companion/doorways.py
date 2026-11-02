"""The way through each door of a wall kept clear: a straight lane in front of it and behind it, free of buildings.

A car or tank lines up on a door's axis about a dozen tiles out and drives straight through without the pathfinder
(NPC Movement's door crossing: the pathfinder will not take a vehicle's box through a 4-wide door, its M-2 probe,
2026-10-26), and an engineer on foot walks the same way. So every planner keeps a lane DOOR_LANE deep on both sides
of each door, its width and a tile more either side across (base_plan.blocked_tiles). A door is a run of gates side by
side through the wall's layers, found on the map itself, so doors already standing are kept as well as new ones.
Only the door's own wall may still grow into its lane, as thick as walls get (wall_room): gates in its columns, walls
beside them.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

DOOR_LANE = 12      # tiles straight out from a door, either side, kept free
LANE_SIDE = 1       # tiles beside the door's width, either side, kept free too
MAX_LAYERS = 4      # walls are at most this thick (perimeter.MAX_LAYERS): a door may grow this deep

Box = tuple[int, int, int, int]


def doors(dump: Mapping[str, Any], force: str | None = None) -> list[tuple[Box, tuple[bool, ...]]]:
    """Each door on the map (of `force` only, when given): (its box of gate tiles x0, y0, x1, y1 inclusive, the ways its
    wall may run: True east-west, False north-south). Gates of anyone but the biters, side by side; a gate across a line
    running east-west faces east or west (perimeter._layer); a door with no direction given is wider along its wall,
    either way when it is square."""
    gates: dict[tuple[int, int], tuple] = {}
    for e in dump.get("entities") or []:
        if isinstance(e, Mapping) and e.get("t") == "gate" and e.get("f") != "enemy" and "x" in e and "y" in e                 and (force is None or e.get("f") == force):
            d = e.get("d")
            gates[(math.floor(e["x"]), math.floor(e["y"]))] = (int(d) % 8 if isinstance(d, (int, float)) else None,
                                                               e.get("f"))
    out, seen = [], set()
    for start in gates:
        if start in seen:
            continue
        seen.add(start)
        group, todo = [start], [start]
        while todo:
            x, y = todo.pop()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in gates and n not in seen and gates[n] == gates[start]:
                    seen.add(n)
                    group.append(n)
                    todo.append(n)
        xs, ys = [p[0] for p in group], [p[1] for p in group]
        box = (min(xs), min(ys), max(xs), max(ys))
        facing, wide, deep = gates[start][0], box[2] - box[0], box[3] - box[1]
        ways = (facing == 4,) if facing is not None else (True, False) if wide == deep else (wide > deep,)
        out.append((box, ways))
    return out


def lane(box: Box, across_ew: bool, depth: int = DOOR_LANE) -> set[tuple[int, int]]:
    """The tiles of a door's lanes: `depth` tiles straight out from both its faces, LANE_SIDE wider either side."""
    x0, y0, x1, y1 = box
    if across_ew:   # (the wall runs east-west: the way through runs north-south)
        cols = range(x0 - LANE_SIDE, x1 + LANE_SIDE + 1)
        rows = list(range(y0 - depth, y0)) + list(range(y1 + 1, y1 + depth + 1))
        return {(x, y) for x in cols for y in rows}
    rows_ = range(y0 - LANE_SIDE, y1 + LANE_SIDE + 1)
    cols_ = list(range(x0 - depth, x0)) + list(range(x1 + 1, x1 + depth + 1))
    return {(x, y) for x in cols_ for y in rows_}


def _deep(box: Box, across_ew: bool) -> int:
    return (box[3] - box[1] + 1) if across_ew else (box[2] - box[0] + 1)


def lanes(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Every door's lanes on the map (worked out once per dump)."""
    if not isinstance(dump, Mapping):
        return set()
    cached = dump.get("_lane_tiles") if isinstance(dump, dict) else None
    if cached is not None:
        return cached
    out: set[tuple[int, int]] = set()
    for box, ways in doors(dump):
        for across_ew in ways:
            out |= lane(box, across_ew)
    if isinstance(dump, dict):
        dump["_lane_tiles"] = out
    return out


def wall_room(dump: Mapping[str, Any], force: str | None = None) -> dict[tuple[int, int], tuple[bool, bool]]:
    """Where `force`'s own doors' walls may still grow into their lanes (as many rows either side as a door is short of
    MAX_LAYERS deep, across the lane's width), where nothing stands and the game builds: a wall builder's only way onto
    a lane. Each tile: (the way its door's wall runs, whether it is in the door's own columns: a gate goes there, a
    wall only beside them)."""
    if not isinstance(dump, Mapping):
        return {}
    key = f"_wall_room:{force}"
    cached = dump.get(key) if isinstance(dump, dict) else None
    if cached is not None:
        return cached
    room: dict[tuple[int, int], tuple[bool, bool]] = {}
    for box, ways in doors(dump, force):
        for across_ew in ways:
            short = MAX_LAYERS - _deep(box, across_ew)
            if short <= 0:
                continue
            lo, hi = (box[0], box[2]) if across_ew else (box[1], box[3])
            for tile in lane(box, across_ew, depth=short):
                room[tile] = (across_ew, lo <= (tile[0] if across_ew else tile[1]) <= hi)
    if room:
        standing: set[tuple[int, int]] = set()
        for e in dump.get("entities") or []:
            if not isinstance(e, Mapping) or e.get("t") in ("tree", "simple-entity") or "x" not in e:
                continue
            w, h = int(e.get("w") or 1), int(e.get("h") or 1)
            if (e.get("d") or 0) in (4, 12):
                w, h = h, w
            x0, y0 = math.floor(e["x"] - w / 2 + 0.5), math.floor(e["y"] - h / 2 + 0.5)
            standing.update((x0 + i, y0 + j) for i in range(w) for j in range(h))
        for y, a, b in dump.get("water") or []:
            standing.update((x, y) for x in range(a, b + 1))
        for b in dump.get("avoid") or []:   # (ground the game refused to build on)
            if isinstance(b, Mapping) and all(isinstance(b.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")):
                standing.update((x, y) for x in range(int(b["x0"]), int(b["x1"]) + 1)
                                for y in range(int(b["y0"]), int(b["y1"]) + 1))
        room = {k: v for k, v in room.items() if k not in standing}
    if isinstance(dump, dict):
        dump[key] = room
    return room
