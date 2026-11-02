"""A steam power plant sized for the base it powers, fed with coal by belt.

Players work out their power the way they work out their plates: what the base
draws at full work against what the engines make. A boiler runs two steam
engines (1.8 MW). The plant extends the proven starter template (offshore pump,
water pipes, boiler, steam pipe, engine) into a row: boilers one tile apart with
a water pipe between them, two engines above each, poles in the gaps between
the engine columns, and a coal belt below the boilers that burner inserters
feed from (burner inserters need no power, so a plant that ran dry restarts).
The game lists where an offshore pump fits (`pumps` in the map dump); the plant
is turned and mirrored to match and must stand clear of buildings, water and ore.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Mapping

from companion.modules import EAST, NORTH, Placed, SOUTH, WEST
from companion import team_area
from companion.smelting import RECTS, footprint

BOILER_KW = 1800.0
ENGINE_KW = 900.0
COAL_PER_BOILER = 1.8 / 4.0          # coal holds 4 MJ: a boiler at full burn takes 0.45/s
# Full-work draw of the buildings a base runs on (kW).
DRAW_KW = {"electric-mining-drill": 90, "assembling-machine-1": 75, "assembling-machine-2": 150,
           "assembling-machine-3": 375, "inserter": 13.2, "fast-inserter": 46.7, "long-handed-inserter": 20,
           "lab": 60, "chemical-plant": 210, "oil-refinery": 420, "pumpjack": 90, "electric-furnace": 180,
           "radar": 300, "pump": 30, "rocket-silo": 250}
SPARE = 1.1
GROWTH = 2.0          # a power expansion at least doubles what stands: players build power well ahead, so it is not
                      # short again for a while (the user, 2026-10-25)
MAX_ROW = 40          # boilers one offshore pump feeds in a row (it could feed far more)


def draw_kw(names: Iterable[str]) -> float:
    return sum(DRAW_KW.get(n, 0.0) for n in names)


def supply_kw(dump: Mapping[str, Any]) -> float:
    """What the engineer's steam engines can make, limited by the boilers feeding them."""
    mine = Counter(e["n"] for e in dump.get("entities", []) if e.get("f") == dump.get("force"))
    return min(mine.get("steam-engine", 0) * ENGINE_KW, mine.get("boiler", 0) * BOILER_KW)


def boilers_needed(dump: Mapping[str, Any], planned: Iterable[str]) -> int:
    """Boilers (two engines each) to add so the base and what is planned run at full work."""
    mine = [e["n"] for e in dump.get("entities", []) if e.get("f") == dump.get("force")]
    short = (draw_kw(mine) + draw_kw(planned)) * SPARE - supply_kw(dump)
    return max(0, math.ceil(short / BOILER_KW))


def template(boilers: int, belt: bool = True, double: bool = False) -> list[tuple[str, float, float, int]]:
    """The plant relative to the water tile beside its pump, in the starter template's frame.
    Without `belt`, the boilers are fed by hand: no coal belt and inserters that nothing supplies.
    `double`: as players build it, two rows of boilers facing away from one coal belt between them
    (the second row mirrored below it, its water branching off the pump's pipe): one belt feeds twice
    the boilers. Boilers alternate between the rows, so a bigger plant only adds to a smaller one."""
    out = [("offshore-pump", 0, 1, NORTH), ("pipe", 0, 2, 0), ("pipe", 0, 3, 0), ("pipe", 1, 3, 0), ("pipe", 2, 3, 0)]
    flip = {NORTH: SOUTH, SOUTH: NORTH}
    for k in range(boilers):
        col, south = (k // 2, k % 2 == 1) if double else (k, False)
        bx = 4 + 4 * col
        parts = [("boiler", bx, 2.5, NORTH), ("pipe", bx, 1, 0), ("steam-engine", bx, -2, NORTH),
                 ("steam-engine", bx, -7, NORTH),
                 ("small-electric-pole", bx + 2, -1, 0), ("small-electric-pole", bx + 2, -6, 0)]
        if belt:
            parts.append(("burner-inserter", bx, 4, SOUTH))
        if k + (2 if double else 1) < boilers:
            parts.append(("pipe", bx + 2, 3, 0))      # water on to the next boiler of the row
        if south:   # the mirror image below the belt (row y=5)
            parts = [(n, x, 10 - y, flip.get(d, d)) for n, x, y, d in parts]
        out += parts
    if double and boilers > 1:
        # water to the second row under the coal belt's way in (a pipe-to-ground pair faces its open
        # side), and a pole joining the two rows' poles
        out += [("pipe-to-ground", 0, 4, NORTH), ("pipe-to-ground", 0, 6, SOUTH), ("pipe", 0, 7, 0),
                ("pipe", 1, 7, 0), ("pipe", 2, 7, 0), ("small-electric-pole", 6, 4, 0)]
    if belt:
        cols = math.ceil(boilers / 2) if double else boilers
        for x in range(2, 4 + 4 * (cols - 1) + 2):
            out.append(("transport-belt", x, 5, EAST))  # the coal belt under the boilers (between the rows)
    return out


def _turn(x: float, y: float, d: int, turns: int, mirror: int, name: str) -> tuple[float, float, int]:
    x = x * mirror
    if mirror < 0 and d in (EAST, WEST):
        d = WEST if d == EAST else EAST
    for _ in range(turns):
        x, y = -y, x
    if name in ("offshore-pump", "boiler", "steam-engine", "transport-belt", "burner-inserter", "pipe-to-ground"):
        d = (d + 4 * turns) % 16
        if name == "steam-engine":
            d %= 8   # engines only use the north/east axis
    return x, y, d


def place(spot: Mapping[str, Any], boilers: int, mirror: int, belt: bool = True,
          old: tuple[int, bool] | None = None, double: bool = False) -> tuple[list[Placed], tuple[int, int], int]:
    """The plant at a pump spot: its buildings, the tile the coal belt must come from, and the
    direction coal travels into the belt. With `old` (boilers, belt) of the row already standing
    there, only what extends it."""
    wx, wy, turns = spot["wx"], spot["wy"], spot["turns"]
    parts = template(boilers, belt, double)
    if old:
        standing = set(template(old[0], old[1], double))
        parts = [p for p in parts if p not in standing]
    out = []
    for name, x, y, d in parts:
        tx, ty, td = _turn(x, y, d, turns, mirror, name)
        out.append(Placed(name, wx + 0.5 + tx, wy + 0.5 + ty, td))
    ex, ey, ed = _turn(1, 5, EAST, turns, mirror, "transport-belt")
    return out, (math.floor(wx + 0.5 + ex), math.floor(wy + 0.5 + ey)), ed


def size_of(name: str, direction: int) -> tuple[int, int]:
    w, h = RECTS.get(name, (1, 1))
    return (h, w) if direction in (EAST, WEST) else (w, h)


def tiles(e: Placed) -> set[tuple[int, int]]:
    w, h = size_of(e.name, e.direction)
    x0, y0 = math.floor(e.x - w / 2 + 0.5), math.floor(e.y - h / 2 + 0.5)
    return {(x0 + i, y0 + j) for i in range(w) for j in range(h)}


def plan_plant(dump: Mapping[str, Any], boilers: int, taken: set[tuple[int, int]],
               near: tuple[float, float], belt: bool = True) -> tuple[list[Placed], tuple[int, int], int] | None:
    """The plant at the pump spot nearest `near` where every building but the pump stands on free
    land; or None. `taken` holds buildings, ore and reserved tiles."""
    found = _new_row(dump, boilers, taken, near, belt)
    return found[:3] if found else None


def _new_row(dump, boilers, taken, near, belt):
    water = {(x, y) for y, a, b in dump.get("water", []) for x in range(a, b + 1)}
    wall = taken | water   # (once: per building it was a union of the whole map's taken tiles)
    team = team_area.team_of(dump)
    facts = dump.get("prototypes") if isinstance(dump.get("prototypes"), dict) else {}
    spots = sorted(team_area.pumps(dump), key=lambda s: (s["x"] - near[0]) ** 2 + (s["y"] - near[1]) ** 2)
    # the nearest spot that fits, two rows if they fit there
    for spot, mirror, double in [(s, m, d) for s in spots[:120] for d in (True, False) for m in (1, -1)]:
        entities, coal_in, coal_dir = place(spot, boilers, mirror, belt, double=double)
        ok = True
        if double and belt:
            # the coal belt can only come in along its own line, over the pipe-to-ground pair
            for ax in (0, -1):
                tx, ty, _ = _turn(ax, 5, 0, spot["turns"], mirror, "pipe")
                if (math.floor(spot["wx"] + 0.5 + tx), math.floor(spot["wy"] + 0.5 + ty)) in wall:
                    ok = False
        for e in entities if ok else ():
            # (the plot's rules as the finished plan is held to them, pump and all: a site that passes here and fails
            # there failed the same way every 20 s, F-21; the next spot or way round is tried instead)
            if not team_area.fits(team, e.name, e.x, e.y, int(e.direction or 0), facts):
                ok = False
                break
            if e.name == "offshore-pump":
                continue   # the game already said it fits
            if tiles(e) & wall:
                ok = False
                break
        if ok and (not belt or coal_in not in wall):
            row = {"wx": spot["wx"], "wy": spot["wy"], "turns": spot["turns"], "mirror": mirror,
                   "boilers": boilers, "belt": belt, "double": double}
            return entities, coal_in, coal_dir, row
    return None


def boilers_to_add(dump: Mapping[str, Any], planned: Iterable[str] = ()) -> int:
    """Boilers (two engines each) a power expansion adds: what the base and its planned buildings need at full
    work, and at least as many again as stand (GROWTH), so power doubles."""
    doubled = math.ceil(supply_kw(dump) * (GROWTH - 1) / BOILER_KW)
    return max(1, boilers_needed(dump, planned), doubled)


def _stands(dump: Mapping[str, Any], spot: Mapping[str, Any], boilers: int, mirror: int, belt: bool,
            double: bool) -> bool:
    """Whether the row kept as `spot` stands on the map as kept: its boilers where its layout puts them. A double row
    kept without its flag was extended as a single one, and its new boilers never got water (run 47, 2026-10-26).
    A map that does not show its first boiler cannot say (true)."""
    standing = {(math.floor(e["x"]), math.floor(e["y"])) for e in dump.get("entities", [])
                if e.get("t") == "boiler" or e.get("n") == "boiler"}
    laid, _, _ = place(spot, boilers, mirror, belt, double=double)
    at = [(math.floor(e.x), math.floor(e.y)) for e in laid if e.name == "boiler"]
    return not at or at[0] not in standing or all(t in standing for t in at)


def plan_row(dump: Mapping[str, Any], boilers: int, taken: set[tuple[int, int]], near: tuple[float, float],
             rows: Iterable[Mapping[str, Any]] = (), belt: bool = True, extend_fed: bool = True):
    """More power the way players add it: water goes from boiler to boiler, so one offshore pump feeds a
    long row; extend the engineer's row (newest first) when it has room, else start a new row at the
    shore nearest `near`. A row whose coal belt was laid for the boilers it has is extended only with
    `extend_fed` (its coal line would not feed many more: a big expansion goes in a row with a coal line of
    its own). Returns (new buildings, coal entry tile, coal direction, the row as it will stand, whether the
    row already had its coal belt), or None."""
    water = {(x, y) for y, a, b in dump.get("water", []) for x in range(a, b + 1)}
    wall = taken | water   # (once: per building it was a union of the whole map's taken tiles)
    for row in reversed([r for r in rows if isinstance(r, Mapping)]):
        if row.get("belt") and not extend_fed:
            continue
        try:
            have, had_belt = int(row["boilers"]), bool(row.get("belt"))
            spot = {"wx": float(row["wx"]), "wy": float(row["wy"]), "turns": int(row["turns"])}
            mirror = 1 if int(row["mirror"]) >= 0 else -1
        except (KeyError, TypeError, ValueError):
            continue
        if have + boilers > MAX_ROW:
            continue
        if not _stands(dump, spot, have, mirror, had_belt, bool(row.get("double"))):
            continue   # (the row as kept is not the row that stands: extended, its water would not reach the new boilers)
        with_belt = belt or had_belt
        entities, coal_in, coal_dir = place(spot, have + boilers, mirror, with_belt, old=(have, had_belt),
                                            double=bool(row.get("double")))
        if all(not (tiles(e) & wall) for e in entities):
            return entities, coal_in, coal_dir, {**row, "boilers": have + boilers, "belt": with_belt}, had_belt
    found = _new_row(dump, boilers, taken, near, belt)
    return (*found, False) if found else None


def plan_remote_row(dump: Mapping[str, Any], boilers: int, taken: set[tuple[int, int]],
                    rows: Iterable[Mapping[str, Any]], near: tuple[float, float], belt: bool = True):
    """Boilers by the coal, the water piped to them: what players do when coal is far from water (an
    underground pipe pair spans ten tiles, a belt one). The water comes from the far end of the
    engineer's last row, so all its boilers stay one chain off one pump. Returns what plan_row
    returns (the new row is saved like any other), or None without a row to draw water from."""
    import math as _m
    from companion.pipes import PipeMap
    from companion.smelting import ore_tiles
    rows = [r for r in rows if isinstance(r, Mapping)]
    coal = ore_tiles(dump.get("resources", {}), "coal")
    if not rows or not coal:
        return None
    last = rows[-1]
    try:
        k = (math.ceil(int(last["boilers"]) / 2) if last.get("double") else int(last["boilers"])) - 1
        mirror0 = 1 if int(last["mirror"]) >= 0 else -1
        tx, ty, _ = _turn(4 + 4 * k + 2, 3, 0, int(last["turns"]), mirror0, "pipe")
        source = (_m.floor(float(last["wx"]) + 0.5 + tx), _m.floor(float(last["wy"]) + 0.5 + ty))
    except (KeyError, TypeError, ValueError):
        return None
    water = {(x, y) for y, a, b in dump.get("water", []) for x in range(a, b + 1)}
    # The coal field first (as the coal line will choose it), then the row right beside its exit.
    from companion.smelting import foreign_tiles, plan_field
    drills = max(1, _m.ceil(boilers * COAL_PER_BOILER / 0.5))   # electric drills mine 0.5 coal/s
    # (`taken` counts ore: the field goes on the coal, but not on what stands on it, a coal loop's drills: run 45's
    # ore field laid over a belt lost the belt, 2026-10-26)
    from companion.base_plan import blocked_tiles
    room = (taken - coal) | (set(blocked_tiles(dump)) & coal) | water | team_area.forbidden(dump)
    other = foreign_tiles(dump.get("resources", {}), "coal")
    field = next((f for n in range(drills, 0, -1) for f in [plan_field(coal, room, n, "coal", near=near,
                                                                       foreign=other)] if f), None)
    target = field.exit if field else min(coal, key=lambda t: (t[0] - near[0]) ** 2 + (t[1] - near[1]) ** 2)
    blocked = taken | water | coal
    if field:
        blocked |= footprint(field.entities())   # the field stays where the coal line will put it
    for radius in range(4, 41, 2):
        for step in range(0, 360, 20):
            fx = round(target[0] + radius * _m.cos(_m.radians(step)))
            fy = round(target[1] + radius * _m.sin(_m.radians(step)))
            for turns in range(4):
                for mirror in (1, -1):
                    spot = {"wx": fx, "wy": fy, "turns": turns}
                    entities, coal_in, coal_dir = place(spot, boilers, mirror, belt)
                    # no pump and no shore pipes: the water arrives by the routed pipe at the
                    # template's (0, 3), beside the row's first pipe
                    inlet_x, inlet_y, _ = _turn(0, 3, 0, turns, mirror, "pipe")
                    inlet = (_m.floor(fx + 0.5 + inlet_x), _m.floor(fy + 0.5 + inlet_y))
                    shore = set()
                    for x, y in ((0, 2), (0, 3)):
                        sx, sy, _ = _turn(x, y, 0, turns, mirror, "pipe")
                        shore.add((_m.floor(fx + 0.5 + sx), _m.floor(fy + 0.5 + sy)))
                    entities = [e for e in entities if e.name != "offshore-pump" and not (
                        e.name == "pipe" and (_m.floor(e.x), _m.floor(e.y)) in shore)]
                    used = set()
                    if any(tiles(e) & blocked for e in entities):
                        continue
                    for e in entities:
                        used |= tiles(e)
                    if belt and (coal_in in blocked or coal_in in used):
                        continue
                    if inlet in blocked or inlet in used:
                        continue
                    pipes = PipeMap(blocked=(blocked | used) - {source, inlet})
                    line = pipes.route("water", source, inlet, limit=60000)
                    if line is None:
                        continue
                    row = {"wx": fx, "wy": fy, "turns": turns, "mirror": mirror, "boilers": boilers,
                           "belt": belt, "remote": True}
                    return entities + line, coal_in, coal_dir, row, False
    return None
