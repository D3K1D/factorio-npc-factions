"""The architect: the designer's model plans an engineer's factory as large sections, asked rarely.

One Codex run (the user's ChatGPT plan; about 5,000 tokens a call before the brief, so the brief is
kept short) on what the plan needs: who the engineer is and what it races for, this game's numbers
(rates.py), the factory ledger's budgets, research and the path to space, the land by cells (land.py),
its priorities, and the plan it has now. The answer, shaped by a JSON schema, is one plan, or two or
three when the choice is open. Each is checked here, never sent back for another try: cells on the
engineer's own ground and one section to a cell, items the game's recipes make, takes worked out
through the recipes, and the whole weighed as the ledger weighs a new segment (85% of supply on
average). What fails a check is cut and noted on the plan; the notes go into the next brief. System
One chooses among the plans left (or the rules: the one with fewest problems).

    plan, how = Architect(codex)(site, world, current_plan, "research unlocked chemical-science-pack")
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from companion import factory_plan as fp
from companion import land as lands
from companion import ledger, rates, ratios, team_area
from companion import mall as malls
from companion.codex_backend import DESIGNER_MODEL

MAX_PLANS = 3
# The architect works in the background (plan_keeper): nothing waits on it, so it has time to think. Its brief of 8
# to 10 thousand characters took past the designers' 120 s in every call of runs 32 and 33 (2026-10-25).
TIMEOUT_S = 600
MAX_ASKED_SECTIONS = 20
LEDGER_LINES = 10
WET = 0.5            # a cell more than half water is no ground for a section

_CELLS = {"type": "array", "items": {"type": "string"}, "maxItems": 12}
_RATE = {"type": "object", "properties": {"item": {"type": "string"}, "per_second": {"type": "number"}},
         "required": ["item", "per_second"], "additionalProperties": False}
_SECTION = {"type": "object", "properties": {
    "id": {"type": "string"},
    "kind": {"type": "string", "enum": list(fp.KINDS)},
    "makes": {"type": "array", "items": _RATE, "maxItems": 12},
    "load": {"type": ["number", "null"]},
    "zone": _CELLS, "room": _CELLS,
    "grow": {"type": "string", "enum": list(fp.GROW)},
    "priority": {"type": "number"},
    "why": {"type": "string"},
    "replaces": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "quality": {"type": ["string", "null"]}},
    "required": ["id", "kind", "makes", "load", "zone", "room", "grow", "priority", "why", "replaces", "quality"],
    "additionalProperties": False}
_LANE = {"type": "object", "properties": {"item": {"type": "string"}, "belts": {"type": "integer"},
                                          "for": {"type": "string"}},
         "required": ["item", "belts", "for"], "additionalProperties": False}
_KEEP = {"type": "object", "properties": {"cells": _CELLS, "for": {"type": "string"}, "priority": {"type": "number"}},
         "required": ["cells", "for", "priority"], "additionalProperties": False}
_PLAN = {"type": "object", "properties": {
    "label": {"type": "string"}, "summary": {"type": "string"},
    "sections": {"type": "array", "items": _SECTION, "maxItems": MAX_ASKED_SECTIONS},
    "lanes": {"type": "array", "items": _LANE, "maxItems": 16},
    "keep": {"type": "array", "items": _KEEP, "maxItems": 8}},
    "required": ["label", "summary", "sections", "lanes", "keep"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"plans": {"type": "array", "items": _PLAN, "maxItems": MAX_PLANS}},
          "required": ["plans"], "additionalProperties": False}
# One chunk of the outlined base (companion/outline.py) at a time: a small brief and a small answer, one design, no
# choice among plans; the rest of the plan is left as it is.
CHUNK_SECTIONS = 3
CHUNK_CELLS = 8
CHUNK_SCHEMA = {"type": "object", "properties": {
    "sections": {"type": "array", "items": _SECTION, "maxItems": CHUNK_SECTIONS},
    "notes": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
    "required": ["sections", "notes"], "additionalProperties": False}


class ArchitectError(Exception):
    """No usable plan came back."""


@dataclass
class Option:
    plan: dict
    problems: list[str] = field(default_factory=list)   # what the checks cut, changed or found

    @property
    def label(self) -> str:
        return str(self.plan.get("label") or "a plan")


# --- the brief ---------------------------------------------------------------

def _builders() -> list[str]:
    goals = ", ".join(fp.bus_goals())
    products = ", ".join(fp.mall_products())
    return [
        "- bus: the main bus. Smelting columns at its head (each plate at most a full belt, 15 a second), mining "
        "fields routed to them, lanes down it and its sections below. Give the bus section its cells, and room "
        "cells below it to grow.",
        "- Sections of kind science, rocket, labs, mall or smelting with no cells of their own go on the bus. "
        f"The bus can make {goals}, every pack at the bus's one rate; more science comes as growth copies of the "
        "whole chain with their own smelting (up to 4). The rocket is its own section once researched. The mall "
        f"fills chests with buildings and can make {products}; a product it does not make yet, or more of one, "
        "comes as a mall extension down the bus.",
        "- power: steam by the nearest shore, sized for the whole base at full work. defense: turrets and walls "
        "where the threats come from (in the team world, the plot's outer edges).",
        "- Any other kind, or a bus kind with cells of its own (smelting or intermediates off the bus, mining "
        "outposts, oil): its ground is kept and cleared of trees, rocks and cliffs until a builder for it exists. "
        "Plan ahead with these too.",
        "- lanes: belts kept free down the bus for later items. keep: cells kept for sections not planned yet.",
        "- A section you leave out retires once a section making what it made runs (name it in replaces). One "
        "that wants more grows its way: extend (into its room), copy (another of it), upgrade (better machines "
        "in place)."]


def _cells_text(land: lands.Land) -> Callable[[Any], str]:
    def ids(box: Iterable[float]) -> str:
        found = [c.id for c in land.cells_in(tuple(box))]
        return "-".join(found[:6]) if found else "off the map"
    return ids


def brief(site: Mapping[str, Any], land: lands.Land, current: Mapping[str, Any] | None, reason: str) -> str:
    """What the architect is asked, in plain lines (a few thousand characters)."""
    from companion import bus_planner
    name = site.get("npc_name") or "an engineer"
    persona = site.get("personality") or {}
    traits = ", ".join(str(t) for t in persona.get("traits") or [])
    team = team_area.team_of(site)
    out = [f"You are the architect for {name}, an NPC engineer in Factorio"
           + (" with Space Age" if site.get("space_age") else "") + ". Plan the large sections of its factory: what "
           "each makes, at what rate, on which ground, with room to grow. The engineers race to reach space as fast "
           "as they can; robots and blueprints refine the factory later, so plan to automate the many buildings "
           "robots will place."]
    if team:
        out.append("It is one of a team on a death world: survival comes first. Its plot's outer edges are its "
                   "stretch of the team's wall.")
    if traits:
        out.append(f"Its personality: {traits}.")
    if not bus_planner.wants_a_bus(site):
        out.append("It builds without a main bus (chaotic): sections on the bus will not be built.")
    out += [f"Why you are asked now: {reason}.", "", "How it is built (the code places every building; you plan "
            "sections):", *_builders()]
    saved = site.get("base_plan") or {}
    spm = float(saved.get("spm") or 0) or bus_planner.goal({**site, "factory_plan": None})[0]
    book = ratios.Book.from_site(site)
    now = [p for p in fp.bus_goals() if fp.makeable(p, site) and (team or p != "military-science-pack")]
    sizes = [(label, bus_size(spm, packs, book, bool(team))) for label, packs in
             (("with what it can make now", now),
              ("with every pack and the rocket", [p for p in fp.bus_goals() if team or p != "military-science-pack"]))]
    sizes = [f"{label} about {w} x {h} tiles" for label, size in sizes if size for w, h in (size,)]
    if sizes and not saved.get("stages"):
        out.append(f"- Size: at {spm:g} science a minute the bus takes, " + "; ".join(sizes)
                   + f" (its smelting head included; cells are {lands.CELL} tiles). More science a minute takes more.")
    out += ["",
            "The checks: zones, rooms and kept ground only on the cells listed below, each cell for one section; "
            "rates are items a second at full speed, worked out through this game's recipes; on average all claims "
            f"on an item stay under {ledger.AVERAGE_LIMIT:.0%} of what makes it (a claim is full speed times load: a "
            f"mall stops when its chests fill, {ledger.LOADS['mall']:.0%}; science runs {ledger.LOADS['science']:.0%})."]
    facts = rates.of(site)
    if facts:
        ready = [n for n, e in facts.entities.items() if e.get("ready") and e.get("kind") in ("crafter", "drill", "lab")]
        numbers = facts.brief(ready)
        if numbers:
            out += ["", "This game's numbers (its own data, mods included; full speed, normal quality):", numbers]
        modules = [n for n, m in facts.modules.items() if m.get("ready") and (m.get("effects") or {}).get("quality")]
        if facts.qualities and modules:
            out.append("Quality: " + ", ".join(sorted(facts.qualities)) + "; quality modules ready: "
                       + ", ".join(sorted(modules)) + ". A section may name the quality it is built for.")
    unlocked = ratios.unlocked(site)
    packs = sorted(i for i in unlocked if i.endswith("-science-pack"))
    out += ["", f"Research: {len(site.get('researched') or [])} technologies done; science packs it can make: "
            + (", ".join(packs) or "none") + "."]
    path = site.get("space_path") if isinstance(site.get("space_path"), Mapping) else None
    if path:
        need = sorted(((str(k), float(v)) for k, v in (path.get("packs") or {}).items()), key=lambda kv: -kv[1])
        out.append(f"Path to space ({path.get('target')}): {path.get('left')} technologies and "
                   f"{float(path.get('units') or 0):.0f} units left; packs needed: "
                   + (", ".join(f"{k} {v:.0f}" for k, v in need) or "none")
                   + "; next: " + (", ".join(str(n) for n in fp._list(path.get("next"))) or "nothing") + ".")
    book = ledger.Ledger.from_site(site)
    budgets = sorted((book.budget(i) for i in book.items()), key=lambda b: -max(b.supply, b.average))
    if budgets:
        out += ["", "The factory now (the ledger):"]
        out += ["- " + b.line() for b in budgets[:LEDGER_LINES]]
        short = book.summary()
        if short:
            out.append(f"Short: {short}")
    if saved.get("stages"):
        done = saved.get("done") if isinstance(saved.get("done"), Mapping) else {}
        stages = fp._list(saved.get("stages"))
        out.append(f"Its bus: {float(saved.get('spm') or 0):g} science a minute of "
                   + ", ".join(fp._list(saved.get("packs"))
                               + [g for g in fp._list(saved.get("extra")) if not malls.is_extension(g)])
                   + f"; {sum(1 for n in stages if n in done)} of {len(stages)} stages built; its mall makes "
                   + (", ".join(fp._list(saved.get("mall"))) or "nothing") + ".")
    ranked = fp.wanted(site)
    if ranked:
        out.append("Its priorities now: " + ", ".join(f"{k} {v:.2f}" for k, v in
                                                     sorted(ranked.items(), key=lambda kv: -kv[1])[:8]) + ".")
    out += ["", f"The land in cells of {lands.CELL} tiles (letters run west to east, numbers north to south; only "
            "its own ground):", *land.lines()]
    if current:
        out += ["", "The plan it has now:", *fp.describe(current, _cells_text(land))]
    out += ["", "Answer with one plan, or two or three when the choice is truly open (label each and say how they "
            "differ). Keep sections that still serve under their ids. Priorities run 0 to 1, higher first."]
    return "\n".join(out)


def chunk_cells(land: lands.Land, chunk: Mapping[str, Any], plan: Mapping[str, Any] | None) -> list:
    """The cells a chunk may use: those under its areas, else those of its sections' zones, else the nearest
    home; at most CHUNK_CELLS."""
    cells = []
    for area in chunk.get("areas") or []:
        try:
            cells += land.cells_in(tuple(float(v) for v in area["box"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not cells and plan:
        ids = set(chunk.get("section_ids") or [])
        for z in plan.get("zones") or []:
            if z.get("for") in ids:
                cells += land.cells_in(tuple(float(v) for v in z["box"]))
    if not cells:
        cells = list(land.cells)
    seen, out = set(), []
    for c in cells:
        if c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out[:CHUNK_CELLS]


def chunk_brief(site: Mapping[str, Any], land: lands.Land, plan: Mapping[str, Any] | None,
                chunk: Mapping[str, Any]) -> str:
    """What the architect is asked about one chunk: two or three thousand characters."""
    name = site.get("npc_name") or "an engineer"
    team = team_area.team_of(site)
    cells = chunk_cells(land, chunk, plan)
    out = [f"You are the architect for {name}, an NPC engineer in Factorio"
           + (" with Space Age" if site.get("space_age") else "") + ", racing to reach space"
           + (" on a death world, survival first" if team else "") + ". Its whole base is outlined; you design it "
           "one chunk at a time, ahead of the engineer, who builds what you plan as it goes. Design only this "
           "chunk; the rest is planned on its own.",
           "", f"The chunk: {chunk.get('id')} ({chunk.get('kind')}): {chunk.get('why') or ''}."]
    targets = chunk.get("targets") or {}
    if targets:
        out.append("What it could give at most: " + ", ".join(f"{k} {v:g} a second" for k, v in targets.items()) + ".")
    ids = set(chunk.get("section_ids") or [])
    mine = [s for s in (plan or {}).get("sections") or [] if s.get("id") in ids]
    if mine:
        out += ["Its sections now:", *fp.describe({**(plan or {}), "sections": mine,
                                                     "zones": [z for z in (plan or {}).get("zones") or []
                                                               if z.get("for") in ids]}, _cells_text(land))]
    others = [c for c in ((plan or {}).get("outline") or {}).get("chunks") or [] if c.get("id") != chunk.get("id")]
    if others:
        out.append("The other chunks (planned on their own): " + ", ".join(str(c.get("id")) for c in others[:16]) + ".")
    unlocked = ratios.unlocked(site)
    packs = sorted(i for i in unlocked if i.endswith("-science-pack"))
    out += [f"Science packs it can make: {', '.join(packs) or 'none'}.",
            "Numbers: an electric mining drill mines 0.5 ore a second over 5 by 5 tiles; a stone furnace makes "
            "0.3125 plates a second and burns 0.0225 coal a second; a yellow belt carries 15 items a second; a boiler "
            "and two steam engines make 1.8 MW and the boiler burns 0.45 coal a second. Furnaces and boilers get "
            "their coal by belt from electric drills on coal, never carried by hand, and mining is electric as soon "
            "as there is power. Players expand a lot at a time: a full belt more of a plate, power doubled.",
            "", f"The cells it may use ({lands.CELL} tiles a side; letters west to east, numbers north to south):",
            *[c.line() for c in cells],
            "", f"Answer with up to {CHUNK_SECTIONS} sections for this chunk: keep its sections' ids; for an ore "
            f"patch, a section of kind mining (and one of kind smelting for an ore smelted into plates) named after "
            "the chunk. Say what each makes at what rate (items a second at full speed), its zone and room among "
            "the cells listed, its priority (0 to 1) and why; and a short note or two."]
    return "\n".join(out)


# --- checking an answer ---------------------------------------------------------

def _bus_cells(site: Mapping[str, Any], land: lands.Land, current: Mapping[str, Any] | None) -> set[str]:
    """Cells the bus stands on or was given: no other section's ground."""
    found = set()
    origin = site.get("origin") or {"x": 0, "y": 0}
    for b in site.get("base") or []:
        box = fp._list(b.get("box")) if isinstance(b, Mapping) else []
        if b.get("purpose") == "bus-base" and len(box) == 4:
            world = (box[0] + float(origin["x"]), box[1] + float(origin["y"]),
                     box[2] + float(origin["x"]), box[3] + float(origin["y"]))
            found |= {c.id for c in land.cells_in(world)}
    bus = next((s for s in fp.live(current) if s.get("kind") == "bus"), None) if current else None
    box = fp.zone_box(current, bus.get("zone")) if bus else None
    if box:
        found |= {c.id for c in land.cells_in(box)}
    return found


def bus_rate(total: float, packs: Iterable[str], book: ratios.Book) -> float:
    """The bus's own science a minute for a plan wanting `total`: as much as one head of smelting feeds (each
    plate within a full belt, with a tenth spare), the rest as growth copies; at least 5, in steps of 5."""
    from companion import base_plan
    science = [p for p in packs if p != "rocket-part"]
    if total <= 0 or not science:
        return 0.0
    try:
        need = ratios.solve({p: 1 / 60 for p in science}, book, inputs=set(base_plan.PLATES))
    except (KeyError, ValueError):
        return max(5.0, round(total / 5) * 5)
    per = max((need.inputs.get(p, 0.0) for p in base_plan.PLATES), default=0.0)
    head = base_plan.BLOCK_FURNACES * base_plan.FURNACE_PLATES   # one plate's smelting block: a full belt
    cap = head / (per * 1.1) if per > 0 else total
    return max(5.0, min(round(total / 5) * 5, math.floor(cap / 5) * 5))


def bus_size(spm: float, wanted: Iterable[str], book: ratios.Book, team: bool = False) -> tuple[int, int] | None:
    """About how much ground a bus takes (tiles across and down, its smelting head included) at `spm` with
    the packs `wanted`: its modules laid down both sides as the bus planner lays them, without the map."""
    from companion import base_plan, bus_planner
    from companion.bus import Bus
    wanted = tuple(wanted)
    packs = tuple(p for p in wanted if p not in bus_planner.EXTENSION_PACKS) or ("automation-science-pack",)
    extra = tuple(p for p in wanted if p in bus_planner.EXTENSION_PACKS)
    columns = (2 + len(packs) + (2 if "logistic-science-pack" in packs else 0) + bus_planner.LATER_LANES
               + (bus_planner.MILITARY_LANES if team else 0) + 4)
    try:
        bus = Bus(0, 0, columns, 100_000)
        for plate, belts in base_plan.plate_belts().items():
            bus.add_lane(plate, belts=belts)

        def side() -> str:
            return "east" if bus.next_module_y <= bus.next_west_y else "west"
        # (its mall after its labs: those of its last pack section where it starts with one, as the planner lays it)
        host = next((g for g in reversed(extra) if g in base_plan.SCIENCE_EXTENSIONS), None) \
            if "logistic-science-pack" in packs else None
        for m in base_plan.modules_for(packs, spm, book, labs=not extra, mall_last=True, with_mall=host is None):
            bus.attach(m, side=side())
        science = ["automation-science-pack", "logistic-science-pack", *packs]
        for goal in base_plan.ordered_goals(extra):
            if goal in base_plan.SCIENCE_EXTENSIONS:
                science.append(goal)
            row0 = max(bus.next_module_y, bus.next_west_y)
            bus.next_module_y = bus.next_west_y = row0 + 2
            for item in base_plan.side_lanes(goal):
                bus.add_lane(item, start=row0, source="side")
            for m in base_plan.section_for(goal, spm, book, packs=tuple(dict.fromkeys(science))) \
                    + (base_plan.bus_malls(book, None) if goal == host else []):
                bus.attach(m, side=side())
    except (KeyError, ValueError, AttributeError):
        return None
    west = max((bus.x - a.x for a in bus.attached if a.side == "west"), default=0)
    east = max((a.x + a.module.width for a in bus.attached if a.side == "east"), default=bus.east)
    rows = max((a.y + a.module.height for a in bus.attached), default=0) + 6
    head = 2 * base_plan.LANE_FURNACES + 6 + base_plan.COAL_ROWS
    width = max(west + east + 2, 2 * (base_plan.BLOCK_W + base_plan.BLOCK_GAP) + 2 * base_plan.BUS_MARGIN)
    return int(width), int(rows + head)


def check(raw: Mapping[str, Any], site: Mapping[str, Any], land: lands.Land, world: Mapping[str, Any],
          current: Mapping[str, Any] | None, reason: str = "") -> Option:
    """One plan from the architect, made safe: what fails a check is cut, and each cut is noted."""
    problems: list[str] = []
    book = ratios.Book.from_site(site)
    facts = rates.of(site)
    qualities = set(facts.qualities) if facts else set()
    known = {c.id for c in land.cells}
    claimed: dict[str, str] = {c: "bus" for c in _bus_cells(site, land, current)}
    old_ids = {s.get("id") for s in (current or {}).get("sections") or []}

    def take(cells: Any, owner: str, what: str) -> list[str]:
        kept = []
        for cell in fp._list(cells)[:12]:
            cid = str(cell).strip().upper()
            c = land.cell(cid)
            if cid not in known or c is None:
                problems.append(f"{owner}: {what} cell {cid} is not its own ground; left out")
            elif claimed.get(cid, owner) != owner:
                problems.append(f"{owner}: {what} cell {cid} is {claimed[cid]}'s; left out")
            elif c.water > WET * (c.box[2] - c.box[0]) * (c.box[3] - c.box[1]):
                problems.append(f"{owner}: {what} cell {cid} is mostly water; left out")
            else:
                claimed[cid] = owner
                kept.append(cid)
        return kept

    rows = [r for r in fp._list(raw.get("sections")) if isinstance(r, Mapping)]
    rows = sorted(enumerate(rows), key=lambda kv: (-float(kv[1].get("priority") or 0), kv[0]))
    sections, ids, cells_of = [], set(), {}
    for _, r in rows[:fp.MAX_SECTIONS]:
        sid = fp.slug(r.get("id") or r.get("kind"), ids)
        ids.add(sid)
        kind = r.get("kind") if r.get("kind") in fp.KINDS else "other"
        makes: dict[str, float] = {}
        for row in fp._list(r.get("makes"))[:fp.MAX_ITEMS]:
            item, rate = str((row or {}).get("item") or ""), (row or {}).get("per_second")
            if not isinstance(rate, (int, float)) or not 0 < rate <= 1000:
                problems.append(f"{sid}: {item} at {rate} a second left out")
            elif item not in ratios.RAW and book.recipe_for(item) is None:
                problems.append(f"{sid}: no recipe in this game makes {item}; left out")
            else:
                makes[item] = round(float(rate), 4)
        load = r.get("load")
        load = max(0.0, min(1.0, float(load))) if isinstance(load, (int, float)) else ledger.LOADS.get(kind)
        quality = r.get("quality") if r.get("quality") in qualities and r.get("quality") != "normal" else None
        if r.get("quality") not in (None, "", "normal") and quality is None:
            problems.append(f"{sid}: quality {r.get('quality')} is not one of this game's; built at normal")
        owner = "bus" if kind == "bus" else sid   # (the bus's ground is the bus's under any name)
        zone, room = take(r.get("zone"), owner, "zone"), take(r.get("room"), owner, "room")
        cells_of[sid] = (zone, room)
        s = {"id": sid, "kind": kind, "makes": makes, "load": load, "grow": r.get("grow") if r.get("grow") in fp.GROW
             else "none", "priority": round(max(0.0, min(1.0, float(r.get("priority") or 0))), 2),
             "why": str(r.get("why") or "")[:160], "quality": quality,
             "replaces": [str(x) for x in fp._list(r.get("replaces")) if x in old_ids][:4]}
        sections.append(s)
    zones = []
    for s in sections:
        zone, room = cells_of[s["id"]]
        for key, cells, share in (("zone", zone, 1.0), ("room", room, 0.5)):
            for n, box in enumerate(land.boxes(cells)):   # (cells that do not touch: a zone each, the biggest first)
                zid = f"{s['id']}-{key}" + (f"-{n + 1}" if n else "")
                left = lands.obstructions(world, box)
                zones.append({"id": zid, "box": list(box), "use": key, "for": s["id"], "clear": any(left.values()),
                              "left": left, "priority": round(s["priority"] * share, 2),
                              "why": (s["why"] or f"{s['kind']} section")[:160]})
                if not n:
                    s[key] = zid
    for k, keep in enumerate(fp._list(raw.get("keep"))[:8]):
        if not isinstance(keep, Mapping):
            continue
        owner = fp.slug(keep.get("for") or "later")
        for n, box in enumerate(land.boxes(take(keep.get("cells"), f"kept for {owner}", "kept"))):
            left = lands.obstructions(world, box)
            zones.append({"id": f"keep-{k + 1}" + (f"-{n + 1}" if n else ""), "box": list(box), "use": "keep",
                          "for": None, "clear": any(left.values()), "left": left,
                          "priority": round(0.5 * max(0.0, min(1.0, float(keep.get("priority") or 0))), 2),
                          "why": f"kept for {keep.get('for') or 'later'}"[:160]})
    lanes = []
    for lane in fp._list(raw.get("lanes"))[:fp.MAX_LANES]:
        item = str((lane or {}).get("item") or "")
        if item not in ratios.RAW and book.recipe_for(item) is None:
            problems.append(f"lane for {item}: no recipe in this game makes it; left out")
            continue
        lanes.append({"item": item, "belts": max(1, min(4, int(lane.get("belts") or 1))),
                      "for": fp.slug(lane.get("for") or "later")})
    plan = fp.new_plan(sections, source="architect", why=reason, site=site, label=str(raw.get("label") or ""),
                       summary=str(raw.get("summary") or ""), lanes=lanes, zones=zones[:fp.MAX_ZONES])
    problems += fp.complete_rates(plan, site, book)
    problems += _rules(plan, site, book)
    plan["notes"] = problems[:fp.MAX_LINES]
    return Option(plan, problems)


def _rules(plan: dict, site: Mapping[str, Any], book: ratios.Book) -> list[str]:
    """The plan against the builders and the ledger: the bus's one rate, what the path to space needs, the
    bus head's smelting, and the 85% line."""
    notes = []
    goals = set(fp.bus_goals())
    on_bus = [s for s in plan["sections"] if fp.on_bus(s)]
    science = {i: r for s in on_bus for i, r in (s.get("makes") or {}).items() if i in goals and i != "rocket-part"}
    if len({round(r, 3) for r in science.values()}) > 1:
        top = max(science.values())
        notes.append(f"the bus makes every pack at one rate: {top * 60:.3g} a minute for all of them")
    saved = site.get("base_plan") or {}
    bus = next((s for s in on_bus if s.get("kind") == "bus"), None)
    if bus is not None and not saved.get("stages") and science:
        bus["spm"] = bus_rate(60 * max(science.values()), science, book)
        box = fp.zone_box(plan, bus.get("zone"))
        size = bus_size(bus["spm"], [i for s in on_bus for i in s.get("makes") or {} if i in goals], book,
                        bool(team_area.team_of(site))) if box else None
        if box and size and (box[2] - box[0] < size[0] or box[3] - box[1] < size[1]):
            notes.append(f"the bus needs about {size[0]} x {size[1]} tiles and its zone is {box[2] - box[0]:.0f} x "
                         f"{box[3] - box[1]:.0f}: it goes where it fits")
    path = site.get("space_path") if isinstance(site.get("space_path"), Mapping) else {}
    planned = {i for s in plan["sections"] for i in s.get("makes") or {}}
    missing = [p for p in (path.get("packs") or {}) if p not in planned]
    if missing:
        notes.append("the path to space needs " + ", ".join(sorted(missing)[:4]) + ", which no section makes")
    for s in plan["sections"]:
        if not fp.builders(s):
            notes.append(f"{s['id']}: no builder for {s['kind']} sections yet; its ground is kept until one exists")
    return notes + fp.balance(plan, site)


def describe(option: Option) -> str:
    """An option as System One sees it."""
    plan = option.plan
    parts = [f"{option.label}: {plan.get('summary') or ''}".strip()]
    parts.append("Sections: " + "; ".join(
        f"{s['id']} ({s['kind']}" + (", " + ", ".join(f"{i} {r:.3g}/s" for i, r in list(s['makes'].items())[:2])
                                     if s.get("makes") else "") + ")" for s in plan["sections"][:10]) + ".")
    if option.problems:
        parts.append(f"The checks found {len(option.problems)} problems: " + "; ".join(option.problems[:2]) + ".")
    return " ".join(parts)[:600]


def pick(options: list[Option], site: Mapping[str, Any], chooser: Any = None,
         log: Callable[[str], None] = print) -> tuple[Option, str]:
    """System One chooses among the usable plans; without it (or when it fails) the one with fewest problems,
    the architect's own order breaking ties."""
    if len(options) == 1:
        return options[0], "one plan offered"
    if chooser is not None:
        choices = [{"id": f"plan-{k + 1}", "description": describe(o)} for k, o in enumerate(options)]
        try:
            picked, _ = chooser.choose(site, choices, question="factory-plan")
            k = int(str(picked).rsplit("-", 1)[-1]) - 1
            if 0 <= k < len(options):
                return options[k], "system one"
        except Exception as exc:
            log(f"{site.get('request_id')}: factory plan chooser failed ({str(exc)[:120]}); fewest problems")
    best = min(range(len(options)), key=lambda k: (len(options[k].problems), k))
    return options[best], "the rules: fewest problems"


class Architect:
    """Plans an engineer's factory through Codex with the designer's model (one call each time it is asked)."""

    def __init__(self, codex: Any, *, model: str = DESIGNER_MODEL, effort: str = "medium",
                 log: Callable[[str], None] = print, chunked: bool = True):
        self.codex, self.model, self.effort, self.log = codex, model, effort, log
        self.chunked = chunked   # the base designed chunk by chunk (plan_keeper), else a whole plan at a time

    def _limit(self) -> dict:
        """The background time limit, where the Codex client takes one (a test's stand-in may not)."""
        import inspect
        try:
            takes = "timeout_s" in inspect.signature(self.codex.ask).parameters
        except (TypeError, ValueError, AttributeError):
            takes = False
        return {"timeout_s": TIMEOUT_S} if takes else {}

    def __call__(self, site: Mapping[str, Any], world: Mapping[str, Any], current: Mapping[str, Any] | None,
                 reason: str, chooser: Any = None) -> tuple[dict, str]:
        land = lands.summarize(world, site)
        prompt = brief(site, land, current, reason)
        rid = site.get("request_id")
        self.log(f"{rid}: architect asked ({reason}); brief {len(prompt)} characters, {len(land.cells)} cells")
        answer = self.codex.ask(prompt, SCHEMA, model=self.model, effort=self.effort, system="architect",
                                purpose=reason, npc=site.get("npc_id"), **self._limit())
        offered = [p for p in fp._list(answer.get("plans"))[:MAX_PLANS] if isinstance(p, Mapping)]
        options = [check(p, site, land, world, current, reason) for p in offered]
        usable = [o for o in options if o.plan["sections"]]
        if not usable:
            raise ArchitectError("no usable plan" + (f": {options[0].problems[0]}" if options and options[0].problems
                                                      else ""))
        chosen, how = pick(usable, site, chooser, self.log)
        self.log(f"{rid}: architect offered {len(offered)} plans, {len(usable)} usable; {chosen.label!r} ({how}, "
                 f"{len(chosen.problems)} problems noted)")
        return chosen.plan, how

    def design_chunk(self, site: Mapping[str, Any], world: Mapping[str, Any], plan: Mapping[str, Any] | None,
                     chunk: Mapping[str, Any]) -> tuple[dict, list[str]]:
        """One chunk of the outlined base: its sections and their zones, checked as a whole plan is (cells of its
        own ground, one section to a cell, items the game's recipes make), as a small plan of their own to merge
        (factory_plan.revise_chunk). Low effort: one chunk is a small question."""
        land = lands.summarize(world, site)
        prompt = chunk_brief(site, land, plan, chunk)
        rid = site.get("request_id")
        reason = f"chunk {chunk.get('id')}"
        self.log(f"{rid}: architect asked for {reason}; brief {len(prompt)} characters")
        answer = self.codex.ask(prompt, CHUNK_SCHEMA, model=self.model, effort="low", system="architect",
                                purpose=reason, npc=site.get("npc_id"), **self._limit())
        raw = {"label": reason, "summary": "", "sections": fp._list(answer.get("sections"))[:CHUNK_SECTIONS],
               "lanes": [], "keep": []}
        option = check(raw, site, land, world, plan, reason)
        if not option.plan["sections"]:
            raise ArchitectError(f"no usable design for {reason}"
                                 + (f": {option.problems[0]}" if option.problems else ""))
        notes = [str(n)[:160] for n in fp._list(answer.get("notes"))[:3]]
        self.log(f"{rid}: architect designed {reason}: {len(option.plan['sections'])} sections, "
                 f"{len(option.plan.get('zones') or [])} zones ({len(option.problems)} problems noted)")
        return option.plan, notes + option.problems[:3]
