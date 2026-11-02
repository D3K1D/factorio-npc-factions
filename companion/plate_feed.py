"""A new bus's first plates from the smelting the base already has (the user, 2026-10-26: a smaller first bus,
producing sooner, grown in place).

The bus's own smelting blocks at its head are big: their furnaces, fields, ore lines and coal lines were 841 of the
2417 buildings of run 57's first bus, all built before any science. The electric smelting blocks the engineer built in
its opening already make plates (run 57, minute 116: 3.75 iron and 2.5 copper a second) into a store at their output,
where it takes its building plates (the user: take automated intermediates off belts or an overflow chest). So the
first bus takes its plates from there: a splitter on a store's output belt, a belt from it to the plate path down
into the bus's lane head. The store stays the overflow the engineer takes from (the splitter feeds it whenever the
bus's lanes back up, and the bus all of it once the store is full), and the bus's own smelting blocks come later, as
growth (base_plan.plan_bus_base).

    sources(site)                       {plate: (port, rate)}: each electric smelting block's store and what it makes
    store_line(dump, port)              its output belt, traced back from the store on the map
    plan(dump, plate, port, rate, start, blocked)   the splitter and belts into the path at `start`, or None
    searching()                         one bus plan's feed searches: one budget, ways found kept
"""

from __future__ import annotations

import contextlib
import contextvars
import math
from dataclasses import replace
from typing import Any, Iterator, Mapping

from companion import belt_lines
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
MAX_LINE = 96   # belts traced back from a store, at most
# States a plate's feed searches may take in all (about 9 s a million) in one bus plan (`searching`: however many
# layouts it tries in turn; a call outside one has them to itself). A feed is one route, worth a long
# search: it saves the bus's own smelting block before its science (run 57's copper, 528 buildings and their ore and
# coal lines), where a tap's budget (belt_lines.TAP_SEARCH) gave up on the 362-belt way round from 230 tiles off.
FEED_SEARCH = 1_200_000
_SEARCH: contextvars.ContextVar[dict | None] = contextvars.ContextVar("plate_feed_search", default=None)


@contextlib.contextmanager
def searching() -> Iterator[None]:
    """One bus plan's feed searches: FEED_SEARCH states for each plate's, however many layouts it tries (one plate's
    long search leaves the other's alone), and a way found kept for the next layout (taken again where its tiles
    are still clear there)."""
    token = _SEARCH.set({"left": {}, "found": {}})
    try:
        yield
    finally:
        _SEARCH.reset(token)


def sources(site: Mapping[str, Any]) -> dict[str, tuple[tuple[int, int], float]]:
    """The base's electric smelting by plate: the world tile where its first array's plates leave for its store (its
    output port: the array's kept spot, companion/electric_start, plus the plate module's port), and what that
    array's built parts make, items/s (a later array has a store of its own). Plates no such block makes are not in
    it."""
    from companion import catalog_planner, electric_start, growth
    out: dict[str, tuple[tuple[int, int], float]] = {}
    for purpose, spec in catalog_planner.PURPOSES.items():
        plate = spec.get(electric_start.SMELTING_FAMILY)
        if plate not in growth.PLATES or plate in out:
            continue
        # (its first array's: the bus is fed from one store; each array has its own, companion/plate_lead.py)
        blocks = [b for b in electric_start.blocks_of(site, purpose) if electric_start.array_of(b) == 0]
        rate = sum(float((((b.get("capacity") or {}).get("makes")) or {}).get(plate) or 0) for b in blocks)
        kept = electric_start.kept(site, purpose, 0) or {}
        spot = next((str(s).split("=", 1)[1] for s in kept.get("sites") or ()
                     if str(s).startswith(f"outpost-{plate}-") and "=" in str(s)), None)
        if rate <= 0 or spot is None:
            continue
        try:
            x, y = (int(v) for v in spot.split(",")[:2])
        except ValueError:
            continue
        port = next(p for p in growth.plate_module(growth.PLATES[plate], plate, 1.0).ports if p.kind == "out")
        out[plate] = ((x + port.x, y + port.y), rate)
    return out


FIND_REACH = 32   # tiles round the expected port its smelting's real output belt is looked for


def _furnace_drops(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """The tiles inserters drop furnace output onto: an inserter whose pickup (its direction's side, this codebase's
    convention) is a furnace."""
    force = dump.get("force")
    mine = [e for e in dump.get("entities") or () if e.get("f") == force]
    furnace = set()
    for e in mine:
        if e.get("t") == "furnace":
            w, h = int(e.get("w") or 2), int(e.get("h") or 2)
            x0, y0 = math.floor(float(e["x"]) - w / 2 + 1e-6), math.floor(float(e["y"]) - h / 2 + 1e-6)
            furnace |= {(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)}
    out = set()
    for e in mine:
        if e.get("t") != "inserter" or e.get("d") is None:
            continue
        step = STEP.get(int(e["d"]))
        if step is None:
            continue
        x, y = math.floor(float(e["x"])), math.floor(float(e["y"]))
        long = 2 if "long" in str(e.get("n")) else 1
        if (x + step[0] * long, y + step[1] * long) in furnace:
            out.add((x - step[0] * long, y - step[1] * long))
    return out


def store_line(dump: Mapping[str, Any], port: tuple[int, int], item: str, rate: float) -> belt_lines.Line | None:
    """The belt carrying plates to the store at `port` (its last tile), traced back on the map: the engineer's own
    belts, each feeding the next, straight ahead first; None when no belt stands there. Only a belt the furnaces put
    their plates on: where the expected port's belt is another (its tile off, or a coal or ore belt passing), the
    smelting's real output belt near it is followed to its end and taken (a feed tapped an empty belt and its
    stage waited forever, its arrays backed up, F-21)."""
    force = dump.get("force")
    belts = {(math.floor(e["x"]), math.floor(e["y"])): int(e.get("d") or 0) for e in dump.get("entities") or ()
             if e.get("f") == force and e.get("t") == "transport-belt"}
    drops = _furnace_drops(dump) & set(belts)
    traced = _traced(belts, port, item, rate) if port in belts else None
    if traced is not None and (not drops or {(x, y) for x, y, _ in traced.tiles} & drops):
        return _after_drops(traced, drops)
    near = sorted((t for t in drops if abs(t[0] - port[0]) + abs(t[1] - port[1]) <= FIND_REACH),
                  key=lambda t: (abs(t[0] - port[0]) + abs(t[1] - port[1]), t))
    for start in near[:4]:
        end, seen = start, {start}
        while True:   # (downstream to its end: where it meets the store)
            dx, dy = STEP.get(belts[end], (0, 0))
            nxt = (end[0] + dx, end[1] + dy)
            if nxt not in belts or nxt in seen or len(seen) >= MAX_LINE:
                break
            end = nxt
            seen.add(end)
        line = _traced(belts, end, item, rate)
        if line is not None:
            return _after_drops(line, drops)
    return None


def _after_drops(line: belt_lines.Line, drops: set[tuple[int, int]]) -> belt_lines.Line:
    """The stretch of a store's belt past the last furnace that puts plates on it: a tap upstream of them carries
    nothing (a feed's splitter stood east of every furnace, its stage waiting forever, F-21)."""
    at = [i for i, (x, y, _) in enumerate(line.tiles) if (x, y) in drops]
    if not at:
        return line
    return replace(line, tiles=list(line.tiles[at[-1]:]))


def _traced(belts: Mapping[tuple[int, int], int], port: tuple[int, int], item: str,
            rate: float) -> belt_lines.Line | None:
    """The belt into `port` traced back: each belt feeding the next, straight ahead first."""
    line, here = [port], port
    while len(line) < MAX_LINE:
        before = [t for t in (((here[0] - dx, here[1] - dy), d) for d, (dx, dy) in STEP.items())
                  if belts.get(t[0]) == t[1] and t[0] not in line]
        if not before:
            break
        straight = [t for t in before if t[1] == belts[here]]
        here = (straight or before)[0][0]
        line.append(here)
    line.reverse()
    return belt_lines.Line(f"store@{port[0]},{port[1]}", item, [(x, y, belts[(x, y)]) for x, y in line], rate, {})


def plan(dump: Mapping[str, Any], plate: str, port: tuple[int, int], rate: float, start: tuple[int, int],
         blocked: set) -> list[Placed] | None:
    """A splitter on the store's output belt and the belts from it into the plate path's first tile `start` (a belt
    heading south), from its west or east side (it joins the path's near lane); None when the store's belt or a way
    from it is not there."""
    line = store_line(dump, port, plate, rate)
    if line is None:
        return None
    search = _SEARCH.get()
    search = search if search is not None else {"left": {}, "found": {}}
    known = search["found"].get((plate, port, start))
    if known is not None and not _taken(known, line) & set(blocked):
        return [replace(e) for e in known]
    for entry, last in (((start[0] - 1, start[1]), EAST), ((start[0] + 1, start[1]), WEST)):
        if entry in blocked:
            continue
        left = search["left"].setdefault(plate, FEED_SEARCH)
        if left <= 0:   # (either side of the path's first tile is much the same way there)
            break
        stats: dict = {}
        branch = belt_lines.tap(dump, plate, entry, 0.0, set(blocked) | {start}, last=last, key=f"feed-{plate}",
                                among=[line], budget=(left, left), stats=stats)
        search["left"][plate] = left - stats.get("searched", 0)
        if branch:
            search["found"][(plate, port, start)] = [replace(e) for e in branch]
            return branch
    return None


def _taken(branch: list[Placed], line: belt_lines.Line) -> set[tuple[int, int]]:
    """The tiles a feed takes besides the store's belt, which stands there already: its splitter's other tile and
    the one behind that (kept clear: nothing feeds it), and its belts (belt_lines.tap)."""
    from companion.smelting import footprint
    along = {(x, y) for x, y, _ in line.tiles}
    splitter = branch[0]
    dx, dy = STEP[splitter.direction]
    side = footprint([splitter]) - along
    return side | {(x - dx, y - dy) for x, y in side} | footprint(branch[1:])


def built(dump: Mapping[str, Any], start: tuple[int, int]) -> bool:
    """Whether a feed into the path at `start` stands: the engineer's own belt beside it, pointing into it."""
    force = dump.get("force")
    for e in dump.get("entities") or ():
        if e.get("f") == force and e.get("t") == "transport-belt":
            at, d = (math.floor(e["x"]), math.floor(e["y"])), int(e.get("d") or 0)
            if (at, d) in (((start[0] - 1, start[1]), EAST), ((start[0] + 1, start[1]), WEST)):
                return True
    return False
