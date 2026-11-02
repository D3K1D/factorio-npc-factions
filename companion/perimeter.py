"""The engineer's wall ring (the team world): a closed ring of wall round its factory, with turrets behind it, moved
out as the factory grows, and at the plot's outer edges (the team's wall ring, each engineer holding its own stretch)
in the end.

As the user builds it (2026-10-24): a few starter turrets close to the factory's vital buildings first,
on the side facing the nests, taken down later and moved to the wall line; walls made by machines into a
chest (companion/wall_block.py), and the wall line put up once enough are stocked for a long stretch (by
hand only in an emergency: an attack or a breach); then turrets along the wall, facing the nearest
nests first, ammunition by hand; and where the biters broke through, one more layer behind the others (up
to four) and one more turret there. The wall line runs a couple of tiles inside the ring's edge (on the plot's
outer edge, inside the plot, so the neighbours' lines join it into one ring); turrets stand behind room for
four layers and a gap (biters reach over a wall).

The ring (the user, 2026-10-26): the plot's outer edge is a long way to hold early on, all of it lined with turrets,
ammunition carried out to them and belts run round it later, though right for later. So the first ring goes round the
factory's main part with room to grow; once the factory has outgrown it, the next one goes up farther out (its sides
near the plot's edge on it), and the sides of the old one that moved are taken down once the new one is walled.

Turrets (the user, 2026-10-26: too many too close together; spread out, side by side only where the biters keep
attacking): one at a time along the wall, as far apart as keeps every stretch of a side facing the nests in reach of
two of them (of one on the other sides), and more beside one only where attacks keep hitting its stretch.

Everything is planned on the map dump in world tiles and handed to the game in the site's local tiles.
The turret, wall, gate and ammo are the best ones the engineer can make now (the game's armory, sent
with the site as `defense`), else gun turrets, stone walls and yellow magazines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from companion import doorways, footprint, layout_planner, ratios, team_area
from companion.base_plan import blocked_tiles
from companion.defense_planner import threats
from companion.belt_flow import SPANS
from companion.modules import EAST, NORTH, SOUTH, WEST, Placed
from companion.smelting import UNDERGROUND_SPAN
from companion.power import connect

TURRETS, WALL, BREACH, STARTER = "plot-turrets", "plot-wall", "breach-repair", "starter-turrets"
LINES = (WALL, TURRETS, BREACH)   # the plans that stand on the ring's sides (and keep which ring and side)
WALL_OFFSET = 2         # the first wall layer, this many tiles inside the ring's (or the plot's) outer edge
MAX_LAYERS = 4          # a breached stretch gets one more layer each time, up to this many
TURRET_OFFSET = 7       # turrets from here inward: behind four layers and a tile of gap
CROSS_OUT = WALL_OFFSET - 1            # a belt or pipe crossing the wall line goes under it from the row outside it
CROSS_IN = WALL_OFFSET + MAX_LAYERS    # to the gap row behind four layers (an underground belt spans 5)
TURRETS_PER_PLAN = 6    # one plan puts up this many turrets (more plans fill the rest)
FIRST_TURRETS = 3       # the first plan, with the nests far off and no attack yet: a start, not the whole ring
NEAR = 150              # nests this near the ring are not far off
HOT_HITS = 2            # attacks or breaches on one turret's stretch (the game keeps 30 minutes of them) make it hot
MAX_STACK = 4           # turrets side by side on the hottest stretches
HIT_REACH = 16          # an attack this near a side's wall line hit that side
SHIELD = 5              # an emergency turret's own wall reaches this far past it
WALL_STAGE = 60         # wall pieces per stage
MAX_ENTITIES = 900      # the game takes plans up to this size
BREACH_SPAN = 8         # a breach-repair covers this far either side of the breach
BREACH_REACH = 3        # its turrets go up to this many spans along the stretch each way, with no room right behind it
USELESS_MIN_RANGE = 8   # turrets that cannot shoot closer than this are no use at the wall
EDGES = ("north", "east", "south", "west")
STARTER_TURRETS = 3     # the starter turrets: a few, close to the factory's vital buildings
STARTER_GAP = 3         # this many tiles outside the buildings they guard
WALL_STOCK = 200        # walls stocked before the wall line goes up ("like 200", the user, 2026-10-24)
WALLED = 0.95           # the wall line counts as done with this share of its tiles walled
# Doors and thickness (the user, 2026-10-25): the wall needs doors for the engineer to leave by, wide enough for cars
# and tanks, as thick as the wall; and a wall made thicker, up to four (turrets shoot over that much), where it is hit
# or walls are spare.
DOOR = 4                # gates side by side: a tank (1.8 x 2.6 tiles) passes at an angle
DOOR_CLEAR = 2          # tiles either side of a door kept free of turrets inside: its way in
WALL_RESERVE = 200      # walls kept in stock for repairs; a layer more is built only from what is over
# The ring (the user, 2026-10-26).
RING_MARGIN = 24        # the first ring this far out from the factory: wall, a gap, turrets and poles, room to grow
RING_STEP = 16          # each ring after it this much farther out again (a bigger factory grows faster)
RING_CLEAR = 12         # the factory this near the ring's edge has outgrown it: the next ring is due
RING_SNAP = 48          # a side this near the plot's edge goes to it (on an outer edge: the team's ring)
CORE_GAP = 2 * RING_MARGIN   # machines this near each other are one part of the factory (rings round each would meet)


class Done(layout_planner.PlanError):
    """Nothing left to build for this goal now (every stretch has its turrets, the ring is walled, the
    breach has its four layers): no failure, the goal can wait."""


@dataclass(frozen=True)
class Edge:
    """One side of the wall ring (or one outer edge of the plot), in world tiles. `across` counts inward from the
    edge of `plot`, the box it is a side of."""
    name: str
    plot: tuple[int, int, int, int]
    lo: int     # first and last tile along the edge the wall line covers
    hi: int

    @property
    def horizontal(self) -> bool:
        return self.name in ("north", "south")

    def row(self, offset: int) -> int:
        """The tile coordinate across the edge `offset` tiles inside the box."""
        x0, y0, x1, y1 = self.plot
        return {"north": y0 + offset, "south": y1 - 1 - offset,
                "west": x0 + offset, "east": x1 - 1 - offset}[self.name]

    def tile(self, along: int, offset: int) -> tuple[int, int]:
        return (along, self.row(offset)) if self.horizontal else (self.row(offset), along)

    def along_of(self, x: float, y: float) -> float:
        return x if self.horizontal else y

    def outward(self, x: float, y: float) -> float:
        """How far a point lies out past this edge's wall line (negative: inside)."""
        row = self.row(WALL_OFFSET) + 0.5
        return {"north": row - y, "south": y - row, "west": row - x, "east": x - row}[self.name]

    def distance(self, x: float, y: float) -> float:
        """How far a point is from this edge's wall line."""
        across = (y if self.horizontal else x) - (self.row(WALL_OFFSET) + 0.5)
        along = self.along_of(x, y)
        off = max(self.lo - along, 0.0, along - (self.hi + 1))
        return math.hypot(across, off)

    @property
    def line(self) -> tuple[str, int]:
        """Which wall line this is: its side and the row its first layer stands on."""
        return self.name, self.row(WALL_OFFSET)


def _origin(site: Mapping[str, Any]) -> tuple[int, int]:
    o = site.get("origin") or {"x": 0, "y": 0}
    return int(math.floor(float(o["x"]))), int(math.floor(float(o["y"])))


def plot_box(site: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """Its plot in world tiles, or None outside the team world."""
    team = team_area.team_of(site)
    if team is None or team.plot is None:
        return None
    ox, oy = _origin(site)
    p = tuple(int(round(v)) for v in team.plot)
    return p[0] + ox, p[1] + oy, p[2] + ox, p[3] + oy


def plot_edges(site: Mapping[str, Any]) -> list[Edge]:
    """The plot's outer edges in world tiles (the site's `team.outer`, else where the plot meets the
    team area's border); a line ends where the next outer edge's line runs, so corners join."""
    team = team_area.team_of(site)
    plot = plot_box(site)
    if team is None or plot is None:
        return []
    ox, oy = _origin(site)
    p = tuple(int(round(v)) for v in team.plot)
    outer = (site.get("team") or {}).get("outer")
    if not isinstance(outer, list) or not outer:
        a = team.area
        outer = [n for n, hit in (("north", p[1] <= a[1]), ("east", p[2] >= a[2]), ("south", p[3] >= a[3]),
                                  ("west", p[0] <= a[0])) if hit]
    outer = [n for n in EDGES if n in outer]
    out = _sides(plot, outer)
    # The team yard's outer edges it holds too (its own line runs on into them): each band is an edge of
    # the yard, stopping short where it meets the yard's other outer edge, someone else's line.
    for band in (site.get("team") or {}).get("bands") or []:
        if not (isinstance(band, Mapping) and band.get("name") in EDGES and team_area._rect(band.get("box"))):
            continue
        b = tuple(int(round(v)) for v in band["box"])
        box = (b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy)
        horizontal = band["name"] in ("north", "south")
        lo, hi = (box[0], box[2] - 1) if horizontal else (box[1], box[3] - 1)
        if band.get("corner_lo"):
            lo += WALL_OFFSET
        if band.get("corner_hi"):
            hi -= WALL_OFFSET
        out.append(Edge(band["name"], box, lo, hi))
    return out


def _sides(box: tuple[int, int, int, int], names: list[str]) -> list[Edge]:
    """The named sides of a box as wall lines; a line ends where the next side's line runs, so corners join."""
    out = []
    for name in [n for n in EDGES if n in names]:
        horizontal = name in ("north", "south")
        lo, hi = (box[0], box[2] - 1) if horizontal else (box[1], box[3] - 1)
        before, after = ("west", "east") if horizontal else ("north", "south")
        if before in names:
            lo += WALL_OFFSET
        if after in names:
            hi -= WALL_OFFSET
        out.append(Edge(name, box, lo, hi))
    return out


# --- the ring ------------------------------------------------------------------------------------------------------

def core(site: Mapping[str, Any], vital: bool = False,
         within: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int] | None:
    """The factory's main part, world tiles: its plans' machines where they stand (not the belts out to their fields,
    nor one box round a block whose drills stand at a far patch: the game's parts, companion/footprint.py) grouped by
    nearness (CORE_GAP), the group with the most at stake (labs and assemblers most). Its other groups are outposts,
    held by turrets where they are pressed (companion/defense_brain.py). With `vital`, drills alone are left out.
    With `within` (a ring it holds): the groups standing in that ring, together, what the ring guards; a group grown
    up far off, however much is at stake there, never drags the ring out to it (rings never shrink). None with
    nothing standing in it (its plans gone from the export, say): the ring holds as it is (review, D-1-ring)."""
    items = []
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or b.get("purpose") in LINES + (STARTER,):
            continue
        for box, machines in footprint.parts(b):
            if vital and all("mining-drill" in name for name in machines):
                continue
            items.append((box, footprint.stake(machines)))
    ox, oy = _origin(site)
    groups = [((math.floor(b[0]) + ox, math.floor(b[1]) + oy, math.ceil(b[2]) + ox, math.ceil(b[3]) + oy), sum(s))
              for b, s in footprint.cluster(items, CORE_GAP)]
    inside = [b for b, _ in groups if within and b[0] < within[2] and within[0] < b[2] and b[1] < within[3]
              and within[1] < b[3]]
    if within:
        return (min(b[0] for b in inside), min(b[1] for b in inside), max(b[2] for b in inside),
                max(b[3] for b in inside)) if inside else None
    return max(groups, key=lambda g: g[1])[0] if groups else None


def _ring_of(block: Mapping[str, Any]) -> tuple[tuple[int, int, int, int], int, str] | None:
    keep = block.get("keep") if isinstance(block, Mapping) else None
    ring = keep.get("ring") if isinstance(keep, Mapping) else None
    if not (isinstance(ring, list) and len(ring) == 4 and keep.get("ring_gen")):
        return None
    return tuple(int(v) for v in ring), int(keep["ring_gen"]), str(keep.get("ring_side") or "")


def held_ring(site: Mapping[str, Any]) -> tuple[tuple[int, int, int, int], int] | None:
    """The ring it holds now (world tiles) and which ring it is: the newest its wall, turret and breach plans keep.
    Plans from before rings (none kept) held the plot's outer edges: ring 0, the plot."""
    best, legacy = None, False
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or b.get("purpose") not in LINES:
            continue
        r = _ring_of(b)
        if r is None:
            legacy = True
        elif best is None or r[1] > best[1]:
            best = (r[0], r[1])
    if best is None and legacy and plot_box(site):
        return plot_box(site), 0
    return best


def ring_around(site: Mapping[str, Any], factory: tuple[int, int, int, int],
                old: tuple[int, int, int, int] | None = None, gen: int = 1) -> tuple[int, int, int, int]:
    """A ring round `factory` (and round `old`, a ring never shrinks), RING_MARGIN out and RING_STEP more for each ring
    after the first; kept in the plot, a side near the plot's edge on it."""
    m = RING_MARGIN + RING_STEP * (gen - 1)
    box = [factory[0] - m, factory[1] - m, factory[2] + m, factory[3] + m]
    if old:
        box = [min(box[0], old[0]), min(box[1], old[1]), max(box[2], old[2]), max(box[3], old[3])]
    plot = plot_box(site)
    if plot:
        box = [max(box[0], plot[0]), max(box[1], plot[1]), min(box[2], plot[2]), min(box[3], plot[3])]
        if box[0] - plot[0] <= RING_SNAP:
            box[0] = plot[0]
        if box[1] - plot[1] <= RING_SNAP:
            box[1] = plot[1]
        if plot[2] - box[2] <= RING_SNAP:
            box[2] = plot[2]
        if plot[3] - box[3] <= RING_SNAP:
            box[3] = plot[3]
    return tuple(int(v) for v in box)


def outgrown(site: Mapping[str, Any], ring: tuple[int, int, int, int], factory: tuple[int, int, int, int]) -> bool:
    """The factory has come within RING_CLEAR of a side of the ring that is not on the plot's edge already."""
    plot = plot_box(site) or (None, None, None, None)
    return ((ring[0] != plot[0] and factory[0] - RING_CLEAR < ring[0])
            or (ring[1] != plot[1] and factory[1] - RING_CLEAR < ring[1])
            or (ring[2] != plot[2] and factory[2] + RING_CLEAR > ring[2])
            or (ring[3] != plot[3] and factory[3] + RING_CLEAR > ring[3]))


def ring_due(site: Mapping[str, Any]) -> tuple[tuple[int, int, int, int], int] | None:
    """The ring to wall now: the one it holds, the next one out once the factory has outgrown it, or the first one
    (round its factory; with no factory yet, the plot's outer edges). None outside the team world."""
    plot = plot_box(site)
    if plot is None:
        return None
    held = held_ring(site)
    if held is None:
        factory = core(site)
        return (ring_around(site, factory) if factory else plot), 1
    ring, gen = held
    factory = core(site, within=ring)
    if factory and outgrown(site, ring, factory):
        return ring_around(site, factory, old=ring, gen=gen + 1), gen + 1
    return held


def current_ring(site: Mapping[str, Any]) -> tuple[tuple[int, int, int, int], int] | None:
    """The ring its turrets go on: the one it holds, else the first one due."""
    return held_ring(site) or ring_due(site)


def ring_edges(site: Mapping[str, Any], ring: tuple[int, int, int, int] | None) -> list[Edge]:
    """The sides of a ring as wall lines: all four, closed; the plot's outer edges once the ring is the whole plot
    (the team's ring, the neighbours holding theirs)."""
    plot = plot_box(site)
    if ring is None or plot is None:
        return []
    if tuple(ring) == plot:
        return plot_edges(site)
    return _sides(tuple(ring), list(EDGES))


def edges(site: Mapping[str, Any]) -> list[Edge]:
    """The sides of the ring it holds now (or will first): where its turrets go."""
    ring = current_ring(site)
    return ring_edges(site, ring[0] if ring else None)


def _keep(ring: tuple[int, int, int, int], gen: int, side: str, layer: int = 1) -> dict:
    return {"ring": list(ring), "ring_gen": gen, "ring_side": side, "ring_layer": layer}


def _layer_of(block: Mapping[str, Any]) -> int:
    keep = block.get("keep") if isinstance(block, Mapping) else None
    return int(keep.get("ring_layer") or 1) if isinstance(keep, Mapping) else 1


def _line_of(site: Mapping[str, Any], block: Mapping[str, Any]) -> tuple[str, int] | None:
    """Which wall line a wall, turret or breach plan stands on: its side and the row of the line's first layer (plans
    from before rings: the plot's outer edge its box lies on)."""
    r = _ring_of(block)
    if r is not None:
        return (r[2], Edge(r[2], r[0], 0, 0).row(WALL_OFFSET)) if r[2] in EDGES else None
    box = block.get("box")
    if not (isinstance(box, list) and len(box) == 4):
        return None
    ox, oy = _origin(site)
    for e in plot_edges(site):
        row = e.row(WALL_OFFSET)
        lo, hi = (box[1] + oy, box[3] + oy) if e.horizontal else (box[0] + ox, box[2] + ox)
        if lo - MAX_LAYERS <= row <= hi + MAX_LAYERS:
            return e.line
    return None


def superseded(site: Mapping[str, Any]) -> list[str]:
    """The sides of older rings it still holds that are not on the ring due now (moved out): to be taken down once the
    new ring is walled, as the game names them ("ring:1:north")."""
    due = ring_due(site)
    if due is None:
        return []
    lines = {e.line for e in ring_edges(site, due[0])}
    out = []
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or b.get("purpose") not in LINES:
            continue
        r = _ring_of(b)
        if r is None or r[1] >= due[1] or r[2] not in EDGES:
            continue
        if _line_of(site, b) not in lines:
            name = f"ring:{r[1]}:{r[2]}"
            if name not in out:
                out.append(name)
    return out


# --- what it fights with -------------------------------------------------------------------------------------------

MAP_MARGIN = 16      # the map reaches this far past the plot and its bands (walls, water, nests by the edge)
MAP_MAX = 400        # the game's largest map dump (base_map.lua MAX_RADIUS)


def map_window(site: Mapping[str, Any]) -> tuple[dict[str, float], int] | None:
    """Where the map for planning the ring must be centred, and how far it must reach (world tiles): over the plot and
    its bands of the yard, which lie farther from home than a base map reaches."""
    boxes = [e.plot for e in plot_edges(site)] + ([plot_box(site)] if plot_box(site) else [])
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    radius = math.ceil(max(x1 - x0, y1 - y0) / 2) + MAP_MARGIN
    return {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2}, min(MAP_MAX, radius)


def _local(site: Mapping[str, Any]) -> tuple[float, float]:
    o = site.get("origin") or {"x": 0, "y": 0}
    return float(o["x"]), float(o["y"])


def arms(site: Mapping[str, Any]) -> dict:
    """What it fights with: turrets (best first), walls, gates and ammo it can make now."""
    d = site.get("defense") if isinstance(site.get("defense"), Mapping) else None
    unlocked = ratios.unlocked(site)
    if d:
        turrets = [t for t in d.get("turrets") or [] if isinstance(t, Mapping) and t.get("kind") in ("ammo", "electric")
                   and float(t.get("min_range") or 0) <= USELESS_MIN_RANGE]
        walls = [w["name"] for w in d.get("walls") or [] if isinstance(w, Mapping) and w.get("name")]
        gates = [g["name"] for g in d.get("gates") or [] if isinstance(g, Mapping) and g.get("name")]
        ammo = {str(k): list(v) for k, v in (d.get("ammo") or {}).items() if isinstance(v, list)}
    else:
        turrets = ([{"name": "gun-turret", "kind": "ammo", "range": 18, "min_range": 0, "w": 2, "h": 2,
                     "ammo_categories": ["bullet"]}] if "gun-turret" in unlocked else [])
        walls = ["stone-wall"] if "stone-wall" in unlocked else []
        gates = ["gate"] if "gate" in unlocked else []
        ammo = {"bullet": ["firearm-magazine"]}
    # Electric turrets first where it can make them: nothing to refill by hand (the user, 2026-10-25: laser turrets
    # first, less manual work); one whose power line does not reach falls back to the next (plan_turrets).
    turrets = sorted(turrets, key=lambda t: t.get("kind") != "electric")
    return {"turrets": turrets, "walls": walls, "gates": gates, "ammo": ammo}


def _ammo_for(turret: Mapping[str, Any], ammo: Mapping[str, list], site: Mapping[str, Any] | None = None) -> str:
    """What a turret is given: the best ammunition it takes (the list is best first) that its engineer can come by
    (carries, its base makes, or it can make: build_choice.can_make), else the best it takes. The best researched was
    taken whatever it took: late-combat's loop chest was to be filled with uranium rounds with no uranium, and stood
    empty, its arms over a bare belt (D-24)."""
    takes = [a for category in turret.get("ammo_categories") or [] for a in ammo.get(category) or []]
    if site is not None and len(takes) > 1:
        # (what it makes, kept up for good, before what it only carries: late-combat's kit of 400 uranium rounds was
        # spent by hand on its turrets and the loop's chest, set for them, stood empty from then on, D-24)
        carried = site.get("inventory") if isinstance(site.get("inventory"), Mapping) else {}
        try:
            return next((item for item in takes if _comes_by(item, site, carried=False)), None)                 or next((item for item in takes if carried.get(item)), None) or takes[0]
        except Exception:   # (a judgment, never in the way)
            pass
    return takes[0] if takes else "firearm-magazine"


def _comes_by(item: str, site: Mapping[str, Any], depth: int = 4, seen: frozenset = frozenset(),
             carried: bool = True) -> bool:
    """Whether its engineer can come by `item` for a turret: carries it (unless `carried` is False: then only what is
    made, kept up), its base makes it (or it is mined by hand), or crafts it by hand from what it comes by. Not a
    machine's recipe it has no machine making (uranium-238 from known ore: the centrifuge's; D-24)."""
    from companion import build_choice
    from companion import blueprints as bp
    held = site.get("inventory") if carried and isinstance(site.get("inventory"), Mapping) else {}
    if held.get(item) or build_choice._had(item, site):
        return True
    if depth <= 0 or item in seen:
        return False
    for recipe in bp._as_list(site.get("recipes")):
        if not isinstance(recipe, Mapping) or recipe.get("handcraft") is False:
            continue
        if not (recipe.get("name") == item or any(isinstance(p, Mapping) and p.get("name") == item
                                                   for p in bp._as_list(recipe.get("products")))):
            continue
        if all(_comes_by(str(i.get("name")), site, depth - 1, seen | {item}, carried)
               for i in bp._as_list(recipe.get("ingredients")) if isinstance(i, Mapping)):
            return True
    return False


def _own(world: Mapping[str, Any], kinds: tuple[str, ...]) -> list[dict]:
    return [e for e in world.get("entities", []) if e.get("f") == world.get("force") and e.get("t") in kinds]


TURRET_KINDS = ("ammo-turret", "electric-turret", "fluid-turret")


def _plan(site: Mapping[str, Any], purpose: str, key: str, placed: list[Placed], stages: list[dict],
          source: str = "perimeter-planner") -> dict:
    ox, oy = _local(site)
    ids = {id(e): f"{key}-{n}" for n, e in enumerate(placed, start=1)}
    entities = [e.to_plan(ids[id(e)], -ox, -oy) for e in placed]
    for stage in stages:
        stage["entity_ids"] = [ids[i] for i in stage.pop("members")]
        for supply in stage.get("manual_supplies", []):
            supply["entity_id"] = ids[supply.pop("member")]
        for witness in stage.get("witnesses", []):
            if "member" in witness:
                witness["entity_id"] = ids[witness.pop("member")]
    if len(entities) > MAX_ENTITIES:
        raise layout_planner.PlanError(f"{purpose}: {len(entities)} buildings, more than one plan takes")
    return {"plan_id": f"{purpose}:{key}", "source": source, "purpose": purpose, "entities": entities,
            "stages": stages}


def _tiles(e: Placed, w: int, h: int) -> set[tuple[int, int]]:
    x0, y0 = math.floor(e.x - w / 2 + 0.01), math.floor(e.y - h / 2 + 0.01)
    return {(x0 + i, y0 + j) for i in range(w) for j in range(h)}


class _Ground:
    """What is taken (buildings, water, other plots, players' ground) and what this plan adds. Ground the factory plan
    keeps for later sections is not taken here: a wall with a gap where the plan keeps ground holds nothing (run 51,
    2026-10-26: a 64-tile hole in the north wall, where the bus kept room). The lanes through its doors are taken,
    save where a door's own wall may still grow into its lane (its next layer, gates in the door's columns, walls
    beside them: companion/doorways.py)."""

    def __init__(self, world: Mapping[str, Any]):
        # (the bus's planned ground too, reserved_hard: the ring crosses it as it crosses any kept room)
        plain = {k: v for k, v in world.items()
                 if k not in ("reserved", "reserved_hard", "_reserved_tiles", "_lane_tiles")}
        plain["_lane_tiles"] = set()   # (the lanes apart, below)
        self.taken = set(blocked_tiles(plain))
        self.room = {k: v for k, v in doorways.wall_room(world, world.get("force")).items() if k not in self.taken}
        self.taken |= doorways.lanes(world)

    def free(self, tiles: set[tuple[int, int]]) -> bool:
        return not tiles & self.taken

    def wall_free(self, tile: tuple[int, int]) -> bool:
        """Whether a wall or gate may stand on `tile`: free, or room a door's own wall may grow into."""
        return tile not in self.taken or tile in self.room

    def take(self, tiles: set[tuple[int, int]]) -> None:
        self.taken |= tiles
        for t in tiles:
            self.room.pop(t, None)


# --- where turrets go ----------------------------------------------------------------------------------------------

def spacing(turret: Mapping[str, Any]) -> int:
    """Tiles between turrets along the wall: every stretch of the wall's face within reach of two of them (from where
    they stand, TURRET_OFFSET in, to the biters at the wall), on every side. Sides away from the nests had them twice as
    far apart; the user's play spaced them 12-17.5 on every side, and its attacks came from sides with no known nests
    (2026-10-30, lesson 10)."""
    reach = float(turret.get("range") or 18)
    depth = max(int(turret.get("w") or 2), int(turret.get("h") or 2))
    across = TURRET_OFFSET + depth / 2 - (WALL_OFFSET - 1)
    half = math.sqrt(max(reach * reach - across * across, 16.0))
    return max(4, int(half))


def _enemy(world: Mapping[str, Any] | None, site: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Nests and worms (world tiles), and where attacks came from and hit."""
    ox, oy = _local(site)
    out = threats(world or {}, site)
    for attack in ((site.get("threat_board") or {}).get("attacks") or []):
        if isinstance(attack, Mapping) and "x" in attack:
            came = attack.get("from") if isinstance(attack.get("from"), Mapping) else attack
            out.append((float(came["x"]) + ox, float(came["y"]) + oy))
    return out


def _hits(site: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Where attacks and breaches hit lately (world tiles; the game keeps 30 minutes of them)."""
    ox, oy = _local(site)
    out = []
    board = site.get("threat_board") or {}
    for hit in list(board.get("attacks") or []) + list(site.get("breaches") or []):
        if isinstance(hit, Mapping) and "x" in hit and "y" in hit:
            out.append((float(hit["x"]) + ox, float(hit["y"]) + oy))
    # (and its wall line's stretches worn hard, each hit counted: hot there, turrets side by side; D-23, Codex's review)
    for s in board.get("worn") or []:
        if isinstance(s, Mapping) and "x" in s and "y" in s:
            out += [(float(s["x"]) + ox, float(s["y"]) + oy)] * max(1, int(s.get("hits") or 1))
    return out


@dataclass
class Spot:
    """A place for turrets along a side: how far the nearest nest is, the side, where along it, how many it lacks,
    how hot it is (attacks and breaches on its stretch lately), and how far either way its turrets may go."""
    distance: float
    edge: Edge
    along: float
    missing: int
    heat: int
    room: int


def _spots(site: Mapping[str, Any], edge_list: list[Edge], guns: list[tuple[float, float]],
           enemy: list[tuple[float, float]], turret: Mapping[str, Any]) -> list[Spot]:
    """Places along the sides lacking turrets, hot ones first, then nearest the nests: one turret each, spaced by
    `spacing` on every side, and on a hot stretch one more beside it for each hit past the
    first (up to MAX_STACK)."""
    hits = _hits(site)
    # (a turret by a corner counts for the side it stands nearest, not for both)
    side_of = {g: min(range(len(edge_list)), key=lambda i: edge_list[i].distance(*g)) for g in guns} if edge_list else {}
    out = []
    for i, edge in enumerate(edge_list):
        length = edge.hi - edge.lo + 1
        if length <= 0:
            continue
        count = max(1, round(length / spacing(turret)))
        step = length / count
        for k in range(count):
            along = edge.lo + step * (k + 0.5)
            heat = sum(1 for h in hits if edge.distance(*h) <= HIT_REACH and abs(edge.along_of(*h) - along) <= step / 2)
            want = min(MAX_STACK, heat) if heat >= HOT_HITS else 1
            have = sum(1 for g in guns if side_of[g] == i
                       and abs(edge.along_of(*g) - along) <= step / 2 and edge.distance(*g) <= TURRET_OFFSET + 4)
            if have >= want:
                continue
            x, y = edge.tile(int(along), TURRET_OFFSET)
            d = min((math.dist((x, y), t) for t in enemy), default=1e9)
            out.append(Spot(d, edge, along, want - have, heat, max(2, int(step / 2))))
    out.sort(key=lambda s: (s.heat < HOT_HITS, s.distance, EDGES.index(s.edge.name), s.along))
    return out


def _guns_of(site: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Its turrets as the game exported them with the site (world tiles)."""
    ox, oy = _local(site)
    return [(float(g["x"]) + ox, float(g["y"]) + oy) for g in site.get("guards") or []
            if isinstance(g, Mapping) and "x" in g and "y" in g]


def turrets_wanted(site: Mapping[str, Any]) -> int:
    """Turrets its ring still lacks: one at each spot along it, more on its hot stretches (from what the game
    exported: its turrets, the nests, the attacks)."""
    kit = arms(site)
    if not kit["turrets"]:
        return 0
    edge_list = edges(site)
    return sum(s.missing for s in _spots(site, edge_list, _guns_of(site), _enemy(None, site), kit["turrets"][0]))


def _beside(edge: Edge, along: float, turret: Mapping[str, Any], n: int, ground: _Ground,
            room: int) -> list[tuple[Placed, set]]:
    """Up to `n` turrets behind the wall line nearest `along`, on free ground within `room` either way, a tile
    apart (beside those standing there: a stack on a hot stretch)."""
    w, h = int(turret.get("w") or 2), int(turret.get("h") or 2)   # (turrets do not turn: w is east-west)
    width_along = w if edge.horizontal else h
    depth = h if edge.horizontal else w
    rows = sorted((edge.row(TURRET_OFFSET), edge.row(TURRET_OFFSET + depth - 1)))
    across = (rows[0] + rows[1] + 1) / 2
    out, mine = [], set()
    for shift in sorted(range(-room, room + 1), key=lambda s: (abs(s), s)):
        if len(out) >= n:
            break
        start = math.floor(along - width_along / 2) + shift
        if start < edge.lo or start + width_along - 1 > edge.hi:
            continue
        a = start + width_along / 2
        x, y = (a, across) if edge.horizontal else (across, a)
        e = Placed(turret["name"], x, y)
        tiles = _tiles(e, w, h)
        step = (1, 0) if edge.horizontal else (0, 1)
        pad = tiles | {(tx + s * step[0], ty + s * step[1]) for tx, ty in tiles for s in (-1, 1)}
        if ground.free(tiles) and not pad & mine:
            out.append((e, tiles))
            mine |= tiles
    return out


def _layer(edge: Edge, lo: int, hi: int, offset: int, wall: str | None, ground: _Ground, doors: set[int] = frozenset(),
           gate: str | None = None) -> list[Placed]:
    """One layer of the wall between `lo` and `hi` along the edge, on its free tiles (belts, pipes, water and others'
    ground crossing it keep their gap): gates in the door's columns, so the door runs through every layer (left
    open, the way in and out, while it has no gate to put there), walls elsewhere."""
    out = []
    for a in range(max(lo, edge.lo), min(hi, edge.hi) + 1):
        t = edge.tile(a, offset)
        if not ground.wall_free(t):
            continue
        across_ew, in_door = ground.room.get(t, (None, False))
        if in_door and across_ew != edge.horizontal:
            continue   # (a wall line across a door's way, not its own: never)
        if a in doors or in_door:   # (a line in front of a door of its own goes through it: gates there too)
            if not gate:
                continue
            # (a gate across a line running east-west faces east, one across a north-south line north)
            out.append(Placed(gate, t[0] + 0.5, t[1] + 0.5, EAST if edge.horizontal else NORTH))
        elif wall:
            out.append(Placed(wall, t[0] + 0.5, t[1] + 0.5))
        else:
            continue
        ground.take({t})
    return out


def _wall_line(edge: Edge, lo: int, hi: int, offset: int, wall: str, ground: _Ground,
               doors: set[int] = frozenset()) -> list[Placed]:
    """Wall pieces on the free tiles of one layer, the door's columns left open."""
    return _layer(edge, lo, hi, offset, wall, ground, doors)


_INWARD = {"north": SOUTH, "south": NORTH, "west": EAST, "east": WEST}
_BACK = {NORTH: SOUTH, SOUTH: NORTH, EAST: WEST, WEST: EAST}


def crossings(edge: Edge, world: Mapping[str, Any]) -> list[dict]:
    """Its own belts and pipes running straight across the wall line, from the row outside it to the gap row behind
    four layers: where the wall would keep a hole the biters walk through (D-8). Each {along, kind ("belt" or
    "pipe"), name, direction (a belt's)}."""
    own = {(math.floor(e["x"]), math.floor(e["y"])): e
           for e in _own(world, ("transport-belt", "underground-belt", "pipe", "pipe-to-ground"))}
    inward = _INWARD[edge.name]
    along_step = {EAST: 1, WEST: -1} if edge.horizontal else {SOUTH: 1, NORTH: -1}
    out = []
    for a in range(edge.lo, edge.hi + 1):
        first = own.get(edge.tile(a, WALL_OFFSET))
        if first is None or first.get("t") not in ("transport-belt", "pipe"):
            continue
        line = [own.get(edge.tile(a, o)) for o in range(CROSS_OUT, CROSS_IN + 1)]
        if any(e is None or e.get("n") != first.get("n") for e in line):
            continue   # (not straight across: a turn, a splitter, an underground already)
        beside = [(s, own.get(edge.tile(a + s, o))) for s in (-1, 1) for o in range(CROSS_OUT, CROSS_IN + 1)]
        if first.get("t") == "transport-belt":
            ways = {int(e.get("d") or 0) for e in line}
            # (a belt beside it loading onto it would be cut off: it is left as it is)
            feeding = any(e and e.get("t") in ("transport-belt", "underground-belt")
                          and along_step.get(int(e.get("d") or 0)) == -s for s, e in beside)
            if len(ways) == 1 and next(iter(ways)) in (inward, _BACK[inward]) and not feeding:
                out.append({"along": a, "kind": "belt", "name": str(first["n"]), "direction": next(iter(ways))})
        elif not any(e and e.get("t") in ("pipe", "pipe-to-ground") for _, e in beside):
            out.append({"along": a, "kind": "pipe", "name": str(first["n"])})   # (one beside it joins it: left)
    return out


def _under(crossing: Mapping[str, Any]) -> str:
    """The underground a crossing goes under the wall by: its belt's tier, or a pipe-to-ground."""
    if crossing["kind"] == "belt":
        return str(crossing["name"]).replace("transport-belt", "underground-belt")
    return "pipe-to-ground"


def _plug(edge: Edge, crossing: Mapping[str, Any], wall: str, unlocked: set) -> list[Placed] | None:
    """A crossing taken under the wall (the user, 2026-10-26: no holes where belts cross): an underground belt or pipe
    pair from the row outside the wall to the gap row behind it, the belt's way, and walls on the rows between, four
    deep (the old pieces there are picked up as it builds); None when it cannot make the underground yet."""
    a, inward = crossing["along"], _INWARD[edge.name]
    out_t, in_t = edge.tile(a, CROSS_OUT), edge.tile(a, CROSS_IN)
    if crossing["kind"] == "belt":
        name, d = _under(crossing), crossing["direction"]
        if name not in unlocked or SPANS.get(name, UNDERGROUND_SPAN) < CROSS_IN - CROSS_OUT:
            return None
        start, end = (out_t, in_t) if d == inward else (in_t, out_t)
        ends = [Placed(name, start[0] + 0.5, start[1] + 0.5, d, kind="input"),
                Placed(name, end[0] + 0.5, end[1] + 0.5, d, kind="output")]
    else:
        if "pipe-to-ground" not in unlocked:
            return None
        # (a pipe-to-ground faces its open side: away from the other end)
        ends = [Placed("pipe-to-ground", out_t[0] + 0.5, out_t[1] + 0.5, _BACK[inward]),
                Placed("pipe-to-ground", in_t[0] + 0.5, in_t[1] + 0.5, inward)]
    return ends + [Placed(wall, edge.tile(a, o)[0] + 0.5, edge.tile(a, o)[1] + 0.5)
                   for o in range(WALL_OFFSET, CROSS_IN)]


def _layers(edge: Edge) -> range:
    return range(WALL_OFFSET, WALL_OFFSET + MAX_LAYERS)


def _solid(world: Mapping[str, Any]) -> set[tuple[int, int]]:
    """What stands in the way on the map itself (buildings, water, cliffs, ground the game refused), whoever's ground
    it is (worked out once per map)."""
    cached = world.get("_solid_tiles") if isinstance(world, dict) else None
    if cached is None:
        cached = set(blocked_tiles({"entities": world.get("entities") or [], "water": world.get("water") or [],
                                    "avoid": world.get("avoid") or [], "_lane_tiles": set()}))
        if isinstance(world, dict):
            world["_solid_tiles"] = cached
    return cached


def route_crossings(site: Mapping[str, Any] | None, world: Mapping[str, Any], edge: Edge) -> list[float]:
    """Where the engineer's ways out cross this side's wall line (along it, world tiles): the straight line from the
    ring's middle to each of its own blocks beyond the side (outposts, mines). The user's play, 2026-10-30, lesson 12:
    gates where its routes crossed the wall. Not toward the nests or where attacks came from: a door there is where
    they hit, and its clear way kept the hot stretch's turrets off it."""
    if not site:
        return []
    # (from its home, where its ways out start (the site's, local): not the edge's own box, a yard band's middle far off
    # its plot's, nor the map's centre, the window it was asked for; Codex)
    ox, oy = _local(site)
    home = site.get("home") if isinstance(site.get("home"), Mapping) else None
    plot = plot_box(site) or edge.plot
    cx, cy = ((float(home["x"]) + ox, float(home["y"]) + oy) if home and "x" in home and "y" in home
              else ((plot[0] + plot[2]) / 2, (plot[1] + plot[3]) / 2))
    aims = []
    for b in site.get("base") or []:
        box = b.get("box") if isinstance(b, Mapping) else None
        if isinstance(box, (list, tuple)) and len(box) == 4:
            aims.append(((float(box[0]) + float(box[2])) / 2 + ox, (float(box[1]) + float(box[3])) / 2 + oy))
    line = edge.row(WALL_OFFSET) + 0.5
    out = []
    for tx, ty in aims:
        if edge.outward(tx, ty) <= 0:
            continue
        a0, a1, b0, b1 = (cx, tx, cy, ty) if edge.horizontal else (cy, ty, cx, tx)   # (along, then across)
        if b1 == b0:
            continue
        t = (line - b0) / (b1 - b0)
        along = a0 + t * (a1 - a0)
        if 0 < t <= 1 and edge.lo <= along <= edge.hi:
            out.append(along)
    return out


def doors(edge: Edge, world: Mapping[str, Any], ground: _Ground | None = None,
          site: Mapping[str, Any] | None = None) -> set[int]:
    """The columns (along the edge) of its door: where its gates stand on the wall's layers, else DOOR free tiles
    nearest where the engineer's ways out cross the side (route_crossings: the middle of them), else nearest the
    middle of the edge, free through every layer the wall may have, and with the way through it clear inside and out
    where there is such a place (a car lines up on it and drives straight through: companion/doorways.py). Chosen
    once: its gates mark it after. Empty when no such room is left."""
    rows = {edge.row(o) for o in _layers(edge)}
    standing = set()
    for e in _own(world, ("gate",)):
        x, y = math.floor(e["x"]), math.floor(e["y"])
        across, along = (y, x) if edge.horizontal else (x, y)
        if across in rows and edge.lo <= along <= edge.hi:
            standing.add(along)
    if standing:
        return standing
    taken = ground.taken if ground is not None else set(blocked_tiles(world))
    solid = _solid(world)
    crossings = sorted(route_crossings(site, world, edge))
    middle = math.floor(crossings[len(crossings) // 2]) if crossings else (edge.lo + edge.hi) // 2
    first = None
    for k in [0] + [s for i in range(1, edge.hi - edge.lo + 1) for s in (i, -i)]:
        cols = range(middle + k - DOOR // 2, middle + k - DOOR // 2 + DOOR)
        if cols[0] < edge.lo or cols[-1] > edge.hi:
            continue
        if any(edge.tile(a, o) in taken for a in cols for o in _layers(edge)):
            continue
        ends = [edge.tile(a, o) for a in (cols[0], cols[-1]) for o in (_layers(edge)[0], _layers(edge)[-1])]
        box = (min(x for x, _ in ends), min(y for _, y in ends), max(x for x, _ in ends), max(y for _, y in ends))
        if not doorways.lane(box, edge.horizontal) & solid:
            return set(cols)
        first = first or set(cols)
    return first or set()


def _keep_doorways(edge: Edge, cols: set[int], ground: _Ground) -> None:
    """The way through a door kept free inside, out past the turret rows: no turret or pole stands in it."""
    if not cols:
        return
    for a in range(min(cols) - DOOR_CLEAR, max(cols) + DOOR_CLEAR + 1):
        for o in range(WALL_OFFSET + MAX_LAYERS, TURRET_OFFSET + 8):
            ground.take({edge.tile(a, o)})


def standing_layers(edge: Edge, world: Mapping[str, Any]) -> int:
    """How many of the edge's wall layers stand (from the outside in), each walled along WALLED of its length."""
    walls = {(math.floor(e["x"]), math.floor(e["y"])) for e in _own(world, ("wall", "gate"))}
    n = 0
    for o in _layers(edge):
        length = edge.hi - edge.lo + 1
        if sum(1 for a in range(edge.lo, edge.hi + 1) if edge.tile(a, o) in walls) < WALLED * length - DOOR:
            break
        n += 1
    return n


def _with_power(edge: Edge, group: list[tuple[Placed, set]], world: Mapping[str, Any],
                ground: _Ground) -> list[Placed] | None:
    """A pole right behind each electric turret (a small pole powers what is within two and a half
    tiles), joined to the engineer's powered network; None when out of its reach."""
    live = set(world.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in _own(world, ("electric-pole",))
               if not live or e.get("net") is None or e.get("net") in live]
    if not powered:
        return None
    poles, used = [], set()
    for turret, tiles in group:
        depth = max(edge.row(TURRET_OFFSET) - (min(t[1] if edge.horizontal else t[0] for t in tiles)),
                    (max(t[1] if edge.horizontal else t[0] for t in tiles)) - edge.row(TURRET_OFFSET)) + 1
        along = math.floor(edge.along_of(turret.x, turret.y))
        t = edge.tile(along, TURRET_OFFSET + depth)
        if t in ground.taken or t in used:
            return None
        used.add(t)
        poles.append(Placed("small-electric-pole", t[0] + 0.5, t[1] + 0.5))
    extra, problems = connect(poles, powered, ground.taken | used)
    if problems:
        return None
    return poles + extra


def _place(edge: Edge, spot: Spot, n: int, kit: Mapping[str, Any], world: Mapping[str, Any], ground: _Ground,
           electric: bool = True) -> tuple[list[Placed], list[Placed], Mapping[str, Any]] | None:
    """`n` turrets at a spot (the best turret that fits: an electric one only where its power line reaches), their
    poles, and the turret; None when none fits."""
    for candidate in kit["turrets"]:
        if candidate.get("kind") == "electric" and not electric:
            continue
        # (within the spot's stretch, where it counts for it: placed past it, a hot spot never filled up and held up
        # every other side, the review, 2026-10-26)
        group = _beside(edge, spot.along, candidate, n, ground, room=max(0, spot.room - 1))
        if not group:
            continue
        extra = []
        if candidate.get("kind") == "electric":
            extra = _with_power(edge, group, world, ground)
            if extra is None:
                continue
        for _, tiles in group:
            ground.take(tiles)
        for pole in extra:
            ground.take({(math.floor(pole.x), math.floor(pole.y))})
        return [e for e, _ in group], extra, candidate
    return None


def plan_turrets(site: Mapping[str, Any], world: Mapping[str, Any], shield: bool = True,
                 max_turrets: int | None = None, wall_limit: int | None = None) -> dict:
    """Turrets along the side of its ring most in need (a hot stretch first, else the one nearest the nests), one at
    each spot short of one, side by side only on hot stretches; kept loaded (by hand, from the engineer's ammo). With
    `shield` (an emergency), each with its own short wall in front, made by hand then and there. Otherwise the wall
    line, from walls made by machine, is their wall (a group's 32 walls made by hand, a stone brick at a time, held the
    first group for 25 minutes in the one-engineer hour, 2026-10-24). An emergency's hand work is bounded
    (companion/threat.py): `max_turrets` in all, and the shields only while `wall_limit` walls last."""
    ring = current_ring(site)
    edge_list = ring_edges(site, ring[0] if ring else None)
    if not edge_list:
        raise layout_planner.PlanError("no ring to hold (not in the team world)")
    kit = arms(site)
    if not kit["turrets"]:
        raise layout_planner.PlanError("no turret it can make yet")
    guns = [(e["x"], e["y"]) for e in _own(world, TURRET_KINDS)]
    spots = _spots(site, edge_list, guns, _enemy(world, site), kit["turrets"][0])
    if not spots:
        raise Done("every stretch of the wall ring has its turrets")
    held = any(isinstance(b, Mapping) and b.get("purpose") == TURRETS for b in site.get("base") or [])
    social = site.get("social") if isinstance(site.get("social"), Mapping) else {}
    calm = (not held and spots[0].heat < HOT_HITS and spots[0].distance > NEAR
            and not (site.get("threat_board") or {}).get("attacks") and not social.get("recent_attacks"))
    budget = FIRST_TURRETS if calm else TURRETS_PER_PLAN
    if max_turrets is not None:
        budget = min(budget, max(1, max_turrets))
    ground = _Ground(world)
    door_of = {}
    for edge in edge_list:
        door_of[edge] = doors(edge, world, ground, site)
        _keep_doorways(edge, door_of[edge], ground)
    placed: list[Placed] = []
    stages: list[dict] = []
    wall = kit["walls"][0] if kit["walls"] and shield and (wall_limit is None or wall_limit > 0) else None
    walls_left = wall_limit
    count, first, key = 0, None, None
    # the side most in need (its first spot with room); the other sides in later plans (a plan's turrets close together
    # for the walk)
    for n, spot in enumerate(spots, start=1):
        if count >= budget:
            break
        if first is not None and spot.edge != first:
            continue
        got = _place(spot.edge, spot, min(spot.missing, budget - count), kit, world, ground)
        if not got:
            continue
        first, key = spot.edge, key or int(spot.along)
        new, extra, turret = got
        along_tiles = [math.floor(first.along_of(e.x, e.y)) for e in new]
        own_wall = (_wall_line(first, min(along_tiles) - SHIELD - 1, max(along_tiles) + SHIELD + 1, WALL_OFFSET, wall,
                               ground, door_of.get(first, set())) if wall else [])
        if walls_left is not None:   # (a shield whole, or none: a few walls with gaps hold nothing)
            if len(own_wall) > walls_left:
                own_wall = []
            walls_left -= len(own_wall)
        count += len(new)
        members = new + extra + own_wall
        placed += members
        ammo = _ammo_for(turret, kit["ammo"], site) if turret.get("kind") == "ammo" else None
        stages.append({"id": f"{first.name}-turret-{n}", "depends_on": [], "members": [id(e) for e in members],
                       "manual_supplies": ([{"member": id(g), "item": ammo, "slot": "ammo"} for g in new]
                                           if ammo else []),
                       "witnesses": [{"kind": "entity_built", "member": id(new[0])}]
                       + ([{"kind": "ammo_loaded", "member": id(new[0])}] if ammo else [])})
    if not stages:
        raise Done("no room left for turrets where the wall ring lacks them")
    gen = ring[1] if ring else 1
    plan = _plan(site, TURRETS, f"r{gen}-{first.name}-{key}", placed, stages)
    plan["keep"] = _keep(ring[0], gen, first.name)
    return plan


def plan_wall(site: Mapping[str, Any], world: Mapping[str, Any], limit: int | None = None) -> dict:
    """The wall line along the next side of the ring due that is not walled yet (most threatened first), one layer,
    with a gate in the middle; walls and buildings already there are left as they are. With `limit` (the walls in
    stock), at most that many pieces: the run of free tiles nearest the nests, the rest in later plans as more walls
    are made. The ring due is the next one out once the factory has outgrown the one it holds; the plan that walls it
    whole takes down the old one's sides that moved."""
    due = ring_due(site)
    edge_list = ring_edges(site, due[0] if due else None)
    if not edge_list:
        raise layout_planner.PlanError("no ring to hold (not in the team world)")
    ring, gen = due
    kit = arms(site)
    if not kit["walls"]:
        raise layout_planner.PlanError("no wall it can make yet")
    enemy = _enemy(world, site)
    ground = _Ground(world)
    gate_name = kit["gates"][0] if kit["gates"] else None
    door_of = {e: doors(e, world, ground, site) for e in edge_list}

    def gap(edge: Edge) -> int:
        """Tiles of its first layer a wall or gate can still go on (a doorway with no gate yet, a belt across: none)."""
        return sum(1 for a in range(edge.lo, edge.hi + 1)
                   if ground.wall_free(edge.tile(a, WALL_OFFSET)) and (gate_name or a not in door_of[edge]))

    def nearness(e: Edge) -> tuple:
        return (min((e.distance(*t) for t in enemy), default=0.0), EDGES.index(e.name))

    # (with no gate to put in a doorway, its four open tiles kept a short side open for good, and the plans for it
    # found no ground: the other sides were never walled, the review, 2026-10-26)
    unlocked = ratios.unlocked(site)
    plugs_of = {e: [p for p in (_plug(e, c, kit["walls"][0], unlocked) for c in crossings(e, world)) if p]
                for e in edge_list}
    open_ = [e for e in edge_list if gap(e) > 0 or plugs_of[e]]   # (a belt across it is a hole to close too)
    offset, spare = WALL_OFFSET, None
    if not open_:
        # Every side has its first layer: a layer more where it is thinnest (the nests' side first), from the walls
        # over the repair reserve (the user, 2026-10-25: thicker walls when walls are spare, up to four).
        spare = (limit or 0) - WALL_RESERVE
        thin = [(standing_layers(e, world), e) for e in edge_list]
        thin = [(n, e) for n, e in thin if n < MAX_LAYERS]
        if not thin or spare < WALL_STOCK:
            raise Done("the wall ring is walled" + (" four layers deep" if not thin else ""))
        n, edge = min(thin, key=lambda ne: (ne[0], nearness(ne[1])))
        offset = WALL_OFFSET + n
        limit = spare
    else:
        edge = min(open_, key=nearness)
    door = door_of[edge]
    # Its belts and pipes across the line go under it (the walls in stock first to those holes)
    plugs, extra, ends = [], 0, 0
    for plug in plugs_of[edge]:
        walls = sum(1 for e in plug if e.name == kit["walls"][0])
        if limit is None or walls <= limit:
            plugs += plug
            extra += len(plug) - 1   # (one tile of its line, five more pieces: its ends, its walls deeper in)
            ends += len(plug) - walls
            limit = None if limit is None else limit - walls
    if not plugs and plugs_of[edge] and gap(edge) == 0:   # (only belts across it left, and too few walls yet)
        raise Done(f"too few walls yet to take the belts across the {edge.name} side under it")
    # The door first (built before the wall is closed: the engineer is never walled in), the wall after.
    gates = _layer(edge, min(door), max(door), offset, None, ground, door, gate_name) if door and gate_name else []
    pieces = _layer(edge, edge.lo, edge.hi, offset, kit["walls"][0], ground, door, gate_name)   # (its own door's done)
    if not pieces and not gates and not plugs:
        raise layout_planner.PlanError(f"no free ground for a wall on the {edge.name} side")
    pieces.sort(key=lambda e: edge.along_of(e.x, e.y))
    if limit is not None and pieces and len(pieces) > max(0 if plugs else 1, limit):
        # the run facing the nearest nests (none known: from the middle, where the gate stands)
        aim = (edge.lo + edge.hi) / 2
        if enemy:
            aim = min(range(edge.lo, edge.hi + 1),
                      key=lambda a: min(math.dist(edge.tile(a, WALL_OFFSET), t) for t in enemy))
        n = max(0 if plugs else 1, limit)

        def off(i: int) -> float:
            ends = edge.along_of(pieces[i].x, pieces[i].y) + edge.along_of(pieces[i + n - 1].x, pieces[i + n - 1].y)
            return abs(ends / 2 - aim)
        start = min(range(len(pieces) - n + 1), key=off) if n else 0
        pieces = pieces[start:start + n]
    placed = gates + plugs + pieces
    stages = []
    if gates:
        stages.append({"id": f"{edge.name}-door-{offset - WALL_OFFSET + 1}", "depends_on": [],
                       "members": [id(g) for g in gates], "manual_supplies": [],
                       "witnesses": [{"kind": "entity_built", "member": id(gates[0])}]})
    if plugs:
        stages.append({"id": f"{edge.name}-under-{offset - WALL_OFFSET + 1}",
                       "depends_on": [stages[0]["id"]] if gates else [],
                       "members": [id(e) for e in plugs], "manual_supplies": [],
                       "witnesses": [{"kind": "entity_built", "member": id(plugs[0])}]})
    for n in range(0, len(pieces), WALL_STAGE):
        part = pieces[n:n + WALL_STAGE]
        stages.append({"id": f"{edge.name}-wall-{offset - WALL_OFFSET + 1}-{n // WALL_STAGE + 1}",
                       "depends_on": [stages[0]["id"]] if gates else [],
                       "members": [id(e) for e in part], "manual_supplies": [],
                       "witnesses": [{"kind": "entity_built", "member": id(part[0])}]})
    key = f"r{gen}-{edge.name}" if offset == WALL_OFFSET else f"r{gen}-{edge.name}-{offset - WALL_OFFSET + 1}"
    plan = _plan(site, WALL, key, placed, stages)
    plan["keep"] = _keep(ring, gen, edge.name, offset - WALL_OFFSET + 1)
    if extra:
        plan["keep"].update(extra=extra, ends=ends)   # (what of it is no piece of its own layer, and no wall: wall_progress)
    _retire_moved(site, plan, {edge.line: len(placed) - extra} if offset == WALL_OFFSET else None)
    return plan


def _retire_moved(site: Mapping[str, Any], plan: dict, adds: Mapping[tuple[str, int], int] | None = None) -> None:
    """The sides of older rings that moved out are taken down by a plan (once built) that leaves the ring due walled
    (`adds`: the first-layer pieces this plan puts on a line): the new ring stands before the old one goes, and its
    walls and turrets come back for the new one. (Old walls the new ring runs through, an old corner where a side that
    stayed met one that moved, the game leaves standing: DesignedStart.retire.)"""
    old = superseded(site)
    if old and walled(site, adds):
        plan["retire"] = list(plan.get("retire") or []) + old


def plan_breach(site: Mapping[str, Any], world: Mapping[str, Any], wall_limit: int | None = None) -> dict:
    """Where the biters broke through last: one more wall layer behind those standing (up to four) along
    the breached stretch, and turrets there: a stretch they keep hitting gets more beside the one standing (at least
    one more). With `wall_limit` (the walls it has and can make in time), the layer's middle that many walls long,
    facing the breach."""
    breaches = [b for b in site.get("breaches") or [] if isinstance(b, Mapping) and "x" in b and "y" in b]
    if not breaches:
        raise layout_planner.PlanError("no breach to repair")
    ring = current_ring(site)
    edge_list = ring_edges(site, ring[0] if ring else None)
    if not edge_list:
        raise layout_planner.PlanError("no ring to hold (not in the team world)")
    newest = max(breaches, key=lambda b: float(b.get("tick") or 0))
    ox, oy = _local(site)
    bx, by = float(newest["x"]) + ox, float(newest["y"]) + oy
    edge = min(edge_list, key=lambda e: e.distance(bx, by))
    along = math.floor(edge.along_of(bx, by))
    lo, hi = along - BREACH_SPAN, along + BREACH_SPAN
    kit = arms(site)
    walls = {(math.floor(e["x"]), math.floor(e["y"])) for e in _own(world, ("wall", "gate"))}
    ground = _Ground(world)
    placed: list[Placed] = []
    stages: list[dict] = []
    layer = None
    # (the hole refilled within the line's own thickness, never a layer more: more walls are not the answer to
    # repeated attacks, more turrets are; D-23, the user)
    rows = {o: [edge.tile(a, o) for a in range(max(lo, edge.lo), min(hi, edge.hi) + 1)]
            for o in range(WALL_OFFSET, WALL_OFFSET + MAX_LAYERS)}
    thick = max((o for o, row in rows.items() if any(t in walls for t in row)), default=WALL_OFFSET)
    for offset in range(WALL_OFFSET, thick + 1):
        row = rows[offset]
        if sum(1 for t in row if t in walls) * 2 < len(row):
            layer = offset   # the first layer (from the outside in) that is not standing
            break
    # (the hole itself: its column's pieces within the line's thickness put back, gates in a door's columns; a single
    # piece gone leaves its row mostly standing, Codex's review)
    if kit["walls"] and newest.get("hole"):
        door = doors(edge, world, ground, site)
        # (a gate lost was a door's: a gate back, though its column no longer reads as a door; Codex's review)
        lost_gate = str(newest.get("hole_piece") or "") in set(kit["gates"])
        for offset in range(WALL_OFFSET, thick + 1):
            if wall_limit is not None and sum(1 for e in placed if e.name in kit["walls"]) >= wall_limit:
                break   # (an urgent plan's walls within what it has: Codex's review)
            t = edge.tile(along, offset)
            if t in walls or not ground.free({t}):
                continue
            gate = (along in door or lost_gate) and kit["gates"]
            piece = (Placed(kit["gates"][0], t[0] + 0.5, t[1] + 0.5, EAST if edge.horizontal else NORTH) if gate
                     else Placed(kit["walls"][0], t[0] + 0.5, t[1] + 0.5))
            placed.append(piece)
            ground.take({t})
            walls.add(t)
        if placed:
            stages.append({"id": f"{edge.name}-hole", "depends_on": [], "members": [id(e) for e in placed],
                           "manual_supplies": [], "witnesses": [{"kind": "entity_built", "member": id(placed[0])}]})
    if layer is not None and kit["walls"]:
        door = doors(edge, world, ground, site)
        pieces = _layer(edge, lo, hi, layer, kit["walls"][0], ground, door, kit["gates"][0] if kit["gates"] else None)
        if wall_limit is not None and len(pieces) > wall_limit:
            pieces.sort(key=lambda e: abs(edge.along_of(e.x, e.y) - along))
            pieces = pieces[:max(0, wall_limit)]
        if pieces:
            placed += pieces
            stages.append({"id": f"{edge.name}-layer-{layer - WALL_OFFSET + 1}", "depends_on": [],
                           "members": [id(e) for e in pieces], "manual_supplies": [],
                           "witnesses": [{"kind": "entity_built", "member": id(pieces[0])}]})
    guns_kit = {**kit, "turrets": [t for t in kit["turrets"] if t.get("kind") == "ammo"]}   # (no new power line here)
    if guns_kit["turrets"]:
        guns = [(e["x"], e["y"]) for e in _own(world, TURRET_KINDS)]
        # the stretch's spot when it lacks turrets (a hot one: beside the one there), else one more at the breach
        spots = _spots(site, [edge], guns, _enemy(world, site), guns_kit["turrets"][0])
        spot = min(spots, key=lambda s: abs(s.along - along), default=None)
        if spot and abs(spot.along - along) > spot.room + BREACH_SPAN:
            spot = None
        target = spot or Spot(0.0, edge, along, 1, 0, BREACH_SPAN)
        # as many as the stretch lacks against the waves that broke through (and the next coming), one at least
        from companion import defense_brain
        x, y = edge.tile(along, TURRET_OFFSET)
        stretch = (x - ox - BREACH_SPAN, y - oy - BREACH_SPAN, x - ox + BREACH_SPAN, y - oy + BREACH_SPAN)
        want = max(1, target.missing, defense_brain.lacking(site, stretch))
        got = _place(edge, target, want, guns_kit, world, ground, electric=False)
        # (no room right behind it: the line extended along the stretch, a step at a time each way, before giving up;
        # "declined: four layers and no room for more turrets" stopped the defense where it was hit most, D-23)
        for k in range(1, BREACH_REACH + 1):
            if got:
                break
            for sign in (1, -1):
                a = along + sign * k * BREACH_SPAN
                if edge.lo <= a <= edge.hi:
                    got = _place(edge, Spot(0.0, edge, a, 1, 0, BREACH_SPAN), want, guns_kit, world, ground,
                                 electric=False)
                    if got:
                        break
        if got:
            new, _, turret = got
            placed += new
            ammo = _ammo_for(turret, kit["ammo"], site)
            stages.append({"id": f"{edge.name}-breach-turrets", "depends_on": [], "members": [id(g) for g in new],
                           "manual_supplies": [{"member": id(g), "item": ammo, "slot": "ammo"} for g in new],
                           "witnesses": [{"kind": "entity_built", "member": id(new[0])},
                                         {"kind": "ammo_loaded", "member": id(new[0])}]})
    if not stages:
        raise Done("the breach has its four layers and no room for more turrets")
    gen = ring[1] if ring else 1
    plan = _plan(site, BREACH, f"r{gen}-{edge.name}-{along}", placed, stages)
    plan["keep"] = _keep(ring[0], gen, edge.name)
    return plan


def wall_progress(site: Mapping[str, Any], adds: Mapping[tuple[str, int], int] | None = None,
                  layers: bool = False) -> tuple[int, int]:
    """Wall pieces its wall-line plans have put up on the ring due (on its lines: a side that did not move keeps its
    walls), and the tiles the whole ring's line takes (from the base the game exported: a plan's buildings are its
    pieces and its gate). The first layer alone, line by line, each at most its length (four layers on a side that
    stayed stood in for a side not built yet, the review, 2026-10-26); with `layers`, every layer."""
    due = ring_due(site)
    edge_list = ring_edges(site, due[0] if due else None)
    length: dict[tuple[str, int], int] = {}
    for e in edge_list:
        length[e.line] = length.get(e.line, 0) + e.hi - e.lo + 1
    on = dict(adds or {})
    for b in site.get("base") or []:
        if isinstance(b, Mapping) and b.get("purpose") == WALL and (layers or _layer_of(b) == 1):
            line = _line_of(site, b)
            if line in length:
                on[line] = on.get(line, 0) + _layer_pieces(b, layers)
    total = sum(length.values())
    if layers:
        return sum(on.get(line, 0) for line in length), total
    return sum(min(on.get(line, 0), n) for line, n in length.items()), total


def _layer_pieces(block: Mapping[str, Any], layers: bool = False) -> int:
    """A wall plan's pieces standing on its own layer (with `layers`, its walls and gates on any): its buildings, less
    what of it is not (a crossing taken under the wall: its ends and its walls deeper in, the keep's `extra`; with
    `layers`, its ends alone), all of that taken off however much of the plan stands: while it is part built the line
    counts short, never ahead (Codex's review, D-8-crossings)."""
    keep = block.get("keep") if isinstance(block.get("keep"), Mapping) else {}
    off = keep.get("ends" if layers else "extra")
    return max(0, int(block.get("buildings") or 0) - (int(off) if isinstance(off, (int, float)) else 0))


def holes_due(site: Mapping[str, Any]) -> bool:
    """Its belts or pipes still crossing the wall line of the ring it holds, on the stretch of a side of the ring due it
    walls, by an underground it can make now (the game finds them on the map: the threat board's `crossings`): a plan
    is due to close them, however walled the ring counts (walls up before this, or before it could make the
    underground)."""
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    found = board.get("crossings") if isinstance(board.get("crossings"), Mapping) else {}
    if not found:
        return False
    unlocked = ratios.unlocked(site)
    due = ring_due(site)
    for edge in ring_edges(site, due[0] if due else None):
        for c in found.get(edge.name) or []:
            if isinstance(c, Mapping) and isinstance(c.get("along"), (int, float)) \
                    and edge.lo <= c["along"] <= edge.hi and c.get("under") in unlocked:
                return True
    return False


def walled(site: Mapping[str, Any], adds: Mapping[tuple[str, int], int] | None = None) -> bool:
    """The whole ring due stands (WALLED of its tiles; its doorways, while it has no gate to put there, and what crosses
    it apart)."""
    placed, total = wall_progress(site, adds)
    open_doors = 0 if arms(site)["gates"] else DOOR * len(ring_edges(site, (ring_due(site) or (None,))[0]))
    return total > 0 and placed >= WALLED * total - open_doors


def thicken_due(site: Mapping[str, Any]) -> bool:
    """A layer more is due: the first layer stands along the whole ring, fewer than MAX_LAYERS do by the game's tally,
    and the walls in stock are over the repair reserve by a stretch's worth (WALL_STOCK)."""
    if not walled(site):
        return False
    placed, total = wall_progress(site, layers=True)
    if total <= 0 or placed >= WALLED * MAX_LAYERS * total:
        return False
    kit = arms(site)
    return bool(kit["walls"]) and stocked(site, kit["walls"][0]) >= WALL_RESERVE + WALL_STOCK


def wall_stock(site: Mapping[str, Any]) -> int:
    """Walls to have in stock before the next stretch of the wall line goes up: WALL_STOCK, or what is
    left of the line when that is less."""
    placed, total = wall_progress(site)
    return max(1, min(WALL_STOCK, total - placed))


def stocked(site: Mapping[str, Any], item: str) -> int:
    """How many of `item` the engineer has: carried, and in its builds' chests."""
    carried = site.get("inventory")
    held = int(carried.get(item) or 0) if isinstance(carried, Mapping) else 0
    for b in site.get("base") or []:
        stock = b.get("stock") if isinstance(b, Mapping) else None
        if isinstance(stock, Mapping):
            held += int(stock.get(item) or 0)
    return held


def core_box(site: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """Its vital buildings together, in world tiles: the factory's main part (core) of the machines that make
    something (its smelting, power, labs, assemblers), not drills alone nor defenses."""
    return core(site, vital=True)


def plan_starter(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """A few turrets just outside the factory's vital buildings, on the side facing the nearest nests (or,
    with none known, the plot's outer edge), loaded by hand and spaced so each stretch of that side is in reach of
    two: the first defense, taken down and moved to the wall ring once turrets stand there."""
    kit = arms(site)
    guns = [t for t in kit["turrets"] if t.get("kind") == "ammo"]   # (no power line to run out here)
    if not guns:
        raise layout_planner.PlanError("no turret it can make yet that needs no power")
    box = core_box(site)
    if box is None:
        raise layout_planner.PlanError("no factory yet to guard")
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    enemy = _enemy(world, site)
    if enemy:
        tx, ty = min(enemy, key=lambda t: math.dist(t, (cx, cy)))
    else:
        outer = plot_edges(site)
        if not outer:
            raise layout_planner.PlanError("no nests known and no outer edge to face")
        tx, ty = {"north": (cx, cy - 1e4), "south": (cx, cy + 1e4), "west": (cx - 1e4, cy),
                  "east": (cx + 1e4, cy)}[outer[0].name]
    dx, dy = tx - cx, ty - cy
    side = ("east" if dx > 0 else "west") if abs(dx) > abs(dy) else ("south" if dy > 0 else "north")
    turret = guns[0]
    w, h = int(turret.get("w") or 2), int(turret.get("h") or 2)
    ground = _Ground(world)
    horizontal = side in ("north", "south")
    lo, hi = (x0, x1) if horizontal else (y0, y1)
    aim = min(max(tx if horizontal else ty, lo), hi)
    width_along = w if horizontal else h
    gap_along = spacing(turret)
    half = STARTER_TURRETS // 2
    targets = [aim + k * gap_along for k in sorted(range(-half, STARTER_TURRETS - half), key=abs)]
    placed: list[Placed] = []
    for target in targets:
        spot = None
        for gap in range(STARTER_GAP, STARTER_GAP + 6):
            across = {"north": y0 - gap - h / 2, "south": y1 + gap + h / 2,
                      "west": x0 - gap - w / 2, "east": x1 + gap + w / 2}[side]
            for shift in [0] + [k for i in range(1, gap_along // 2 + 1) for k in (i, -i)]:
                a = math.floor(target - width_along / 2) + shift + width_along / 2
                e = Placed(turret["name"], a, across) if horizontal else Placed(turret["name"], across, a)
                tiles = _tiles(e, w, h)
                if ground.free(tiles):
                    spot = (e, tiles)
                    break
            if spot:
                break
        if spot:
            ground.take(spot[1])
            placed.append(spot[0])
    if not placed:
        raise layout_planner.PlanError(f"no room for turrets on the {side} side of the factory")
    ammo = _ammo_for(turret, kit["ammo"], site)
    stages = [{"id": f"starter-{side}", "depends_on": [], "members": [id(e) for e in placed],
               "manual_supplies": [{"member": id(e), "item": ammo, "slot": "ammo"} for e in placed],
               "witnesses": [{"kind": "entity_built", "member": id(placed[0])},
                             {"kind": "ammo_loaded", "member": id(placed[0])}]}]
    return _plan(site, STARTER, side, placed, stages)
