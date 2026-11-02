"""Power lines: small-pole chains that join planned buildings to a power network."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from companion.modules import Placed

REACH = 7.5   # small electric pole wire reach
ROUTE_TRIES = 3   # the network's poles nearest a group tried in turn for a pole route to it


def groups(poles: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Poles split into wired groups."""
    left = list(poles)
    out = []
    while left:
        group, frontier = [left.pop()], None
        frontier = list(group)
        while frontier:
            p = frontier.pop()
            near = [q for q in left if math.dist(p, q) <= REACH]
            for q in near:
                left.remove(q)
                group.append(q)
                frontier.append(q)
        out.append(group)
    return out


def chain(start: tuple[float, float], goal: tuple[float, float], blocked: set[tuple[int, int]],
          limit: int = 200) -> list[Placed] | None:
    """Poles from `start` to within reach of `goal` on free tiles, each within reach of the last:
    straight on where it can, around lakes and buildings where it must."""
    line = _straight(start, goal, set(blocked), limit)
    if line is None:
        line = _around(start, goal, blocked)
    if line is not None:
        blocked.update((math.floor(e.x), math.floor(e.y)) for e in line)
    return line


# Where the next pole may stand, from the last one: far enough to cover ground, within wire reach.
_STEPS = [(dx, dy) for dx in range(-7, 8) for dy in range(-7, 8) if 4.0 <= math.hypot(dx, dy) <= 7.0]


def _around(start, goal, blocked, budget: int = 60000) -> list[Placed] | None:
    """A* over pole spots: the fewest poles that reach `goal` without standing on a blocked tile."""
    import heapq
    origin = (math.floor(start[0]), math.floor(start[1]))

    def spot(t):
        return start if t == origin else (t[0] + 0.5, t[1] + 0.5)

    def h(t):   # poles still needed (a little greedy: long lines stay quick to find)
        return 2.0 * max(0.0, math.dist(spot(t), goal) - REACH) / REACH

    frontier = [(h(origin), 0, origin)]
    came, cost = {origin: None}, {origin: 0}
    while frontier and budget > 0:
        _, g, t = heapq.heappop(frontier)
        if g > cost[t]:
            continue
        here = spot(t)
        if math.dist(here, goal) <= REACH:
            out = []
            while came[t] is not None:
                out.append(Placed("small-electric-pole", t[0] + 0.5, t[1] + 0.5))
                t = came[t]
            return out[::-1]
        budget -= 1
        for dx, dy in _STEPS:
            n = (t[0] + dx, t[1] + dy)
            if n in blocked or math.dist(here, (n[0] + 0.5, n[1] + 0.5)) > REACH:
                continue
            if g + 1 < cost.get(n, 1e9):
                cost[n], came[n] = g + 1, t
                heapq.heappush(frontier, (g + 1 + h(n), g + 1, n))
    return None


def _straight(start, goal, blocked, limit) -> list[Placed] | None:
    out, current = [], start
    for _ in range(limit):
        if math.dist(current, goal) <= REACH:
            return out
        best, best_d = None, None
        cx, cy = math.floor(current[0]), math.floor(current[1])
        for dx in range(-7, 8):
            for dy in range(-7, 8):
                p = (cx + dx + 0.5, cy + dy + 0.5)
                step = math.dist(current, p)
                if 2 <= step <= REACH and (cx + dx, cy + dy) not in blocked:
                    d = math.dist(p, goal)
                    if d < math.dist(current, goal) - 1 and (best_d is None or d < best_d):
                        best, best_d = p, d
        if best is None:
            return None
        out.append(Placed("small-electric-pole", best[0], best[1]))
        blocked.add((math.floor(best[0]), math.floor(best[1])))
        current = best
    return None   # (the caller's blocked tiles are only updated with the line it keeps)


def live_poles(dump: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The engineer's poles on a network a generator feeds (the map's live_nets; any pole when the map does not say):
    what a plan's poles must join. A pole group with no generator powers nothing: an array joined to one was built
    and its furnaces waited on fuel from unpowered drills for 200 minutes (F-21)."""
    live = set(dump.get("live_nets") or ())
    return [(e["x"], e["y"]) for e in dump.get("entities", [])
            if e.get("t") == "electric-pole" and e.get("f") == dump.get("force")
            and (not live or e.get("net") is None or e.get("net") in live)]


SUPPLY = {"small-electric-pole": 2.5, "medium-electric-pole": 3.5, "big-electric-pole": 2.0, "substation": 9.0}
ELECTRIC = ("electric-mining-drill", "inserter", "fast-inserter", "long-handed-inserter", "bulk-inserter",
            "stack-inserter", "assembling-machine-1", "assembling-machine-2", "assembling-machine-3", "lab",
            "electric-furnace", "chemical-plant", "oil-refinery", "pumpjack", "radar", "roboport", "beacon")
SIZE = {"electric-mining-drill": 3, "assembling-machine-1": 3, "assembling-machine-2": 3, "assembling-machine-3": 3,
        "lab": 3, "electric-furnace": 3, "chemical-plant": 3, "oil-refinery": 5, "pumpjack": 3, "radar": 3,
        "roboport": 4, "beacon": 3}


def cover(planned: list[Placed], dump: Mapping[str, Any], blocked: set[tuple[int, int]]) -> list[Placed]:
    """A small pole beside each machine of `planned` that needs power and stands in no pole's supply area (planned
    poles, and the map's poles on live networks): power.connect joins pole groups, it does not see what they power,
    and a block whose inserters stood outside every pole's area waited forever with "inserter: no_power" (F-21)."""
    supply = [(e.x, e.y, SUPPLY[e.name]) for e in planned if e.name in SUPPLY]
    live = set(dump.get("live_nets") or ())
    supply += [(float(e["x"]), float(e["y"]), SUPPLY.get(str(e.get("n")), 2.5)) for e in dump.get("entities") or ()
               if e.get("t") == "electric-pole" and e.get("f") == dump.get("force")
               and (not live or e.get("net") is None or e.get("net") in live)]
    used = set(blocked) | {(math.floor(e.x), math.floor(e.y)) for e in planned}
    for e in planned:
        n = SIZE.get(e.name, 1)
        used |= {(math.floor(e.x - n / 2 + 0.5) + dx, math.floor(e.y - n / 2 + 0.5) + dy)
                 for dx in range(n) for dy in range(n)}
    out = []
    for e in planned:
        if e.name not in ELECTRIC:
            continue
        half = SIZE.get(e.name, 1) / 2
        if any(abs(e.x - px) < r + half and abs(e.y - py) < r + half for px, py, r in supply):
            continue
        x0, y0 = math.floor(e.x - half), math.floor(e.y - half)
        n = SIZE.get(e.name, 1)
        ring = [(x0 + dx, y0 + dy) for dx in range(-1, n + 1) for dy in range(-1, n + 1)
                if dx in (-1, n) or dy in (-1, n)]
        spot = next((t for t in ring if t not in used), None)
        if spot is None:
            continue   # (no room beside it: the corrector's repair, or the plan's own check, says so)
        pole = Placed("small-electric-pole", spot[0] + 0.5, spot[1] + 0.5)
        out.append(pole)
        used.add(spot)
        supply.append((pole.x, pole.y, SUPPLY[pole.name]))
    return out


def connect(planned: Iterable[Placed], powered: list[tuple[float, float]],
            blocked: set[tuple[int, int]]) -> tuple[list[Placed], list[str]]:
    """Extra poles so every group of planned poles reaches the powered network (or a group already
    joined to it). Returns the poles and any groups that could not be reached."""
    # (any pole: a library block brings medium poles of its own, R-5b; grouped at the small pole's reach, the least)
    poles = [(e.x, e.y) for e in planned if e.name.endswith("electric-pole") or e.name == "substation"]
    connected = list(powered)
    extra, problems = [], []
    pending = groups(poles)
    # Join the groups nearest the network first; each joined group then extends the network.
    while pending:
        pending.sort(key=lambda g: min((math.dist(p, q) for p in g for q in connected), default=1e9))
        group = pending.pop(0)
        if not connected:
            connected.extend(group)
            continue
        pairs = sorted(((p, q) for p in group for q in connected), key=lambda pq: (math.dist(*pq), pq))
        a, b = pairs[0]
        if math.dist(a, b) > REACH:
            # (the nearest pole of the network may be boxed in: the next nearest ones are tried too, R-5)
            line, tried = None, set()
            for p, q in pairs:
                if q in tried:
                    continue
                tried.add(q)
                line = chain(p, q, blocked)
                if line is not None or len(tried) >= ROUTE_TRIES:
                    break
            if line is None:
                problems.append(f"no pole route from {a} to the network")
                continue
            extra.extend(line)
            connected.extend((e.x, e.y) for e in line)
        connected.extend(group)
    return extra, problems
