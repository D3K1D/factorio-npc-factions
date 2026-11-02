"""A book block's ports, read from its own belts: what comes in where, on which lane, and what goes out.

A blueprint from the user's books says nothing of its ports. Its belts do. Belts joined along their flow (an
underground pair, a splitter's two tiles) make one network. Each network shows:
- the machines inserters take from it for, by recipe;
- the machines that put onto it;
- its open ends at the block's edge: a head the outside feeds, a tail that gives to the outside.

So:
- an input is a head network's ingredients, less what the block makes itself. An item two networks could both bring
  goes to the one where nothing else explains it (a block 5 engine takes steel from one belt, gears and pipes from
  another);
- an output is a tail network's products no machine of the block takes. A tail of an input's own network is a
  pass-through, for tiling;
- an input's lane is set where a machine of the block puts its own items onto the same belt. Those land on the far
  lane from the inserter, so the input comes on the other (block 3's copper: its gear assembler fills the south lane).
  Belts side-loaded into one another each go onto a lane of their own, so their inputs take either lane;
- a rate is what the machines that take an item draw at full work (the game's facts and recipes).

    trace(entities, site)   {"ports": [...], "passes": [...], "bus_side": edge}; ports as blueprint_packs.ports() gives
                            them, with rate, lane, direction, outside (its own edge tile, as a
                            sidecar's) and beyond (the tile outside it, where a feed ends)
    module(build, site)     a modules.Module (Ports with lanes) for the bus to place, or None
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping

from companion.modules import EAST, NORTH, SOUTH, WEST

STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
BELTS = ("transport-belt", "underground-belt", "splitter", "loader")
MACHINES = ("assembling-machine", "furnace", "lab", "rocket-silo")
UNDERGROUND_REACH = 10


def _kind(site, name: str) -> str:
    facts = (site.get("game_facts") or {}).get("entities") or {}
    e = facts.get(name) or {}
    return str(e.get("type") or e.get("kind") or "")


def _size(site, name: str) -> tuple[int, int]:
    e = ((site.get("game_facts") or {}).get("entities") or {}).get(name) or {}
    return int(e.get("width") or 1), int(e.get("height") or e.get("width") or 1)


def _tiles(site, e) -> set[tuple[int, int]]:
    from companion import nuclear_cell
    w, h = _size(site, e["prototype"])
    return nuclear_cell.tiles_of((float(e["position"]["x"]), float(e["position"]["y"])), w, h,
                                 int(e.get("direction") or 0) % 16)


def _tile(e) -> tuple[int, int]:
    return math.floor(float(e["position"]["x"])), math.floor(float(e["position"]["y"]))


def trace(entities: list[Mapping[str, Any]], site: Mapping[str, Any]) -> dict:
    """The block's ports from its belts (see the module's note). Positions as the entities have them."""
    from companion import rates
    facts = rates.of(site)
    recipes = {str(r.get("name")): r for r in site.get("recipes") or [] if isinstance(r, Mapping)}
    belts: dict[tuple[int, int], tuple[str, int, str | None]] = {}   # tile -> (kind, direction, underground end)
    names: dict[tuple[int, int], str] = {}
    splitters: list[tuple[set, int]] = []
    machines: dict[tuple[int, int], Mapping[str, Any]] = {}
    inserters = []
    taken: set[tuple[int, int]] = set()
    for e in entities:
        name, d = str(e["prototype"]), int(e.get("direction") or 0) % 16
        t = _kind(site, name)
        tiles = _tiles(site, e)
        taken |= tiles
        if t in ("transport-belt", "underground-belt", "loader", "loader-1x1") or name.endswith(("transport-belt", "underground-belt")):
            belts[_tile(e)] = ("underground" if "underground" in (t or name) else "belt", d, e.get("belt_to_ground_type"))
            names[_tile(e)] = name
        elif t == "splitter" or name.endswith("splitter"):
            splitters.append((tiles, d))
            for s in tiles:
                belts[s] = ("splitter", d, None)
        elif t in MACHINES or (e.get("recipe") and t != "inserter"):
            for s in tiles:
                machines[s] = e
        elif t == "inserter" or name.endswith("inserter"):
            inserters.append(e)
    if not belts:
        return {"ports": [], "passes": [], "bus_side": None}
    xs, ys = [x for x, _ in taken], [y for _, y in taken]
    box = (min(xs), min(ys), max(xs), max(ys))

    def reach_of(name):
        e = ((site.get("game_facts") or {}).get("entities") or {}).get(name or "") or {}
        return int(e.get("reach") or UNDERGROUND_REACH)

    def ahead(t):
        kind, d, end = belts[t]
        dx, dy = STEP[d]
        if kind == "underground" and end == "input":
            # (the nearest exit of its own prototype ahead, within its reach: entrances between do not stop it)
            for k in range(1, reach_of(names.get(t)) + 1):
                q = (t[0] + dx * k, t[1] + dy * k)
                if (q in belts and belts[q][0] == "underground" and belts[q][1] == d
                        and names.get(q) == names.get(t) and belts[q][2] == "output"):
                    return q
            return None
        q = (t[0] + dx, t[1] + dy)
        return q if q in belts else None
    parent: dict = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    def merges(a, b):
        """Whether `a` feeds `b` from its side, its lanes merging onto b's lane on that side: an underground end
        entered other than from its own entrance, or a belt entered from a side while it has another feeder too (one
        fed from a side alone is a curve, which keeps both lanes)."""
        kind, d, end = belts[b]
        if kind == "underground":
            paired = belts[a][0] == "underground" and belts[a][2] == "input" and belts[a][1] == d
            straight = (b[0] - STEP[d][0], b[1] - STEP[d][1])
            return not paired and not (end == "input" and a == straight)
        straight = (b[0] - STEP[d][0], b[1] - STEP[d][1])
        return a != straight and len(fed.get(b, [])) > 1

    def side_of(a, b):
        """The lane of `b` a side-load from `a` lands on."""
        sx, sy = STEP[belts[b][1]]
        return "left" if (a[0] - b[0], a[1] - b[1]) == (sy, -sx) else "right"

    def lanes_kept(head):
        """The belt tiles a head's two lanes reach as they are: along its flow, round curves and under the ground,
        not past a side-load."""
        seen, todo = {head}, [head]
        while todo:
            a = todo.pop()
            b = ahead(a)
            if b is None or b in seen or merges(a, b):
                continue
            seen.add(b)
            todo.append(b)
        return seen

    def lane_at(t, lane, end):
        """The lane an item put on `t` on `lane` is on at `end`, following the flow (a side-load moves it to the
        side it came in from); None when the flow does not reach `end`."""
        seen = set()
        while t not in seen:
            if t == end:
                return lane
            seen.add(t)
            b = ahead(t)
            if b is None:
                return None
            if merges(t, b):
                lane = side_of(t, b)
            t = b
        return None

    fed: dict[tuple[int, int], list] = {}
    for t in belts:
        find(t)
        a = ahead(t)
        if a is not None:
            parent[find(t)] = find(a)
            fed.setdefault(a, []).append(t)
    for tiles, _ in splitters:
        first = sorted(tiles)[0]
        for s in tiles:
            parent[find(s)] = find(first)

    # what each network gives and takes, by the inserters at it (an inserter faces where it picks up)
    takes: dict = {}
    puts: dict = {}
    drops: dict = {}   # network -> [(tile, the lane its items land on, along that belt)]
    for e in inserters:
        d = int(e.get("direction") or 0) % 16
        reach = 2 if "long" in str(e["prototype"]) else 1
        dx, dy = STEP[d]
        at = _tile(e)
        pick, drop = (at[0] + dx * reach, at[1] + dy * reach), (at[0] - dx * reach, at[1] - dy * reach)
        if pick in belts and drop in machines:
            takes.setdefault(find(pick), []).append(machines[drop])
        if drop in belts and pick in machines:
            puts.setdefault(find(drop), []).append(machines[pick])
            bd = belts[drop][1]
            bx, by = STEP[bd]
            # (the far lane from the inserter, which stands at (dx, dy) from the drop tile; the belt's left is (by, -bx))
            if (dx, dy) in ((bx, by), (-bx, -by)):
                lane = "right"   # (in line with the belt: the game puts it on the right lane)
            else:
                lane = "right" if (dx, dy) == (by, -bx) else "left"
            drops.setdefault(find(drop), []).append((drop, lane, machines[pick]))

    def products(e):
        r = recipes.get(str(e.get("recipe") or ""))
        return {str(p["name"]) for p in (r or {}).get("products") or [] if isinstance(p, Mapping) and p.get("name")}

    def ingredients(e):
        r = recipes.get(str(e.get("recipe") or ""))
        return {str(i["name"]) for i in (r or {}).get("ingredients") or [] if isinstance(i, Mapping) and i.get("name")}
    made = set().union(*[products(e) for e in {id(m): m for m in machines.values()}.values()]) if machines else set()
    used_in = set().union(*[ingredients(e) for e in {id(m): m for m in machines.values()}.values()]) if machines else set()
    labs = any(_kind(site, str(m["prototype"])) == "lab" for m in machines.values())

    def outside(t, d):
        dx, dy = STEP[d]
        return (t[0] - dx, t[1] - dy)

    def edge_of(q):
        x0, y0, x1, y1 = box
        return ("west" if q[0] < x0 else "east" if q[0] > x1 else "north" if q[1] < y0 else
                "south" if q[1] > y1 else None)
    heads: dict = {}
    tails: dict = {}
    for t, (kind, d, end) in belts.items():
        dx, dy = STEP[d]
        behind = (t[0] - dx, t[1] - dy)
        if kind == "underground" and end == "output":
            if t not in fed:   # (an exit whose entrance is outside the block: fed from beyond its edge)
                k = 1
                while not edge_of((t[0] - dx * k, t[1] - dy * k)) and k < reach_of(names.get(t)):
                    k += 1
                q = (t[0] - dx * k, t[1] - dy * k)
                if edge_of(q):
                    heads.setdefault(find(t), []).append((t, d, q))
        elif edge_of(behind) and t not in fed:
            # (fed by nothing: open behind. One fed only from a side, nothing behind it, is a curve in the game, and
            # takes nothing from behind; a splitter's tile so too)
            heads.setdefault(find(t), []).append((t, d, behind))
        if ahead(t) is None and edge_of((t[0] + dx, t[1] + dy)) and not (kind == "underground" and end == "input"):
            tails.setdefault(find(t), []).append((t, d, (t[0] + dx, t[1] + dy)))
    # the items each input network brings: what its takers need that the block does not make
    wanted = {}
    for net in heads:
        need = set()
        for m in takes.get(net, []):
            need |= ingredients(m)
        if labs and not need:   # (labs take packs: whatever comes)
            need = {"*science-pack"}
        wanted[net] = need - made
    fixed: dict = {}
    for _ in range(len(wanted) + 1):   # (an item one network alone could bring is that network's)
        for net, need in wanted.items():
            if len(need) == 1:
                fixed[next(iter(need))] = net
        for net, need in wanted.items():
            for item, home in fixed.items():
                if home != net and item in need and len(need) > 1:
                    need.discard(item)
    ports, passes = [], []
    rate_of = _rates(facts, recipes, machines, takes)
    for net, ends in heads.items():
        items = sorted(wanted.get(net) or [])
        side_loaded = len(ends) > 1
        lane = None
        if drops.get(net) and not side_loaded:
            reached = lanes_kept(ends[0][0])
            if all(t in reached for t, _, _ in drops[net]):   # (no side-load between: its lanes reach them as they are)
                lanes = {l for _, l, _ in drops[net]}
                if len(lanes) == 1:   # (the block's own items on one lane: the input on the other)
                    lane = "left" if lanes == {"right"} else "right"
        for k, (t, d, beyond) in enumerate(sorted(ends)):
            item = items[k] if side_loaded and k < len(items) else items[0] if len(items) == 1 else None
            ports.append({"x": t[0] + 0.5, "y": t[1] + 0.5, "edge": edge_of(beyond), "kind": "belt", "way": "in",
                          "item": item, "items": items if side_loaded or item is None else [item],
                          # (side-loaded into one belt for the same machines: either item on either entrance)
                          "interchangeable": side_loaded,
                          "rate": round(rate_of(net, item, len(ends)), 4) if item else None, "lane": lane,
                          "direction": d, "outside": [t[0], t[1]], "beyond": list(beyond)})
    for net, ends in tails.items():
        if net in heads:
            for t, d, beyond in ends:
                passes.append({"x": t[0] + 0.5, "y": t[1] + 0.5, "edge": edge_of(beyond), "direction": d,
                               "outside": [t[0], t[1]], "beyond": list(beyond), "of": sorted(wanted.get(net) or [])})
        given = set()   # (what its machines put on it: a pass-through's too)
        for m in puts.get(net, []):
            given |= products(m)
        out = sorted(_finals(given, used_in))
        # (each item's lane where its machines put it on: one port an item, with its lane when it keeps to one)
        for t, d, beyond in ends:
            lane_of: dict = {}   # (each item's lane at this tail, followed from where it was put on)
            for at, lane, m in drops.get(net, []):
                for item in products(m) & set(out):
                    lane_of.setdefault(item, set()).add(lane_at(at, lane, t))
            for item in out:
                lanes = lane_of.get(item) or set()
                ports.append({"x": t[0] + 0.5, "y": t[1] + 0.5, "edge": edge_of(beyond), "kind": "belt", "way": "out",
                              "item": item, "items": out, "rate": None,
                              "lane": next(iter(lanes)) if len(lanes) == 1 else None,
                              "direction": d, "outside": [t[0], t[1]], "beyond": list(beyond)})
    sides = Counter(p["edge"] for p in ports)
    return {"ports": ports, "passes": passes, "bus_side": sides.most_common(1)[0][0] if sides else None}


def _finals(given: set, used_in: set) -> set:
    """What a pack belt carries out: its items no machine of the block takes."""
    return {i for i in given if i not in used_in}


def _rates(facts, recipes, machines, takes):
    """A function: the items a second the machines taking `item` from a network draw at full work, split over its
    `heads` ends when side-loaded."""
    def rate(net, item, heads=1):
        total = 0.0
        seen = set()
        for m in takes.get(net, []):
            if id(m) in seen:
                continue
            seen.add(id(m))
            r = recipes.get(str(m.get("recipe") or ""))
            if not r or not facts:
                continue
            speed = float((facts.entities.get(str(m["prototype"])) or {}).get("speed") or 1)
            energy = float(r.get("energy") or 1)
            amount = sum(float(i.get("amount") or 0) for i in r.get("ingredients") or []
                         if isinstance(i, Mapping) and i.get("name") == item)
            total += speed / energy * amount
        return total
    return rate


def module(build: Mapping[str, Any], site: Mapping[str, Any]):
    """The block as a modules.Module for the bus: its entities from the top-left tile, Ports with their lanes (inputs
    and pack outputs only; pass-throughs and the bus side ride as attributes). None when its belts give no input."""
    from companion.modules import Module, Placed, Port
    ents = list(build.get("entities") or [])
    traced = trace(ents, site)
    if not any(p["way"] == "in" and p["item"] for p in traced["ports"]):
        return None
    tiles = set().union(*[_tiles(site, e) for e in ents])
    x0, y0 = min(x for x, _ in tiles), min(y for _, y in tiles)
    m = Module(str(build.get("label") or (build.get("import") or {}).get("label") or "book block"),
               [Placed(e["prototype"], float(e["position"]["x"]) - x0, float(e["position"]["y"]) - y0,
                       int(e.get("direction") or 0), e.get("recipe"), e.get("belt_to_ground_type"),
                       mirror=bool(e.get("mirror"))) for e in ents],
               width=max(x for x, _ in tiles) - x0 + 1, height=max(y for _, y in tiles) - y0 + 1)
    for p in traced["ports"]:
        if not p["item"]:
            continue
        m.ports.append(Port(p["item"], p["way"], math.floor(p["x"]) - x0, math.floor(p["y"]) - y0, p["direction"],
                            float(p["rate"] or 0), p["lane"]))
    m.bus_side = traced["bus_side"]
    m.passes = traced["passes"]
    return m
