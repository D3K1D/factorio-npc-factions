"""The perimeter ammo loop (D-19; the user's rule U-1: hand feeding is not automation).

The class: what its turrets fire is carried to each turret by hand. The turret_ammo upkeep took a quarter to near half
of the engineer's time in the runs, and fell behind as the waves grew. Its variants: the wall ring's turrets along a
side; turrets added to the ring later; a ring outgrown and a new one walled further out; a crossing under the wall (a
belt, a door's way in) on the loop's line; the loop's source not built yet (no magazine row); better ammunition once
researched; a loop with no power for its inserters.

One mechanism, as the user put it (2026-10-29): "a belt that goes around the entire perimeter and loops so it always has
ammo to grab via the belt + an inserter + power lines so the inserters have power". Turrets put down inside the base
early get no belt (it gets messy): they stay on the sweeps (DesignedStart.defense_supply). For the ring it holds:

    the loop     one belt round all four sides, LOOP_OFFSET tiles in from the ring's edge (just inside its turrets),
                 closed; what stands on its line (a door's way in, a belt crossing) it goes under, underground belts
                 a few tiles at a time
    its arms     an inserter from the loop into each of the ring's turrets (ARM_OFFSET: between the two)
    its power    small poles along it every POLE_STEP tiles, joined to the power network
    its feed     by the ladder: a belt from the defense supply's magazine row (companion/defense_production.py: an
                 inserter out of its end chest) side-loaded onto the loop; else a chest the engineer fills, an inserter
                 from it onto the loop (one stop for the whole ring), linked to the row once that stands

Turrets added to the ring later get their arms only (the loop runs round the whole ring already); a ring outgrown and
a new one walled further out gets a loop of its own. The game loads a turret by hand only while no inserter of its own
brings it ammunition (DesignedStart.auto_fed): a new turret before its arm stands, the kit's, a loop run dry.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

PURPOSE = "ammo-line"
ARM_OFFSET = 9        # tiles in from the ring's edge: its turrets stand at 7 and 8 (perimeter.TURRET_OFFSET)
LOOP_OFFSET = 10      # the loop's line
HOP = 4               # tiles an underground pair passes under (yellow: its exit 5 past its entrance)
LANE_SEARCH = 20000   # route states a detour round what is too wide to go under may search
POLE_STEP = 7         # loop tiles between its poles (a small pole's wire reaches 7.5; arms get one of their own)
SIDES = ("north", "east", "south", "west")   # (the loop a side a plan: a ring's whole loop is more than one plan takes)
TURRET_BAND = (7.5, 8.5)    # a turret's centre this far in from the ring's edge stands on its line (tiles 7 and 8),
                            # where an arm at ARM_OFFSET reaches it
LOOP_IRON = 0.5       # iron plates a second it takes to lay a loop of a few hundred belts in its turn (or them in hand)
CHEST = "iron-chest"  # (no wood needed: a map without trees blocked a wooden one)
INSERTER = "inserter"
BELT = "transport-belt"
UNDER = "underground-belt"
POLE = "small-electric-pole"
NEEDS = (INSERTER, BELT, UNDER, POLE)
INWARD = {"north": (0, 1), "south": (0, -1), "west": (1, 0), "east": (-1, 0)}
FACING = {(0, 1): SOUTH, (0, -1): NORTH, (1, 0): EAST, (-1, 0): WEST}   # (an inserter faces where it picks up)
STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}


# --- what it holds -------------------------------------------------------------------------------------------------

def _loops(site: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Its loop plans' keeps (and those of their arms and links): built, or being built; one left part built is not
    (its side laid again, what stands taken over: Codex's review)."""
    return [b["keep"] for b in site.get("base") or [] if isinstance(b, Mapping) and b.get("purpose") == PURPOSE
            and isinstance(b.get("keep"), Mapping) and b["keep"].get("loop")
            and (b.get("building") or b.get("planned") is None
                 or int(b.get("buildings") or 0) >= int(b.get("planned") or 0))]


def _ring(site: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """The ring it holds (world tiles); None while it holds none. A ring that is the plot's outer edges (one to three
    sides, the neighbours holding the rest) gets an open line along them, its feed at its upstream end."""
    from companion import perimeter
    held = perimeter.held_ring(site)
    if held is None:
        return None
    ring = tuple(int(v) for v in held[0])
    return ring if perimeter.ring_edges(site, ring) else None


def sides_of(site: Mapping[str, Any], ring) -> list[str]:
    """Its ring's sides in the order its line runs (clockwise), an open line's from its upstream end."""
    from companion import perimeter
    present = {e.name for e in perimeter.ring_edges(site, ring)}
    ordered = [s for s in SIDES if s in present]
    if len(ordered) == len(SIDES):
        return ordered
    # (an open line starts at a side with no side before it, clockwise)
    start = next(s for s in ordered if SIDES[(SIDES.index(s) - 1) % 4] not in present)
    k = SIDES.index(start)
    return [SIDES[(k + i) % 4] for i in range(4) if SIDES[(k + i) % 4] in present]


def closed_loop(site: Mapping[str, Any], ring) -> bool:
    return len(sides_of(site, ring)) == len(SIDES)


def _looped(site: Mapping[str, Any], ring) -> list[Mapping[str, Any]]:
    return [k for k in _loops(site) if tuple(int(v) for v in k["loop"]) == tuple(ring)]


def _offset(edge, x: float, y: float) -> float:
    """How far (x, y) lies in from the ring's edge on `edge`'s side."""
    x0, y0, x1, y1 = edge.plot
    return {"north": y - y0, "south": y1 - y, "west": x - x0, "east": x1 - x}[edge.name]


def ring_turrets(site: Mapping[str, Any], ring) -> list[tuple[Any, float, float]]:
    """Its gun turrets standing on the ring's turret line: (edge, x, y), world centres. Turrets inside the base are not
    (the user: they stay on the sweeps)."""
    from companion import perimeter
    ox, oy = perimeter._local(site)
    out = []
    for g in site.get("guards") or []:
        if not (isinstance(g, Mapping) and g.get("kind") == "ammo-turret" and "x" in g and "y" in g):
            continue
        x, y = float(g["x"]) + ox, float(g["y"]) + oy
        for edge in perimeter.ring_edges(site, ring):
            if TURRET_BAND[0] <= _offset(edge, x, y) <= TURRET_BAND[1] \
                    and edge.lo - 2 <= edge.along_of(x, y) <= edge.hi + 2:
                out.append((edge, x, y))
                break
    return out


def _armed(site: Mapping[str, Any], ring) -> set[tuple[int, int]]:
    """The ring's turrets (world centres, rounded) its loop's arms feed."""
    return {(round(float(t[0])), round(float(t[1]))) for k in _looped(site, ring) for t in k.get("turrets") or []
            if isinstance(t, (list, tuple)) and len(t) >= 2}


def laid(site: Mapping[str, Any], ring) -> set[str]:
    """The sides of the ring its loop runs along already (planned: a plan a side)."""
    return {str(k.get("side")) for k in _looped(site, ring) if k.get("side") in SIDES}


def unarmed(site: Mapping[str, Any], ring) -> list[tuple[Any, float, float]]:
    """The ring's turrets with no arm, on sides its loop runs along (the others get theirs with their side)."""
    armed, sides = _armed(site, ring), laid(site, ring)
    return [t for t in ring_turrets(site, ring) if (round(t[1]), round(t[2])) not in armed and t[0].name in sides]


def unlinked(site: Mapping[str, Any], ring) -> Mapping[str, Any] | None:
    """Its loop's chest filled by hand while a magazine row of its ammunition stands, not linked yet: the ladder's
    next step, a belt from the row into that chest."""
    keeps = _looped(site, ring)
    if any(k.get("source") == "row" for k in keeps):
        return None
    head = next((k for k in keeps if k.get("source") == "chest" and k.get("head")), None)
    return head if head and _row_outputs_kept(site, str(head.get("ammo"))) else None


def what_is_due(site: Mapping[str, Any], pressed_only: bool = False) -> str | None:
    """"loop" (the ring has none, turrets stand on it), "arms" (turrets on it with no arm), "link" (its chest to the
    row), or None."""
    from companion import catalog_planner as cp, ratios
    if PURPOSE in set(cp.bp._as_list(site.get("failed_purposes"))):
        return None
    if not all(n in ratios.unlocked(site) for n in NEEDS) or not cp.has_power(site) or not _ammo(site):
        return None
    ring = _ring(site)
    if ring is None or not ring_turrets(site, ring):
        return None
    if len(laid(site, ring)) < len(sides_of(site, ring)):
        # (once its ring's turrets are drawn on: full ones want no loop yet, its belts not spent before they're needed;
        # or, `pressed_only` aside, once the base makes iron enough to spare a loop's belts (LOOP_AFFORD): on a normal
        # map its turrets are kept full by hand, never drawn on, and the hand feeding never ended, D-24)
        if not _iron_enough(site):
            return None
        if _drawn(site, ring) or laid(site, ring):   # (a loop begun is carried on round: part_laid)
            return "loop"
        return None if pressed_only or not _affords(site) else "loop"
    if unarmed(site, ring):
        return "arms"
    return "link" if unlinked(site, ring) else None


DRAWN = 10   # magazines: a ring turret below this has had its ammunition drawn on by the waves


def _drawn(site: Mapping[str, Any], ring) -> bool:
    from companion import perimeter
    ox, oy = perimeter._local(site)
    at = {(round(float(g["x"]) + ox), round(float(g["y"]) + oy)): float(g.get("ammo") or 0)
          for g in site.get("guards") or [] if isinstance(g, Mapping) and "x" in g and "y" in g}
    return any(at.get((round(x), round(y)), DRAWN) < DRAWN for _, x, y in ring_turrets(site, ring))


def due(site: Mapping[str, Any]) -> bool:
    return what_is_due(site) is not None


def part_laid(site: Mapping[str, Any]) -> bool:
    """A loop begun and not yet round its ring (a side laid, more due): it is carried on between reinforce rounds, no
    attack on now. late-combat laid its north side by minute 8 and reinforce took every turn after, its other sides
    never laid, the turrets reinforce added fed by hand (D-24, the coordinator: a loop starved behind reinforce defeats
    the reinforce)."""
    ring = _ring(site)
    return ring is not None and bool(laid(site, ring)) and what_is_due(site) == "loop"


LOOP_AFFORD = 2.0   # iron plates a second the base makes, from which a loop's belts are spared without waves pressing


def _affords(site: Mapping[str, Any]) -> bool:
    from companion import plate_lead
    return plate_lead.income(site, "iron-plate") >= LOOP_AFFORD


def _iron_enough(site: Mapping[str, Any]) -> bool:
    """A loop is a few hundred belts: laid while its iron comes in (LOOP_IRON a second), or with them in hand."""
    from companion import plate_lead
    held = site.get("inventory") if isinstance(site.get("inventory"), Mapping) else {}
    return plate_lead.income(site, "iron-plate") >= LOOP_IRON or float(held.get(BELT) or 0) >= 200


def _ammo(site: Mapping[str, Any]) -> str | None:
    """What the loop carries: the ammunition a magazine row of its own makes (the belt comes from there), else the best
    its gun turret takes that it can make."""
    from companion import perimeter
    kit = perimeter.arms(site)
    gun = next((t for t in kit["turrets"] if t.get("kind") == "ammo"), None)
    if not gun:
        return None
    takes = [a for c in gun.get("ammo_categories") or [] for a in kit["ammo"].get(c) or []]
    for item in takes:
        if _row_outputs_kept(site, item):
            return item
    return perimeter._ammo_for(gun, kit["ammo"], site)


def _row_outputs_kept(site: Mapping[str, Any], ammo: str) -> list[tuple[float, float]]:
    """Its magazine rows' output chests of `ammo` (local tiles, as defense_production.plan keeps them)."""
    from companion import defense_production
    out = []
    for b in site.get("base") or []:
        keep = b.get("keep") if isinstance(b, Mapping) and b.get("purpose") == defense_production.GOAL else None
        if isinstance(keep, Mapping) and keep.get("item") == ammo:
            out += [(float(p[0]), float(p[1])) for p in keep.get("outputs") or [] if isinstance(p, (list, tuple))]
    return out


def _row_chests(site: Mapping[str, Any], world: Mapping[str, Any], ammo: str) -> list[tuple[int, int]]:
    """Those output chests standing (world tiles)."""
    from companion import perimeter
    ox, oy = perimeter._local(site)
    standing = {(math.floor(float(e["x"])), math.floor(float(e["y"]))) for e in world.get("entities") or []
                if e.get("f") == world.get("force") and e.get("t") == "container"}
    return [t for t in ((math.floor(x + ox), math.floor(y + oy)) for x, y in _row_outputs_kept(site, ammo))
            if t in standing]


# --- the plans -------------------------------------------------------------------------------------------------------

def plan(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """What is due (what_is_due): the loop, its arms for turrets added since, or its link to the magazine row."""
    from companion import perimeter
    kind = what_is_due(site)
    ring = _ring(site)
    if kind is None or ring is None:
        raise perimeter.Done("the ring's turrets have their loop and arms")
    if kind == "loop":
        return plan_loop(site, world, ring)
    if kind == "arms":
        return plan_arms(site, world, ring)
    return plan_link(site, world, ring)


def _path(site: Mapping[str, Any], ring) -> list[tuple[tuple[int, int], int]]:
    """The line's tiles in order, clockwise, each with the way its belt runs: east along the north side, south down
    the east, west along the south, north up the west; round to its start on a whole ring. Where a side has none
    before or after it (the plot's outer edges), it runs from or to that side's end (its wall line's lo/hi)."""
    sides = _edges_by_side(site, ring)
    row = {n: e.row(LOOP_OFFSET) for n, e in sides.items()}
    out = []
    for name in sides_of(site, ring):
        e = sides[name]
        if name == "north":
            a0, a1 = row.get("west", e.lo), (row["east"] - 1 if "east" in row else e.hi)
            out += [((x, row[name]), EAST) for x in range(a0, a1 + 1)]
        elif name == "east":
            a0, a1 = row.get("north", e.lo), (row["south"] - 1 if "south" in row else e.hi)
            out += [((row[name], y), SOUTH) for y in range(a0, a1 + 1)]
        elif name == "south":
            a0, a1 = row.get("east", e.hi), (row["west"] + 1 if "west" in row else e.lo)
            out += [((x, row[name]), WEST) for x in range(a0, a1 - 1, -1)]
        else:
            a0, a1 = row.get("south", e.hi), (row["north"] + 1 if "north" in row else e.lo)
            out += [((row[name], y), NORTH) for y in range(a0, a1 - 1, -1)]
    return out


def _edges_by_side(site: Mapping[str, Any], ring) -> dict:
    """Its ring's edges by side, one a side: a plot edge and the yard band beyond it on the same line, meeting, are
    one (Codex's review: NPC-7's south edge and its band; one replaced the other, its turrets armed with no belt)."""
    from dataclasses import replace
    from companion import perimeter
    out: dict = {}
    for e in sorted(perimeter.ring_edges(site, ring), key=lambda e: (e.name, e.lo)):
        have = out.get(e.name)
        if have is None:
            out[e.name] = e
        elif have.row(LOOP_OFFSET) == e.row(LOOP_OFFSET) and e.lo <= have.hi + 1:
            out[e.name] = replace(have, hi=max(have.hi, e.hi))
        elif (e.hi - e.lo) > (have.hi - have.lo):   # (apart: the longer one; turrets off it get no arm, _arms)
            out[e.name] = e
    return out


def _side_of(d: int) -> str:
    return {EAST: "north", SOUTH: "east", WEST: "south", NORTH: "west"}[d]


def _lay(path, hard: set, soft: set, detour=None) -> list[Placed]:
    """Belts along `path`, going under what stands on it: a run of `hard` tiles (buildings) under one underground pair
    (HOP at most); a door's way in (`soft`: kept clear, walked through) under pairs whose ends may stand in it, as many
    as it takes. A run longer than a pair passes goes round it by `detour(from, to)` (belts from the tile before it to
    the first clear tile after it, or None); round a corner it cannot be laid."""
    from companion import layout_planner
    n = len(path)
    out: list[Placed] = []
    i = 0
    while i < n:
        (x, y), d = path[i]
        if (x, y) not in hard and (x, y) not in soft:
            out.append(Placed(BELT, x + 0.5, y + 0.5, d))
            i += 1
            continue
        # (a blocked run from i: its entrance the belt before it, on the same straight run)
        if not out or out[-1].name != BELT or out[-1].direction != d:
            raise layout_planner.PlanError(f"the ammo loop cannot go under what stands at {x},{y} (by a corner)")
        entrance = out.pop()
        out.append(Placed(UNDER, entrance.x, entrance.y, d, kind="input"))
        j = i
        while True:
            # (its exit: the first clear tile past what it goes under, within HOP past the entrance; across a door's
            # way the farthest that is no building, on this straight run, and under again from there)
            start = j - 1
            reach = [k for k in range(j, min(start + HOP + 1, n - 1) + 1)
                     if all(path[m][1] == d for m in range(j, k + 1))]
            clear = [k for k in reach if path[k][0] not in hard and path[k][0] not in soft]
            # (in a door's way: the farthest exit the chain goes on from, its next tile no building: Codex's review)
            onward = [k for k in reversed(reach) if path[k][0] not in hard
                      and (k + 1 >= n or path[k + 1][0] not in hard)]
            exit_at = clear[0] if clear else (onward[0] if onward else None)
            if exit_at is None and detour is not None:
                # (too long to go under, a building wider than a pair passes: round it, to the first clear tile after it;
                # the late-combat base's ring crossed one, D-20)
                after = next((k for k in range(j, n) if path[k][0] not in hard and path[k][0] not in soft
                              and all(path[m][1] == d for m in range(j, k + 1))), None)
                way = detour((math.floor(entrance.x), math.floor(entrance.y)), path[after][0]) if after else None
                if way:
                    out.pop()   # (its entrance: a plain belt again, the detour's first)
                    tail = way[-1]
                    way[-1] = Placed(tail.name, tail.x, tail.y, d if tail.name == BELT else tail.direction,
                                     kind=getattr(tail, "kind", None))
                    out.extend(way)
                    j = after + 1
                    break
            if exit_at is None:
                raise layout_planner.PlanError(f"the ammo loop cannot go under what stands at {x},{y}")
            (ex, ey), _ = path[exit_at]
            out.append(Placed(UNDER, ex + 0.5, ey + 0.5, d, kind="output"))
            j = exit_at + 1
            if path[exit_at][0] in soft and j < n and path[j][0] in soft:   # (still in a door's way: under again)
                if path[j][1] != d:
                    raise layout_planner.PlanError(f"the ammo loop cannot leave a door's way at {ex},{ey}")
                (nx, ny), _ = path[j]
                out.append(Placed(UNDER, nx + 0.5, ny + 0.5, d, kind="input"))
                j += 1
                continue
            break
        i = j
    return out


def _poles_for(arms: list[Placed], along: list[Placed], ground, edges) -> list[Placed]:
    """Small poles inside the loop every POLE_STEP tiles of it, and one by each arm none of those reaches."""
    from companion import layout_planner
    poles: list[Placed] = []

    def near(p, e):
        return abs(p.x - e.x) <= 2.5 and abs(p.y - e.y) <= 2.5

    for k, b in enumerate(along):
        if k % POLE_STEP:
            continue
        x, y = math.floor(b.x), math.floor(b.y)
        for edge in edges:   # (inside the loop: from the side it runs along)
            if edge.row(LOOP_OFFSET) == (y if edge.horizontal else x):
                ix, iy = INWARD[edge.name]
                spot = (x + ix, y + iy)
                if ground.free({spot}):
                    poles.append(Placed(POLE, spot[0] + 0.5, spot[1] + 0.5))
                    ground.take({spot})
                break
    for arm in arms:
        if any(near(p, arm) for p in poles):
            continue
        ax, ay = math.floor(arm.x), math.floor(arm.y)
        spot = next((s for s in ((ax + 1, ay), (ax - 1, ay), (ax, ay + 1), (ax, ay - 1)) if ground.free({s})), None)
        if spot is None:
            raise layout_planner.PlanError(f"no room for a pole by the ammo loop's arm at {ax},{ay}")
        poles.append(Placed(POLE, spot[0] + 0.5, spot[1] + 0.5))
        ground.take({spot})
    return poles


def _arms(turrets, ground, behind: set | None = None) -> tuple[list[Placed], list[tuple[float, float]]]:
    """An inserter for each turret on the ring, between it and the loop, facing the loop it picks from (`behind`: the
    loop tiles laid; none where the line does not run)."""
    arms, fed = [], []
    for edge, x, y in turrets:
        ix, iy = INWARD[edge.name]
        a = math.floor(edge.along_of(x, y))
        for along in (a - 1, a):   # (either of the turret's two tiles along the ring)
            tile = edge.tile(along, ARM_OFFSET)
            if behind is not None and (tile[0] + ix, tile[1] + iy) not in behind:
                continue
            if ground.free({tile}):
                arms.append(Placed(INSERTER, tile[0] + 0.5, tile[1] + 0.5, FACING[(ix, iy)]))
                ground.take({tile})
                fed.append((x, y))
                break
    return arms, fed


def _onward(world, whole, ground) -> None:
    """The loop's line where only its own pieces reach: the tile a loop belt runs on to (a side laid runs on into the
    next), or a loop tile an arm of it picks from. The map blocks the tile ahead of every belt and an inserter's
    pickup (base_plan.blocked_tiles: no new belt to feed or be taken from by accident); on the loop's own line that is
    the loop going on (the proving ring: the east side's first tile taken by the north side's last belt)."""
    line = {p[0]: p[1] for p in whole}
    occupied: set[tuple[int, int]] = set()
    onward: set[tuple[int, int]] = set()
    for e in world.get("entities") or []:
        w, h = int(e.get("w") or 1), int(e.get("h") or 1)
        x0, y0 = math.floor(float(e["x"]) - w / 2 + 0.5), math.floor(float(e["y"]) - h / 2 + 0.5)
        occupied |= {(x0 + i, y0 + j) for i in range(w) for j in range(h)}
        if e.get("f") != world.get("force") or e.get("d") is None:
            continue
        at, d = (math.floor(float(e["x"])), math.floor(float(e["y"]))), int(e["d"])
        dx, dy = STEP.get(d, (0, 0))
        if e.get("n") in (BELT, UNDER) and line.get(at) == d:
            onward.add((at[0] + dx, at[1] + dy))
        elif e.get("n") == INSERTER and (at[0] - dx, at[1] - dy) in line:
            onward.add((at[0] - dx, at[1] - dy))
    # (never what is blocked for its own sake: water, ground the game refused, other plots; Codex. Not the ground the
    # factory plan keeps: the ring crosses that as perimeter._Ground does, and the late-combat loop's east side never
    # came, its first tile, ahead of the north side's last belt, in kept ground; D-24)
    from companion import perimeter
    plain = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles", "_lane_tiles")}
    plain["entities"], plain["_lane_tiles"] = [], set()
    ground_itself = set(perimeter.blocked_tiles(plain))
    for t in (onward & set(line)) - occupied - ground_itself:
        ground.taken.discard(t)


def _part_built(site: Mapping[str, Any], ring, side: str) -> set[tuple[int, int]]:
    """The tiles (world) of its own pieces a loop plan for this side left part built kept (not counted as laid:
    _loops); empty when there is none."""
    from companion import perimeter
    ox, oy = perimeter._local(site)
    out: set[tuple[int, int]] = set()
    for b in site.get("base") or []:
        k = b.get("keep") if isinstance(b, Mapping) and b.get("purpose") == PURPOSE else None
        if isinstance(k, Mapping) and k.get("side") == side and k.get("loop")                 and tuple(int(v) for v in k["loop"]) == tuple(ring) and not b.get("building")                 and b.get("planned") is not None and int(b.get("buildings") or 0) < int(b.get("planned") or 0):
            out |= {(math.floor(float(t[0]) + ox), math.floor(float(t[1]) + oy)) for t in k.get("tiles") or []
                    if isinstance(t, (list, tuple)) and len(t) >= 2}
    return out


def _reuse(world, path, side, ring, site, ground, mine: set) -> None:
    """A side left part built is laid again over its own pieces (the game takes over what stands): its belts and
    undergrounds on the loop's line running its way, its arms on their line facing the loop (Codex's review)."""
    from companion import perimeter
    line = {p[0]: p[1] for p in path}
    edge = next(e for e in perimeter.ring_edges(site, ring) if e.name == side)
    arm_row, facing = edge.row(ARM_OFFSET), FACING[INWARD[side]]
    for e in world.get("entities") or []:
        if e.get("f") != world.get("force") or e.get("d") is None:
            continue
        at = (math.floor(float(e["x"])), math.floor(float(e["y"])))
        if at not in mine:
            continue
        d = int(e["d"])
        if e.get("n") in (BELT, UNDER) and line.get(at) == d:
            ground.taken.discard(at)
        elif e.get("n") == INSERTER and d == facing and (at[1] if edge.horizontal else at[0]) == arm_row:
            ground.taken.discard(at)


def _ground(world):
    """What is taken, and the doors' ways in apart (the loop goes under those, its ends may stand in them)."""
    from companion import doorways, perimeter
    ground = perimeter._Ground(world)
    plain = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles", "_lane_tiles")}
    plain["_lane_tiles"] = set()
    standing = set(perimeter.blocked_tiles(plain))   # (a door's way with something standing in it: that is a building)
    soft = (doorways.lanes(world) & ground.taken) - standing
    return ground, soft


def _live_poles(world) -> list[tuple[float, float]]:
    """Its poles on a network a generator feeds (the map's live_nets; any pole when the map does not say), as
    power.live_poles (NPC Factory's, F-21) once that is merged: joined to a pole group with no generator, the loop's
    arms stand unpowered (D-21)."""
    from companion import power
    if hasattr(power, "live_poles"):
        return power.live_poles(world)
    live = set(world.get("live_nets") or ())
    return [(float(e["x"]), float(e["y"])) for e in world.get("entities") or []
            if e.get("t") == "electric-pole" and e.get("f") == world.get("force")
            and (not live or e.get("net") is None or e.get("net") in live)]


def _connect(poles, world, ground) -> list[Placed]:
    from companion import layout_planner, perimeter, power
    powered = _live_poles(world)
    if not powered:
        raise layout_planner.PlanError("no power network for the ammo loop's inserters")
    extra, problems = power.connect(poles, powered, ground.taken)
    if problems:
        raise layout_planner.PlanError("the ammo loop's inserters are out of its power network's reach")
    ground.take({(math.floor(p.x), math.floor(p.y)) for p in extra})
    return extra


def plan_loop(site: Mapping[str, Any], world: Mapping[str, Any], ring) -> dict:
    """The loop along its next side (where turrets stand first), the arms of that side's turrets, its poles, and with
    the first side its feed."""
    from companion import perimeter
    ammo = _ammo(site)
    ground, soft = _ground(world)
    done = laid(site, ring)
    turrets = ring_turrets(site, ring)
    order = sides_of(site, ring)
    if closed_loop(site, ring):   # (round a whole ring: where its turrets stand first)
        side = min((s for s in order if s not in done),
                   key=lambda s: (-sum(1 for t in turrets if t[0].name == s), SIDES.index(s)))
    else:   # (an open line: from its upstream end, its feed there)
        side = next(s for s in order if s not in done)
    whole = _path(site, ring)
    path = [p for p in whole if _side_of(p[1]) == side]
    mine = _part_built(site, ring, side)
    if mine:   # (only its own pieces' tiles: a belt of the factory's on its line is never taken over)
        _reuse(world, path, side, ring, site, ground, mine)
    _onward(world, whole, ground)
    from companion.smelting import route
    bounds = tuple(int(v) for v in (world.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6)))
    line_tiles = {p[0] for p in whole}
    # (and off the tiles its turrets' arms stand on: a detour across them left a turret with no arm; Codex's review)
    arm_tiles = {edge.tile(a, ARM_OFFSET) for edge, x, y in turrets
                 for a in (math.floor(edge.along_of(x, y)) - 1, math.floor(edge.along_of(x, y)))}

    def detour(a, b):   # (round what is too wide to go under: off its line, through nothing taken)
        return route(a, b, (set(ground.taken) | line_tiles | arm_tiles) - {a, b}, bounds=bounds, limit=120,
                     expansions=LANE_SEARCH)
    try:
        belts = _lay(path, ground.taken - soft, soft, detour)
    except Exception as exc:   # (what stands there, said: a planner failure names its cause)
        from companion import layout_planner
        at = {(math.floor(float(e["x"])), math.floor(float(e["y"]))): e.get("n") for e in world.get("entities") or []}
        near = sorted({str(at[p[0]]) for p in path[:3] + path[-3:] if p[0] in at})
        from companion import doorways, perimeter as _p
        first = path[0][0]
        plain = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles", "_lane_tiles")}
        plain["_lane_tiles"] = set()
        why = [n for n, s in (("door", doorways.lanes(world)), ("blocked", set(_p.blocked_tiles(plain))),
                              ("soft", soft)) if first in s]
        raise layout_planner.PlanError(f"{exc}; at the {side} side's ends: {', '.join(near) or 'nothing standing'}"
                                       f" ({'/'.join(why) or 'free'})")
    ground.take({(math.floor(b.x), math.floor(b.y)) for b in belts})
    ground.take({p[0] for p in whole if p[0] not in soft})   # (the loop's other sides kept clear for theirs)
    edges = perimeter.ring_edges(site, ring)
    # (an arm only where a belt of the loop is laid behind it: not where it went round something; Codex's review)
    behind = {(math.floor(b.x), math.floor(b.y)) for b in belts}
    arms, fed = _arms([t for t in turrets if t[0].name == side], ground, behind)
    first = not done
    # (on an open line its feed goes at its start: what goes on only runs on downstream)
    head, supplies, source = (_feed(site, world, belts if closed_loop(site, ring) else belts[:6], ammo, ground)
                              if first else ([], [], ""))
    poles = _poles_for(arms + [e for e in head if e.name == INSERTER], belts, ground, edges)
    extra = _connect(poles, world, ground)
    stages, n = [], len(belts)
    quarter = max(1, math.ceil(n / 4))
    for s in range(0, n, quarter):   # (its side in parts)
        part = belts[s:s + quarter]
        stages.append({"id": f"loop-{len(stages) + 1}", "depends_on": [], "members": [id(e) for e in part],
                       "witnesses": [{"kind": "entity_built", "member": id(part[-1])}]})
    loop_ids = [s["id"] for s in stages]
    if poles + extra:
        stages.append({"id": "power", "depends_on": [], "members": [id(e) for e in poles + extra],
                       "witnesses": [{"kind": "entity_built", "member": id((poles + extra)[0])}]})
    if arms:
        stages.append({"id": "arms", "depends_on": [s["id"] for s in stages], "members": [id(e) for e in arms],
                       "witnesses": [{"kind": "entity_built", "member": id(arms[0])}]})
    if head:
        stages.append({"id": f"feed-{source}", "depends_on": loop_ids, "members": [id(e) for e in head],
                       "manual_supplies": supplies, "witnesses": [{"kind": "entity_built", "member": id(head[0])}]})
    members = belts + poles + extra + arms + head
    out = perimeter._plan(site, PURPOSE, f"loop-{ring[0]}-{ring[1]}-{side}", members, stages, source="ammo-loop")
    ox, oy = perimeter._local(site)
    out["keep"] = {**_keep(site, ring, fed, ammo, source or "side", head if source == "chest" else None),
                   "side": side, "tiles": [[math.floor(e.x) - ox, math.floor(e.y) - oy] for e in belts + arms][:256]}
    out["ammo_line"] = {"kind": "loop", "side": side, "turrets": len(fed), "belts": len(belts),
                        "poles": len(poles + extra), "source": source or "side", "ammo": ammo}
    return out


def plan_arms(site: Mapping[str, Any], world: Mapping[str, Any], ring) -> dict:
    """Arms for the ring's turrets added since its loop was laid, and a pole by any its loop's poles do not reach."""
    from companion import layout_planner, perimeter
    ground, _ = _ground(world)
    laid_belts = {(math.floor(float(e["x"])), math.floor(float(e["y"]))) for e in world.get("entities") or []
                  if e.get("f") == world.get("force") and e.get("n") in (BELT, UNDER)}
    arms, fed = _arms(unarmed(site, ring), ground, laid_belts)   # (only over a belt standing behind it)
    if not arms:
        raise layout_planner.PlanError("no room for an arm between the ring's new turrets and its loop")
    standing = _live_poles(world)   # (a pole on a network no generator feeds powers no arm)
    bare = [a for a in arms if not any(abs(a.x - x) <= 2.5 and abs(a.y - y) <= 2.5 for x, y in standing)]
    poles = _poles_for(bare, [], ground, [])
    extra = _connect(poles, world, ground) if poles else []
    stages = [{"id": "arms", "depends_on": [], "members": [id(e) for e in arms + poles + extra],
               "witnesses": [{"kind": "entity_built", "member": id(arms[0])}]}]
    out = perimeter._plan(site, PURPOSE, f"arms-{ring[0]}-{ring[1]}-{len(_armed(site, ring))}", arms + poles + extra,
                          stages, source="ammo-loop")
    out["keep"] = _keep(site, ring, fed, _ammo(site), "arms", None)
    out["ammo_line"] = {"kind": "arms", "turrets": len(fed), "belts": 0, "poles": len(poles + extra),
                        "source": "arms", "ammo": _ammo(site)}
    return out


def plan_link(site: Mapping[str, Any], world: Mapping[str, Any], ring) -> dict:
    """A belt from the magazine row's end chest into the loop's chest (filled by hand till now): an inserter out of the
    row's chest, the belt, an inserter into the loop's chest."""
    from companion import layout_planner, perimeter
    from companion.smelting import route
    head = unlinked(site, ring)
    ox, oy = perimeter._local(site)
    hx, hy = math.floor(float(head["head"][0][0]) + ox), math.floor(float(head["head"][0][1]) + oy)
    ground, _ = _ground(world)
    bounds = tuple(int(v) for v in (world.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6)))
    for cx, cy in _row_chests(site, world, str(head["ammo"])):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            arm, start = (cx + dx, cy + dy), (cx + 2 * dx, cy + 2 * dy)
            if not ground.free({arm, start}):
                continue
            for ex, ey in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                into, end = (hx + ex, hy + ey), (hx + 2 * ex, hy + 2 * ey)
                if not ground.free({into, end}):
                    continue
                way = route(start, end, set(ground.taken) - {start, end}, bounds=bounds, limit=600)
                if not way:
                    continue
                arms = [Placed(INSERTER, arm[0] + 0.5, arm[1] + 0.5, FACING[(-dx, -dy)]),
                        Placed(INSERTER, into[0] + 0.5, into[1] + 0.5, FACING[(ex, ey)])]
                ground.take({arm, into} | {(math.floor(e.x), math.floor(e.y)) for e in way})
                poles = _poles_for(arms, [], ground, [])
                extra = _connect(poles, world, ground)
                members = arms + way + poles + extra
                stages = [{"id": "link", "depends_on": [], "members": [id(e) for e in members],
                           "witnesses": [{"kind": "entity_built", "member": id(arms[1])}]}]
                out = perimeter._plan(site, PURPOSE, f"link-{hx}-{hy}", members, stages, source="ammo-loop")
                out["keep"] = {**_keep(site, ring, [], head["ammo"], "row", None), "head": head["head"]}
                out["ammo_line"] = {"kind": "link", "turrets": 0, "belts": len(way), "poles": len(poles + extra),
                                    "source": "row", "ammo": head["ammo"]}
                return out
    raise layout_planner.PlanError("no way for a belt from the magazine row to the ammo loop's chest")


def _keep(site, ring, fed, ammo, source, head) -> dict:
    from companion import perimeter
    ox, oy = perimeter._local(site)
    out = {"loop": [int(v) for v in ring], "turrets": [[round(x), round(y)] for x, y in fed], "ammo": ammo,
           "source": source}
    if head:
        out["head"] = [[head[0].x - ox, head[0].y - oy]]
    return out


def _feed(site, world, belts, ammo, ground) -> tuple[list[Placed], list[dict], str]:
    """What goes onto the loop: a belt from the magazine row's end chest side-loaded onto it (an inserter out of the
    chest), at the loop tile nearest the row; else a chest beside the loop the engineer fills, an inserter from it
    onto the loop, at the loop tile nearest home."""
    from companion import layout_planner, perimeter
    from companion.smelting import route
    plain = [(math.floor(b.x), math.floor(b.y), b.direction) for b in belts if b.name == BELT]
    bounds = tuple(int(v) for v in (world.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6)))

    def inward(d):   # (the side of a loop belt toward the inside of the ring: right of its way, clockwise)
        sx, sy = STEP[d]
        return (-sy, sx)
    for cx, cy in _row_chests(site, world, str(ammo)):
        for x, y, d in sorted(plain, key=lambda t: abs(t[0] - cx) + abs(t[1] - cy))[:12]:
            ix, iy = inward(d)
            side = (x + ix, y + iy)   # (a belt here pointing onto the loop side-loads it)
            if not ground.free({side}):
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                arm, start = (cx + dx, cy + dy), (cx + 2 * dx, cy + 2 * dy)
                if not ground.free({arm, start}):
                    continue
                way = route(start, side, set(ground.taken) - {start, side}, bounds=bounds, limit=600)
                if not way:
                    continue
                tail = way[-1]
                if tail.name == BELT:
                    way[-1] = Placed(BELT, tail.x, tail.y, FACING[(-ix, -iy)])
                ins = Placed(INSERTER, arm[0] + 0.5, arm[1] + 0.5, FACING[(-dx, -dy)])
                ground.take({arm} | {(math.floor(e.x), math.floor(e.y)) for e in way})
                return [ins] + way, [], "row"
    home = site.get("home") if isinstance(site.get("home"), Mapping) else None
    ox, oy = perimeter._local(site)
    hx, hy = (float(home["x"]) + ox, float(home["y"]) + oy) if home else (0.0, 0.0)
    for x, y, d in sorted(plain, key=lambda t: abs(t[0] - hx) + abs(t[1] - hy)):
        ix, iy = inward(d)
        arm, spot = (x + ix, y + iy), (x + 2 * ix, y + 2 * iy)
        if ground.free({arm, spot}):
            ground.take({arm, spot})
            chest = Placed(CHEST, spot[0] + 0.5, spot[1] + 0.5)
            ins = Placed(INSERTER, arm[0] + 0.5, arm[1] + 0.5, FACING[(ix, iy)])
            return [chest, ins], [{"member": id(chest), "item": ammo, "slot": "input"}], "chest"
    raise layout_planner.PlanError("no room beside the ammo loop for its chest")
