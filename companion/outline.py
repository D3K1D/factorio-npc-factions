"""The whole base outlined from the start, for the architect to design chunk by chunk.

Right after the opening the rules outline the base the engineer will grow into: the bus with its mall, a power area by
water with room to double and double again, every ore patch nearby with its extraction, and the production chains to
the rocket (the user, 2026-10-25: "fully utilizing all nearby nodes and collecting their resources, a large power
generation area, the main bus, mall, production chains for items. The NPC just fills it in as it goes"). Each part is
a chunk. The architect designs one chunk at a time in the background, the one nearest to being built first ("it should
plan the base chunk by chunk so it has time to design instead of all at once"), and the engineer's planners fill the
plan in as they go. The outline itself keeps no ground: kept ground shut the bus out of its own ore before (run 31,
2026-10-25); a chunk's design adds the zones that do.

    out = make(site, world, plan)        # plan["outline"]: {"revision", "chunks": [...]}
    chunk = next_chunk(out, tick)        # the next to design, or None
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MIN_PATCH = 40        # ore tiles: a smaller patch is not worth its own extraction
OIL_GAP = 12          # tiles: oil wells this close together are one field
MAX_CHUNKS = 64
MAX_AREAS = 4
MAX_PATCHES = 24      # patches outlined at most, nearest home first
REACH = 640           # tiles from home: farther patches wait for a later survey
POWER_W, POWER_H = 40, 48   # the power area by water: room for rows of boilers and engines (about four times a
                            # first plant's sixteen engines)
BELT = 15.0           # items/s on a full yellow belt
DRILL_TILES = 9       # ore tiles an electric drill covers
DRILL_RATE = 0.5      # its ore a second
SECTIONS = 3          # sections one chunk's design holds (architect.CHUNK_SECTIONS)
FUEL_PATCHES = 2        # coal patches the fuel chunk draws on, nearest home first
COOLDOWN = 600        # ticks between two chunks designed for one engineer
RETRY = 3600          # ticks before a chunk whose design failed is tried again, doubling up to MAX_RETRY
MAX_RETRY = 36000
RESOURCES = ("iron-ore", "copper-ore", "coal", "stone", "uranium-ore", "crude-oil")


@dataclass
class Patch:
    resource: str
    box: tuple[int, int, int, int]       # world tiles, x0, y0, x1, y1 (edges)
    tiles: int
    center: tuple[float, float]
    distance: float


def _components(rows: Iterable, gap: int = 1) -> list[list[tuple[int, int, int]]]:
    """Runs of tiles (y, x0, x1) grouped into patches: runs on the same or next rows (within `gap`) that overlap
    (within `gap`) are one patch."""
    runs = sorted((int(r[0]), int(r[1]), int(r[2])) for r in rows if len(r) >= 3)
    parent = list(range(len(runs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    by_row: dict[int, list[int]] = {}
    for i, (y, _, _) in enumerate(runs):
        by_row.setdefault(y, []).append(i)
    for i, (y, a, b) in enumerate(runs):
        for dy in range(0, gap + 1):
            for j in by_row.get(y + dy, ()):
                if j <= i and dy == 0:
                    continue
                _, c, d = runs[j]
                if c <= b + gap and d >= a - gap:
                    parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, run in enumerate(runs):
        groups.setdefault(find(i), []).append(run)
    return list(groups.values())


def survey_patches(world: Mapping[str, Any], home: tuple[float, float] = (0.0, 0.0)) -> list[Patch]:
    """Every ore patch on the map (oil: every field of wells), nearest home first, the small ones left out."""
    out = []
    resources = world.get("resources") or {}
    for name in RESOURCES:
        rows = resources.get(name) or []
        oil = name == "crude-oil"
        for group in _components(rows, gap=OIL_GAP if oil else 1):
            tiles = sum(b - a + 1 for _, a, b in group)
            if not oil and tiles < MIN_PATCH:
                continue
            x0, x1 = min(a for _, a, _ in group), max(b for _, _, b in group) + 1
            y0, y1 = min(y for y, _, _ in group), max(y for y, _, _ in group) + 1
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d = math.dist((cx, cy), home)
            if d <= REACH:
                out.append(Patch(name, (x0, y0, x1, y1), tiles, (cx, cy), d))
    out.sort(key=lambda p: p.distance)
    return out[:MAX_PATCHES]


def patch_id(p: Patch) -> str:
    """A patch's name by where it lies (a new survey's nearer patch renames none of the others)."""
    return f"{p.resource}@{round(p.center[0])},{round(p.center[1])}"


def _power_spot(world: Mapping[str, Any], home: tuple[float, float]) -> tuple[int, int, int, int] | None:
    """A box by the nearest water where an offshore pump fits (the map's `pumps`), else by the nearest water."""
    spots = []
    for p in world.get("pumps") or []:
        try:
            spots.append((float(p["x"]), float(p["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not spots:
        for y, a, b in world.get("water") or []:
            spots.append(((a + b) / 2, float(y)))
    if not spots:
        return None
    x, y = min(spots, key=lambda s: math.dist(s, home))
    return (math.floor(x - POWER_W / 2), math.floor(y - POWER_H / 2), math.floor(x + POWER_W / 2),
            math.floor(y + POWER_H / 2))


def _bus_box(site: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """The saved bus's ground (its corner is in the map's tiles), with room past its end to grow
    (factory_plan.ROOM_ROWS) and above it for its smelting head."""
    saved = site.get("base_plan") or {}
    try:
        x, y, columns, length = (int(saved[k]) for k in ("x", "y", "columns", "length"))
    except (KeyError, TypeError, ValueError):
        return None
    from companion.factory_plan import ROOM_ROWS
    return (math.floor(x - 8), math.floor(y - 60), math.floor(x + 3 * columns + 8), math.floor(y + length + ROOM_ROWS))


def _key(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def make(site: Mapping[str, Any], world: Mapping[str, Any], plan: Mapping[str, Any] | None = None) -> dict:
    """The outline for the base: chunks for the bus (with the sections on it), the power area, every patch, and the
    plan's other sections, in the order they are likely built. What an earlier outline knew of each chunk (its
    design, its failures) carries over when the chunk is the same; a chunk whose inputs changed is designed again."""
    home = (float((world.get("home") or {}).get("x") or 0), float((world.get("home") or {}).get("y") or 0))
    sections = [s for s in (plan or {}).get("sections") or [] if isinstance(s, Mapping)
                and s.get("status") not in ("retired", "retiring")]
    by_kind: dict[str, list[str]] = {}
    for s in sections:
        by_kind.setdefault(str(s.get("kind")), []).append(str(s.get("id")))
    from companion import factory_plan as fp
    chunks = []
    bus = _bus_box(site)
    on_bus = [str(s.get("id")) for s in sections if fp.on_bus(s) and "@" not in str(s.get("chunk") or "")]
    for k in range(0, max(1, len(on_bus)), SECTIONS):
        chunks.append({"id": "bus" if k == 0 else f"bus-{k // SECTIONS + 1}", "kind": "bus",
                       "section_ids": on_bus[k:k + SECTIONS],
                       "areas": [{"id": "bus", "role": "bus", "box": list(bus)}] if bus else [],
                       "why": "the main bus: smelting at its head, science and the mall down it"})
    power = _power_spot(world, home)
    chunks.append({"id": "power", "kind": "power", "section_ids": by_kind.get("power", [])[:2],
                   "areas": [{"id": "power", "role": "power", "box": list(power)}] if power else [],
                   "why": "a large power area by water, with room to double and double again"})
    # Fuel (the user, 2026-10-25: the architect plans the furnaces' and the boilers' coal by belt from the coal
    # fields): the nearest coal, the smelting at the bus's head and the power area, joined by coal belts sized for
    # every furnace and boiler, so nothing burns coal carried by hand.
    coal = [p for p in survey_patches(world, home) if p.resource == "coal"][:FUEL_PATCHES]
    if coal:
        areas = [{"id": patch_id(p), "role": "coal", "box": list(p.box)} for p in coal]
        areas += [{"id": "bus-head", "role": "furnaces", "box": list(bus)}] if bus else []
        areas += [{"id": "power", "role": "boilers", "box": list(power)}] if power else []
        chunks.append({"id": "fuel", "kind": "fuel", "section_ids": [], "areas": areas,
                       "targets": {"coal": round(sum(min(BELT, p.tiles / DRILL_TILES * DRILL_RATE) for p in coal), 2)},
                       "why": "coal by belt for every furnace and boiler: electric drills on the nearest coal and coal "
                              "belts from there along the furnaces at the bus's head and on to the boilers, sized for "
                              "all of them and room to double; the burner drills go as soon as there is power"})
    # The perimeter: each side of the wall ring the engineer holds, a chunk (the user, 2026-10-25: the architect plans
    # the wall round the factory in its edge chunks, with turrets and doors, thicker where it is hit). Where the wall
    # and its turrets go the rules lay out (companion/perimeter.py); the architect weighs how much and how soon.
    from companion import perimeter
    placed, total = perimeter.wall_progress(site)
    layers = placed / total if total else 0.0
    for e in perimeter.edges(site):
        far = perimeter.TURRET_OFFSET + 4
        a, b = (e.row(0), e.row(far)) if e.name in ("north", "west") else (e.row(far), e.row(0))
        box = [e.lo, a, e.hi + 1, b + 1] if e.horizontal else [a, e.lo, b + 1, e.hi + 1]
        chunks.append({"id": f"edge-{e.name}-{e.lo}", "kind": "defense", "section_ids": by_kind.get("defense", [])[:1],
                       "areas": [{"id": f"edge-{e.name}-{e.lo}", "role": "wall", "box": box}],
                       "why": (f"the {e.name} side of the wall ring, {e.hi - e.lo + 1} tiles (the ring goes round the "
                               f"factory and moves out as it grows): a wall (about {layers:.1f} layers of the line "
                               f"stand) with doors a tank fits through, thickened to four layers where it is hit or "
                               f"walls are spare, turrets spread out behind it, side by side where it is hit often "
                               f"(laser turrets where they can be powered), later ammunition belts inside it")})
    for p in survey_patches(world, home):
        rate = BELT if p.resource == "crude-oil" else round(min(BELT, p.tiles / DRILL_TILES * DRILL_RATE), 2)
        pid = patch_id(p)
        theirs = [str(s.get("id")) for s in sections if s.get("chunk") == pid][:SECTIONS]
        chunks.append({"id": pid, "kind": "resource", "section_ids": theirs,
                       "areas": [{"id": pid, "role": "patch", "box": list(p.box)}],
                       "targets": {p.resource: rate}, "tiles": p.tiles, "distance": round(p.distance),
                       "why": f"{p.resource}, {p.tiles} tiles {round(p.distance)} tiles from home: mined in full"})
    used = {i for c in chunks for i in c["section_ids"]}
    for s in sections:
        sid = str(s.get("id"))
        if sid not in used and len(chunks) < MAX_CHUNKS:
            chunks.append({"id": f"section-{sid}", "kind": str(s.get("kind")), "section_ids": [sid], "areas": [],
                           "why": str(s.get("why") or "")[:160]})
    chunks = chunks[:MAX_CHUNKS]
    old = {c.get("id"): c for c in ((plan or {}).get("outline") or {}).get("chunks") or [] if isinstance(c, Mapping)}
    for order, c in enumerate(chunks):
        c["areas"] = c["areas"][:MAX_AREAS]
        c["order"] = order
        c["input_key"] = _key(c["kind"], c["section_ids"], [a["box"] for a in c["areas"]], c.get("targets"))
        was = old.get(c["id"])
        if was and was.get("input_key") == c["input_key"]:
            for k in ("designed_key", "designed_tick", "failures", "retry_tick", "notes"):
                if k in was:
                    c[k] = was[k]
    return {"revision": _key([c["input_key"] for c in chunks]), "chunks": chunks}


def pending(outline: Mapping[str, Any] | None) -> list[dict]:
    """The chunks not designed for their present inputs, in order."""
    return [c for c in (outline or {}).get("chunks") or [] if c.get("designed_key") != c.get("input_key")]


def next_chunk(outline: Mapping[str, Any] | None, tick: int, last_done: int | None = None) -> dict | None:
    """The chunk to design next: the first not designed for its present inputs whose retry time has come, and none
    sooner than COOLDOWN after the last one designed."""
    if last_done is not None and tick - last_done < COOLDOWN:
        return None
    return next((c for c in pending(outline) if tick >= int(c.get("retry_tick") or 0)), None)


def designed(outline: Mapping[str, Any], chunk_id: str, tick: int, notes: Iterable[str] = ()) -> None:
    """Mark a chunk designed for its present inputs."""
    for c in outline.get("chunks") or []:
        if c.get("id") == chunk_id:
            c["designed_key"], c["designed_tick"], c["failures"] = c.get("input_key"), tick, 0
            c["notes"] = [str(n)[:160] for n in notes][:3]
            c.pop("retry_tick", None)


def failed(outline: Mapping[str, Any], chunk_id: str, tick: int) -> None:
    """A chunk's design failed: it is tried again later, the wait doubling each time; the others go on meanwhile."""
    for c in outline.get("chunks") or []:
        if c.get("id") == chunk_id:
            c["failures"] = int(c.get("failures") or 0) + 1
            c["retry_tick"] = tick + min(MAX_RETRY, RETRY * 2 ** (c["failures"] - 1))


def invalidate(outline: Mapping[str, Any], kinds: Iterable[str] | None = None) -> int:
    """Something moved (research, a shortage, a priority): the chunks of these kinds (all, by default) are designed
    again. Returns how many."""
    want = set(kinds) if kinds is not None else None
    n = 0
    for c in outline.get("chunks") or []:
        if want is None or c.get("kind") in want:
            if c.get("designed_key"):
                n += 1
            c.pop("designed_key", None)
    return n
