"""Follow known items through belt lanes before a layout reaches the game."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping

from companion.modules import Placed
from companion.ratios import Book
from companion.smelting import STEP, UNDERGROUND_SPAN, footprint

SIDES = ("left", "right")
BELTS = {"transport-belt", "fast-transport-belt", "express-transport-belt", "turbo-transport-belt"}
UNDERGROUNDS = {"underground-belt", "fast-underground-belt", "express-underground-belt", "turbo-underground-belt"}
SPLITTERS = {"splitter", "fast-splitter", "express-splitter", "turbo-splitter"}
SPANS = {"fast-underground-belt": 7, "express-underground-belt": 9, "turbo-underground-belt": 11}
MACHINES = {"stone-furnace": 2, "steel-furnace": 2, "electric-furnace": 3,
            "assembling-machine-1": 3, "assembling-machine-2": 3, "assembling-machine-3": 3}
Lane = tuple[int, int, str]
Graph = dict[Lane, set[Lane]]


@dataclass(frozen=True)
class Witness:
    """Keep the first conflicting join visible even far downstream of it."""

    tile: tuple[int, int]
    source: tuple[int, int]
    item: str


def _entities(entities: Iterable[Placed], world: Mapping | None) -> list[Placed]:
    existing = [Placed(e.get("n", e.get("t", "")), e["x"], e["y"], e.get("d", 0),
                       e.get("recipe", e.get("r")), e.get("kind", e.get("ug", e.get("belt_to_ground_type"))))
                for e in (world or {}).get("entities", []) if "x" in e and "y" in e]
    # A rebuilt plan describes its tiles more precisely than an older world dump.
    planned = list(entities)
    covered = {(e.x, e.y) for e in planned}
    return [e for e in existing if (e.x, e.y) not in covered] + planned


def _tiles(e: Placed) -> list[tuple[int, int]]:
    if e.name in SPLITTERS:
        if e.direction in (0, 8):
            return [(math.floor(e.x - .5), math.floor(e.y)), (math.floor(e.x + .5), math.floor(e.y))]
        return [(math.floor(e.x), math.floor(e.y - .5)), (math.floor(e.x), math.floor(e.y + .5))]
    return [(math.floor(e.x), math.floor(e.y))]


def _belts(entities: Iterable[Placed], world: Mapping | None) -> dict[tuple[int, int], Placed]:
    return {t: e for e in _entities(entities, world) if e.name in BELTS | UNDERGROUNDS | SPLITTERS
            for t in _tiles(e)}


def _surface(e: Placed) -> bool:
    return e.name not in UNDERGROUNDS or e.kind != "input"


def _incoming(belts: Mapping[tuple[int, int], Placed]) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """The belts feeding each tile: those whose next tile it is (an underground's entrance feeds its exit)."""
    incoming = defaultdict(list)
    for (x, y), e in belts.items():
        dx, dy = STEP[e.direction]
        if _surface(e):
            incoming[x + dx, y + dy].append((x, y))
    return incoming


def _joins(e: Placed, at: tuple[int, int], feeds: list[tuple[int, int]],
           belts: Mapping[tuple[int, int], Placed]) -> tuple[bool, list[tuple[int, int]]]:
    """Whether a belt feeds `e` from behind, and the belts feeding it from a side (not head-on)."""
    x, y = at
    dx, dy = STEP[e.direction]
    rear = (x - dx, y - dy) in feeds and belts[x - dx, y - dy].direction == e.direction
    sides = [p for p in feeds if (p[0] - x) * dx + (p[1] - y) * dy == 0
             and belts[p].direction != (e.direction + 8) % 16]
    return rear, sides


def lanes(entities: Iterable[Placed], world: Mapping | None = None) -> Graph:
    """Map (tile x, tile y, left/right) to successors, preserving lanes through turns."""
    belts = _belts(entities, world)
    graph = {(x, y, s): set() for x, y in belts for s in SIDES}
    incoming = _incoming(belts)
    for (x, y), e in belts.items():
        dx, dy = STEP[e.direction]
        if e.name in UNDERGROUNDS and e.kind != "output":
            for distance in range(1, SPANS.get(e.name, UNDERGROUND_SPAN) + 1):
                at = x + distance * dx, y + distance * dy
                other = belts.get(at)
                if not other or other.name != e.name or other.direction != e.direction:
                    continue
                if other.kind != "input":
                    for s in SIDES:
                        graph[x, y, s].add((*at, s))
                if other.kind == "output":
                    break   # an unknown world subtype may instead be another entrance
    for target, feeds in incoming.items():
        e = belts.get(target)
        if e is None:
            continue
        x, y = target
        dx, dy = STEP[e.direction]
        rear = (x - dx, y - dy) in feeds
        sides = [p for p in feeds if (p[0] - x) * dx + (p[1] - y) * dy == 0]
        for px, py in feeds:
            source = belts[px, py]
            if source.direction == (e.direction + 8) % 16:
                continue
            straight = source.direction == e.direction
            if straight and e.name in UNDERGROUNDS and e.kind == "output":
                continue   # the exit receives its rear lanes through the tunnel
            turn = e.name in BELTS and not rear and len(sides) == 1
            near = "left" if (px - x) * dy - (py - y) * dx > 0 else "right"
            for s in SIDES:
                graph[px, py, s].add((x, y, s if straight or turn else near))
    # A splitter balances belt halves, preserving each item's side of travel.
    for tile, e in belts.items():
        if e.name in SPLITTERS:
            for other in _tiles(e):
                for s in SIDES:
                    graph[*tile, s].add((*other, s))
    return graph


@lru_cache(maxsize=1)
def _recipes():
    return Book.from_site({}).recipes


def _drop(belts, x, y, items, out, *, far=False, origin=None):
    tile = math.floor(x), math.floor(y)
    e = belts.get(tile)
    if e is None:
        return
    dx, dy = STEP[e.direction]
    px, py = origin if origin is not None else (x, y)
    side = (px - tile[0] - .5) * dy - (py - tile[1] - .5) * dx
    if abs(side) < 1e-6:
        names = ("right",) if far else SIDES
    else:
        names = ("left" if (side > 0) != far else "right",)
    for name in names:
        out[*tile, name].update(items)


def sources(entities: Iterable[Placed], world: Mapping | None = None) -> dict[Lane, set[str]]:
    """Only drills over known resources and inserters with known products seed the graph."""
    entities = list(entities)
    belts = _belts(entities, world)
    all_entities = _entities(entities, world)
    machines = {}
    for e in all_entities:
        size = MACHINES.get(e.name)
        if size and e.recipe:
            for x in range(math.floor(e.x - size / 2), math.ceil(e.x + size / 2)):
                for y in range(math.floor(e.y - size / 2), math.ceil(e.y + size / 2)):
                    machines[x, y] = e.recipe
    out = defaultdict(set)
    recipes = _recipes()
    live = (world or {}).get("recipes") or []
    if isinstance(live, list):
        recipes = {**recipes, **Book._recipes({r["name"]: r for r in live if isinstance(r, Mapping) and "name" in r})}
    for e in all_entities:
        dx, dy = STEP.get(e.direction, (0, -1))
        if e.name in ("electric-mining-drill", "burner-mining-drill"):
            electric = e.name == "electric-mining-drill"
            reach = 2.5 if electric else 1
            x0, x1 = math.floor(e.x - reach), math.ceil(e.x + reach) - 1
            y0, y1 = math.floor(e.y - reach), math.ceil(e.y + reach) - 1
            items = {n for n, rows in (world or {}).get("resources", {}).items() if n != "crude-oil"
                     and any(y0 <= y <= y1 and a <= x1 and b >= x0 for y, a, b in rows)}
            distance = 1.85 if electric else 1.2
            _drop(belts, e.x + dx * distance, e.y + dy * distance, items, out)
        elif e.name.endswith("inserter"):
            distance = 2 if e.name == "long-handed-inserter" else 1
            recipe = recipes.get(machines.get((math.floor(e.x + dx * distance),
                                               math.floor(e.y + dy * distance))))
            if recipe:
                _drop(belts, e.x - dx * distance, e.y - dy * distance,
                      {n for n, amount in recipe.products if amount > 0}, out, far=True, origin=(e.x, e.y))
    return dict(out)


def propagate(graph: Graph, seeds: Mapping[Lane, set[str]]) -> tuple[dict[Lane, set[str]], dict[Lane, Witness]]:
    """Visit each lane once per item, retaining the join where contamination began."""
    items = {lane: set() for lane in graph}
    witnesses = {}
    queue = deque((lane, item, lane) for lane in sorted(seeds) for item in sorted(seeds[lane]) if lane in graph)
    while queue:
        lane, item, source = queue.popleft()
        if item in items[lane]:
            continue
        if items[lane] and lane not in witnesses:
            witnesses[lane] = witnesses.get(source, Witness(lane[:2], source[:2], item))
        items[lane].add(item)
        queue.extend((target, item, lane) for target in sorted(graph[lane]))
    return items, witnesses


def check(entities: Iterable[Placed], world: Mapping | None = None) -> list[str]:
    """A shared lane can block every consumer of either item, so reject it before building. Only lanes on the
    planned belts count: what already stands is not this plan's to answer for, and a mixed lane elsewhere in the
    base must not hold back every plan after it."""
    entities = list(entities)
    planned = {t for e in entities if e.name in BELTS | UNDERGROUNDS | SPLITTERS for t in _tiles(e)}
    items, witnesses = propagate(lanes(entities, world), sources(entities, world))
    return [f"belt lane {lane[:2]} {lane[2]} carries {', '.join(sorted(items[lane]))}; "
            f"join at {w.tile} from belt {w.source} introduced {w.item}"
            for lane, w in sorted(witnesses.items()) if lane[:2] in planned]


def junctions(entities: Iterable[Placed], world: Mapping | None = None) -> list[tuple[int, int]]:
    """The planned belts fed from both sides and not from behind: each side input lands on its own lane (ore on
    one, coal on the other). Lose either input (biters, a piece not built yet, one rebuilt later) and the belt
    turns into a curve that pours the other input onto both lanes, for good where nothing takes it off: run 32
    (2026-10-25) lost its iron ore piece in an attack, and coal filled the furnace column's ore lane."""
    entities = list(entities)
    planned = {t for e in entities if e.name in BELTS for t in _tiles(e)}
    belts = _belts(entities, world)
    incoming = _incoming(belts)
    out = []
    for at in sorted(planned):
        e = belts.get(at)
        if e is None or e.name not in BELTS:
            continue
        rear, sides = _joins(e, at, incoming.get(at, []), belts)
        if not rear and len(sides) == 2:
            out.append(at)
    return out


def _drops(entities: Iterable[Placed]) -> set[tuple[int, int]]:
    """Tiles inserters and drills put items on (an inserter faces where it picks up)."""
    out = set()
    for e in entities:
        dx, dy = STEP.get(e.direction, (0, -1))
        if e.name.endswith("inserter"):
            d = 2 if e.name == "long-handed-inserter" else 1
            out.add((math.floor(e.x - dx * d), math.floor(e.y - dy * d)))
        elif e.name in ("electric-mining-drill", "burner-mining-drill"):
            d = 1.85 if e.name == "electric-mining-drill" else 1.2
            out.add((math.floor(e.x + dx * d), math.floor(e.y + dy * d)))
    return out


def anchors(entities: Iterable[Placed], world: Mapping | None = None,
            blocked: Iterable[tuple[int, int]] = ()) -> tuple[list[Placed], list[tuple[int, int]]]:
    """An empty belt behind each such junction, facing into it, keeps it a straight belt with two sideloads
    whatever is lost, as players anchor theirs. Returns the anchors to add, and the junctions with no free tile
    behind them (a tile no building or ground keeps, that nothing feeds or drops onto)."""
    entities = list(entities)
    if world:   # (trees and rocks are cleared before building)
        world = {**world, "entities": [e for e in world.get("entities") or [] if e.get("t") not in ("tree", "simple-entity")]}
    known = _entities(entities, world)
    belts = _belts(entities, world)
    incoming = _incoming(belts)
    taken = footprint(known) | set(belts) | set(blocked)
    drops = _drops(known)
    added, stuck = [], []
    for at in junctions(entities, world):
        e = belts[at]
        dx, dy = STEP[e.direction]
        behind = (at[0] - dx, at[1] - dy)
        if behind in taken or behind in drops or incoming.get(behind):
            stuck.append(at)
            continue
        added.append(Placed("transport-belt", behind[0] + .5, behind[1] + .5, e.direction))
        taken.add(behind)
    return added, stuck


def contracts(entities: Iterable[Placed], world: Mapping | None = None) -> dict[tuple[int, int], dict[str, list[str]]]:
    """What each lane of the planned plain belts is meant to carry, from the drills and machines known to feed
    it: the game checks the live lanes against it and picks off what does not belong. A lane with no known
    source has no contract (a chest or a loader may feed it): nothing on it counts as wrong."""
    entities = list(entities)
    planned = {t for e in entities if e.name in BELTS for t in _tiles(e)}
    items, _ = propagate(lanes(entities, world), sources(entities, world))
    out = {}
    for at in sorted(planned):
        row = {s: sorted(items.get((*at, s), ())) for s in SIDES}
        row = {s: v for s, v in row.items() if v}
        if row:
            out[at] = row
    return out
