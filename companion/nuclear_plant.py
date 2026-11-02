"""The nuclear plant: the unit cell (companion/nuclear_cell.py) sized from the game's numbers (companion/nuclear.py),
placed by the shore, water piped from offshore pumps to every exchanger chain, fuel handed in by inserter and spent
cells taken out, poles over the turbines joined to the live grid.

Each reactor has a fuel chest on its outer side, with an inserter into the reactor and one taking spent cells out to a
chest of their own. The fuel chest is the plant's port: the fuel cell block's belt ends there (that block, and the
uranium mining behind it, are built apart). The turbines join the grid the steam plant already feeds, so the base
moves onto nuclear power only as the reactors heat and steam comes: until then the steam plant carries it, and power
never drops while the plant comes up.

Every piece's size and connections come from the game's facts (game_facts.lua exports them for reactors, heat pipes,
heat exchangers and generators), so a mod's reactor or turbine is laid the same way.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import layout_planner, nuclear, nuclear_cell, rates, team_area
from companion.modules import EAST, Placed, WEST

POLE = "small-electric-pole"
POLE_SUPPLY = 2.5
CHEST = "iron-chest"
INSERTER = "inserter"
MARGIN = 2   # free tiles round the plant (its poles, a walk round it)
REACTORS = 2   # the unit cell's (a bigger plant is more cells, later)
PUMP_TRIES = 6   # shore spots tried beyond the pumps wanted, when one has no pipe to the plant
PUMP_GAP = 2     # tiles kept between two offshore pumps of one plant


def protos(site: Mapping[str, Any], names: Mapping[str, str]) -> dict | None:
    """The pieces' sizes and connections from the game's facts, in nuclear_cell's form; None when a piece's
    connections are not exported (an older mod build)."""
    facts = rates.of(site)
    if facts is None:
        return None
    out = {}
    for role, name in names.items():
        e = facts.entities.get(name) or {}
        heat = e.get("heat") if isinstance(e.get("heat"), list) else []
        fluid = e.get("fluid") if isinstance(e.get("fluid"), list) else []
        if not e.get("width") or (not heat and role != "turbine") or (not fluid and role in ("exchanger", "turbine")):
            return None
        out[name] = {"w": int(e["width"]), "h": int(e["height"]), "heat": heat, "fluid": fluid}
    return out


def heat_pipe(site: Mapping[str, Any]) -> str | None:
    """The heat pipe: a 1x1 heat piece joining all four sides (the ready one first)."""
    facts = rates.of(site)
    found = [(n, e) for n, e in (facts.entities.items() if facts else []) if e.get("kind") == "heat"
             and e.get("width") == 1 and e.get("height") == 1 and len(e.get("heat") or []) >= 4]
    return max(found, key=lambda ne: (bool(ne[1].get("ready")), ne[0]))[0] if found else None


def fuel_side(reactor: tuple[float, float], width: int, outward: int) -> list[Placed]:
    """A reactor's fuel and spent cells on its side away from the other reactor (`outward` -1 west, +1 east): its fuel
    chest and the inserter from it into the reactor a row above the middle, the inserter out to the spent cells'
    chest a row below. (An inserter's direction is the side it picks up from.)"""
    x, y = reactor
    near, far = x + outward * (width / 2 + 0.5), x + outward * (width / 2 + 1.5)
    out_side, in_side = (EAST, WEST) if outward > 0 else (WEST, EAST)
    return [Placed(CHEST, far, y - 1), Placed(INSERTER, near, y - 1, out_side),
            Placed(INSERTER, near, y + 1, in_side), Placed(CHEST, far, y + 1)]


def local_plant(site: Mapping[str, Any]) -> dict:
    """The plant laid round (0, 0): the sized cell (its two reactors) and each reactor's fuel side. Raises PlanError
    when the game's facts lack a piece or its pieces do not join as the cell needs."""
    size = nuclear.sizing(site, REACTORS)
    if size is None:
        raise layout_planner.PlanError("no reactor, heat exchanger and turbine in the game's facts")
    names = {"reactor": size["reactor"], "pipe": heat_pipe(site), "exchanger": size["heat_exchanger"],
             "turbine": size["turbine"]}
    parts = protos(site, names) if names["pipe"] else None
    if parts is None:
        raise layout_planner.PlanError("the game's facts give no heat connections for the nuclear pieces")
    try:
        cell = nuclear_cell.unit_cell(parts, size["heat_exchangers"], size["turbines"], names)
        problems = nuclear_cell.check(cell, parts, names)
    except (ValueError, KeyError, IndexError, TypeError) as failed:
        raise layout_planner.PlanError(f"the nuclear cell cannot be laid: {failed}") from None
    if problems:
        raise layout_planner.PlanError(f"the nuclear cell does not meet: {problems[0]}")
    placed = [Placed(n, x, y, d) for n, x, y, d in cell["entities"]]
    tiles = set(cell["tiles"])
    r_w = parts[names["reactor"]]["w"]
    for k, reactor in enumerate(cell["reactors"]):
        outward = -1 if k == 0 else 1
        for e in fuel_side(reactor, r_w, outward):
            placed.append(e)
            tiles.add((math.floor(e.x), math.floor(e.y)))
        # (kept free beyond the fuel chest: the inserter and the belt bringing the cells, companion/uranium_plant.py)
        chest = (math.floor(reactor[0] + outward * (r_w / 2 + 1.5)), math.floor(reactor[1] - 1))
        tiles |= {(chest[0] + outward, chest[1]), (chest[0] + 2 * outward, chest[1])}
    # the tiles every steam and water connection reaches outside the plant: no pipe but the water's goes there
    open_ends = set()
    for n, x, y, d in cell["entities"]:
        for box in parts[n]["fluid"] if n in (names["exchanger"], names["turbine"]) else []:
            for c in box["c"]:
                reach = nuclear_cell.connection((x, y), d, c)[1]
                if reach not in tiles:
                    open_ends.add(reach)
    return {"entities": placed, "tiles": tiles, "water": list(cell["water"]), "open": open_ends - set(cell["water"]),
            "reactors": cell["reactors"], "sizing": size, "parts": parts, "names": names}


def _poles(electric: list[tuple[set, Placed]], taken: set[tuple[int, int]], box) -> list[Placed]:
    """Small poles over every turbine and inserter (their real footprints): greedily the free tile covering the most
    not yet covered."""
    left = set(range(len(electric)))
    poles: list[Placed] = []
    free = {(x, y) for x in range(box[0], box[2]) for y in range(box[1], box[3]) if (x, y) not in taken}

    reach = math.ceil(POLE_SUPPLY - 0.5)   # (a pole's supply covers this many tiles each way)
    near: dict[tuple[int, int], set[int]] = {}
    for i, (tiles, _) in enumerate(electric):
        for a, b in tiles:
            for x in range(a - reach, a + reach + 1):
                for y in range(b - reach, b + reach + 1):
                    near.setdefault((x, y), set()).add(i)
    covers = {t: near[t] for t in free if t in near}
    while left and covers:
        best = max(covers, key=lambda t: (len(covers[t] & left), -t[1], -t[0]))
        got = covers.pop(best) & left
        if not got:
            break
        left -= got
        poles.append(Placed(POLE, best[0] + 0.5, best[1] + 0.5))
    if left:
        raise layout_planner.PlanError("no pole place over every turbine of the nuclear plant")
    return poles


def plan(site: Mapping[str, Any], world: Mapping[str, Any], book: Mapping[str, dict] | None = None) -> dict:
    """The nuclear plant as a plan for /npc-plan submit: the user's nuclear book stamped (book_plan) when the packs
    folder has one, else the generated unit cell (cell_plan)."""
    from companion import nuclear_book
    book = nuclear_book.load() if book is None else book
    return book_plan(site, world, book) if book else cell_plan(site, world)


def cell_plan(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """The generated plant: near the shore nearest home, its offshore pumps piped to every exchanger chain, its poles
    joined to the live grid. Raises PlanError when it cannot be had."""
    from companion.base_plan import blocked_tiles, find_fit
    from companion.oil_plant import cliff_tiles
    from companion.power import connect, live_poles
    from companion.rocket_plan import _pump, _tiles as world_tiles, pipe_map
    local = local_plant(site)
    size = local["sizing"]
    spots = team_area.pumps(world)
    if not spots:
        raise layout_planner.PlanError("no shore for the nuclear plant's water")
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    shore = min(spots, key=lambda s: (s["x"] - home[0]) ** 2 + (s["y"] - home[1]) ** 2)
    cliffs = cliff_tiles(world)
    taken = set(blocked_tiles(world)) | team_area.forbidden(world) | cliffs
    for y, a, b in world.get("water", []):
        taken |= {(x, y) for x in range(a, b + 1)}
    xs, ys = [x for x, _ in local["tiles"]], [y for _, y in local["tiles"]]
    box0 = (min(xs) - MARGIN, min(ys) - MARGIN, max(xs) + MARGIN + 1, max(ys) + MARGIN + 1)
    shape = {(x, y) for x in range(box0[0], box0[2]) for y in range(box0[1], box0[3])}
    fit = find_fit(taken, shape, home, tuple(world["area"]), anchor=(shore["x"], shore["y"]))
    if fit is None:
        raise layout_planner.PlanError("no room for the nuclear plant near the shore")
    ox, oy = fit
    out = [Placed(e.name, e.x + ox, e.y + oy, e.direction) for e in local["entities"]]
    tiles = {(x + ox, y + oy) for x, y in local["tiles"]}
    used = taken | tiles
    pipes = pipe_map(world, [], {})
    pipes.blocked |= tiles | cliffs
    for e in out:
        if e.name == "pipe":   # (the water on between exchanger rows)
            pipes.pipes[(math.floor(e.x), math.floor(e.y))] = "water"
    for t in local["open"]:
        pipes.reserved[(t[0] + ox, t[1] + oy)] = ""   # (a steam end, a chain's inner water end: no pipe joins there)
    ports = [(x + ox, y + oy) for x, y in local["water"]]
    for t in ports:
        pipes.reserved[t] = "water"
    # Water: the offshore pumps the sizing asks, the first piped to a chain's port, then every other port and pump
    # joined to that network (each route's goal its new end).
    from companion import land
    bare = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles")}
    kept = land.reserved_tiles(world) - set(blocked_tiles(bare))   # (kept ground with nothing on it)
    network: set[tuple[int, int]] = set()
    pumps, want = 0, max(1, size.get("offshore_pumps") or 1)
    tried: set[tuple[int, int]] = set()
    for _ in range(want + PUMP_TRIES):
        if pumps >= want:
            break
        pump = _pump(world, (used - kept) | tried, ports[0])   # (a shore cannot move: kept ground is no bar to its pump)
        if pump is None:
            break
        entity, outlet = pump
        pump_tiles = world_tiles(entity.name, entity.x, entity.y, entity.direction)
        used |= pump_tiles | {outlet}
        tried |= pump_tiles | {outlet}   # (a spot with no pipe to the plant is not tried again, kept ground or not)
        pipes.blocked |= pump_tiles
        path = pipes.route("water", ports[0], outlet, joins=network) if network else pipes.route("water", ports[0], outlet)
        if path is None:
            continue
        pipes.reserved[outlet] = "water"
        out += [entity] + path
        network |= {outlet, ports[0]} | {(math.floor(p.x), math.floor(p.y)) for p in path if p.name == "pipe"}
        used |= {(math.floor(p.x), math.floor(p.y)) for p in path}
        pumps += 1
        px, py = math.floor(entity.x), math.floor(entity.y)   # (the next pump PUMP_GAP tiles clear of this one)
        tried |= {(px + i, py + j) for i in range(-PUMP_GAP, PUMP_GAP + 1) for j in range(-PUMP_GAP, PUMP_GAP + 1)}
    if pumps < want:
        # (short of water its turbines would run below the power it is sized for: no plant rather than a weak one)
        raise layout_planner.PlanError(f"only {pumps} of the {want} offshore pumps its water needs have a pipe to "
                                       "the nuclear plant")
    for port in ports[1:]:
        path = pipes.route("water", ports[0], port, joins=network)
        if path is None:
            raise layout_planner.PlanError("no water pipe to every heat exchanger chain")
        out += path
        network |= {port} | {(math.floor(p.x), math.floor(p.y)) for p in path if p.name == "pipe"}
        used |= {(math.floor(p.x), math.floor(p.y)) for p in path}
    # Power: poles over the turbines and inserters, joined to the grid a generator feeds.
    names, parts = local["names"], local["parts"]
    electric = []
    for e in out:
        if e.name == names["turbine"]:
            p = parts[e.name]
            electric.append((nuclear_cell.tiles_of((e.x, e.y), p["w"], p["h"], e.direction), e))
        elif e.name == INSERTER:
            electric.append(({(math.floor(e.x), math.floor(e.y))}, e))
    box = (box0[0] + ox, box0[1] + oy, box0[2] + ox, box0[3] + oy)
    taken_now = used | set(pipes.pipes) | set(pipes.ends) | set(pipes.reserved)
    poles = _poles(electric, taken_now, box)
    out += poles
    # (a power line keeps off ground kept for another section where it can, crosses it where it must; cliffs never)
    hard = (taken_now - kept) | tiles | cliffs | {(math.floor(p.x), math.floor(p.y)) for p in poles}
    extra, problems = connect(out, live_poles(world), hard | kept)
    if problems:
        extra, problems = connect(out, live_poles(world), hard)
    if problems:
        raise layout_planner.PlanError(f"nuclear plant: {problems[0]}")
    out += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    dx, dy = -float(origin["x"]), -float(origin["y"])
    entities = [e.to_plan(f"nuclear-{n}", dx, dy) for n, e in enumerate(out, start=1)]
    reactor_ids = [entities[i]["id"] for i, e in enumerate(out) if e.name == names["reactor"]]
    fuel_chests = [entities[i]["id"] for i, e in enumerate(out) if e.name == CHEST][0::2]
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"nuclear-plant-{safe}", "source": "nuclear-plant", "purpose": "nuclear-power",
            "entities": entities, "sizing": size, "pumps": pumps, "fuel_chests": fuel_chests,
            "notes": [f"{size['reactors']} reactors, {size['heat_exchangers']} heat exchangers, {size['turbines']} "
                      f"turbines for {size['power_mw']:g} MW; fuel cells into its chests"],
            "stages": [{"id": "nuclear-plant", "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": [], "witnesses": [{"kind": "entity_built", "entity_id": i}
                                                             for i in reactor_ids]}]}


# --- the user's nuclear book, stamped (companion/nuclear_book.py) ------------------------------------------------

FIRST_TIER = 2   # reactors a new plant starts with: the neighbour bonus from the first
# Reactors the plant grows to in place: 4c holds 2c in its own column. (8c is wider: its fuel belts run on over a 4c's
# spent-cell sinks, which a plan cannot take down; growing on is by tiling, not planned yet.)
GROW_TO = 4


def growth(site: Mapping[str, Any], book: Mapping[str, dict], p: Mapping[str, Any], tanks: bool) -> set[tuple[int, int]]:
    """The tiles the pieces piece `p` grows into in place (to GROW_TO reactors) take, in `p`'s own frame, with the
    tiles their cells come in by: kept clear of the plant's water and power lines from the first."""
    from companion import nuclear_book
    facts = rates.of(site)
    out: set[tuple[int, int]] = set()
    now, frame = p, (0.0, 0.0)
    for n, _ in nuclear_book.tiers(book):
        if n <= len(now["reactors"]) or n > GROW_TO:
            continue
        for label in nuclear_book.labels(book, n):
            try:
                q = nuclear_book.piece(site, book[label], tanks)
            except ValueError:
                continue
            shift = nuclear_book.align(facts, now["book"], q["book"])
            if shift is None:
                continue
            frame = (frame[0] - shift[0], frame[1] - shift[1])
            fx, fy = round(frame[0]), round(frame[1])
            out |= {(x + fx, y + fy) for x, y in q["tiles"]}
            out |= {(t[0] + fx, t[1] + fy) for l in q["lines"] for t in (l["feed"], l["on"])}
            now = q
            break
    return out


def _key(name: str, x: float, y: float) -> tuple[str, int, int]:
    return name, round(x * 2), round(y * 2)


def _own(world: Mapping[str, Any]) -> set[tuple[str, int, int]]:
    return {_key(e["n"], e["x"], e["y"]) for e in world.get("entities", []) if e.get("f") == world.get("force")}


def standing(site: Mapping[str, Any], world: Mapping[str, Any], book: Mapping[str, dict]) -> dict | None:
    """The book's piece that stands on the map (the engineer's reactors are its): {"tier", "label", "tanks",
    "piece", "offset"}; None when none does."""
    from companion import nuclear_book
    facts = rates.of(site)
    mine = world.get("force")
    reactors = [e for e in world.get("entities", []) if e.get("f") == mine
                and ((facts.entities.get(e.get("n")) or {}).get("kind") if facts else None) == "reactor"]
    if not reactors:
        return None
    have = _own(world)
    for tier, label in nuclear_book.tiers(book):
        if tier != len(reactors):
            continue
        for tanks in (True, False):
            try:
                p = nuclear_book.piece(site, book[label], tanks, buildable=False)   # (it stands: made already)
            except ValueError:
                continue
            first = p["reactors"][0]
            for r in reactors:
                dx, dy = r["x"] - first.x, r["y"] - first.y
                if dx != round(dx) or dy != round(dy):
                    continue
                hits = sum(_key(e.name, e.x + dx, e.y + dy) in have for e in p["book"])
                if hits >= 0.9 * len(p["book"]):
                    return {"tier": tier, "label": label, "tanks": tanks, "piece": p, "offset": (int(dx), int(dy))}
    return None


def feeds(site: Mapping[str, Any], world: Mapping[str, Any], book: Mapping[str, dict] | None = None) -> list[dict]:
    """Where the cells' belt goes into the standing book plant: each fuel line's feed tile (the belt ends there facing
    `face`, sideloading its head) and the tile past its spent-cell sink where a belt may go on to the next line; empty
    when no book plant stands."""
    from companion import nuclear_book
    book = nuclear_book.load() if book is None else book
    now = standing(site, world, book) if book else None
    if now is None:
        return []
    ox, oy = now["offset"]
    return [{"feed": (l["feed"][0] + ox, l["feed"][1] + oy), "face": l["face"], "on": (l["on"][0] + ox, l["on"][1] + oy)}
            for l in now["piece"]["lines"]]


def book_plan(site: Mapping[str, Any], world: Mapping[str, Any], book: Mapping[str, dict]) -> dict:
    """The book's first piece (FIRST_TIER reactors) by the shore nearest home, or the next piece over the one that
    stands, on the same spot (an in-place upgrade: only what is not there yet). With its steam tanks where they fit,
    without them where room is short. Raises PlanError when it cannot be had."""
    from companion import nuclear_book
    facts = rates.of(site)
    order = nuclear_book.tiers(book)
    if not order:
        raise layout_planner.PlanError("the nuclear book has no pieces")
    now = standing(site, world, book)
    failed = []
    if now is None:
        tier, label = next(((n, l) for n, l in order if n >= FIRST_TIER), order[-1])
        for tanks in (True, False):
            try:
                p = nuclear_book.piece(site, book[label], tanks)
                return _stamp(site, world, p, None, f"{label}{'' if tanks else ', its steam tanks left out'}",
                              growth(site, book, p, tanks))
            except (ValueError, layout_planner.PlanError) as exc:
                failed.append(str(exc))
        raise layout_planner.PlanError(f"no room for the nuclear book's {label}: {failed[-1]}")
    bigger = next(((n, l) for n, l in order if n > now["tier"] and n <= GROW_TO), None)
    if bigger is None:
        raise layout_planner.PlanError(f"the nuclear plant is the book's biggest piece ({now['label']}); tiling it "
                                       "sideways is not planned yet")
    # (the piece of that size that holds the one standing: a peak load piece's tanks grow into the next peak load one)
    for label in nuclear_book.labels(book, bigger[0]):
        try:
            q = nuclear_book.piece(site, book[label], now["tanks"])
        except ValueError as exc:
            failed.append(f"{label}: {exc}")
            continue
        shift = nuclear_book.align(facts, now["piece"]["book"], q["book"])
        if shift is not None:
            ox, oy = now["offset"]
            return _stamp(site, world, q, (round(ox - shift[0]), round(oy - shift[1])), f"{label}, grown in place",
                          growth(site, book, q, now["tanks"]))
        failed.append(f"{label} does not hold its {now['label']}")
    raise layout_planner.PlanError(f"no piece of {bigger[0]} reactors grows from the {now['label']}: {failed[-1]}")


def _pumps_per_row(site: Mapping[str, Any], p: Mapping[str, Any], rows: int) -> int:
    """Offshore pumps a row of the piece's heat exchangers needs: their water at full heat (each exchanger's steam, as
    nuclear.sizing works it out from the game's facts) over what the best pump gives."""
    facts = rates.of(site)
    size = nuclear.sizing(site, REACTORS)
    pump = max(((n, e) for n, e in (facts.entities.items() if facts else []) if e.get("kind") == "pump"),
               key=lambda ne: (bool(ne[1].get("ready")), float(ne[1].get("per_second") or 0)), default=None)
    if not size or not size.get("heat_exchangers") or pump is None or not float(pump[1].get("per_second") or 0):
        return 1
    each = float(size["steam_per_second"]) / float(size["heat_exchangers"])
    exchangers = sum(1 for e in p["book"] if facts and (facts.entities.get(e.name) or {}).get("kind") == "boiler")
    return max(1, math.ceil(exchangers / max(1, rows) * each / float(pump[1]["per_second"]) - 1e-6))


def _stamp(site: Mapping[str, Any], world: Mapping[str, Any], p: Mapping[str, Any], offset: tuple[int, int] | None,
           label: str, grows: set[tuple[int, int]] = frozenset()) -> dict:
    """Piece `p` on the map at `offset` (None: by the shore nearest home, with room for what it grows into, `grows`),
    what stands there already left out: its water piped from an offshore pump (or the water already piped) to an inlet
    of every row of exchangers not yet watered, its poles joined to the live grid; neither crosses `grows`."""
    from companion import land
    from companion.base_plan import blocked_tiles, find_fit
    from companion.nuclear_book import SINK_INSERTER, SPENT
    from companion.oil_plant import cliff_tiles
    from companion.power import connect, live_poles
    from companion.rocket_plan import _pump, _tiles as world_tiles, pipe_map
    from companion.nuclear_book import heat_problems
    facts = rates.of(site)
    cold = heat_problems(facts, p["book"])   # (every exchanger joined to a reactor's heat: the game's own points)
    if cold:
        raise layout_planner.PlanError(f"the nuclear book's {label} does not join: {cold[0]}")
    have = _own(world)
    cliffs = cliff_tiles(world)
    taken = set(blocked_tiles(world)) | team_area.forbidden(world) | cliffs
    bare = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles")}
    kept = land.reserved_tiles(world) - set(blocked_tiles(bare))
    spots = team_area.pumps(world)
    if not spots:
        raise layout_planner.PlanError("no shore for the nuclear plant's water")
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    rows: dict[int, list[dict]] = {}   # (an inlet per row of exchangers: the ports of one row share its water)
    for w in p["water"]:
        rows.setdefault(w["of"][1], []).append(w)
    if offset is None:
        shore = min(spots, key=lambda s: (s["x"] - home[0]) ** 2 + (s["y"] - home[1]) ** 2)
        shape = set(p["tiles"]) | {l["feed"] for l in p["lines"]} | {l["on"] for l in p["lines"]} | set(grows)
        for w in p["water"]:
            shape |= {w["pair"], w["join"]}
        ring = {(x + dx, y + dy) for x, y in shape for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        fit = find_fit(taken, ring, home, tuple(world["area"]), anchor=(shore["x"], shore["y"]))
        if fit is None:
            raise layout_planner.PlanError(f"no room for the nuclear book's {label} near the shore")
        offset = fit
    ox, oy = offset
    out = [Placed(e.name, e.x + ox, e.y + oy, e.direction, e.recipe, e.kind) for e in p["entities"]
           if _key(e.name, e.x + ox, e.y + oy) not in have]
    new_tiles = set()
    for e in out:
        f = (facts.entities.get(e.name) or {}) if facts else {}
        new_tiles |= nuclear_cell.tiles_of((e.x, e.y), int(f.get("width") or 1), int(f.get("height") or 1), e.direction)
    if new_tiles & taken:
        raise layout_planner.PlanError(f"the nuclear book's {label} does not fit there: something stands in its way")
    later = {(x + ox, y + oy) for x, y in grows} - new_tiles   # (where it grows: no pipe or pole of its lines there)
    used = taken | new_tiles | later
    # Water: one inlet a row not watered yet (its pair pipe-to-ground not on the map), the one nearest the shore.
    pipes = pipe_map(world, out, {})
    pipes.blocked |= new_tiles | cliffs | later
    need = []
    for row in rows.values():
        if any(_key("pipe-to-ground", w["pair"][0] + ox + 0.5, w["pair"][1] + oy + 0.5) in have for w in row):
            continue
        need.append(min(row, key=lambda w: min(math.dist((w["join"][0] + ox, w["join"][1] + oy), (s["x"], s["y"]))
                                                for s in spots)))
    # Each row's own offshore pumps, as many as its exchangers boil water (the base's water serves its boilers: 16
    # exchangers take more than a pump gives); only with no shore spot to pipe from, the water already piped.
    theirs: set[tuple[int, int]] = {t for t, f in pipes.pipes.items() if f == "water"}
    tried: set[tuple[int, int]] = set()
    pumps_each = _pumps_per_row(site, p, len(rows))
    for w in need:
        pair = (w["pair"][0] + ox, w["pair"][1] + oy)
        join = (w["join"][0] + ox, w["join"][1] + oy)
        if {pair, join} & used:
            raise layout_planner.PlanError("no room for the nuclear plant's water inlet")
        out.append(Placed("pipe-to-ground", pair[0] + 0.5, pair[1] + 0.5, w["pair_direction"]))
        used.add(pair)
        pipes.blocked.add(pair)
        pipes.reserved[join] = "water"
        network: set[tuple[int, int]] = set()
        placed = 0
        for _ in range(pumps_each + PUMP_TRIES):
            if placed >= pumps_each:
                break
            pump = _pump(world, (used - kept) | tried, join)   # (a shore cannot move: kept ground is no bar)
            if pump is None:
                break
            entity, outlet = pump
            spot = world_tiles(entity.name, entity.x, entity.y, entity.direction)
            tried |= spot | {outlet}
            pipes.blocked |= spot
            path = pipes.route("water", join, outlet, joins=network) if network else pipes.route("water", join, outlet)
            if path is None:
                continue
            out += [entity] + path
            used |= spot | {(math.floor(q.x), math.floor(q.y)) for q in path}
            network |= {join, outlet} | {(math.floor(q.x), math.floor(q.y)) for q in path if q.name == "pipe"}
            placed += 1
            # (the next pump PUMP_GAP tiles clear of this one: the game lists each shore spot as one pump alone, and
            # a second beside the first was refused, stage-nuclear 2026-10-31)
            px, py = math.floor(entity.x), math.floor(entity.y)
            tried |= {(px + i, py + j) for i in range(-PUMP_GAP, PUMP_GAP + 1) for j in range(-PUMP_GAP, PUMP_GAP + 1)}
        if placed < pumps_each:
            if placed or not theirs:
                raise layout_planner.PlanError(f"only {placed} of the {pumps_each} offshore pumps a row of the "
                                               "nuclear plant's exchangers needs have a pipe to it")
            start = min(theirs, key=lambda t: math.dist(t, join))   # (no shore of its own: the water piped nearest)
            path = pipes.route("water", start, join, joins=theirs)
            if path is None:
                raise layout_planner.PlanError("no water pipe to the nuclear plant")
            out += path
            used |= {(math.floor(q.x), math.floor(q.y)) for q in path}
    # Power: the book's own poles reach one another; the one nearest the grid is joined to it (the game wires the
    # rest as they are built).
    live = live_poles(world)
    poles = [e for e in out if e.name.endswith("electric-pole") or e.name == "substation"]
    if poles and live:
        near = min(poles, key=lambda e: min(math.dist((e.x, e.y), q) for q in live))
        hard = (used - kept) | cliffs | set(pipes.pipes) | set(pipes.ends)
        extra, problems = connect([near], live, hard | kept)
        if problems:
            extra, problems = connect([near], live, hard)
        if problems:
            raise layout_planner.PlanError(f"nuclear plant: {problems[0]}")
        out += extra
    # The inserters it adds (spent cells' and their sinks') get a small pole where none of the book's reaches them
    # (a 2c's sink stands past its substations' supply), wired to the book's poles.
    from companion.uranium_plant import SUPPLY
    mine = world.get("force")
    reach = [(e.x, e.y, SUPPLY.get(e.name, 2.5)) for e in out if e.name in SUPPLY]
    on_grid = set(live)   # (a pole of its own on a network no generator feeds powers nothing)
    reach += [(e["x"], e["y"], SUPPLY.get(e["n"], 2.5)) for e in world.get("entities", [])
              if e.get("f") == mine and e.get("n") in SUPPLY and (e["x"], e["y"]) in on_grid]
    book_poles = [(x, y) for x, y, _ in reach]   # (what the new poles wire to: the book's own, joined above)
    added: list[Placed] = []
    taken_now = used | set(pipes.pipes) | set(pipes.ends) | {(math.floor(e.x), math.floor(e.y)) for e in out}
    for e in [e for e in out if e.name.endswith("inserter")]:
        t = (math.floor(e.x), math.floor(e.y))
        if any(abs(t[0] + 0.5 - x) < s + 0.5 and abs(t[1] + 0.5 - y) < s + 0.5 for x, y, s in reach):
            continue
        got = _poles([({t}, e)], taken_now, (t[0] - 2, t[1] - 2, t[0] + 3, t[1] + 3))
        added += got
        taken_now |= {(math.floor(q.x), math.floor(q.y)) for q in got}
        reach += [(q.x, q.y, POLE_SUPPLY) for q in got]
    if added:
        extra, problems = connect(added, live + book_poles, (taken_now - kept) | cliffs)
        if problems:
            raise layout_planner.PlanError(f"nuclear plant: {problems[0]}")
        out += added + extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    dx, dy = -float(origin["x"]), -float(origin["y"])
    sinks = {(s["inserter"][0] + ox, s["inserter"][1] + oy) for s in p["sinks"]}
    entities = []
    for n, e in enumerate(out, start=1):
        row = e.to_plan(f"nuclear-{n}", dx, dy)
        if e.name == SINK_INSERTER and (math.floor(e.x), math.floor(e.y)) in sinks:
            row.update(filters=[{"index": 1, "name": SPENT}], use_filters=True)   # (only the spent cells)
        entities.append(row)
    if not entities:
        raise layout_planner.PlanError(f"the nuclear book's {label} stands already")
    reactor_ids = [entities[i]["id"] for i, e in enumerate(out)
                   if facts and (facts.entities.get(e.name) or {}).get("kind") == "reactor"]
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    witnesses = [{"kind": "entity_built", "entity_id": i} for i in reactor_ids] or \
        [{"kind": "entity_built", "entity_id": entities[0]["id"]}]
    return {"plan_id": f"nuclear-plant-{safe}", "source": "nuclear-book", "purpose": "nuclear-power",
            "entities": entities, "pumps": sum(1 for e in out if e.name == "offshore-pump"),
            "notes": [f"the nuclear book's {label}: {len(p['reactors'])} reactors"],
            "stages": [{"id": "nuclear-plant", "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": [], "witnesses": witnesses}]}
