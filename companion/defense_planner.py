"""Defending the base where biters will strike: turrets and a wall on the side that faces their nests.

Biters attack what their nests can reach, and they reach the nearest block first: an oil field, an
outlying mining field, a power plant by a distant lake, or the edge of the main base itself. For
each group of nests within reach of the base (from the map and the engineer's base memory), the
engineer defends the block nearest to it: gun turrets just outside the block on the side facing the
nests (more for bigger groups), a short stone wall in front of them, and magazines kept loaded
(upkeep). A wall segment where attacks will come from, as players build them, rather than a full
ring that would cost thousands of stone. A block already covered by a defense is left alone.

Walls follow research, as the guides build them: turrets alone before stone walls, one row of wall
once walls are researched, a double row once gates are (mid game). A radar by the turrets watches the
nests beyond (the engineer learns of nests its radars see), unless one of its radars already does.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import layout_planner
from companion import ratios
from companion.base_plan import blocked_tiles
from companion.defense_brain import _box_distance
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST
from companion.power import connect
from companion.smelting import footprint

PURPOSE = "outpost-defense"
THREAT_RADIUS = 200       # nests and worms this close threaten a block
CLUSTER = 40              # nests this close together are one threat
COVER = 48                # a defense this close to a block's threatened side covers it
ENEMY_KINDS = ("unit-spawner", "turret")
# Builds that are themselves the defense: they cover what stands near them and are never defended in turn
# (outposts once guarded the team world's starter turrets, one-engineer hour 2026-10-24).
DEFENSES = (PURPOSE, "basic-defense", "starter-turrets", "plot-turrets", "plot-wall", "breach-repair")


def threats(world: Mapping[str, Any], site: Mapping[str, Any] | None = None) -> list[tuple[float, float]]:
    """Nests and worms, in world coordinates, each once: those on the map, those the game exported with the site, and
    the nests its pollution reaches farther out (the threat board's: they send the attacks)."""
    found = [(e["x"], e["y"]) for e in world.get("entities", [])
             if e.get("f") == "enemy" and e.get("t") in ENEMY_KINDS]
    origin = (site or {}).get("origin") or {"x": 0, "y": 0}
    board = (site or {}).get("threat_board") if isinstance((site or {}).get("threat_board"), Mapping) else {}
    reached = (board.get("nests") or {}).get("reached") if isinstance(board.get("nests"), Mapping) else None
    for t in list((site or {}).get("threats") or []) + list(reached or []):
        if isinstance(t, Mapping) and "x" in t and "y" in t:
            found.append((float(t["x"]) + float(origin["x"]), float(t["y"]) + float(origin["y"])))
    return list(dict.fromkeys(found))   # (one nest on the map and in the site's lists is one nest)


def _clusters(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for p in sorted(points):
        for group in out:
            if any(math.dist(p, q) <= CLUSTER for q in group):
                group.append(p)
                break
        else:
            out.append([p])
    return out


def frontier(site: Mapping[str, Any], world: Mapping[str, Any]) -> list[dict]:
    """For each group of nests within reach, the block of the base nearest to it, unless a defense
    already covers that block's threatened side; nearest threat first: {block, box (world), threat,
    distance, nests}."""
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    enemy = threats(world, site)
    if not enemy:
        return []
    from companion import team_area
    team = team_area.team_of(site)
    blocks, defenses = [], []
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or not b.get("box"):
            continue
        x0, y0, x1, y1 = (float(v) for v in b["box"])
        box = (x0 + ox, y0 + oy, x1 + ox, y1 + oy)
        if b.get("purpose") in DEFENSES:
            defenses.append((b, box))
        elif team and not (x1 <= team.area[0] or y1 <= team.area[1] or x0 >= team.area[2] or y0 >= team.area[3]):
            continue   # the team world: inside the team area its plot's edge and starter turrets hold it
        else:
            blocks.append((b, box))
    out, seen = [], set()
    for group in _clusters(enemy):
        cx = sum(x for x, _ in group) / len(group)
        cy = sum(y for _, y in group) / len(group)
        near = min(blocks, key=lambda bb: _box_distance((cx, cy), bb[1]), default=None)
        if near is None:
            continue
        block, box = near
        distance = _box_distance((cx, cy), box)
        if distance > THREAT_RADIUS or str(block.get("id")) in seen:
            continue
        # the point of the block nearest the nests: where the attack lands
        hit = (min(max(cx, box[0]), box[2]), min(max(cy, box[1]), box[3]))
        if any(_box_distance(hit, d) <= COVER for _, d in defenses):
            continue
        seen.add(str(block.get("id")))
        threat = min(group, key=lambda p: math.dist(p, hit))
        out.append({"block": block, "box": box, "threat": threat, "distance": distance, "nests": len(group)})
    return sorted(out, key=lambda o: o["distance"])


outposts = frontier   # the name the catalog planner used when only outposts were defended


RADAR_REACH = 100     # one of the engineer's radars this close to a defense already watches its side
CLUSTER_TURRETS = 3   # a walled cluster: three gun turrets around one ammo chest (as the strategy guides build them)
CLUSTER_GAP = 9       # clusters this far apart along a threatened side
DIR = {(0, -1): NORTH, (1, 0): EAST, (0, 1): SOUTH, (-1, 0): WEST}


def _cluster(chest: tuple[int, int], f: tuple[int, int]) -> list[Placed]:
    """Three gun turrets around an ammo chest at tile `chest`, the front one toward `f` (the threat), the
    others to the sides; an inserter from the chest loads each, a pole behind the chest powers them.
    The engineer fills one chest instead of walking to every turret."""
    fx, fy = f
    lx, ly = -fy, fx

    def at(a: int, b: int) -> tuple[int, int]:
        return chest[0] + fx * a + lx * b, chest[1] + fy * a + ly * b

    def turret(tiles: list[tuple[int, int]]) -> Placed:
        return Placed("gun-turret", min(t[0] for t in tiles) + 1.0, min(t[1] for t in tiles) + 1.0)

    out = [Placed("iron-chest", chest[0] + 0.5, chest[1] + 0.5)]
    for a, b in ((1, 0), (0, 1), (0, -1)):
        ix, iy = at(a, b)
        out.append(Placed("inserter", ix + 0.5, iy + 0.5, DIR[(chest[0] - ix, chest[1] - iy)]))   # picks the chest
    out.append(turret([at(a, b) for a in (2, 3) for b in (-1, 0)]))     # front
    out.append(turret([at(a, b) for a in (0, 1) for b in (2, 3)]))      # sides
    out.append(turret([at(a, b) for a in (0, 1) for b in (-3, -2)]))
    px, py = at(-1, 0)
    out.append(Placed("small-electric-pole", px + 0.5, py + 0.5))
    return out


def plan_defense(site: Mapping[str, Any], world: Mapping[str, Any], outpost: Mapping[str, Any],
                 turrets: int | None = None) -> dict:
    """Walled turret clusters (one per three turrets wanted: two, one more per extra nest in the group,
    at most four) on the side facing the threat, a wall in front with a tile of gap (biters reach over
    a wall), powered from the network; where no power reaches, turrets loaded by hand instead."""
    if turrets is None:
        turrets = min(4, 1 + int(outpost.get("nests", 1)))
    recipes = ratios.unlocked(site)

    def can(name: str) -> bool:
        return not recipes or name in recipes      # nothing reported: the planner's default (walls)

    wall_rows = 0 if not can("stone-wall") else (2 if "gate" in recipes else 1)
    x0, y0, x1, y1 = outpost["box"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tx, ty = outpost["threat"]
    dx, dy = tx - cx, ty - cy
    taken = blocked_tiles(world)
    horizontal = abs(dx) >= abs(dy)
    side = (1 if dx > 0 else -1) if horizontal else (1 if dy > 0 else -1)
    f = (side, 0) if horizontal else (0, side)
    edge = (x1 if side > 0 else x0) if horizontal else (y1 if side > 0 else y0)
    lo, hi = (y0, y1) if horizontal else (x0, x1)
    line = math.floor(edge + 3 * side)   # the chests' row, three tiles out from the block
    placed: list[Placed] = []
    clusters = []
    before = set(taken)
    for b in _spaced(lo, hi, max(1, math.ceil(turrets / CLUSTER_TURRETS)), gap=CLUSTER_GAP):
        chest = (line, math.floor(b)) if horizontal else (math.floor(b), line)
        parts = _cluster(chest, f)
        if not footprint(parts) & taken:
            clusters.append((chest, parts))
            taken |= footprint(parts)
    radar = None
    if clusters and can("radar"):
        chest, _ = clusters[len(clusters) // 2]
        mine = [(e["x"], e["y"]) for e in world.get("entities", [])
                if e.get("t") == "radar" and e.get("f") == world.get("force")]
        if not any(math.dist(p, chest) <= RADAR_REACH for p in mine):
            radar = _radar(chest, f, taken)
            if radar:
                taken |= footprint([radar])
    clusters = [parts for _, parts in clusters]
    if clusters:
        placed = [e for c in clusters for e in c] + ([radar] if radar else [])
        live = set(world.get("live_nets") or ())
        powered = [(e["x"], e["y"]) for e in world.get("entities", [])
                   if e.get("t") == "electric-pole" and e.get("f") == world.get("force")
                   and (not live or e.get("net") is None or e.get("net") in live)]
        extra, problems = connect(placed, powered, taken)
        if problems or not powered:
            placed, clusters, taken, radar = [], [], before, None   # no power out here: turrets loaded by hand
        else:
            placed += extra
            taken |= footprint(extra)
    front = 5 if clusters else 3     # the wall: a tile in front of the front turrets
    if not clusters:
        along = [(line, b) if horizontal else (b, line) for b in _spaced(lo, hi, turrets)]
        for px, py in along:
            turret = Placed("gun-turret", math.floor(px) + 1.0, math.floor(py) + 1.0)
            if not footprint([turret]) & taken:
                placed.append(turret)
                taken |= footprint([turret])
    if not any(e.name == "gun-turret" for e in placed):
        raise layout_planner.PlanError("no room for turrets on the threatened side of the outpost")
    span = [b for e in placed for b in ((e.y,) if horizontal else (e.x,))]
    for row in range(wall_rows):
        out = line + (front + row) * side
        for b in range(math.floor(min(lo, min(span) - 3)) - 2, math.ceil(max(hi, max(span) + 3)) + 3):
            tile = (out, b) if horizontal else (b, out)
            if tile not in taken:   # belts, pipes and rails crossing the line keep their gap
                placed.append(Placed("stone-wall", tile[0] + 0.5, tile[1] + 0.5))
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    block = str(outpost["block"].get("id", "outpost"))
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", block)[:40]
    entities = [e.to_plan(f"defense-{n}", -ox, -oy) for n, e in enumerate(placed, start=1)]
    guns = [e["id"] for e in entities if e["prototype"] == "gun-turret"]
    chests = [e["id"] for e in entities if e["prototype"] == "iron-chest"]
    walls = [e["id"] for e in entities if e["prototype"] == "stone-wall"]
    radars = [e["id"] for e in entities if e["prototype"] == "radar"]
    core = [e["id"] for e in entities if e["prototype"] not in ("stone-wall", "radar")]
    # Turrets first, loaded (their chest filled, or each turret by hand); the wall after (a stage that
    # fails does not take the turrets down with it).
    supplies = ([{"entity_id": c, "item": "firearm-magazine", "slot": "input"} for c in chests] if chests else
                [{"entity_id": g, "item": "firearm-magazine", "slot": "ammo"} for g in guns])
    stages = [{"id": f"defend-{safe}", "depends_on": [], "entity_ids": core, "manual_supplies": supplies,
               "witnesses": [{"kind": "entity_built", "entity_id": guns[0]},
                             {"kind": "ammo_loaded", "entity_id": guns[0]}]}]
    if walls:
        stages.append({"id": f"wall-{safe}", "depends_on": [f"defend-{safe}"], "entity_ids": walls,
                       "manual_supplies": [], "witnesses": [{"kind": "entity_built", "entity_id": walls[0]}]})
    if radars:
        stages.append({"id": f"radar-{safe}", "depends_on": [f"defend-{safe}"], "entity_ids": radars,
                       "manual_supplies": [], "witnesses": [{"kind": "entity_built", "entity_id": radars[0]}]})
    return {"plan_id": f"{PURPOSE}:{block}", "source": "defense-planner", "purpose": PURPOSE, "entities": entities,
            "stages": stages}


def _radar(chest: tuple[int, int], f: tuple[int, int], taken: set[tuple[int, int]]) -> Placed | None:
    """A radar behind a cluster (away from the threat), where the cluster's pole powers it."""
    fx, fy = f
    lx, ly = -fy, fx
    for a, b in ((-2, 2), (-2, -2), (-3, 0)):
        x, y = chest[0] + fx * a + lx * b, chest[1] + fy * a + ly * b
        radar = Placed("radar", x + 0.5, y + 0.5)
        if not footprint([radar]) & taken:
            return radar
    return None


def _spaced(a: float, b: float, n: int, gap: float = 3.0) -> list[float]:
    """`n` positions spread along [a, b], at least `gap` apart (a turret is two tiles wide): on a short
    side the line runs past the block's ends, centred on it."""
    if n == 1:
        return [(a + b) / 2]
    step = max((b - a) / n, gap)
    middle = (a + b) / 2
    return [middle + (i - (n - 1) / 2) * step for i in range(n)]


ALLY_PURPOSE = "ally-defense"
ALLY_BOX = 12        # the ally's base: this far around where they stood when they asked
NO_THREAT = 400      # nests farther than this are not the danger: face the way the land is open


def plan_for_ally(site: Mapping[str, Any], world: Mapping[str, Any], turrets: int = 4) -> dict:
    """Turrets and a wall by an ally's base (where they stood when they asked), facing the nearest nests:
    the engineer's own buildings, which it keeps loaded like its own defenses."""
    help_ = site.get("help") or {}
    origin = site.get("origin") or {"x": 0, "y": 0}
    px, py = float(help_["x"]) + float(origin["x"]), float(help_["y"]) + float(origin["y"])
    box = (px - ALLY_BOX, py - ALLY_BOX, px + ALLY_BOX, py + ALLY_BOX)
    enemy = threats(world, site)
    nearest = min(enemy, key=lambda p: math.dist(p, (px, py)), default=None)
    if nearest is None or math.dist(nearest, (px, py)) > NO_THREAT:
        nearest = (px, py - 100.0)   # nothing known: the north, until nests show up
    group = [p for p in enemy if math.dist(p, nearest) <= CLUSTER] or [nearest]
    block = {"id": f"ally-{help_.get('player_index', 'player')}", "purpose": ALLY_PURPOSE}
    plan = plan_defense(site, world, {"block": block, "box": box, "threat": nearest,
                                      "distance": math.dist(nearest, (px, py)), "nests": len(group)}, turrets)
    plan["purpose"] = ALLY_PURPOSE
    plan["plan_id"] = f"{ALLY_PURPOSE}:{block['id']}"
    return plan

