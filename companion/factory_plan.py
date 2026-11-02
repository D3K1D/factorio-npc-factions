"""An engineer's factory plan: the large sections of its factory, planned ahead and kept in the save.

The architect (companion/architect.py, the designer's model, asked rarely) plans the factory as sections.
Each says what it makes and takes and at what rates, how busy it is expected to run, which ground it
takes (its zone) and the room it keeps to grow, how it grows, its status, its priority and why. The plan
also keeps lanes on the bus for later, ground for sections still to come, and the ground to clear before
it is needed (the clearing planner reads those zones). The game keeps the plan with the engineer
(`/npc-plan factory`, mod/scripts/factory_plan.lua) and sends it back as site["factory_plan"].

The code builds the sections with the planners that exist. Sections on the main bus (science, the mall,
labs, its smelting and its growth) set the bus planner's targets (bus_targets) in place of its fixed
defaults; power and defense sections are built by their own planners; a kind no planner builds yet keeps
its ground reserved and cleared until one does. Progress comes from the factory ledger. A revision
becomes a list of changes: growing into the room kept, upgrading in place, and retiring what a new
section replaces once that one runs and the ledger shows nothing goes short without it.

    plan = plan_of(site)                     # the plan the game sent, or None
    bus_targets(site)                        # what the bus planner builds for it (None: its defaults)
    next_purpose(site, can)                  # the planned section to build next, and the goal that builds it
    due(site, plan, opening_over=True)       # why the architect should plan again now, or None
    refresh(site, plan, world)               # progress, the bus's ground, cleared zones
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from companion import ledger, rates, ratios, team_area

SCHEMA = "npc-factory-plan-v1"
# Kinds of section. A section of a bus kind without ground of its own goes on the main bus.
KINDS = ("bus", "science", "mall", "labs", "smelting", "rocket", "power", "defense", "mining", "intermediate",
         "oil", "other")
BUS_KINDS = frozenset({"bus", "science", "mall", "labs", "smelting", "rocket"})
# The catalog goals that build a section of a kind off the bus, first available first. A kind not here
# has no builder yet: its ground is kept and cleared, and it waits.
BUILDERS = {"power": ("power-plant", "steam-power", "power-expansion", "boiler-coal"),
            "defense": ("breach-repair", "plot-turrets", "plot-wall", "outpost-defense", "basic-defense")}
ON_THE_BUS = ("bus-base", "upgrade")   # goals that build or rework the bus's own sections
UNRESERVED = ("clear-area",)           # goals that build nothing: no ground is kept from them
PLACED_BY_RULE = ("defense",)          # section kinds whose buildings follow the plot's edges, not their cells
GROW = ("extend", "copy", "upgrade", "none")
STATUSES = ("planned", "waiting", "building", "built", "retiring", "retired")
FLUIDS = frozenset({"water", "steam", "crude-oil", "petroleum-gas", "light-oil", "heavy-oil", "lubricant",
                    "sulfuric-acid"})

# The game keeps at most these (factory_plan.lua): the host trims to them, so what it keeps is what is stored.
MAX_SECTIONS, MAX_ZONES, MAX_LANES, MAX_ITEMS, MAX_RETIRE, MAX_LINES = 24, 32, 24, 12, 8, 8
DONE = 0.95            # share of a section's planned rate built that makes it built
ROOM_ROWS = 64         # rows past the bus's planned end kept as its room to grow (one cell)
COOLDOWN = 60 * 3600   # ticks between two plans of one engineer's architect (seven engineers: at most seven an hour)
ASKED_COOLDOWN = 15 * 3600   # ...when its strategist asks
NEW_BUILDINGS = 4      # buildings research made ready since the plan, that count as a milestone
SUPPLY_DROP = 0.5      # an item's supply fallen by this share since the plan (biters, ore running out)
SUPPLY_FLOOR = 1.0     # items/s: an item that small does not count
PRIORITY_SHIFT = 0.5   # priorities moved this much in all since the plan
PRIORITY_ITEMS = 12    # the most wanted items the architect weighs (and their shift is measured on)
REISSUE = 30 * 3600    # ticks before a retirement the game did not carry out is asked again
MAX_MALLS = 4          # mall extensions down the bus


def _list(value: Any) -> list:
    # (the game's JSON writer turns an empty list into {})
    if isinstance(value, Mapping):
        return [] if not value else list(value.values())
    return list(value or [])


def _rates(value: Any) -> dict[str, float]:
    out = {}
    if isinstance(value, Mapping):
        for k, v in value.items():
            try:
                rate = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(rate) and rate > 0:
                out[str(k)] = rate
    return out


def slug(text: Any, taken: Iterable[str] = ()) -> str:
    base = re.sub(r"[^a-z0-9-]+", "-", str(text or "section").lower()).strip("-")[:40] or "section"
    out, n, used = base, 1, set(taken)
    while out in used:
        n += 1
        out = f"{base}-{n}"
    return out


# --- the plan as data -------------------------------------------------------

def normalize(raw: Any) -> dict | None:
    """A plan as the game sent it (or as the host made it) in one shape: lists as lists, rates as numbers."""
    if not isinstance(raw, Mapping) or not raw.get("version"):
        return None
    if raw.get("_n"):
        return raw  # (already in shape)
    plan = {k: v for k, v in raw.items() if k not in ("sections", "zones", "lanes", "retire", "notes", "changes")}
    plan["_n"] = True
    plan["version"] = int(raw["version"])
    plan["sections"] = []
    for s in _list(raw.get("sections")):
        if isinstance(s, Mapping) and s.get("id"):
            plan["sections"].append({**s, "makes": _rates(s.get("makes")), "takes": _rates(s.get("takes")),
                                     "replaces": [str(r) for r in _list(s.get("replaces"))]})
    plan["zones"] = [{**z, "box": [float(v) for v in _list(z.get("box"))]} for z in _list(raw.get("zones"))
                     if isinstance(z, Mapping) and z.get("id") and len(_list(z.get("box"))) == 4]
    plan["lanes"] = [dict(l) for l in _list(raw.get("lanes")) if isinstance(l, Mapping) and l.get("item")]
    plan["retire"] = [{**r, "what": [str(w) for w in _list(r.get("what"))]} for r in _list(raw.get("retire"))
                      if isinstance(r, Mapping) and r.get("section")]
    plan["notes"] = [str(n) for n in _list(raw.get("notes"))]
    plan["changes"] = [str(n) for n in _list(raw.get("changes"))]
    basis_ = raw.get("basis") if isinstance(raw.get("basis"), Mapping) else {}
    plan["basis"] = {**basis_, "packs": [str(p) for p in _list(basis_.get("packs"))],
                     "short": [str(p) for p in _list(basis_.get("short"))],
                     "supply": _rates(basis_.get("supply")), "priorities": _rates(basis_.get("priorities"))}
    return plan


_cache: dict[int, tuple[Any, dict]] = {}
_book: list = []


def vanilla() -> ratios.Book:
    if not _book:
        _book.append(ratios.Book.vanilla())
    return _book[0]


def plan_of(site: Mapping[str, Any]) -> dict | None:
    """The engineer's plan (site["factory_plan"]) in shape, or None when it has none."""
    raw = site.get("factory_plan") if isinstance(site, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    if raw.get("_n"):
        return raw
    found = _cache.get(id(raw))
    if found is None or found[0] is not raw:
        if len(_cache) > 64:
            _cache.clear()
        found = _cache[id(raw)] = (raw, normalize(raw))
    return found[1]


def bounded(plan: Mapping[str, Any]) -> dict:
    """The plan as the game keeps it (factory_plan.lua's limits), without the host's own marks."""
    def top(values: Mapping[str, float]) -> dict:
        best = sorted(values.items(), key=lambda kv: -kv[1])[:MAX_ITEMS]
        return {k[:64]: round(float(v), 4) for k, v in best}
    # (the outline is the host's: it is surveyed again from the map whenever needed, and would only crowd the command)
    out = {k: v for k, v in plan.items() if not str(k).startswith("_") and k != "outline"}
    out["sections"] = [{**{k: v for k, v in s.items() if v is not None and v != [] and not str(k).startswith("_")},
                        "makes": top(s.get("makes") or {}), "takes": top(s.get("takes") or {})}
                       for s in plan.get("sections") or []][:MAX_SECTIONS]
    out["zones"] = [{k: v for k, v in z.items() if v is not None} for z in plan.get("zones") or []][:MAX_ZONES]
    out["lanes"] = list(plan.get("lanes") or [])[:MAX_LANES]
    out["retire"] = list(plan.get("retire") or [])[:MAX_RETIRE]
    out["notes"] = [str(n)[:160] for n in plan.get("notes") or []][:MAX_LINES]
    out["changes"] = [str(n)[:160] for n in plan.get("changes") or []][:MAX_LINES]
    b = dict(plan.get("basis") or {})
    b["supply"], b["priorities"] = top(b.get("supply") or {}), top(b.get("priorities") or {})
    out["basis"] = {k: v for k, v in b.items() if v not in (None, [], {})}
    return out


def section(plan: Mapping[str, Any] | None, section_id: Any) -> dict | None:
    return next((s for s in (plan or {}).get("sections") or [] if s.get("id") == section_id), None)


def live(plan: Mapping[str, Any] | None) -> list[dict]:
    """Sections that are not retired (retiring ones still stand)."""
    return [s for s in (plan or {}).get("sections") or [] if s.get("status") != "retired"]


def on_bus(s: Mapping[str, Any]) -> bool:
    """A section the main bus builds: the bus itself, and bus kinds without ground of their own."""
    return s.get("kind") == "bus" or (s.get("kind") in BUS_KINDS and not s.get("zone"))


def zone_box(plan: Mapping[str, Any] | None, zone_id: Any) -> tuple | None:
    z = next((z for z in (plan or {}).get("zones") or [] if z.get("id") == zone_id), None) if zone_id else None
    return tuple(z["box"]) if z else None


def makeable(item: str, site: Mapping[str, Any]) -> bool:
    """Whether the engineer can make `item` now: a recipe for it is unlocked (or, when the site carries no
    recipes, the research of that name is done)."""
    unlocked = ratios.unlocked(site)
    if unlocked:
        return item in unlocked
    researched = set(site.get("researched") or ())
    return item == "automation-science-pack" or item in researched or (item == "rocket-part"
                                                                       and "rocket-silo" in researched)


def bus_goals() -> tuple[str, ...]:
    """What the bus planner can make on the bus: science packs and the rocket (its builder's vocabulary)."""
    from companion import base_plan, bus_planner
    return tuple(dict.fromkeys((*base_plan.TIERS, *bus_planner.EXTENSION_PACKS)))


def mall_products() -> tuple[str, ...]:
    from companion import mall
    return tuple(r for group in mall.GROUPS for r in group)


_weighing = threading.local()   # a priority read under way on this thread (see wanted)


def priorities(site: Mapping[str, Any]) -> dict[str, float]:
    """The engineer's deliberate priorities (site["priorities"]: what its strategist set) as name -> 0..1:
    a mapping of names to numbers or to rows with a priority, a list of such rows, or the strategist's rows
    with a level (companion/priorities.py: "high" and so on, read as the weight a setting of it carries).
    Empty without any. These move only when the strategist moves them, so the architect is asked again on
    them (the model's own weights shift with every request)."""
    raw = site.get("priorities") if isinstance(site, Mapping) else None
    rows: list[tuple[str, Any]] = []
    if isinstance(raw, Mapping):
        rows = [(str(k), v) for k, v in raw.items()]
    elif isinstance(raw, list):
        for r in raw:
            if isinstance(r, Mapping):
                name = r.get("item") or r.get("id") or r.get("name") or r.get("section")
                if name:
                    rows.append((str(name), r))
    out = {}
    for name, v in rows:
        if isinstance(v, Mapping):
            level = v.get("level")
            v = next((v[k] for k in ("priority", "value", "weight") if isinstance(v.get(k), (int, float))), None)
            if v is None and isinstance(level, str):
                from companion.priorities import SET_WEIGHT
                v = SET_WEIGHT.get(level)
        if isinstance(v, (int, float)) and math.isfinite(v):
            out[name] = float(v)
    top = max(out.values(), default=0.0)
    if top > 1:
        out = {k: v / top for k, v in out.items()}
    return {k: max(0.0, min(1.0, v)) for k, v in out.items()}


def wanted(site: Mapping[str, Any]) -> dict[str, float]:
    """What the engineer wants most now, for the architect's brief: the priority model's weights for its most
    wanted items (companion/priorities.py; read only, its hysteresis left alone), with its deliberate
    priorities over them. Not from inside the model itself: its goals ask the bus planner, whose targets
    weigh priorities again (each round branching, the integration's tests never finished)."""
    out: dict[str, float] = {}
    if not getattr(_weighing, "busy", False):
        _weighing.busy = True
        try:
            from companion import priorities as model
            out = {item: round(p.weight, 3) for item, p in model.assess(site, hold=False).ranked(PRIORITY_ITEMS)}
        except Exception:
            out = {}
        finally:
            _weighing.busy = False
    return {**out, **priorities(site)}


def effective_priority(s: Mapping[str, Any], site: Mapping[str, Any]) -> float:
    """The plan's priority for a section, moved halfway toward the engineer's own priority for it or for
    what it makes, when it has one."""
    own = float(s.get("priority") or 0.0)
    ranked = priorities(site)
    found = [ranked[n] for n in (s.get("id"), s.get("kind"), *(s.get("makes") or {})) if n in ranked]
    return 0.5 * own + 0.5 * max(found) if found else own


# --- the bus's targets ------------------------------------------------------

@dataclass
class BusTargets:
    spm: float                         # the bus's own science a minute (growth copies make it again)
    packs: tuple[str, ...]             # what the plan wants the bus to make, most important first
    growth: int                        # growth copies of the bus's science wanted
    reserve: int | None = None         # lane columns kept for later goals (None: the bus planner's own)
    mall: tuple[str, ...] | None = None   # what its mall makes (None: everything it can)
    mall_rates: dict | None = None     # items/s the plan wants of each mall product
    area: tuple | None = None          # where the bus goes, for its first stage


def bus_targets(site: Mapping[str, Any]) -> BusTargets | None:
    """The bus planner's targets from the plan's sections on the bus, or None without a plan (or with
    nothing on the bus): then the bus planner keeps its own defaults."""
    plan = plan_of(site)
    sections = [s for s in live(plan) if on_bus(s)]
    if not sections:
        return None
    from companion import growth
    goals = bus_goals()
    order = sorted(sections, key=lambda s: -effective_priority(s, site))
    wanted: dict[str, float] = {}
    for s in order:
        for item, rate in (s.get("makes") or {}).items():
            if item in goals:
                wanted[item] = max(wanted.get(item, 0.0), rate)
    science = [r for item, r in wanted.items() if item != "rocket-part"]
    total = 60 * max(science, default=0.0)
    bus = next((s for s in sections if s.get("kind") == "bus"), None)
    saved = site.get("base_plan") or {}
    if saved.get("stages") and saved.get("spm"):
        spm = float(saved["spm"])            # a bus being built keeps its rate; more comes as growth
    elif bus and bus.get("spm"):
        spm = float(bus["spm"])
    elif total:
        spm = max(5.0, round(total / 5) * 5)
    else:
        from companion import bus_planner
        spm = bus_planner.goal({**site, "factory_plan": None})[0]
    # Growth copies for the science that grows by copies (or into its room); what grows by upgrading in place
    # is left to the upgrade planner.
    copied = [r for s in sections if s.get("grow") in ("copy", "extend")
              for item, r in (s.get("makes") or {}).items() if item in goals and item != "rocket-part"]
    most = 60 * max(copied, default=0.0)
    # (each a section at the rate the bus aims at: a first bus may make part of it, bus_planner.FIRST_SHARE)
    aim = float(saved.get("aim") or spm) if saved.get("stages") and saved.get("spm") else spm
    copies = max(0, math.ceil((most - spm) / aim - 1e-3)) if most and spm and aim else 0   # (rates kept to 4 places)
    reserve = sum(int(l.get("belts") or 1) for l in plan.get("lanes") or []) or None
    products = mall_products()
    mall_rates: dict[str, float] = {}
    for s in order:
        if s.get("kind") == "mall":
            for item, rate in (s.get("makes") or {}).items():
                if item in products:
                    mall_rates[item] = max(mall_rates.get(item, 0.0), rate)
    mall = tuple(p for p in products if p in mall_rates) or None
    return BusTargets(spm=spm, packs=tuple(wanted), growth=min(growth.MAX_GROWTH, copies), reserve=reserve,
                      mall=mall, mall_rates=mall_rates or None, area=zone_box(plan, bus and bus.get("zone")))


def lanes_kept(site: Mapping[str, Any], default: int) -> int:
    """Lane columns a new bus keeps for later goals: the plan's lanes, never fewer than the bus planner's
    own reserve (its extensions need those)."""
    targets = bus_targets(site)
    return max(default, targets.reserve or 0) if targets else default


def mall_recipes(site: Mapping[str, Any], unlocked: Iterable[str] | None) -> list[str] | None:
    """The unlocked recipes a new bus's mall may make: only the products the plan names, when it names any
    the mall can make now (else all of them, as without a plan)."""
    targets = bus_targets(site)
    if not targets or not targets.mall:
        return None if unlocked is None else list(unlocked)
    if unlocked is None:   # (a site without its recipe list: everything counts as unlocked)
        return list(targets.mall)
    kept = [r for r in unlocked if r in targets.mall]
    return kept or list(unlocked)


def per_copy(item: str, site: Mapping[str, Any]) -> float:
    """Items/s one mall assembler makes of `item` (the mall's own machine, at full speed)."""
    from companion import mall
    speed = 0.5
    facts = rates.of(site)
    if facts and facts.entities.get(mall.MACHINE, {}).get("speed"):
        speed = float(facts.entities[mall.MACHINE]["speed"])
    for r in site.get("recipes") or []:
        if isinstance(r, Mapping) and r.get("name") == item and r.get("energy"):
            amount = next((float(p.get("amount") or 1) for p in _list(r.get("products")) if p.get("name") == item), 1.0)
            return speed / float(r["energy"]) * amount
    recipe = vanilla().recipes.get(item)
    return speed / recipe.energy * recipe.makes(item) if recipe else 1.0


def mall_extensions(site: Mapping[str, Any], targets: BusTargets | None = None) -> list[str]:
    """The next mall extension a finished bus is due (a goal for base_plan), or none: the plan's mall
    products the bus's malls do not make yet, and those it wants faster than its malls make them."""
    targets = targets or bus_targets(site)
    if not targets or not targets.mall:
        return []
    from companion import mall
    saved = site.get("base_plan") or {}
    extra = [str(g) for g in saved.get("extra") or ()]
    # (the bus's own mall once: its saved recipes, or its mall.OWN goal where it was planned with one)
    own = [] if any(mall.is_own(g) for g in extra) else [list(saved.get("mall") or [])]
    malls = own + [mall.extension_recipes(g) for g in extra if mall.is_extension(g)]
    if len(malls) - 1 >= MAX_MALLS:
        return []
    copies: dict[str, int] = {}
    for recipes in malls:
        for r in recipes:
            copies[r] = copies.get(r, 0) + 1
    want = []
    for item in targets.mall:
        if not makeable(item, site):
            continue
        rate = (targets.mall_rates or {}).get(item, 0.0)
        if copies.get(item, 0) == 0 or copies[item] * per_copy(item, site) < rate * 0.999:
            want.append(item)
    if not want:
        return []
    return [mall.extension_goal(len(malls) + 1, want)]


# --- what to build next -----------------------------------------------------

def builders(s: Mapping[str, Any]) -> tuple[str, ...]:
    """The goals that build a section: the bus's own (a section that grows by upgrading tries an upgrade in
    place first, when the upgrade planner finds one of its blocks flat out), else its kind's."""
    if on_bus(s):
        return ON_THE_BUS[::-1] if s.get("grow") == "upgrade" else ON_THE_BUS[:1]
    return BUILDERS.get(str(s.get("kind")), ())


def next_purpose(site: Mapping[str, Any], can: Callable[[str], bool]) -> tuple[str, dict] | None:
    """The goal that builds the most important planned section not built yet, with the section, or None
    when no planned section can be built now."""
    plan = plan_of(site)
    for s in sorted(live(plan), key=lambda s: -effective_priority(s, site)):
        if s.get("status") in ("built", "retiring"):
            continue
        for purpose in builders(s):
            if can(purpose):
                return purpose, s
    return None


def zones_to_clear(site: Mapping[str, Any]) -> list[dict]:
    """The plan's ground still to clear of trees, rocks and cliffs, most important first (the zones marked
    `clear`, as the clearing planner reads them from site["factory_plan"]["zones"])."""
    zones = (plan_of(site) or {}).get("zones") or []
    return sorted((z for z in zones if z.get("clear")), key=lambda z: -float(z.get("priority") or 0))


def reserved_boxes(plan: Mapping[str, Any] | None, purpose: Any) -> list[list[float]]:
    """The ground kept from a goal's planner: every zone of the plan except those of the sections that goal
    builds (and of retired sections, whose ground is free again), and the outline's advisory zones (a chunk's
    design for ground no goal builds yet: kept, it would shut the bus out of the ore it expands on). The bus
    builds its own power, so a power zone is not kept from it."""
    if not plan or purpose in UNRESERVED:
        return []
    mine, gone = set(), set()
    for s in plan.get("sections") or []:
        if s.get("status") == "retired":
            gone.add(s.get("id"))
        elif (purpose in ON_THE_BUS and (on_bus(s) or s.get("kind") == "power")) \
                or purpose in BUILDERS.get(str(s.get("kind")), ()):
            mine.add(s.get("id"))
    # (a section placed by rule along the plot's edges, its wall line and turrets, keeps no cells: they say
    # nothing of where it stands, and kept, they shut the bus out of its ore, run 26, 2026-10-25)
    ruled = {s.get("id") for s in plan.get("sections") or [] if s.get("kind") in PLACED_BY_RULE}
    kept = [z for z in plan.get("zones") or [] if z.get("for") not in mine | gone | ruled and not z.get("outline")]
    return [list(z["box"]) for z in kept if not region(z, plan.get("zones") or [])]


def region(zone: Mapping[str, Any], zones: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a zone is a region rather than ground for a section's buildings: it holds most of another
    section's zone. Kept as ground, such a zone (a plan saved with one box round a defense section's edge
    cells, the size of the plot) shut every other planner out of its own sections (run 25, 2026-10-25)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in zone["box"])
    except (KeyError, TypeError, ValueError):
        return False
    for other in zones:
        if other is zone or other.get("for") == zone.get("for") and zone.get("for") is not None:
            continue
        try:
            a0, b0, a1, b1 = (float(v) for v in other["box"])
        except (KeyError, TypeError, ValueError):
            continue
        area = max(0.0, a1 - a0) * max(0.0, b1 - b0)
        inside = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
        if area > 0 and inside >= 0.5 * area:
            return True
    return False


# --- rates ------------------------------------------------------------------

def bus_inputs() -> set[str]:
    """What the bus brings its sections: plates from its head, and the side lanes fed from outside."""
    from companion import base_plan
    side = {i for lanes in base_plan.SIDE_LANES.values() for i in lanes}
    return set(base_plan.PLATES) | (side - set(ratios.RAW))


def complete_rates(plan: dict, site: Mapping[str, Any], book: ratios.Book | None = None) -> list[str]:
    """Each section's takes worked out from what it makes, through the game's recipes (and the fuel its
    furnaces burn, from the game's facts). Returns what could not be worked out."""
    book = book or ratios.Book.from_site(site)
    facts = rates.of(site)
    machines = set(site.get("machines") or ()) or None
    allowed = machines | {"stone-furnace"} if machines else None
    made_by_plan = {i for s in live(plan) for i in s.get("makes") or {}}
    notes = []
    for s in plan.get("sections") or []:
        makes = s.get("makes") or {}
        if not makes:
            s["takes"] = {}
            continue
        inputs = bus_inputs() if on_bus(s) else (made_by_plan - set(makes)) | bus_inputs()
        try:
            need = ratios.solve(makes, book, inputs=inputs - set(makes), allowed=allowed)
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            notes.append(f"{s['id']}: its rates could not be worked out ({exc})")
            continue
        takes = {k: v for k, v in {**need.inputs, **need.raw}.items() if k not in FLUIDS and k not in makes}
        for step in need.steps:
            burn = facts.fuel_per_second(step.machine) if facts else None
            if burn is None and step.machine in ("stone-furnace", "steel-furnace"):
                burn = 0.09 / 4.0   # (a stone furnace's 90 kW on 4 MJ coal, when the game's facts are missing)
            if burn:
                takes[rates.FUEL] = takes.get(rates.FUEL, 0.0) + burn * step.machines
        s["takes"] = {k: round(v, 4) for k, v in takes.items() if v > 1e-6}
    return notes


def balance(plan: Mapping[str, Any], site: Mapping[str, Any]) -> list[str]:
    """The plan weighed as the ledger weighs a new segment: for each item, what the built factory and the
    planned sections make against what they claim on average (full speed times load), against the
    ledger's 85% line. Raw resources and fluids are mined and pumped where they are, so they are left out."""
    book = ledger.Ledger.from_site(site)
    supply: dict[str, float] = {}
    claims: dict[str, float] = {}
    for item in book.items():
        b = book.budget(item)
        supply[item], claims[item] = b.supply, b.average
    for s in live(plan):
        if s.get("status") == "retiring":
            continue
        left = 1.0 - float(s.get("progress") or 0.0)
        load = s.get("load")
        load = float(load) if isinstance(load, (int, float)) else ledger.LOADS.get(str(s.get("kind")), 1.0)
        for item, r in (s.get("makes") or {}).items():
            supply[item] = supply.get(item, 0.0) + r * left
        for item, r in (s.get("takes") or {}).items():
            claims[item] = claims.get(item, 0.0) + r * left * load
    notes = []
    for item, claim in sorted(claims.items(), key=lambda kv: -kv[1]):
        if item in ratios.RAW or item in FLUIDS or claim <= 1e-6:
            continue
        have = supply.get(item, 0.0)
        if have <= 0:
            notes.append(f"nothing makes {item} ({claim:.3g} a second claimed on average)")
        elif claim > ledger.AVERAGE_LIMIT * have:
            notes.append(f"{item}: {claim:.3g} of {have:.3g} a second claimed on average ({claim / have:.0%}, "
                         f"over the {ledger.AVERAGE_LIMIT:.0%} line)")
    return notes


# --- progress ---------------------------------------------------------------

def _blocks(site: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], ledger.Segment, tuple | None]]:
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    out = []
    for b in site.get("base") or []:
        if not isinstance(b, Mapping):
            continue
        box = _list(b.get("box"))
        world = (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy) if len(box) == 4 else None
        out.append((b, ledger.Segment.from_block(b), world))
    return out


def _inside(box: tuple | None, area: tuple | None) -> bool:
    if not box or not area:
        return False
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] <= cx < area[2] and area[1] <= cy < area[3]


def blocks_of(s: Mapping[str, Any], plan: Mapping[str, Any], site: Mapping[str, Any]) -> list:
    """The built blocks that serve a section: the bus's for a section on it, those standing in its zone or
    room for one with ground of its own, else those of its kind."""
    found = _blocks(site)
    if on_bus(s):
        return [f for f in found if f[0].get("purpose") == "bus-base"]
    areas = [zone_box(plan, s.get("zone")), zone_box(plan, s.get("room"))]
    if any(areas):
        return [f for f in found if any(_inside(f[2], a) for a in areas if a)]
    return [f for f in found if f[1].kind == s.get("kind")]


def blocks_to_retire(s: Mapping[str, Any], plan: Mapping[str, Any], site: Mapping[str, Any]) -> list:
    """The built blocks that are a section's own, to take down when it retires: those standing in its ground;
    on the bus (or with no ground) only blocks that make what it makes. Never the bus itself."""
    if s.get("kind") == "bus":
        return []
    areas = [zone_box(plan, s.get("zone")), zone_box(plan, s.get("room"))]
    found = _blocks(site)
    if any(areas):
        return [f for f in found if any(_inside(f[2], a) for a in areas if a)]
    makes = set(s.get("makes") or ())
    if not makes:
        return []
    return [f for f in found if set(f[1].makes) & makes and (f[0].get("purpose") == "bus-base") == on_bus(s)]


def progress(s: Mapping[str, Any], plan: Mapping[str, Any], site: Mapping[str, Any]) -> float:
    """How much of a section is built: what its blocks can make of each planned item against its planned
    rate (the least of them), the bus's stages done, or for a section with no rates whether anything of
    it stands."""
    if s.get("kind") == "bus" and on_bus(s):
        saved = site.get("base_plan") or {}
        stages = _list(saved.get("stages"))
        done = saved.get("done") if isinstance(saved.get("done"), Mapping) else {}
        return len([n for n in stages if n in done]) / len(stages) if stages else 0.0
    found = blocks_of(s, plan, site)
    makes = s.get("makes") or {}
    if not makes:
        return 1.0 if found else 0.0
    shares = []
    for item, rate in makes.items():
        have = sum(seg.makes.get(item, 0.0) for _, seg, _ in found)
        shares.append(min(1.0, have / rate) if rate > 0 else 1.0)
    return min(shares) if shares else 0.0


def buildable(s: Mapping[str, Any], site: Mapping[str, Any]) -> bool:
    """Whether some planner can build the section now: a builder for its kind, and (on the bus) what it
    makes unlocked and in the bus planner's vocabulary."""
    if not builders(s):
        return False
    if on_bus(s) and s.get("kind") != "bus":
        goals = set(bus_goals()) | set(mall_products()) | set(bus_inputs())
        made = [i for i in s.get("makes") or {} if i in goals]
        return not s.get("makes") or any(makeable(i, site) for i in made)
    return True


def status_of(s: Mapping[str, Any], share: float, site: Mapping[str, Any]) -> str:
    if s.get("status") in ("retiring", "retired"):
        return str(s["status"])
    if share >= DONE:
        return "built"
    if share > 0:
        return "building"
    return "planned" if buildable(s, site) else "waiting"


def refresh(site: Mapping[str, Any], plan: dict | None, world: Mapping[str, Any] | None = None) -> bool:
    """Bring the plan up to date with the factory: each section's progress and status (from the ledger),
    the ground the bus has taken and its room (from the map), and zones kept for later that are now clear
    (marked so; the clearing planner skips them). Returns whether anything changed worth storing."""
    if not plan:
        return False
    changed = False
    for s in plan.get("sections") or []:
        share = round(progress(s, plan, site) * 20) / 20   # (5% steps: stored only when it moves)
        status = status_of(s, share, site)
        if share != s.get("progress") or status != s.get("status"):
            changed |= status != s.get("status") or abs(share - float(s.get("progress") or 0)) >= 0.1
            s["progress"], s["status"] = share, status
    if world and isinstance(world, Mapping) and world.get("ok", True):
        changed |= _bus_ground(site, plan, world)
        changed |= _cleared(site, plan, world)
    return changed


def _bus_ground(site: Mapping[str, Any], plan: dict, world: Mapping[str, Any]) -> bool:
    """The ground the bus has taken (its blocks, and its lanes down to their planned end) as the bus
    section's zone, and the rows below that as its room to grow, kept from other plans."""
    from companion import land
    bus = next((s for s in live(plan) if s.get("kind") == "bus"), None)
    saved = site.get("base_plan") or {}
    if not bus or not saved.get("stages"):
        return False
    boxes = [box for b, _, box in _blocks(site) if b.get("purpose") == "bus-base" and box]
    if not boxes:
        return False
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    y1 = max(y1, float(saved.get("y") or 0) + float(saved.get("length") or 0))
    have = zone_box(plan, bus.get("zone"))
    if have:
        x0, y0, x1, y1 = min(x0, have[0]), min(y0, have[1]), max(x1, have[2]), max(y1, have[3])
    zone = [math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)]
    changed = False
    for key, box, use, share in (("zone", zone, "zone", 1.0),
                                 ("room", [zone[0], zone[3], zone[2], zone[3] + ROOM_ROWS], "room", 0.5)):
        zid = bus.get(key) or f"bus-{key}"
        z = next((z for z in plan["zones"] if z.get("id") == zid), None)
        if z is None:
            if len(plan["zones"]) >= MAX_ZONES:
                continue
            left = land.obstructions(world, box)
            plan["zones"].append({"id": zid, "box": box, "use": use, "for": bus["id"], "clear": sum(left.values()) > 0,
                                  "left": left, "priority": round(float(bus.get("priority") or 0.5) * share, 2),
                                  "why": f"the main bus {'and its lanes' if use == 'zone' else 'grows into these rows'}"})
            bus[key] = zid
            changed = True
        elif key == "zone" and [round(v) for v in z["box"]] != box:
            z["box"] = box
            changed = True
        elif key == "room" and _overlap(z["box"], zone):
            z["box"] = box   # the bus grew into its room: the room moves on below it
            changed = True
    return changed


def _overlap(a: Iterable[float], b: Iterable[float]) -> bool:
    a, b = list(a), list(b)
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _covers(world: Mapping[str, Any], box: Iterable[float]) -> bool:
    area = world.get("area")
    b = list(box)
    return bool(area) and area[0] <= b[0] and area[1] <= b[1] and b[2] <= area[2] and b[3] <= area[3]


def _cleared(site: Mapping[str, Any], plan: dict, world: Mapping[str, Any]) -> bool:
    from companion import land
    changed = False
    blast = makeable("cliff-explosives", site)
    for z in plan.get("zones") or []:
        if not z.get("clear") or not _covers(world, z["box"]):
            continue
        left = land.obstructions(world, tuple(z["box"]))
        if left != z.get("left"):
            z["left"] = left
            changed = True
        if not any(left.values()):
            z["clear"], z["cleared"] = False, int(site.get("tick") or 0)
            changed = True
        elif not left["trees"] and not left["rocks"] and not blast and float(z.get("priority") or 0) > 0.05:
            z["priority"] = 0.05   # only cliffs left, and nothing to blast them with yet: it can wait
            changed = True
    return changed


# --- when to plan again -----------------------------------------------------

def basis(site: Mapping[str, Any], *, paused: bool = False) -> dict:
    """What a plan is made on, kept with it to tell later when the factory or its goals moved enough to plan
    again: the science packs it can make, how many buildings and recipes are ready, what the ledger supplies
    and what is short, the priorities and how far the path to space has to go."""
    unlocked = ratios.unlocked(site)
    goals = set(bus_goals())
    packs = sorted(i for i in unlocked if i.endswith("-science-pack") or i in goals)
    facts = rates.of(site)
    ready = sum(1 for e in facts.entities.values() if e.get("ready")) if facts else None
    book = ledger.Ledger.from_site(site)
    supply = {i: round(book.budget(i).supply, 3) for i in book.items()}
    supply = dict(sorted(((k, v) for k, v in supply.items() if v > 0), key=lambda kv: -kv[1])[:MAX_ITEMS])
    path = site.get("space_path") if isinstance(site.get("space_path"), Mapping) else {}
    out = {"packs": packs[:12], "ready": ready, "recipes": len(site.get("recipes") or []), "supply": supply,
           "short": [b.item for b in book.short()][:8],
           "priorities": dict(sorted(priorities(site).items(), key=lambda kv: -kv[1])[:MAX_ITEMS]),
           "path": path.get("left"), "paused": paused or None}
    return {k: v for k, v in out.items() if v is not None}


def due(site: Mapping[str, Any], plan: Mapping[str, Any] | None, *, opening_over: bool,
        ai_ready: bool = True) -> str | None:
    """Why the architect should plan now, or None. The first plan once the opening is done and power runs;
    then a new one only when something moved since the plan was made: research made new science or a
    handful of buildings ready, an item the plan uses went short or its supply fell away, the priorities
    moved, or the AI is back after the rules planned through a pause. Never sooner than COOLDOWN after
    the last time it was asked."""
    if plan is None:
        return "the opening is done and power runs" if opening_over else None
    tick = int(site.get("tick") or 0)
    if tick - int(plan.get("asked") or plan.get("tick") or 0) < COOLDOWN:
        return None
    then, now = plan.get("basis") or {}, basis(site)
    new = sorted(set(now["packs"]) - set(then.get("packs") or ()))
    if new:
        return "research unlocked " + ", ".join(new)
    if isinstance(then.get("ready"), int) and isinstance(now.get("ready"), int) \
            and now["ready"] - then["ready"] >= NEW_BUILDINGS:
        return f"research made {now['ready'] - then['ready']} more buildings ready"
    used = {i for s in live(plan) for i in (*(s.get("makes") or {}), *(s.get("takes") or {}))}
    short = [i for i in now["short"] if i not in (then.get("short") or ()) and i in used]
    if short:
        return f"the ledger: {short[0]} is short"
    for item, before in (then.get("supply") or {}).items():
        after = now["supply"].get(item, 0.0)
        if before >= SUPPLY_FLOOR and after < (1 - SUPPLY_DROP) * before:
            return f"the ledger: {item} fell from {before:.3g} to {after:.3g} a second"
    old, new_p = then.get("priorities") or {}, now["priorities"]
    moved = sum(abs(new_p.get(k, 0.0) - old.get(k, 0.0)) for k in set(old) | set(new_p))
    if moved >= PRIORITY_SHIFT:
        top = max(new_p, key=new_p.get) if new_p else None
        return "the priorities moved" + (f" (now first: {top})" if top else "")
    if plan.get("source") == "rules" and then.get("paused") and ai_ready:
        return "the AI is back after a pause"
    return None


# --- making and revising plans ----------------------------------------------

def new_plan(sections: list[dict], *, source: str, why: str, site: Mapping[str, Any], label: str = "",
             summary: str = "", lanes: list | None = None, zones: list | None = None,
             notes: list | None = None, paused: bool = False) -> dict:
    tick = int(site.get("tick") or 0)
    for s in sections:
        s.setdefault("status", "planned")
        s.setdefault("progress", 0.0)
    return {"_n": True, "schema": SCHEMA, "version": 1, "tick": tick, "updated": tick, "asked": tick,
            "source": source, "label": label[:64], "why": why[:160], "summary": summary[:240],
            "sections": sections, "zones": list(zones or []), "lanes": list(lanes or []), "retire": [],
            "notes": list(notes or [])[:MAX_LINES], "changes": [], "basis": basis(site, paused=paused)}


def default_plan(site: Mapping[str, Any], *, why: str = "the rules", paused: bool = False) -> dict:
    """The plan the rules make (the AI paused or out of answers): the bus planner's own defaults written down
    as sections, so the bus builds as it would without a plan, and the plan's ground, lanes and clearing
    still work. Its science grows as the bus planner grows it (every growth copy), its mall is the bus's."""
    from companion import base_plan, bus_planner, growth, mall
    spm, _ = bus_planner.goal({**site, "factory_plan": None})
    first = bus_planner.first_rate(spm)
    saved = site.get("base_plan") or {}
    if saved.get("stages") and saved.get("spm"):
        first = float(saved["spm"])
        spm = float(saved.get("aim") or first)
    team = team_area.team_of(site)
    total = first + spm * growth.MAX_GROWTH   # (its first bus, then growth sections at what it aims at)
    goals = list(base_plan.TIERS[:2]) + (["military-science-pack"] if team else []) + [base_plan.TIERS[2], "rocket-part"]
    sections = [{"id": "bus", "kind": "bus", "spm": spm, "priority": 0.9, "grow": "copy",
                 "why": "the main bus: smelting at its head, science and the mall down it"}]
    for k, pack in enumerate(goals):
        rocket = pack == "rocket-part"
        sections.append({"id": slug(pack.replace("-science-pack", "-science")), "kind": "rocket" if rocket else "science",
                         "makes": {pack: base_plan.ROCKET_RATE if rocket else round(total / 60, 4)},
                         "load": ledger.LOADS["rocket" if rocket else "science"], "priority": round(0.85 - 0.05 * k, 2),
                         "grow": "none" if rocket else "copy",
                         "why": "the rocket" if rocket else "science on the bus, grown as the bus grows"})
    unlocked = {str(r.get("name")) for r in site.get("recipes") or [] if isinstance(r, Mapping)} or None
    recipes = list(saved["mall"]) if saved.get("stages") and saved.get("mall") is not None else mall.recipes_for(unlocked)
    if recipes:
        sections.append({"id": "mall", "kind": "mall", "makes": {r: round(per_copy(r, site), 4) for r in recipes},
                         "load": ledger.LOADS["mall"], "priority": 0.6, "grow": "copy",
                         "why": "buildings into chests for the engineer to build from"})
    try:
        need = ratios.solve({p: total / 60 for p in base_plan.TIERS}, ratios.Book.from_site(site),
                            inputs=set(base_plan.PLATES))
        for plate in base_plan.PLATES:
            if need.inputs.get(plate):
                sections.append({"id": slug(plate.replace("-plate", "-smelting")), "kind": "smelting",
                                 "makes": {plate: round(need.inputs[plate] * 1.1, 3)}, "priority": 0.85,
                                 "grow": "copy", "why": "plates for the bus: its head first, then each growth copy"})
    except (KeyError, ValueError):
        pass
    sections.append({"id": "power", "kind": "power", "priority": 0.8, "grow": "extend",
                     "why": "steam power sized for the whole base at full work"})
    if team:
        sections.append({"id": "defense", "kind": "defense", "priority": 0.95, "grow": "extend",
                         "why": "the plot's outer edges: turrets and the wall"})
    later = bus_planner.LATER_LANES + (bus_planner.MILITARY_LANES if team else 0)
    lanes, kept = [], 0
    for goal in base_plan.EXTENSIONS:
        for item in base_plan.side_lanes(goal):
            if kept < later and len(lanes) < MAX_LANES - 1:
                lanes.append({"item": item, "belts": 1, "for": slug(goal.replace("-science-pack", "-science"))})
                kept += 1
    if later > kept:
        lanes.append({"item": "intermediates", "belts": later - kept, "for": "bus"})
    plan = new_plan(sections, source="rules", why=why, site=site, label="the bus as planned by the rules",
                    summary=f"A main bus at {spm:g} science a minute, grown copy by copy; every pack on the path to "
                            "the rocket added as research allows.", lanes=lanes, paused=paused)
    complete_rates(plan, site)
    return plan


def diff(old: Mapping[str, Any], new: dict, site: Mapping[str, Any]) -> list[str]:
    """Carry what the old plan knew into the new one and say what changed: built sections keep their
    status, progress and ground; a section that wants more grows into its room (extend), copies itself
    (copy) or is upgraded in place (upgrade); one left out, or replaced, retires once what replaces it
    runs. Returns the changes, plainly."""
    changes: list[str] = []
    old_live = live(old)
    matched: dict[str, dict] = {}
    for s in new["sections"]:
        o = section(old, s["id"])
        if o is None or o.get("status") == "retired":
            # (a renamed section: the same kind making the same things)
            o = next((x for x in old_live if x["id"] not in matched and x.get("kind") == s.get("kind")
                      and set(x.get("makes") or {}) & set(s.get("makes") or {})), None)
        if o is None:
            changes.append(f"{s['id']}: new {s.get('kind')} section")
            continue
        matched[o["id"]] = s
        for key in ("status", "progress"):
            if o.get(key) is not None:
                s[key] = o[key]
        if o.get("status") in ("built", "building"):
            # built ground stays where it is
            for key in ("zone", "room"):
                if o.get(key) and o.get(key) != s.get(key):
                    box = zone_box(old, o[key])
                    if box and not any(z["id"] == o[key] for z in new["zones"]):
                        new["zones"].append({**next(z for z in old["zones"] if z["id"] == o[key]), "for": s["id"]})
                    s[key] = o[key]
            if s.get("kind") == "bus" and o.get("spm"):
                s["spm"] = o["spm"]
        for item, rate in (s.get("makes") or {}).items():
            before = (o.get("makes") or {}).get(item)
            if before and rate > before * 1.05:
                how = {"extend": "into the room kept for it", "copy": "by another copy",
                       "upgrade": "upgraded in place"}.get(str(s.get("grow")), "")
                changes.append(f"{s['id']}: {item} from {before:.3g} to {rate:.3g} a second" + (f", {how}" if how else ""))
                if s.get("status") == "built":
                    s["status"] = "building"
            elif before and rate < before * 0.95:
                changes.append(f"{s['id']}: {item} lowered from {before:.3g} to {rate:.3g} a second (nothing taken down)")
    # Replaced or left out: what stands retires once its replacement runs; what was only planned is dropped.
    replaced_by = {r: s["id"] for s in new["sections"] for r in s.get("replaces") or ()}
    for o in old_live:
        if o["id"] in matched and o["id"] not in replaced_by:
            continue
        after = replaced_by.get(o["id"]) or next(
            (s["id"] for s in new["sections"] if set(s.get("makes") or {}) & set(o.get("makes") or {})
             and s["id"] != o["id"]), None)
        if o.get("status") in ("built", "building", "retiring"):
            if o["id"] in matched:   # replaced by a section of the same name: the old one stands until then
                continue
            bus = o.get("kind") == "bus"   # a bus that stands is never taken down by a plan: it stays in it
            new["sections"].append(dict(o) if bus else {**o, "status": "retiring"})
            for key in ("zone", "room"):   # (its ground stays its own while it stands)
                z = next((z for z in old.get("zones") or [] if o.get(key) and z.get("id") == o[key]), None)
                if z and not any(n.get("id") == z["id"] for n in new["zones"]):
                    new["zones"].append(dict(z))
            if bus:
                changes.append(f"{o['id']}: kept (a built bus stays)")
                continue
            new["retire"].append({"section": o["id"], "after": after})
            changes.append(f"{o['id']}: retires" + (f" once {after} runs" if after else ""))
        else:
            changes.append(f"{o['id']}: dropped (not built yet)")
    for r in old.get("retire") or []:   # retirements under way carry on
        if not r.get("done") or section(new, r["section"]):
            if not any(x["section"] == r["section"] for x in new["retire"]):
                new["retire"].append(dict(r))
    for z in old.get("zones") or []:    # cleared ground stays known as cleared
        same = next((n for n in new["zones"] if n.get("id") == z.get("id")), None)
        if same and z.get("cleared") and same.get("box") == z.get("box"):
            same.update(clear=False, cleared=z["cleared"])
    return changes


def revise(old: Mapping[str, Any] | None, new: dict, site: Mapping[str, Any]) -> dict:
    """The new plan as the next version of the old one, with what changed."""
    if old is None:
        new["changes"] = [f"first plan: {len(new['sections'])} sections"]
        return new
    new["changes"] = diff(old, new, site)[:MAX_LINES]
    new["version"] = int(old.get("version") or 0) + 1
    refresh(site, new)
    return new


def binding(s: Mapping[str, Any]) -> bool:
    """Whether a section's zones keep ground: the bus builds it, or a goal builds its kind. Others (an ore patch's
    mining, smelting off the bus) are advisory until a builder exists for them."""
    return on_bus(s) or str(s.get("kind")) in BUILDERS


def revise_chunk(current: Mapping[str, Any], chunk: Mapping[str, Any], design: Mapping[str, Any],
                 site: Mapping[str, Any]) -> tuple[dict, list[str]]:
    """The kept plan with one chunk's design (architect.design_chunk) merged in: its sections' rates, load,
    growth, priority and reasons, and their zones; every other section and zone exactly as it was. A section
    being built or built keeps its rates and ground (only its priority and reasons change); a section new to the
    plan (an ore patch's mining) comes only from a patch's chunk, while the plan has room. Zones of sections no
    goal builds yet are advisory ("outline": no ground kept, nothing cleared for them). Returns the plan and what
    changed."""
    import copy
    plan = copy.deepcopy(dict(current))
    sections = plan.setdefault("sections", [])
    zones = plan.setdefault("zones", [])
    by_id = {s.get("id"): s for s in sections}
    allowed = set(chunk.get("section_ids") or [])
    tick = int(site.get("tick") or 0)
    changes = []
    for new in design.get("sections") or []:
        sid = new.get("id")
        old = by_id.get(sid)
        created = old is None
        if old is not None and sid not in allowed:
            changes.append(f"{sid}: not this chunk's; left as it was")
            continue
        if old is None:
            if chunk.get("kind") != "resource" or len(sections) >= MAX_SECTIONS:
                changes.append(f"{sid}: no room for a new section here; left out")
                continue
            old = {**{k: v for k, v in new.items() if k not in ("zone", "room")}, "status": "planned",
                   "progress": 0.0, "chunk": chunk.get("id")}
            sections.append(old)
            by_id[sid] = old
            changes.append(f"{sid}: new, from chunk {chunk.get('id')}")
        else:
            fixed = old.get("status") in ("building", "built", "retiring")
            keys = ("priority", "why") if fixed else ("makes", "load", "grow", "priority", "why", "quality")
            for k in keys:
                if k in new:
                    old[k] = new[k]
            old["chunk"] = chunk.get("id")
            changes.append(f"{sid}: designed with chunk {chunk.get('id')}" + (" (built: its rates kept)" if fixed else ""))
            if fixed:
                continue
        theirs = [dict(z) for z in design.get("zones") or [] if z.get("for") == sid]
        if not theirs:
            continue
        if not created and on_bus(old) and old.get("kind") != "bus":   # (ground of its own takes it off the bus)
            changes.append(f"{sid}: stays on the bus; the cells proposed for it left out")
            continue
        zones[:] = [z for z in zones if z.get("for") != sid]
        old.pop("zone", None)
        old.pop("room", None)
        zone = next((z for z in theirs if z.get("use") == "zone"), None)
        if zone is not None:
            old["zone"] = zone["id"]
        room = next((z for z in theirs if z.get("use") == "room"), None)
        if room is not None:
            old["room"] = room["id"]
        advisory = not binding(old)
        for z in theirs:
            if advisory:
                z["outline"], z["clear"] = True, False
            zones.append(z)
    if len(zones) > MAX_ZONES:   # the advisory ones go first
        keep = [z for z in zones if not z.get("outline")]
        zones[:] = (keep + [z for z in zones if z.get("outline")])[:MAX_ZONES]
        changes.append("zones past the plan's limit left out (advisory first)")
    plan["version"] = int(plan.get("version") or 0) + 1
    plan["updated"] = tick
    plan["source"] = "architect"
    plan["changes"] = changes[:MAX_LINES]
    complete_rates(plan, site)
    return plan, changes


def retirements(site: Mapping[str, Any], plan: dict | None) -> tuple[list[str], bool]:
    """What a submitted plan should retire now (the game's retire list: purposes and "stage:" names): the
    blocks of sections whose replacement runs, as long as the ledger shows that what they make stays
    covered without them. Retired sections whose blocks are gone are marked retired. Updates the plan;
    returns the names and whether the plan changed."""
    if not plan:
        return [], False
    tick = int(site.get("tick") or 0)
    out: list[str] = []
    changed = False
    for r in plan.get("retire") or []:
        old = section(plan, r.get("section"))
        if old is None or old.get("status") == "retired":
            continue
        found = blocks_to_retire(old, plan, site)
        if not found:
            old["status"], old["progress"] = "retired", 0.0
            r["done"] = r.get("done") or tick
            changed = True
            continue
        after = section(plan, r.get("after")) if r.get("after") else None
        if after is not None and after.get("status") != "built":
            continue
        if r.get("done") and tick - int(r["done"]) < REISSUE:
            continue
        ids = {id(b) for b, _, _ in found}
        rest = ledger.Ledger(seg for b, seg, _ in _blocks(site) if id(b) not in ids)
        if any(rest.budget(i).average > rest.budget(i).supply for i in old.get("makes") or {}):
            continue   # something still needs what it makes: it stays
        names = [f"stage:{b['stage']}" if b.get("stage") else str(b.get("purpose")) for b, _, _ in found]
        names = [n for n in dict.fromkeys(names) if n and n != "None"][:16]
        if not names:
            continue
        r["what"], r["done"] = names, tick
        out += names
        changed = True
    return list(dict.fromkeys(out)), changed


# --- for the architect's brief ------------------------------------------------

def describe(plan: Mapping[str, Any] | None, cells_of: Callable[[Any], str] | None = None) -> list[str]:
    """The plan in a few plain lines: each section, the lanes and ground kept, what is retiring, the notes."""
    if not plan:
        return []
    def where(s: Mapping[str, Any]) -> str:
        parts = []
        for key, word in (("zone", "zone"), ("room", "room")):
            box = zone_box(plan, s.get(key))
            if box:
                parts.append(f"{word} {cells_of(box) if cells_of else [round(v) for v in box]}")
        return ", ".join(parts) or ("on the bus" if on_bus(s) else "no ground yet")
    lines = [f"v{plan.get('version')} by the {plan.get('source') or 'architect'} at minute "
             f"{int(plan.get('tick') or 0) // 3600}: {plan.get('label') or ''}. {plan.get('summary') or ''}".strip()]
    for s in plan.get("sections") or []:
        makes = ", ".join(f"{i} {r:.3g}/s" for i, r in (s.get("makes") or {}).items())
        load = s.get("load")
        lines.append(f"- {s['id']} [{s.get('kind')}] " + (f"makes {makes}; " if makes else "")
                     + (f"load {float(load):.0%}; " if isinstance(load, (int, float)) else "")
                     + f"{where(s)}; grows {s.get('grow') or 'none'}; {s.get('status') or 'planned'} "
                     + f"{float(s.get('progress') or 0):.0%}; priority {float(s.get('priority') or 0):.2f}")
    if plan.get("lanes"):
        lines.append("Bus lanes kept: " + ", ".join(f"{l['item']} x{l.get('belts', 1)}" for l in plan["lanes"]))
    keep = [z for z in plan.get("zones") or [] if z.get("use") == "keep"]
    if keep:
        lines.append("Ground kept: " + "; ".join(f"{cells_of(z['box']) if cells_of else z['id']} for {z.get('for') or 'later'}"
                                                 for z in keep))
    pending = [z for z in plan.get("zones") or [] if z.get("clear")]
    if pending:
        lines.append(f"Ground to clear: {len(pending)} zones")
    for r in plan.get("retire") or []:
        if not (section(plan, r["section"]) or {}).get("status") == "retired":
            lines.append(f"Retiring {r['section']}" + (f" once {r['after']} runs" if r.get("after") else ""))
    if plan.get("notes"):
        lines.append("Notes from the checks: " + "; ".join(plan["notes"][:6]))
    return lines
