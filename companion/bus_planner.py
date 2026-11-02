"""Turning the base plan into build requests, one stage at a time.

The goal is sized by difficulty, personality and progress (thinking ahead: lane
columns are reserved for the lanes later goals will add), tidy engineers build
a bus while chaotic ones keep building wherever there is room, and the game
remembers the plan (`base_plan` in the site export) between stages. With a factory plan (factory_plan.py)
its sections on the bus set the targets instead: the rate, the packs, the lanes kept, the mall and its
extensions, how far it grows and where it goes. The last
stage names the older builds the finished bus supersedes; the game takes them
down unless something it keeps still depends on them, and some engineers keep
them anyway (hoarders keep everything, traditional and cautious ones their first
furnaces).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from typing import Any, Callable, Mapping

from companion import (background, belt_flow, belt_lines, construction, factory_plan, growth, lane_ledger, ledger,
                       layout_planner,
                       outpost, plate_feed, team_area)
from companion.base_plan import FEED_SHARE, FURNACE_PLATES, PLATES, blocked_tiles, build_cost, plan_bus_base
from companion.ratios import solve
from companion import mall as malls
from companion.mall import recipes_for
from companion.module_library import ModuleLibrary
from companion.style import Style, style_for
from companion.modules import ASSEMBLER, loader_limit
from companion.smelting import footprint
from companion.ratios import Book

BASE_SPM = {"easy": 10.0, "normal": 15.0, "hard": 30.0}
# The first bus makes this share of the science it aims at; growth sections below it, each at the rate it aims at, add
# the rest and more (the user, 2026-10-26: "small builds early, grown in place"; a whole first bus for 20 a minute was
# 2417 buildings, run 57's map, and its smelting stage alone took 33 game minutes to build).
FIRST_SHARE = 0.5
# Columns kept for later goals. Blue: coal, plastic, sulfur, steel, red circuits, engines, blue packs.
# The rocket: rocket fuel, bricks, iron ore, processing units, LDS, electric engines, concrete, foundations.
LATER_LANES = 7 + 8
# The team world's military science too: coal, stone bricks, steel, piercing rounds, military packs.
MILITARY_LANES = 5
EXTENSION_PACKS = ("chemical-science-pack", "rocket-part", "military-science-pack")   # bus extensions (new lanes fed from outside)
PURPOSE = "bus-base"
EXPAND_RETRY_TICKS = 216000   # an expansion with no room is tried again after an hour (ground cleared, research)
PLACES = 3              # places weighed for a new bus: where it first fits, and others (place_bus)
PLACE_SECONDS = 40.0    # what finding and laying out the others may take (a bus plan runs in the background)
# What a finished bus does instead: plates, and the red and green science it carries.
SUPERSEDED = ("iron-smelting-start", "iron-smelting-expansion", "copper-smelting-start",
              "electric-iron-mining", "electric-copper-mining", "plate-belts")
SCIENCE_LINES = {"automation-science-pack": "red-science-automation",
                 "logistic-science-pack": "green-science-automation"}
OFF_BUS_SCIENCE = ("blue-science-automation",)   # science the bus does not make yet: it needs the old lab
FIRST_BUILD = "iron-smelting-start"
# Hand-fed smelting a bus smelting stage replaces: taken down as soon as that stage runs, not when the
# whole bus is done (keeping burner pairs fuelled by hand takes most of an engineer's day).
EARLY_RETIRE = {"smelting-iron-plate": ("iron-smelting-start", "iron-smelting-expansion", "electric-iron-mining"),
                "smelting-copper-plate": ("copper-smelting-start", "electric-copper-mining")}


def first_rate(aim: float) -> float:
    """Science a minute the first bus is built for, of the `aim`: FIRST_SHARE of it, in fives, never under five."""
    return max(5.0, float(round(float(aim) * FIRST_SHARE / 5) * 5))


def growth_wanted(site: Mapping[str, Any], saved: Mapping[str, Any]) -> int:
    """Growth sections the bus grows by, each adding the science it aims at (saved `aim`, the rate it was first planned
    for; a bus planned before its first share was kept aims at its own rate): as many as the factory plan wants
    (factory_plan.bus_targets; without a plan, MAX_GROWTH), and one at least while the bus makes less than its aim."""
    spm = float(saved.get("spm") or 0)
    aim = float(saved.get("aim") or spm)
    targets = factory_plan.bus_targets(site)
    copies = targets.growth if targets else growth.MAX_GROWTH
    return max(copies, 1 if spm < aim else 0, min(growth.MAX_GROWTH, tier_sections(site, saved, spm, aim)))


def tier_sections(site: Mapping[str, Any], saved: Mapping[str, Any], spm: float, aim: float) -> int:
    """Growth sections the science path wants of the bus's packs (companion/tier_path.py: a pack it makes falling
    short of what the research ahead takes), each adding `aim` a minute after the bus's own `spm`."""
    if spm <= 0 or aim <= 0:
        return 0
    try:
        from companion import tier_path
        path = tier_path.assess(site)
        # (the packs its growth sections make: growth.TIERS; grey on the bus is grown by a block beside it)
        made = [str(p) for p in [*(saved.get("packs") or ()), *(saved.get("extra") or ())]
                if path.tier(str(p)) and str(p) in growth.TIERS]
        most = max((path.due(p).rate * 60 for p in made if path.due(p).due), default=0.0)
    except Exception:   # (a signal: the factory plan's count stands without it)
        return 0
    return max(0, math.ceil((most - spm) / aim - 1e-3))


def due_now(site: Mapping[str, Any]) -> bool:
    """The bus is the base's next growth: its engineer builds one, it has not failed here, the first red line by
    machine stands, and the bus is not finished. From then on its research comes first and the plates grow as its
    own smelting heads, not as arrays apart from it (the first lane followed them at minute 43, F-21; the user:
    "planned out like I did but build it out as it goes, 1 lane can still be used")."""
    from companion import catalog_planner
    if not wants_a_bus(site) or "bus-base" in {str(p) for p in site.get("failed_purposes") or ()}:
        return False
    if not catalog_planner.has_built("red-science-automation", catalog_planner.built_purposes(site)):
        return False
    saved = site.get("base_plan") or {}
    return not saved.get("stages") or bool(remaining(site))


def wants_a_bus(site: Mapping[str, Any]) -> bool:
    """Chaotic engineers do not plan a bus; everyone else does once they can."""
    traits = {str(t) for t in (site.get("personality") or {}).get("traits") or []}
    return "chaotic" not in traits


def goal(site: Mapping[str, Any]) -> tuple[float, tuple[str, ...]]:
    """Science per minute and packs: difficulty sets the base, ambition scales it, research decides
    which packs are in reach. With a factory plan: its rate, and the packs it plans that can be made now."""
    targets = factory_plan.bus_targets(site)
    if targets is not None:
        packs = tuple(p for p in targets.packs if factory_plan.makeable(p, site)) or ("automation-science-pack",)
        if packs == ("automation-science-pack",) and block_for_green(site, targets.spm):
            packs = (*packs, "logistic-science-pack")   # (the user's block makes both: planned whole, U-2)
        return targets.spm, packs
    axes = (site.get("personality") or {}).get("axes") or {}
    ambition = float(axes.get("ambition", 0.5) or 0.5)
    spm = BASE_SPM.get(str(site.get("difficulty", "normal")), 15.0) * (0.6 + 0.8 * ambition)
    spm = max(5.0, round(spm / 5) * 5)
    researched = set(site.get("researched") or [])
    # (planned whole: green too once its research is under way or next, its stages coming long after it lands; a bus
    # planned at minute 18 for red alone left green to an extension after its labs, past minute 60, F-21)
    coming = {str(r) for r in site.get("coming_recipes") or ()}
    packs = ["automation-science-pack"]
    if "logistic-science-pack" in researched or "logistic-science-pack" in coming or block_for_green(site, spm):
        packs.append("logistic-science-pack")
        if team_area.team_of(site) and "military-science-pack" in researched:
            packs.append("military-science-pack")   # the team world arms first: grey packs before blue
        if "chemical-science-pack" in researched:
            packs.append("chemical-science-pack")
            if "rocket-silo" in researched:
                packs.append("rocket-part")   # not a pack, but the next goal the bus grows for
    return spm, tuple(packs)


def block_for_green(site: Mapping[str, Any], spm: float) -> bool:
    """Whether the library holds a block the bus can take for red and green both (science_blocks.bus_block, the
    user's science book, U-2): the bus is then planned with green from the start, the block its first science stage,
    built once its recipes are researched (_ready_stage)."""
    from companion import science_blocks
    return science_blocks.bus_block(("automation-science-pack", "logistic-science-pack"), Book.from_site(site),
                                    spm / 60, _unlocked(site) or None) is not None


def _ready_stage(site: Mapping[str, Any], layout: Any, done: Mapping[str, Any], stage: Any) -> Any:
    """The stage to build: `stage`, else, while what it makes is not researched yet (a science block planned with
    green from the start, U-2), the next not built whose recipes all are; a PlanError when none is."""
    have = _unlocked(site)

    def locked(s: Any) -> list[str]:
        return sorted({str(e.recipe) for e in s.entities if getattr(e, "recipe", None)} - have) if have else []
    waits = locked(stage) if stage is not None else []
    if not waits:
        return stage
    later = next((s for s in layout.stages if s.name not in done and not locked(s)), None)
    if later is None:
        raise layout_planner.PlanError(f"{stage.name} waits for research of {', '.join(waits)}")
    layout.notes.append(f"research first: {stage.name} waits for {', '.join(waits)}, {later.name} goes first")
    return later


BLUE_SUPERSEDED = ("blue-science-automation", "blue-supply-automation", "engine-assembly", "steel-smelting",
                   "research-lab", "red-science-automation", "green-science-automation", "plate-belts",
                   "military-science-automation")


def extendable(site: Mapping[str, Any]) -> tuple[str, ...]:
    """New science packs a finished bus can be extended for (blue, once researched)."""
    saved = site.get("base_plan") or {}
    if not saved.get("stages") or not saved.get("packs") or (remaining(site) and not science_runs(site)):
        return ()
    have = set(saved.get("packs") or ()) | set(saved.get("extra") or ())
    _, packs = goal(site)
    new = tuple(p for p in packs if p not in have)
    if new:
        return new
    # The factory plan's mall wants more than the bus's malls make: another mall down the bus.
    targets = factory_plan.bus_targets(site)
    more = factory_plan.mall_extensions(site, targets) if targets else []
    if more:
        return tuple(more)
    # Nothing new to research for: grow, a section at a time (more of the same science, more smelting), as
    # far as the factory plan wants (without one: as far as growth goes).
    grown = [g for g in saved.get("extra") or () if growth.is_growth(str(g))]
    if len(grown) < growth_wanted(site, saved):
        science = [p for p in have if p in growth.TIERS]
        return (growth.goal_name(len(grown) + 1, science),)
    return ()


def superseded(site: Mapping[str, Any], packs: tuple[str, ...]) -> list[str]:
    """The builds this engineer will take down once its bus is finished."""
    traits = {str(t) for t in (site.get("personality") or {}).get("traits") or []}
    if "hoarder" in traits:
        return []
    done = {str(p) for p in site.get("completed_purposes") or []}
    out = [p for p in SUPERSEDED if p in done]
    out += [SCIENCE_LINES[p] for p in packs if SCIENCE_LINES.get(p) in done]
    if "research-lab" in done and not done & set(OFF_BUS_SCIENCE):
        out.append("research-lab")
    if traits & {"traditionalist", "cautious"}:
        out = [p for p in out if p != FIRST_BUILD]
    return out


def retired_early(site: Mapping[str, Any], stage: str) -> list[str]:
    """The hand-fed smelting a finished bus smelting stage replaces (see EARLY_RETIRE)."""
    traits = {str(t) for t in (site.get("personality") or {}).get("traits") or []}
    if "hoarder" in traits:
        return []
    done = {str(p) for p in site.get("completed_purposes") or []}
    out = [p for p in EARLY_RETIRE.get(stage, ()) if p in done]
    if traits & {"traditionalist", "cautious"}:
        out = [p for p in out if p != FIRST_BUILD]
    return out


def superseded_blue(site: Mapping[str, Any]) -> list[str]:
    """Once the bus makes blue packs: the old science lines and labs, and the bus's first labs (red and
    green only), make way for the new ones."""
    traits = {str(t) for t in (site.get("personality") or {}).get("traits") or []}
    if "hoarder" in traits:
        return []
    done = {str(p) for p in site.get("completed_purposes") or []}
    out = [p for p in (*SUPERSEDED, *BLUE_SUPERSEDED) if p in done]
    if traits & {"traditionalist", "cautious"}:
        out = [p for p in out if p != FIRST_BUILD]
    return list(dict.fromkeys(out)) + ["stage:bus-labs"]


def science_runs(site: Mapping[str, Any]) -> bool:
    """The bus's own science runs: every labs section it plans stands (its later rows, a mall or more smelting, may
    still be to come: it grows meanwhile, rather than waiting for the whole bus, F-18)."""
    saved = site.get("base_plan") or {}
    done = saved.get("done") or {}
    labs = [n for n in saved.get("stages") or () if str(n).startswith(LABS_STAGE)]
    return bool(labs) and all(n in done for n in labs)   # (every labs section it plans: an extension's too)


def remaining(site: Mapping[str, Any]) -> list[str]:
    saved = site.get("base_plan") or {}
    done = saved.get("done") or {}
    return [s for s in saved.get("stages") or [] if s not in done]


def lean_bus(world: Mapping[str, Any], book: Book, kwargs: Mapping[str, Any], fewest: int) -> Any:
    """Where a bus keeping lanes for all later goals fits nowhere: the widest that does, from the narrowest that
    carries today's goals (a bus with too few lanes for them fails at its dry run, before any ground is
    searched) up, or None when even that fits nowhere. A bus that fits beats none (run 19, 2026-10-25: 26
    columns, 20 of them for later, about 80 tiles wide, fit nowhere); the widest that fits keeps what lanes it
    can for later (run 23's plot, cut into strips by 439 cliffs: 14 of 26 fit, where the narrowest kept 6). The widths
    are tried by where the bus's buildings fit only (a probe), and the bus laid out once, at the widest: laying the
    whole base out for every width tried took most of the 7 to 8 minutes of run 55's bus plans (2026-10-26)."""
    wide = int(kwargs["columns"])

    def fits(columns: int) -> tuple[bool, str]:
        try:
            plan_bus_base(world, book, **dict(kwargs, columns=columns, fit_only=True))
            return True, ""
        except ValueError as exc:
            return False, str(exc)
    low, ok = max(2, fewest), False
    while low < wide:   # the narrowest that carries today's lanes
        ok, why = fits(low)
        if ok or "column" not in why:
            break
        low += 1
    if not ok:
        return None   # (narrow enough and still no room: a wider one will not fit either)
    lo, hi = low, wide - 1   # then the widest that still fits (wider is harder to fit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid)[0]:
            lo = mid
        else:
            hi = mid - 1
    best = None
    for columns in range(lo, low - 1, -1):   # (a width whose layout fails after all: the next narrower)
        try:
            best = plan_bus_base(world, book, **dict(kwargs, columns=columns))
            break
        except ValueError:
            continue
    if best is None:
        return None
    why = cliffs_in_way(world, book, kwargs)
    best.notes.append(f"the bus keeps {columns} of {wide} lanes: a wider one fit nowhere"
                      + (f" ({why})" if why else ""))
    return best


def first_science_cost(layout: Any, book: Book, home: tuple[float, float]) -> float:
    """What a bus layout's stages take to build up to its first labs: plates and the walk out (base_plan.build_cost)."""
    entities = []
    for stage in layout.stages:
        entities += stage.entities
        if stage.name.startswith("bus-labs"):
            break
    return build_cost(entities, book, home)


def shortfalls(layout: Any) -> int:
    """What a bus layout goes without: its notes (a route or field that did not fit, coal or items by hand, ground kept
    for later crossed, no power plant), but for how many lanes it keeps (lean_bus)."""
    return sum(1 for n in layout.notes if not n.startswith("the bus keeps "))


def place_bus(world: Mapping[str, Any], book: Book, layout: Any, kwargs: Mapping[str, Any], fewest: int) -> Any:
    """Where a new bus goes: of the place it first fits (`layout`, laid out with `kwargs`) and up to PLACES - 1 others,
    the one with the fewest shortfalls (a layout without a route or its power looks cheap) that takes least to build
    before its first science (first_science_cost). Each other is the widest bus, from the first one's width down to
    `fewest` lanes, that fits away from the places weighed so far (find_fit's `avoid`). No new search starts after
    PLACE_SECONDS (one under way runs on: a layout with long feed and ore routes takes up to about half a minute). The
    fit alone takes the place nearest its anchor (the ore) for the widest bus that fits anywhere, and places about as
    near can differ a lot: run 52's map (2026-10-26) had its first fit 255 tiles from home at 20 lanes (6116 plates
    and walk before its labs) and another 136 from home at 19 (5229)."""
    home = (float(world["home"]["x"]), float(world["home"]["y"]))
    started = time.monotonic()
    heads = [(layout.bus.x, layout.bus.y)]
    best, best_key = layout, (shortfalls(layout), first_science_cost(layout, book, home))
    first_short, first_cost = best_key
    width = int(kwargs.get("columns") or layout.columns)
    while len(heads) < PLACES and time.monotonic() - started < PLACE_SECONDS:
        found = None
        for columns in range(width, max(2, fewest) - 1, -1):
            if time.monotonic() - started >= PLACE_SECONDS:
                break
            try:
                plan_bus_base(world, book, **dict(kwargs, columns=columns, avoid=tuple(heads), fit_only=True))
            except ValueError:
                continue
            found = columns
            break
        if found is None or time.monotonic() - started >= PLACE_SECONDS:
            break
        try:
            with plate_feed.searching():   # (its own feed searches: weighed as the first was)
                other = plan_bus_base(world, book, **dict(kwargs, columns=found, avoid=tuple(heads)))
        except ValueError:
            break
        heads.append((other.bus.x, other.bus.y))
        key = (shortfalls(other), first_science_cost(other, book, home))
        if key < best_key:
            best, best_key = other, key
    if best is not layout:
        why = (f"takes least to build before its first science ({best_key[1]:.0f} plates and walk, {first_cost:.0f} "
               f"where it first fit)" if best_key[0] == first_short else
               f"goes without least ({best_key[0]} shortfalls, {first_short} where it first fit)")
        best.notes.append(f"of {len(heads)} places for the bus, {best.bus.x},{best.bus.y} with {best.columns} lanes {why}")
    return best


def cliffs_in_way(world: Mapping[str, Any], book: Book, kwargs: Mapping[str, Any]) -> str:
    """Why a full bus fit nowhere, when cliffs are why: it fits once they are gone (blasted with cliff explosives,
    once those can be made)."""
    clear = {**world, "entities": [e for e in world.get("entities") or [] if e.get("t") != "cliff"]}
    try:
        plan_bus_base(clear, book, **dict(kwargs, fit_only=True))
    except ValueError:
        return ""
    return "cliffs in the way: it fits once they are blasted"


def plan_stage(site: Mapping[str, Any], world: Mapping[str, Any], book: Book | None = None,
               library: ModuleLibrary | None = None) -> dict:
    """The next stage of this engineer's bus base as a plan in export-local coordinates. Proven module
    designs from `library` may replace built-in modules; the choice is kept for the whole bus."""
    with plate_feed.searching():
        plan = _stage(site, world, book, library)[0]
    return {k: v for k, v in plan.items() if k != LANE_ASKS}


def signature(site: Mapping[str, Any]) -> str:
    """What a bus stage is planned from, besides the map: where the site is (plans are in its local tiles), the bus as
    kept, its goal and extensions, the factory plan's targets for it (rate, ground, lanes kept, mall), the power rows,
    and for a new bus its mall's recipes, its machine and loaders (a bus being built keeps those it was planned with).
    Two requests alike in these plan the same stage. (Which older builds it retires is worked out again by the request
    that takes it: retirement_for.)"""
    from dataclasses import asdict
    targets = factory_plan.bus_targets(site)
    saved = site.get("base_plan") or {}
    parts = [site.get("origin"), saved, goal(site), extendable(site), asdict(targets) if targets else None,
             site.get("power_rows") or ()]
    if not saved.get("stages"):
        machines = site.get("machines") or {}
        parts += [recipes_for(factory_plan.mall_recipes(site, _unlocked(site) or None)),
                  "assembling-machine-2" in machines or not machines, loader_limit(site)]
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _unlocked(site: Mapping[str, Any]) -> set[str]:
    """The recipes the site says are unlocked."""
    return {str(r.get("name")) for r in site.get("recipes") or [] if isinstance(r, Mapping)}


def _still_holds(site: Mapping[str, Any], job: Any) -> bool:
    """Whether a bus stage planned in the background for the site as it was still holds now: the same bus (its
    origin and stages, or none yet for a new one) and its stage not built since. Its ground is checked after."""
    try:
        plan, seen = job.result[0], job.result[2]
    except (TypeError, IndexError, AttributeError):
        return False
    planned, saved = plan.get("base_plan") or {}, site.get("base_plan") or {}
    if seen.get("_origin") != site.get("origin"):
        return False   # (planned in another frame: its tiles would land elsewhere)
    if list(planned.get("extra") or ()) != list((*(saved.get("extra") or ()), *extendable(site))):
        return False   # (what the bus is extended for has changed since: a plan for the old one)
    if saved.get("stages"):
        same = (planned.get("x"), planned.get("y")) == (saved.get("x"), saved.get("y")) \
            and list(planned.get("stages") or ())[:len(saved["stages"])] == list(saved["stages"])
    else:
        same = True
    return same and planned.get("stage") not in (saved.get("done") or {})


def plan_in_background(site: Mapping[str, Any], world: Mapping[str, Any], library: ModuleLibrary | None = None, *,
                       wait_s: float = background.WAIT_S) -> dict:
    """plan_stage, worked out in a thread of its own (companion/background.py): a bus stage on a crowded plot took 7
    to 8 real minutes, past what the game waits for a plan (run 55, 2026-10-26). Waited for at most `wait_s`; past
    that background.Pending is raised (the engineer builds something else meanwhile) and a later request takes the
    plan, once nothing has come to stand on its ground since (else it is searched again)."""
    npc, tick = site.get("npc_id"), int(site.get("tick") or 0)
    key = (PURPOSE, signature(site))

    def work(seen: dict) -> tuple[dict, list, dict]:
        with plate_feed.searching():   # (the plan's feed searches: one budget for all its layouts)
            return (*_stage(site, seen, None, library), seen)
    for _ in range(2):
        snapshot = {**world, "_origin": site.get("origin")}   # (the planners' caches in it are the search's own)
        job = background.search(npc, key, lambda: work(snapshot), tick=tick, wait_s=wait_s,
                                adopt=lambda j: _still_holds(site, j))
        if job is None:
            under_way = [j.seconds for j in background.running(npc) if j.key == key]
            raise background.Pending(PURPOSE, under_way[0] if under_way else 0.0)
        plan, entities, seen = job.outcome()
        took = [f"planned in the background in {job.seconds:.0f} s"]
        asks = plan.get(LANE_ASKS)
        plan = {k: v for k, v in plan.items() if k != LANE_ASKS}
        if job.tick == tick:
            return {**plan, "notes": [*(plan.get("notes") or ()), *took]} if job.seconds >= 1 else plan
        # (the lanes as measured now: a tap asked of them before may be short since: searched again, the ladder then)
        if asks and lane_answer(site, world, plan["base_plan"]["stage"], *asks) is not None:
            continue
        # (ground built on since it was searched, by what the engineer built while it waited: searched again)
        if set(_in_way_tiles(entities, world)) - set(_in_way_tiles(entities, seen)):
            continue
        safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
        stage = re.sub(r"[^A-Za-z0-9_-]", "-", plan["base_plan"]["stage"])
        plan = {k: v for k, v in plan.items() if k != "retire"}
        retire = retirement_for(site, plan["base_plan"])
        return {**plan, **({"retire": retire} if retire else {}), "plan_id": f"{PURPOSE}-{stage}-{safe}",
                "notes": [*(plan.get("notes") or ()), *took]}
    raise background.Pending(PURPOSE, 0.0)


LABS_STAGE = "bus-labs"   # (the bus's own labs: its science runs once they stand)


def last_labs(names: Iterable[str], done: Mapping[str, Any], stage: str) -> bool:
    """`stage` is the last of the bus's labs sections still to build: with it, its science runs."""
    left = [n for n in names if str(n).startswith(LABS_STAGE) and n not in done]
    return left == [stage]


def _retire(site: Mapping[str, Any], stage: str, last: bool, packs: tuple, extra: tuple) -> list[str]:
    """The older builds a stage takes down: what the bus supersedes, once its own science runs (`last`: its last labs
    section, or its last stage; not the mall or smelting rows after them, and not when it grows on, F-18), and the
    hand-fed smelting a bus smelting stage replaces."""
    science = last
    retire = (superseded_blue(site) if "chemical-science-pack" in extra else superseded(site, packs)) if science else []
    return list(dict.fromkeys(list(retire) + retired_early(site, stage)))


def retirement_for(site: Mapping[str, Any], planned: Mapping[str, Any]) -> list[str]:
    """_retire for a stage planned earlier (its plan's base_plan), from what this site has built now."""
    saved = site.get("base_plan") or {}
    done = (saved.get("done") or {}) if saved.get("stages") else {}
    names = list(planned.get("stages") or ())
    last = sum(1 for n in names if n not in done) == 1 or last_labs(names, done, str(planned.get("stage")))
    return _retire(site, str(planned.get("stage")), last, tuple(planned.get("packs") or ()),
                   tuple(planned.get("extra") or ()))


def _stage(site: Mapping[str, Any], world: Mapping[str, Any], book: Book | None = None,
           library: ModuleLibrary | None = None) -> tuple[dict, list]:
    """plan_stage, and the stage's buildings (world coordinates)."""
    book = book or Book.from_site(site)
    traits = [str(t) for t in (site.get("personality") or {}).get("traits") or []]
    saved = site.get("base_plan") or {}
    # A bus keeps the style and machines it was planned with, so later stages fit the built ones.
    style = Style.decode(saved.get("style") if saved.get("stages") else style_for(site).code())
    machines = site.get("machines") or {}
    machine = saved.get("machine") if saved.get("stages") and saved.get("machine") else (
        ASSEMBLER if ("assembling-machine-2" in machines or not machines) and not style.cheap_machines
        else "assembling-machine-1")
    unlocked = _unlocked(site) or None
    # The mall's recipes are fixed when the bus is first planned: research done meanwhile must not
    # change its size and move the stages not built yet.
    if saved.get("stages") and saved.get("mall") is not None:
        unlocked = list(saved["mall"])
    mall = recipes_for(unlocked)
    # Loaders where a chest feeds a lane faster than an inserter can: decided once for the whole bus, like
    # the mall (a bus planned before loaders keeps its inserters).
    loader_above = saved.get("loader_above") if saved.get("stages") else loader_limit(site)
    extra: tuple[str, ...] = ()
    again = None   # (how a saved bus is laid out, to lay it out again with an expansion)
    if saved.get("stages"):
        spm, packs = float(saved["spm"]), tuple(saved.get("packs") or ("automation-science-pack",))
        aim = float(saved.get("aim") or spm)
        extra = tuple(saved.get("extra") or ()) + extendable(site)
        chosen = dict(d.split("=", 1) for d in saved.get("designs") or [] if "=" in d)
        again = dict(science_per_minute=spm, packs=packs, machine=machine,
                     origin=(int(saved["x"]), int(saved["y"])), columns=int(saved["columns"]),
                     unlocked=unlocked, library=library, traits=traits, chosen=chosen,
                     ahead=bool(saved.get("ahead")), extra=extra,
                     base_labs="bus-labs" in (saved.get("stages") or []), style=style,
                     sites=_sites(saved), power_rows=site.get("power_rows") or (),
                     tight=bool(saved.get("tight")), loader_above=loader_above, defense=_defense_kit(site),
                     feeds=plate_feed.sources(site), fed=_fed(saved), aim=aim)
        layout = plan_bus_base(world, book, **again)
    else:
        aim, wanted = goal(site)
        spm = first_rate(aim)   # (the rest of the aim comes as growth: growth_wanted)
        # Blue (already researched) goes in as the bus's extension from the start, with one set of labs.
        packs = tuple(p for p in wanted if p not in EXTENSION_PACKS)
        science = tuple(p for p in wanted if p in EXTENSION_PACKS)
        later = LATER_LANES + (MILITARY_LANES if team_area.team_of(site) else 0)
        columns = (2 + len(packs) + (2 if "logistic-science-pack" in packs else 0)
                   + factory_plan.lanes_kept(site, later) + style.reserve)
        unlocked = factory_plan.mall_recipes(site, unlocked)
        mall = recipes_for(unlocked)
        # Science first: the bus's mall after its science and labs (malls.OWN, kept with the bus's extensions so
        # later stages lay it out the same; after the packs it starts with, whose labs it follows), built once the
        # bus makes science (the user, 2026-10-26: a bus producing sooner; the engineer crafts until then). A bus
        # with green science has a mall (modules_for).
        own = (malls.extension_goal(malls.OWN, mall),) if mall and "logistic-science-pack" in packs else ()
        extra = science + own
        targets = factory_plan.bus_targets(site)
        first = dict(science_per_minute=spm, packs=packs, machine=machine, columns=columns, unlocked=unlocked,
                     library=library, traits=traits, extra=extra, base_labs=not science, style=style,
                     power_rows=site.get("power_rows") or (), loader_above=loader_above,
                     feeds=plate_feed.sources(site), aim=aim)
        # The plan's ground first, then anywhere; then, where a bus keeping lanes for later goals fits nowhere,
        # one with today's lanes only (later goals then get lanes of their own): a bus that fits beats none
        # (run 19, 2026-10-25: 26 columns, 20 of them for later, about 80 tiles wide, fit nowhere).
        attempts = ([dict(first, area=targets.area)] if targets and targets.area else []) + [first]
        layout, used = None, first
        fewest = columns - factory_plan.lanes_kept(site, later)
        for n, kwargs in enumerate(attempts):
            try:
                layout, used = plan_bus_base(world, book, **kwargs), kwargs
            except ValueError:
                if n == len(attempts) - 1:
                    layout = lean_bus(world, book, first, fewest)
                    if layout is None:
                        raise
                    used = dict(first, columns=layout.columns)
                    break
                continue
            break
        if not used.get("area"):   # (on the ground the factory plan kept for it, it stands there)
            layout = place_bus(world, book, layout, used, fewest)
            if targets and targets.area and used is first:   # (anywhere, not lean: as before)
                layout.notes.append("the bus did not fit the ground the factory plan kept for it")
    done = (saved.get("done") or {}) if saved.get("stages") else {}
    stage = _ready_stage(site, layout, done, next((s for s in layout.stages if s.name not in done), None))
    # Capacity first while growing: an expansion under way is finished, a plate's own block is completed, and a
    # plate that is still short gets a full belt more (a resource expansion), a finished bus too.
    failed = dict(saved.get("expand_failed") or {})
    first, why = expansion_first(layout, done, extra)
    if first is None:
        first, why = walls_first(site, layout, done)
    if first is None:
        first, why = smelting_first(site, layout, done, book, spm, (*packs, *extra), machine)
    if first is None and again is not None:
        grown = start_expansion(site, world, book, layout, done, extra, again, spm, packs, machine, failed)
        if grown:
            layout, extra, first, why = grown
    if first is None and stage is not None:
        # Every new tap asks the lane ledger first: it goes in only where its lane covers what is drawn already and it
        # with it; else the lane's supply grows first (its block's rest, a resource expansion), or the stage waits,
        # saying why (the user, F-18: "counts the output total from that belt line").
        asks = lane_asks(layout, done, stage)
        refused = lane_answer(site, world, stage.name, *asks) if asks else None
        if refused:
            item, said = refused
            first = _rest_of_block(layout, done, item) if item in PLATES else None
            why = f"lane supply first: {first.name}, as {said}" if first is not None else ""
            if first is None and again is not None:
                grown = start_expansion(site, world, book, layout, done, extra, again, spm, packs, machine, failed,
                                        force=item)
                if grown:
                    layout, extra, first, why = grown
                    why = f"lane supply first: {why}"
            if first is None:
                raise layout_planner.PlanError(f"lane supply first, {stage.name} waits: {said}")
    else:
        asks = None
    if first is not None:
        stage = first
        layout.notes.append(why)
        asks = None
    if stage is None:
        raise layout_planner.PlanError("the bus base is complete")
    names = [s.name for s in layout.stages]
    if any(n.startswith(stage.name) for n in layout.notes):
        raise layout_planner.PlanError("; ".join(layout.notes)[:160])
    # (what is built, and this stage: one built out of order leaves the stages it skipped for later)
    earlier = [s for s in layout.stages if s.name in done or s is stage]
    # Every lane-splitting junction so far gets an empty belt behind it, put down with this stage: those of earlier
    # stages too, so a bus built before anchors gets them as it grows (belt_flow.anchors). The tile behind must be
    # free of the whole layout as well as the world.
    keep = blocked_tiles(world) | footprint(e for s in layout.stages for e in s.entities)
    added, stuck = belt_flow.anchors([e for s in earlier for e in s.entities], world, keep)
    stage.entities.extend(added)
    if stuck:
        layout.notes.append(f"{len(stuck)} belt junctions have no room for an anchor behind them (first {stuck[0]})")
    planned = [e for s in earlier for e in s.entities]
    problems = belt_flow.check(planned, world)
    if problems:
        raise layout_planner.PlanError(problems[0])
    lanes = belt_flow.contracts(planned, world)
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", stage.name)
    entities = [e.to_plan(f"{safe}-{n}", -ox, -oy) for n, e in enumerate(stage.entities, start=1)]
    # What each lane is meant to carry goes with its belt: the game picks off what does not belong (corrector.lua)
    for e, placed in zip(entities, stage.entities):
        row = lanes.get((math.floor(placed.x), math.floor(placed.y))) if placed.name in belt_flow.BELTS else None
        if row:
            e["lanes"] = row
    supplies = []
    at = {(round(e["position"]["x"] + ox, 1), round(e["position"]["y"] + oy, 1)): e["id"]
          for e in entities if e["prototype"] not in ("transport-belt", "pipe", "small-electric-pole")}
    for s in stage.supplies:
        key = (round(s["x"], 1), round(s["y"], 1))
        if key in at:
            supplies.append({"entity_id": at[key], "item": s["item"], "slot": s["slot"]})
    witnesses = _witnesses(stage, entities)
    bus = layout.bus
    names_now = [s.name for s in layout.stages]
    retire = _retire(site, stage.name, sum(1 for s in layout.stages if s.name not in done) == 1
                     or last_labs(names_now, done, stage.name), packs, extra)
    row = {"power_row": layout.power_row} if stage.name == "power" and layout.power_row else {}
    # a module's stage: its ports on the bus and its rates, kept with the plan for later expansion
    ports = ledger.stage_ports(stage.name, bus.attached, -ox, -oy)
    row.update({"ledger": ports} if ports else {})
    row.update({LANE_ASKS: asks} if asks else {})   # (asked again when the plan is taken later; not for the game)
    return ({**row, **({"retire": retire} if retire else {}), **({"notes": layout.notes} if layout.notes else {}),
            "plan_id": f"{PURPOSE}-{safe}-{re.sub(r'[^A-Za-z0-9_-]', '-', str(site.get('request_id', 'x')))}",
            "source": "base-planner", "purpose": PURPOSE, "entities": entities,
            "stages": [{"id": stage.name, "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": supplies, "witnesses": witnesses,
                        **({"line_ops": line_ops} if (line_ops := belt_lines.ops(stage.entities)
                                                      + belt_lines.bus_lanes(bus, planned)) else {})}],
            "base_plan": {"x": bus.x, "y": bus.y, "columns": layout.columns or bus.columns, "length": bus.length,
                          "spm": spm, "aim": aim, "tight": 1 if layout.tight else 0,
                          "packs": list(packs), "stage": stage.name, "stages": names, "mall": mall,
                          "designs": [f"{k}={v}" for k, v in sorted(layout.designs.items())],
                          "ahead": 1 if layout.ahead else 0, "extra": list(extra), "style": style.code(),
                          "machine": machine, "sites": [f"{k}={v}" for k, v in sorted(layout.sites.items())],
                          **({"loader_above": loader_above} if loader_above is not None else {}),
                          **({"expand_failed": failed} if failed else {}),
                          **({"zones": zones} if (zones := planned_zones(layout)) else {})}}, list(stage.entities))


ZONE_MARGIN = 2   # (tiles round the bus's planned buildings kept with them)
MAX_ZONES = 16    # (boxes kept with the record: the nearest merged past this)


def planned_zones(layout: Any) -> list[list[int]]:
    """The ground the whole planned bus takes, built or not, as world boxes: one a stage (its buildings' own tiles,
    a margin round them: its lanes at their full width, its sections, smelting and fields), those that overlap
    merged, a far power or outpost site its own box (not the ground between). Kept from every other goal from its
    first stage on, ore and all, so a lane, section or field built later finds its ground free (F-18)."""
    from companion.smelting import footprint
    boxes = []
    for st in getattr(layout, "stages", None) or []:
        tiles = footprint(st.entities)
        if tiles:
            xs, ys = [x for x, _ in tiles], [y for _, y in tiles]
            boxes.append([min(xs) - ZONE_MARGIN, min(ys) - ZONE_MARGIN, max(xs) + 1 + ZONE_MARGIN,
                          max(ys) + 1 + ZONE_MARGIN])
    return _merged(boxes)


def _merged(boxes: list[list[int]]) -> list[list[int]]:
    """Overlapping boxes as one; past MAX_ZONES, the two nearest joined until within it."""
    def join(a, b):
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]

    def touch(a, b):
        return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
    out = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if touch(out[i], out[j]):
                    out[i] = join(out[i], out[j])
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    while len(out) > MAX_ZONES:
        gap = lambda a, b: max(0, max(a[0], b[0]) - min(a[2], b[2])) + max(0, max(a[1], b[1]) - min(a[3], b[3]))
        i, j = min(((i, j) for i in range(len(out)) for j in range(i + 1, len(out))),
                   key=lambda ij: gap(out[ij[0]], out[ij[1]]))
        out[i] = join(out[i], out[j])
        del out[j]
    return out


def replay(site: Mapping[str, Any], world: Mapping[str, Any], book: Book | None = None,
           library: ModuleLibrary | None = None):
    """The engineer's saved bus, laid out again exactly as planned (its style, machines, mall, module
    designs and extension), or None when it has none: what upgrades and repairs work from."""
    saved = site.get("base_plan") or {}
    if not saved.get("stages"):
        return None
    book = book or Book.from_site(site)
    traits = [str(t) for t in (site.get("personality") or {}).get("traits") or []]
    style = Style.decode(saved.get("style"))
    machines = site.get("machines") or {}
    machine = saved.get("machine") or (ASSEMBLER if "assembling-machine-2" in machines or not machines
                                       else "assembling-machine-1")
    unlocked = list(saved["mall"]) if saved.get("mall") is not None else None
    chosen = dict(d.split("=", 1) for d in saved.get("designs") or [] if "=" in d)
    return plan_bus_base(world, book, science_per_minute=float(saved["spm"]),
                         packs=tuple(saved.get("packs") or ("automation-science-pack",)), machine=machine,
                         origin=(int(saved["x"]), int(saved["y"])), columns=int(saved["columns"]),
                         unlocked=unlocked, library=library, traits=traits, chosen=chosen,
                         ahead=bool(saved.get("ahead")), extra=tuple(saved.get("extra") or ()),
                         base_labs="bus-labs" in (saved.get("stages") or []), style=style, sites=_sites(saved),
                         power_rows=site.get("power_rows") or (), tight=bool(saved.get("tight")),
                         loader_above=saved.get("loader_above"), defense=_defense_kit(site),
                         feeds=plate_feed.sources(site), fed=_fed(saved), aim=saved.get("aim"))


def _defense_kit(site: Mapping[str, Any]) -> dict:
    """What an outpost's wall and turrets are built of: the best it can make now (companion/perimeter.arms)."""
    from companion import perimeter
    try:
        return perimeter.arms(site)
    except Exception:   # (no armory: an outpost's wall of what the rules know)
        return {}


def _fed(saved: Mapping[str, Any]) -> dict[str, bool]:
    """The plates a bus being built takes from the base's own smelting (its feed-<plate> stages), each with whether
    that stage is done."""
    done = saved.get("done") or {}
    return {str(s)[len("feed-"):]: str(s) in done for s in saved.get("stages") or () if str(s).startswith("feed-")}


def _sites(saved: Mapping[str, Any]) -> dict[str, str]:
    out = dict(s.split("=", 1) for s in saved.get("sites") or [] if isinstance(s, str) and "=" in s)
    if "steel" not in out and "bus-steel" in (saved.get("stages") or []):
        out["steel"] = "module"   # planned before blue's steel came by direct insertion: it stays a module
    return out


LANE_ASKS = "_lane_asks"


def lane_gate(site: Mapping[str, Any], world: Mapping[str, Any] | None, layout: Any, done: Mapping[str, Any],
              stage: Any) -> tuple[str, str] | None:
    """The new taps `stage` puts on the bus, each asked of the lane ledger (companion/lane_ledger.py: what each lane
    carries, measured in the game where it can be, against every claim on it, this plan's grants too): a tap of a
    belt of several takes from the belt on its side (`reach`), a lone lane's from that lane. Lanes the game has not
    recorded yet (laid by this plan, or not sampled) count by their design. A tap is held back only on what the game
    measured: a lane or group known to carry too little. One judged by design alone (not sampled yet, or backed up:
    it gives at least that) stands on the plan's own sizing (smelting_first, an expansion), and its answer is logged
    all the same. Returns (item, why) for the first refused, else None; every answer is logged with the plan's
    grants."""
    asks = lane_asks(layout, done, stage)
    return lane_answer(site, world, stage.name, *asks) if asks else None


def lane_asks(layout: Any, done: Mapping[str, Any], stage: Any) -> tuple[list, dict, list] | None:
    """What `stage` asks of the lanes: (the bus's lanes with it, as line ops; each lane's claims before it; the new
    taps, [lane id, key, items/s]), or None when it adds no tap."""
    bus = getattr(layout, "bus", None)
    if bus is None:
        return None
    before = [e for s in layout.stages if s.name in done and s is not stage for e in s.entities]
    old = {op["id"]: dict(op["claims"]) for op in belt_lines.bus_lanes(bus, before)}
    after = belt_lines.bus_lanes(bus, [*before, *stage.entities])
    new = [{k: op[k] for k in ("id", "item", "group", "supply") if k in op} for op in after]
    asked = [[op["id"], key, rate] for op in after for key, rate in op["claims"].items()
             if key not in old.get(op["id"], {})]
    return (new, old, asked) if asked else None


def lane_answer(site: Mapping[str, Any], world: Mapping[str, Any] | None, stage: str, new: list, old: dict,
                asked: list) -> tuple[str, str] | None:
    """The ledger's answer to lane_asks: (item, why) of the first tap held back, else None; logged."""
    book = lane_ledger.LaneBook.from_site(site, world)
    ops = {op["id"]: op for op in new}
    for op in new:
        if book.lane(op["id"]) is None:   # (counted by its design until the game records it)
            book.plan_lane(op["id"], op["item"], op["supply"], group=op.get("group"), claims=old.get(op["id"]) or {})
    for lid, key, rate in asked:
        op = ops[lid]
        lane = book.lane(op["id"])
        group = lane.line.group if lane is not None else None
        several = group is not None and len(book.lanes(group=group)) > 1
        grant = (book.grant(rate, key, group=group, reach=[op["id"]]) if several
                 else book.grant(rate, key, line=op["id"]))
        if not grant.ok and _measured(book, grant.line or op["id"]):
            _log_gate(site, stage, book)
            return op["item"], grant.why
    _log_gate(site, stage, book)
    return None


def _measured(book: Any, line_id: str) -> bool:
    """Whether the game measured what this lane, or its group, carries (not its design, nor a backed-up "at least")."""
    lane = book.lane(line_id)
    if lane is None:
        return False
    g = book.group(lane.line.group) if lane.line.group else None
    return lane.carry_from == "measured" or (g is not None and len(g.lanes) > 1 and g.carry_from == "measured")


def _log_gate(site: Mapping[str, Any], stage: str, book: Any) -> None:
    """The ledger's answers for a stage's taps, in data/decisions/lanes.jsonl beside its signals."""
    import os
    from pathlib import Path
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    path = Path(folder) / "decisions" / lane_ledger.LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": time.time(), "tick": site.get("tick"), "npc": site.get("npc_id"),
                                 "gate": stage, "grants": book.grants}) + "\n")
    except OSError:
        pass


def _capacity(stage: str) -> bool:
    """Whether a bus stage adds smelting (the rest of a block, an expansion or growth's plate modules and their
    supply, an outpost): what building takes is the rest."""
    return stage.startswith(("supply-", "bus-smelt-", "outpost-smelt-")) or \
        (stage.startswith("smelting-") and stage.endswith("-more"))


def built_supply(layout: Any, done: Mapping[str, Any], plate: str) -> tuple[float, float]:
    """Plates/s at full speed from the built smelting of `plate`: at the bus head (its block and the rest of it),
    which feeds every machine down the bus, and below it (plate modules of growth sections and expansions), whose
    plates go on down the lane only, to the machines below and the lane's end, where the engineer takes them."""
    def furnaces(names) -> int:
        return sum(1 for s in layout.stages if s.name in done and names(s.name)
                   for e in s.entities if e.name.endswith("furnace"))
    head = furnaces(lambda n: n in (f"smelting-{plate}", f"smelting-{plate}-more"))
    below = furnaces(lambda n: n.startswith((f"bus-smelt-{plate}-", f"outpost-smelt-{plate}-")))
    return head * FURNACE_PLATES, below * FURNACE_PLATES


def pressed(site: Mapping[str, Any], layout: Any, done: Mapping[str, Any], book: Book, spm: float,
            packs: tuple[str, ...], machine: str) -> list[tuple[str, float, float, float, float, bool, bool]]:
    """The plates whose built smelting falls short at 85%, the most pressed first: (plate, head, below, machines'
    draw, building's, head short, building short), items/s. The head is short when the machines take more than it
    makes; building when what the head has left over and what is made below cannot cover the building still to do
    (construction.allowance; the engineer grabs plates anywhere, the machines only from above them). A plate
    whose block at the bus head is not built yet is not judged."""
    targets = {p: spm / 60 for p in packs if p.endswith("science-pack")}
    try:
        demand = solve(targets, book, inputs=set(PLATES), allowed={machine, "chemical-plant", "oil-refinery",
                                                                    "stone-furnace", "steel-furnace"}).inputs
    except (KeyError, ValueError):
        demand = {}
    rest = [e.name for s in layout.stages if s.name not in done and not _capacity(s.name) for e in s.entities]
    kept = construction.allowance(site, demand, construction.cost(rest, book))
    out = []
    room = construction.HEADROOM
    sources = None
    for plate in PLATES:
        # (a plate fed from the base's own smelting: what the feed brings is its head, until its own block stands)
        fed = f"feed-{plate}" in done and f"smelting-{plate}" not in done
        if f"smelting-{plate}" not in done and not fed:
            continue
        head, below = built_supply(layout, done, plate)
        if fed:
            sources = plate_feed.sources(site) if sources is None else sources
            head += (sources.get(plate) or (None, 0.0))[1] * FEED_SHARE
        need, extra = float(demand.get(plate, 0.0)), float(kept.get(plate, 0.0))
        head_short = head * room < need
        building_short = max(0.0, head * room - need) + below * room < extra
        if head_short or building_short:
            out.append((plate, head, below, need, extra, head_short, building_short))
    out.sort(key=lambda r: -(r[3] + r[4]) / max((r[1] + r[2]) * room, 1e-6))
    return out


def _rest_of_block(layout: Any, done: Mapping[str, Any], plate: str):
    """The rest of a plate's block at the bus head still to build, if it has furnaces (a block planned for today's
    need only may leave just its field's spare drills for later); for a plate fed from the base's own smelting, its
    own block first."""
    if f"feed-{plate}" in done:
        own = next((s for s in layout.stages if s.name == f"smelting-{plate}" and s.name not in done), None)
        if own is not None:
            return own
    return next((s for s in layout.stages if s.name == f"smelting-{plate}-more" and s.name not in done
                 and any(e.name.endswith("furnace") for e in s.entities)), None)


def smelting_first(site: Mapping[str, Any], layout: Any, done: Mapping[str, Any], book: Book, spm: float,
                   packs: tuple[str, ...], machine: str) -> tuple[Any, str]:
    """While the base grows, the rest of a plate's smelting block (a full belt) comes next, not last, when what
    stands cannot cover both its machines and the building still to do: over-producing while growing is the goal
    (run 32 built its whole bus on nine iron furnaces sized for science alone, 2026-10-25). The most pressed plate
    first. Returns the stage, and why, or (None, ""). Not before the bus's labs stand: its first lanes go in thin
    from the smelting that stands, used at once, and the rest of a block grows them after (the user: "planned out,
    built as it goes, 1 lane can still be used"; the rest of copper's block first held the first lane to minute 58,
    green-pace, F-21)."""
    if not any(str(d).startswith(LABS_STAGE) for d in done):
        return None, ""   # (until its labs stand, its science running: science on thin lanes first, then growth)
    for plate, head, below, need, extra, _, _ in pressed(site, layout, done, book, spm, packs, machine):
        stage = _rest_of_block(layout, done, plate)
        if stage is not None:   # (more at the head serves the machines and building alike)
            return stage, (f"more smelting first: {stage.name}, as {plate} runs {(head + below) * 60:.0f} a minute "
                           f"while its machines take {need * 60:.0f} and building {extra * 60:.0f}")
    return None, ""


def _expansion(goal: str) -> tuple[str, int, float, str] | None:
    """(plate, index, rate, tag) of a resource expansion's goal, on the bus or an outpost; else None."""
    if growth.is_expansion(goal):
        return (*growth.parse_expansion(goal), growth.expansion_tag(goal))
    if outpost.is_outpost(goal):
        return (*outpost.parse(goal), outpost.tag(goal))
    return None


def expansion_first(layout: Any, done: Mapping[str, Any], extra: tuple[str, ...]) -> tuple[Any, str]:
    """A resource expansion under way is finished before anything else on the bus."""
    for goal in extra:
        known = _expansion(goal)
        if known:
            plate, index, _, tag = known
            stage = next((s for s in layout.stages if growth.in_section(s.name, tag) and s.name not in done), None)
            if stage is not None:
                return stage, f"finishing the {plate} expansion {index}"
    return None, ""


def walls_first(site: Mapping[str, Any], layout: Any, done: Mapping[str, Any]) -> tuple[Any, str]:
    """An outpost's wall goes up as walls are made: a stretch of it waiting comes next once the walls in stock
    cover it (its plates and the bus never wait on them meanwhile). Returns the stage, and why, or (None, "")."""
    from companion import perimeter
    for s in layout.stages:
        if not s.name.startswith("outpost-wall-") or s.name in done or not s.entities:
            continue
        counts: dict[str, int] = {}
        for e in s.entities:
            counts[e.name] = counts.get(e.name, 0) + 1
        wall = max(counts, key=counts.get)
        if perimeter.stocked(site, wall) >= counts[wall]:
            return s, f"walls in stock for the outpost's wall ({s.name})"
    return None, ""


# Where an expansion fits is searched in the background: an outpost's spot can take a minute and more of belt searches
# (run 33's plot: 74 s), past what a request may wait. A quick answer is taken at once; else the bus goes on with its
# next stage, and the expansion found is laid out at a later stage's request, its spot and fields kept (the bus's sites).
EXPANSION_WAIT_S = 5
_searches: dict = {}            # (engineer, bus origin, plate, expansion index) -> the search
_searches_lock = threading.Lock()


def _expansion_tries(plate: str, index: int) -> list:
    """On the bus's column where it fits, else an outpost by an ore patch (companion/outpost.py); a full belt, else
    half of one."""
    tries = [(growth.expansion_name(plate, index, r), growth.expansion_tag) for r in (growth.BELT, growth.BELT / 2)]
    return tries + [(outpost.name(plate, index, r), outpost.tag) for r in (growth.BELT, growth.BELT / 2)]


def _fits(grown: Any, goal: str, tag_of: Any, label: str, world: Mapping[str, Any], done: Mapping[str, Any]):
    """Whether a layout with an expansion holds it whole: (its first stage to build, why not)."""
    bad = [n for n in grown.notes if n.startswith(label)]
    if bad:
        return None, bad[0][len(label):].strip()
    tag = tag_of(goal)
    clash = _obstructed(grown, tag, world)
    if clash:   # (the bus lays its extensions along its column; cliffs, water or buildings may stand there)
        return None, f"its buildings would stand on {clash}"
    first = next((s for s in grown.stages if growth.in_section(s.name, tag) and s.name not in done), None)
    return first, ("" if first is not None else "nothing of it left to build")


def _search(world: Mapping[str, Any], book: Book, again: dict, extra: tuple, done: Mapping[str, Any], plate: str,
            index: int, job: dict) -> None:
    """The first of the expansion's tries that fits (its goal, and the sites its spot and fields were kept in), else
    why none did."""
    label = f"the {plate} expansion {index}:"
    for goal, tag_of in _expansion_tries(plate, index):
        try:
            grown = plan_bus_base(world, book, **{**again, "extra": (*extra, goal)})
        except ValueError as exc:
            job.setdefault("why", []).append(str(exc)[:120])
            continue
        first, why = _fits(grown, goal, tag_of, label, world, done)
        if first is not None:
            job["goal"], job["sites"] = goal, dict(grown.sites)
            return
        job.setdefault("why", []).append(why)


def start_expansion(site: Mapping[str, Any], world: Mapping[str, Any], book: Book, layout: Any,
                    done: Mapping[str, Any], extra: tuple[str, ...], again: dict, spm: float,
                    packs: tuple[str, ...], machine: str, failed: dict, force: str | None = None):
    """A plate still short of the building to do, its own block all built, gets a resource expansion: a full belt
    more (on the bus's column, else an outpost by an ore patch), else half of one. Where it fits is searched in the
    background (one search an engineer at a time); one found is laid out here once checked against the map as it
    is now. One that fits nowhere is noted in `failed` (plate -> tick) and tried again after EXPAND_RETRY_TICKS.
    `force`: a plate whose lane the ledger found short for a new tap (lane_gate), expanded though the plan's own
    count finds it enough. Returns (layout, extra, its first stage, why), or None."""
    tick = int(site.get("tick") or 0)
    index = 1 + sum(1 for g in extra if _expansion(g))
    npc, origin = site.get("npc_id"), again.get("origin")
    rows = pressed(site, layout, done, book, spm, (*packs, *extra), machine)
    if force is not None and force not in {r[0] for r in rows}:
        rows.append((force, *built_supply(layout, done, force), 0.0, 0.0, False, True))
    for plate, head, below, need, kept, _, building_short in rows:
        supply = head + below
        # (an expansion's plates go down the bus from below its machines: it serves building and what is below)
        if not building_short and plate != force:
            continue
        if plate not in growth.PLATES or tick - int(failed.get(plate, -EXPAND_RETRY_TICKS)) < EXPAND_RETRY_TICKS:
            continue
        if _rest_of_block(layout, done, plate) is not None:
            continue   # (its own block's rest comes first)
        key = (npc, origin, plate, index)
        with _searches_lock:
            job = _searches.get(key)
            busy = any(k[0] == npc and k != key and j["thread"].is_alive() for k, j in _searches.items())
            if job is None and not busy:
                job = {"tick": tick}
                job["thread"] = threading.Thread(
                    target=_search, args=(world, book, dict(again), tuple(extra), dict(done), plate, index, job),
                    name=f"expansion-{npc}-{plate}", daemon=True)
                _searches[key] = job
                job["thread"].start()
        if job is None:
            layout.notes.append(f"a {plate} expansion waits for another being worked out")
            return None
        job["thread"].join(EXPANSION_WAIT_S)
        if job["thread"].is_alive():
            layout.notes.append(f"where a {plate} expansion fits is being worked out in the background")
            return None
        with _searches_lock:
            _searches.pop(key, None)
        label = f"the {plate} expansion {index}:"
        why_not = "; ".join(dict.fromkeys(job.get("why") or ["it fits nowhere"]))
        if job.get("goal"):
            goal = job["goal"]
            tag_of = outpost.tag if outpost.is_outpost(goal) else growth.expansion_tag
            try:
                grown = plan_bus_base(world, book, **{**again, "sites": {**(again.get("sites") or {}), **job["sites"]},
                                                      "extra": (*extra, goal)})
                first, why = _fits(grown, goal, tag_of, label, world, done)
            except ValueError as exc:
                first, why = None, str(exc)[:120]
            if first is not None:
                rate = _expansion(goal)[2]
                where = "by an ore patch" if outpost.is_outpost(goal) else "on the bus"
                return grown, (*extra, goal), first, (
                    f"expanding {plate} first: {rate:g} a second more {where}, as it runs {supply * 60:.0f} a minute "
                    f"while its machines take {need * 60:.0f} and building {kept * 60:.0f}")
            why_not = f"what was found no longer fits ({why})"
        failed[plate] = tick
        layout.notes.append(f"no room for a {plate} expansion now: {why_not}")
    return None


def _obstructed(layout: Any, tag: str, world: Mapping[str, Any]) -> str | None:
    """What stands where the buildings of the extension section tagged `tag` would go (see _in_way)."""
    return _in_way([e for s in layout.stages if growth.in_section(s.name, tag) for e in s.entities], world)


def _in_way(mine: list, world: Mapping[str, Any]) -> str | None:
    """What stands where these buildings would go, as "cliff (12 tiles)"; else None."""
    from collections import Counter
    hit = Counter(_in_way_tiles(mine, world).values())
    return ", ".join(f"{k} ({n} tiles)" for k, n in hit.most_common()) or None


def _in_way_tiles(mine: list, world: Mapping[str, Any]) -> dict[tuple[int, int], str]:
    """The tiles of these buildings where something stands: cliffs, water, ground the game refused, buildings (the
    same piece of its own in the same spot is none, nor a burner build the bus replaces), and what."""
    from companion.base_plan import replaceable
    want = footprint(mine)
    same = {(e.name, math.floor(e.x), math.floor(e.y)) for e in mine}
    taken: dict[tuple[int, int], str] = {}
    for y, a, b in world.get("water") or []:
        for x in range(int(a), int(b) + 1):
            taken[x, int(y)] = "water"
    for box in world.get("avoid") or []:
        try:
            for x in range(int(box["x0"]), int(box["x1"]) + 1):
                for y in range(int(box["y0"]), int(box["y1"]) + 1):
                    taken[x, y] = "ground the game refused"
        except (KeyError, TypeError, ValueError):
            continue
    for e in world.get("entities") or []:
        if e.get("t") in ("tree", "simple-entity", "fish", "unit", "character", "corpse") or \
                (e.get("n"), math.floor(e["x"]), math.floor(e["y"])) in same:
            continue
        w, h = e.get("w", 1), e.get("h", 1)
        if (e.get("d") or 0) in (4, 12):
            w, h = h, w
        for i in range(w):
            for j in range(h):
                taken[math.floor(e["x"] - w / 2 + 0.5) + i, math.floor(e["y"] - h / 2 + 0.5) + j] = \
                    "cliffs" if e.get("t") == "cliff" else "buildings"
    return {t: taken[t] for t in want - replaceable(world) if t in taken}


def _witnesses(stage, entities: list[dict]) -> list[dict]:
    if stage.name == "power":
        engine = next(e for e in entities if e["prototype"] == "steam-engine")
        return [{"kind": "entity_built", "entity_id": engine["id"]},
                {"kind": "power_connected", "entity_id": engine["id"]}]
    furnace = next((e for e in entities if e["prototype"].endswith("furnace")), None)
    if stage.name.startswith("smelting-") and furnace is not None:
        plate = stage.name[len("smelting-"):].removesuffix("-more")
        return [{"kind": "entity_built", "entity_id": furnace["id"]},
                {"kind": "product_counter_delta", "entity_id": furnace["id"], "item": plate, "min_delta": 5}]
    if stage.name.startswith("feed-"):   # (plates from the base's own smelting on the way into the lanes)
        built = [{"kind": "entity_built", "entity_id": entities[0]["id"]}]
        return built if getattr(stage, "proof", "") == "built" else \
            built + [{"kind": "belt_items", "entity_id": entities[0]["id"], "min_delta": 1}]
    lab = next((e for e in entities if e["prototype"] == "lab"), None)
    if stage.name.startswith(LABS_STAGE) and lab is not None:   # (every labs section: the old science retires on it)
        return [{"kind": "entity_built", "entity_id": lab["id"]}, {"kind": "power_connected", "entity_id": lab["id"]}]
    silo = next((e for e in entities if e["prototype"] == "rocket-silo"), None)
    if silo:
        return [{"kind": "entity_built", "entity_id": silo["id"]}, {"kind": "power_connected", "entity_id": silo["id"]}]
    if stage.name == "oil-complex":
        refinery = next(e for e in entities if e["prototype"] == "oil-refinery")
        return [{"kind": "entity_built", "entity_id": refinery["id"]},
                {"kind": "product_counter_delta", "entity_id": refinery["id"], "item": refinery["recipe"],
                 "min_delta": 2}]
    last = [e for e in entities if e.get("recipe")]
    # The mall proves itself with its first machine (pipes need only iron); others with their last.
    target = (last[0] if stage.name.startswith("bus-mall") else last[-1]) if last else entities[0]
    if not target.get("recipe"):
        return [{"kind": "entity_built", "entity_id": target["id"]}]
    return [{"kind": "entity_built", "entity_id": target["id"]},
            {"kind": "power_connected", "entity_id": target["id"]},
            {"kind": "product_counter_delta", "entity_id": target["id"], "item": target["recipe"], "min_delta": 1}]


WorldFetch = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
