"""Which goal an engineer should build next, weighed rather than read off a list (the user, 2026-10-25: broad,
long-term mechanisms in place of fixed orders tuned to one run; and in time a learned model in place of rules
and the LLM, so every weight here is a parameter, and every choice is written down with what it saw).

For each goal it could take up now:

    value       what it adds, item by item: the rate (from its layout's machines and recipes, or its spec's
                drill pairs) times what one is worth in raw resources (from the recipes) times how much the
                factory needs it now (companion/priorities.py, its every signal: the way to space, survival,
                fuel, research, shortfalls)
    unlocks     a share of the value of the goals that wait on it built first
    hand work   the fuel its burners will need carried (engineer-seconds a minute), costlier the more of the
                engineer's measured time already goes to hand work; and the hand work it takes away, where it
                replaces burner builds (the bus, coal by belt)
    speed-up    the game minutes it takes off building what comes after it: the plates, bricks and stone the
                goals ahead take (their buildings, through the recipes) over what the base can spare for building,
                less the same with what this goal adds. Plates are what every build waits on: valued at what they
                make alone, a burner pair more on iron counted for next to nothing, and one pair carried the opening
                for 19 minutes (run 35, 2026-10-25)
    hand made   the crafting and mining by hand its machines take over (the game's measure of it), in the
                speed-up as the engineer's minutes freed over HAND_HORIZON
    cost        the buildings it takes, in minutes: placing them, and the plates for them at the spare income now
    prior       the opening's order (companion/catalog_planner.py: OPENING, team_opening) as one more weight,
                PRIOR, that a learned model can turn down to nothing
    due         how near or past its aim the milestone it brings nearer is, by the user's pace targets
                (companion/pace.py), weighed DUE_WEIGHT

    score = (value + unlocks + SPEED_WEIGHT * speed-up + hand work saved - hand work added)
            / (1 + cost / COST_SCALE) + PRIOR * prior + DUE_WEIGHT * due

The best score is chosen, and kept a while unless another clears it by MARGIN (no flitting between goals). The
weights are fitted offline from the decision logs (companion/goal_fit.py); fitted ones are used only when the
file is named in NPC_GOAL_WEIGHTS.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

PRIOR = 1.0                  # the opening's order, as a weight (a learned model turns this down)
PLAN_PRIOR = 0.5             # the factory plan's next section, as a weight
CONTINUE_PRIOR = 1.0         # a project under way (a bus with stages left): what is built waits on what is not
FAIL_ODDS = 0.5              # a goal's odds of planning, halved for each time it failed lately (the game's tally)
UNLOCK_SHARE = 0.4           # of the value of a goal waiting on this one built first
UNLOCK_DEPTH = 3             # goals waiting in turn followed down a chain (each counted UNLOCK_SHARE of the one before)
HAND_SECONDS_PER_FUEL = 0.5  # engineer-seconds to fetch and carry one piece of fuel to a burner
# Score per engineer-second a minute of hand work, with its time free: the same scale as the speed-up, the hand work
# counted HAND_HORIZON ahead (priced apart, at 0.02, a burner block that left all its coal to be mined by hand still
# came before the coal loop, and the engineer mined coal for 22 burners while the iron it held waited, run 36,
# 2026-10-25).
HAND_WEIGHT = 0.3 * 15.0 / 60.0
LOAD_CAP = 0.9               # (its time this taken already: the dearest hand work gets)
MODEL_DUTY = 0.5             # burners run about half their time (a furnace waits for ore, a drill for room): run
                             # 20's fuel work was about 0.4 of what its burners draw flat out
BUILD_SECONDS = 4.0          # to make and place one building
COST_SCALE = 6.0             # minutes of cost that halve a goal's worth
MARGIN = 0.15                # a new goal must beat the one in hand by this much
HOLD_TICKS = 18000           # (and the one in hand is kept at most this long without that)
DRILL_RATE = {"burner-mining-drill": 0.25, "electric-mining-drill": 0.5}
SPEED_WEIGHT = 0.3           # score per game minute taken off building what comes next
SPEED_CAP = 30.0             # (minutes counted at most)
INCOME_FLOOR = 0.05          # items a second spare for building, at least, in the reckoning (the engineer hand-smelts)
MATERIAL_CAP = 30.0          # minutes of waiting for a goal's own plates counted in its cost at most
HAND_HORIZON = 15.0          # game minutes ahead the hand making a goal takes over is counted for, in its speed-up
DUE_WEIGHT = 0.0             # score per unit of due (companion/pace.py): logged, off until a fit and a run earn it
ASK_PRIOR = 1.0              # a goal its strategist asked for "next" or "later", as a weight among the opening's steps
GUARD_WEIGHT = 0.0           # score per unit of guard (how soon defenses must go up): logged, off until learned
GUARD_DRAWN_SCALE = 10.0     # small biters of the wave its pollution draws that make that pull nearly count in full
GUARD_EVOLUTION = 0.5        # evolution's own part (bigger biters sooner, whatever the pollution)
GUARD_MILITANT = 0.35        # a survivor's prior (a death world): its defenses weigh this much before any sign
MILITANT_GUARD_WEIGHT = 3.0  # and a survivor weighs guard by this (about ten minutes saved) while GUARD_WEIGHT is off
RAW_DEPTH = 6
LOG_NAME = "goals.jsonl"
HAND_TAGS = ("supply", "collect", "ammo", "store", "rebuild_acquire")   # time_use: upkeep that is hand work
WEIGHTS_ENV = "NPC_GOAL_WEIGHTS"   # a fitted weights file (companion/goal_fit.py) to weigh with instead
WEIGHTS_SCHEMA = "npc-goal-weights-v1"


# --- the weights ---------------------------------------------------------------------------------------------

def default_weights() -> dict[str, float]:
    """The hand-set weights (read when asked, so a test may change one)."""
    return {"value": 1.0, "unlocks": 1.0, "speed": SPEED_WEIGHT, "hand": 1.0, "cost_scale": COST_SCALE,
            "prior": PRIOR, "plan": PLAN_PRIOR, "continue": CONTINUE_PRIOR, "due": DUE_WEIGHT, "ask": ASK_PRIOR,
            "guard": GUARD_WEIGHT}


_fitted: dict[str, Any] = {"key": None, "weights": {}}


def fitted_weights() -> dict[str, float]:
    """The weights in the file NPC_GOAL_WEIGHTS names, or none: only known names with finite values (a scale above
    nothing, no weight below it); a file that cannot be read gives none."""
    path = os.environ.get(WEIGHTS_ENV)
    if not path:
        return {}
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return {}
    if _fitted["key"] != key:
        out = {}
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            given = data.get("weights") if isinstance(data, Mapping) and data.get("schema") == WEIGHTS_SCHEMA else None
            for name in default_weights():
                value = given.get(name) if isinstance(given, Mapping) else None
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                try:
                    value = float(value)
                except OverflowError:   # (an integer past any float)
                    continue
                if math.isfinite(value) and value >= 0 and (value > 0 or name != "cost_scale"):
                    out[name] = value
        except (OSError, ValueError):
            out = {}
        _fitted.update(key=key, weights=out)
    return dict(_fitted["weights"])


def weights() -> dict[str, float]:
    return {**default_weights(), **fitted_weights()}


def score(parts: Mapping[str, Any], w: Mapping[str, float]) -> float:
    """A goal's score from its parts (a row of the weighing) and the weights: the one formula, for the weighing and
    for fitting its weights."""
    gain = (w["value"] * parts["value"] + w["unlocks"] * parts["unlocks"] + w["speed"] * parts["speedup"]
            + w["hand"] * (parts["hand_saved"] - parts["hand_added"]))
    return ((gain / (1 + parts["cost"] / w["cost_scale"]) + w["prior"] * parts["prior"]
             + w["plan"] * float(bool(parts.get("planned"))) + w["continue"] * float(bool(parts["under_way"]))
             + w["due"] * float(parts.get("due") or 0.0) + w["ask"] * float(bool(parts.get("asked")))
             + (w["guard"] + MILITANT_GUARD_WEIGHT * float(bool(parts.get("militant"))))
             * float(parts.get("guard") or 0.0)) * parts["odds"])


# --- how soon defenses must go up ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Guard:
    """How soon its defenses must go up: no fixed minute (the user, 2026-10-26): the higher the pollution and the
    evolution, the sooner, and at once if it was attacked before its walls were up; otherwise they may go up slowly."""
    value: float = 0.0           # 0 calm to 1
    level: float = 0.0           # the threat now (companion/threat.py: attacks, the cover they found short, breaches)
    hit_unwalled: bool = False   # attacked (ever: the game keeps it) with no wall of its ring standing
    drawn: float = 0.0           # the wave its pollution draws, grown as the biters evolve (small biters)
    nests: float = 0.0           # how near the nests are: 1 soaking its pollution, less the farther, some if none known
    evolution: float = 0.0

    @property
    def urgent(self) -> bool:
        """Defenses before anything asked for: attacked with no wall standing, or threatened now."""
        from companion import threat
        return self.hit_unwalled or self.level >= threat.ALERT

    def features(self) -> dict:
        return {"value": round(self.value, 3), "level": round(self.level, 3), "hit_unwalled": self.hit_unwalled,
                "drawn": round(self.drawn, 2), "nests": round(self.nests, 3), "evolution": round(self.evolution, 3)}


def nest_nearness(site: Mapping[str, Any], areas: list) -> float:
    """How much the nests draw on its pollution where it is drawn most (companion/defense_brain.py nest_pull: 1 once
    it reaches a nest, less the farther the nearest known or the nearest one could stand)."""
    from companion import defense_brain
    return max((defense_brain.nest_pull(site, a.box) for a in areas), default=defense_brain.NO_NESTS_KNOWN)


def militant_of(site: Mapping[str, Any]) -> bool:
    """Survival first by nature (a death world's survivor trait, catalog_planner.MILITANT)."""
    from companion import catalog_planner
    traits = {str(x) for x in catalog_planner.bp._as_list((site.get("personality") or {}).get("traits"))}
    return bool(traits & catalog_planner.MILITANT)


def guard_of(site: Mapping[str, Any]) -> Guard:
    """How soon its defenses must go up, from the threat now, an attack with no wall standing, and the wave its
    pollution draws as the biters evolve, as near as the nests are (any one of them: a noisy or)."""
    from companion import catalog_planner, defense_brain, perimeter, threat
    t = threat.estimate(site)
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    evolution = min(1.0, max(0.0, float(board.get("evolution") or 0)))
    hit_unwalled = threat.unwalled_hit(site)
    try:
        areas = defense_brain.assess(site)
    except Exception:   # (a judgment, never in the way)
        areas = []
    drawn = max((a.drawn for a in areas), default=0.0) * (1.0 + defense_brain.EVOLUTION_SCALE * evolution)
    nests = nest_nearness(site, areas)
    pull = 1.0 - math.exp(-drawn / GUARD_DRAWN_SCALE)   # (each part's draw already weighs the nests' pull)
    prior = GUARD_MILITANT if militant_of(site) else 0.0
    value = threat.either([t.level, 1.0 if hit_unwalled else 0.0, pull, GUARD_EVOLUTION * evolution, prior])
    return Guard(value=value, level=t.level, hit_unwalled=hit_unwalled, drawn=drawn, nests=nests, evolution=evolution)


def guards(purpose: str) -> bool:
    """A goal that puts up defenses or makes them (the wall row)."""
    from companion import catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    return defense(purpose) or bool(spec.get("wall_block") or spec.get("defense_production"))


def guard_share(purpose: str, site: Mapping[str, Any]) -> float:
    """How much a goal is one of its defenses: in full, or (companion/pace.py's PREREQ_SHARE) as what one waits on
    (brick smelting for the wall row)."""
    from companion import catalog_planner, pace
    if guards(purpose):
        return 1.0
    done, mine = catalog_planner.built_purposes(site), pace.stands_for(purpose)
    if any(guards(p) and p not in done and mine & pace.prerequisites(p, done) for p in catalog_planner.PURPOSES):
        return pace.PREREQ_SHARE
    return 0.0


# --- what a goal adds ----------------------------------------------------------------------------------------

def raw_cost(item: str, book: Any, depth: int = 0, seen: frozenset = frozenset()) -> float:
    """Raw resources in one `item` (ores, coal, stone, oil, water count 1): what making it ties up."""
    if book is None or depth > RAW_DEPTH or item in seen:
        return 1.0
    recipe = book.recipe_for(item)
    if recipe is None or not recipe.ingredients:
        return 1.0
    made = recipe.makes(item) or 1.0
    return max(1.0, sum(raw_cost(n, book, depth + 1, seen | {item}) * float(a) for n, a in recipe.ingredients) / made)


def layout_of(purpose: str, site: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The entities of the layout it would build: the game's own suggestion where there is one."""
    plan = (site.get("suggestions") or {}).get(purpose) if isinstance(site.get("suggestions"), Mapping) else None
    entities = plan.get("entities") if isinstance(plan, Mapping) else None
    return [e for e in entities or [] if isinstance(e, Mapping)]


def flows(purpose: str, site: Mapping[str, Any], book: Any = None
          ) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """(final products a second, inputs it needs from the rest of the base a second, machines it places): what
    its own machines make and use of each other cancels (a red science line's gears are not its output)."""
    makes, takes, machines = full_flows(purpose, site, book)
    final = {i: r for i, r in makes.items() if i not in takes}
    need = {i: r - makes.get(i, 0.0) for i, r in takes.items() if r > makes.get(i, 0.0)}
    return final, need, machines


def outputs(purpose: str, site: Mapping[str, Any], book: Any = None) -> tuple[dict[str, float], dict[str, int]]:
    """(final products a second, machines it places)."""
    final, _, machines = flows(purpose, site, book)
    return final, machines


def full_flows(purpose: str, site: Mapping[str, Any], book: Any = None
               ) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """(items a second its machines make, and use, at full speed; the machines it places): from its layout's
    machines and recipes, else its spec's drill pairs, else nothing known."""
    from companion import catalog_planner, layout_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    makes: dict[str, float] = {}
    takes: dict[str, float] = {}
    machines: dict[str, int] = {}
    entities = layout_of(purpose, site)
    if not entities and spec.get("defense_production"):   # (its row, as laid out: companion/defense_production.py)
        from companion import defense_production
        entities = defense_production.layout(site)
    if not entities and (spec.get("tier") or spec.get("military")):
        # (the science path's build, as laid out at its rate: companion/tier_builds.py; the grey goal's, its line)
        from companion import tier_builds
        entities = tier_builds.layout(site, "military-science-pack" if spec.get("military") else None)
    if entities:
        mined = 0.0       # ore its drills mine (a drill has no recipe)
        smelters = 0.0    # furnaces with no recipe set: they smelt what is put in them
        for e in entities:
            name = str(e.get("prototype"))
            machines[name] = machines.get(name, 0) + 1
            recipe = book.recipes.get(str(e.get("recipe"))) if book is not None and e.get("recipe") else None
            if recipe is not None and recipe.energy > 0:
                speed = book.machines[name].speed if name in book.machines else 0.5
                for item, amount in recipe.products:
                    makes[item] = makes.get(item, 0.0) + speed / recipe.energy * float(amount)
                for item, amount in recipe.ingredients:
                    takes[item] = takes.get(item, 0.0) + speed / recipe.energy * float(amount)
            elif name == "lab":
                makes["research"] = makes.get("research", 0.0) + 1.0
            elif name in ("steam-engine", "solar-panel"):
                makes["power"] = makes.get("power", 0.0) + (0.9 if name == "steam-engine" else 0.06)
            elif name in DRILL_RATE:
                mined += DRILL_RATE[name]
            elif "furnace" in name:
                smelters += book.machines[name].speed if book is not None and name in book.machines else 1.0
        resource = str(spec.get("resource") or "")
        if mined and resource and not makes.get(resource):
            # (the game's layouts set no recipe: drills on the goal's ore, into furnaces when it has any; the
            # electric mining goal counted for nothing, so the burner rows kept growing, run 20, 2026-10-25)
            product = layout_planner.PRODUCTS.get(resource, resource) if smelters else resource
            recipe = book.recipe_for(product) if book is not None and product != resource else None
            ore = dict(recipe.ingredients).get(resource) if recipe is not None else None
            if ore and recipe.energy > 0:
                rate = min(mined / float(ore), smelters / recipe.energy) * recipe.makes(product)
                makes[product] = makes.get(product, 0.0) + rate   # (its ore mined and smelted within it)
            elif product == resource:
                makes[resource] = makes.get(resource, 0.0) + mined
    elif spec.get("resource") and spec.get("pairs"):
        resource, pairs = str(spec["resource"]), int(spec["pairs"])
        rate = DRILL_RATE["burner-mining-drill"] * pairs
        product = layout_planner.PRODUCTS.get(resource, resource) if spec.get("output") == "furnace" else resource
        if product != resource:   # (smelted: as much as the recipe gives for the ore the drills mine)
            recipe = book.recipe_for(product) if book is not None else None
            ore = dict(recipe.ingredients).get(resource) if recipe is not None else None
            rate = rate / float(ore) * recipe.makes(product) if ore else rate
        makes[product] = rate
        machines["burner-mining-drill"] = pairs
        if spec.get("output") == "furnace":
            machines["stone-furnace"] = pairs
    elif spec.get("bus") and isinstance(site.get("base_plan"), Mapping) and site["base_plan"].get("stages"):
        # A bus under way makes its science goal once its stages are up: its packs at its rate. Counted for
        # nothing, a half-built bus lost to whatever opening step came next (run 29, 2026-10-25).
        saved = site["base_plan"]
        spm = float(saved.get("spm") or 0)
        for pack in saved.get("packs") or ():
            makes[str(pack)] = makes.get(str(pack), 0.0) + spm / 60.0
    elif spec.get("electric_smelting") or spec.get("electric_supply"):   # (its spec's size, belt-fed)
        from companion import electric_start
        made, placed = electric_start.makes(purpose, site)
        makes.update(made)
        machines.update(placed)
    elif spec.get("wall_block"):   # (laid out by its own planner: walls at the row's rate, from bricks)
        from companion import wall_block
        makes["stone-wall"] = wall_block.WALL_RATE
        takes["stone-brick"] = wall_block.WALL_RATE * 5
        machines["assembling-machine-1"] = 1
    return makes, takes, machines


def item_value(item: str, prio: Any, book: Any) -> float:
    if item == "research":   # (a lab turns science into research: worth what its packs are wanted for)
        packs = ("automation-science-pack", "logistic-science-pack")
        return max((prio.of(p).weight for p in packs), default=0.3) * 2.0
    if item == "power":
        return prio.of("power").weight * 2.0
    return prio.of(item).weight * raw_cost(item, book)


def hand_work(machines: Mapping[str, int], facts: Any = None) -> float:
    """Engineer-seconds a minute its burners' fuel will take to carry."""
    from companion import ledger
    fuel = ledger.fuel_takes({"machines": dict(machines)}, facts)
    return sum(fuel.values()) * 60.0 * HAND_SECONDS_PER_FUEL


def hand_load(site: Mapping[str, Any], facts: Any = None) -> float:
    """The share of the engineer's time its base's burners take already in fuel carried by hand: every block's
    burners, less those fed otherwise (a coal loop feeds itself; a build that `replaces` another's machines
    feeds them, as coal by belt feeds the boilers)."""
    from companion import catalog_planner
    fed: dict[str, set] = {}
    for p in catalog_planner.built_purposes(site):
        spec = catalog_planner.PURPOSES.get(p) or {}
        for q in spec.get("replaces") or ():
            fed.setdefault(q, set()).update(spec.get("replaces_machines") or {"*"})
    total = 0.0
    for b in site.get("base") or []:
        if not isinstance(b, Mapping):
            continue
        spec = catalog_planner.PURPOSES.get(str(b.get("purpose"))) or {}
        if spec.get("output") == "loop" or b.get("purpose") in catalog_planner.BELT_FED:
            continue   # (it feeds itself, or its coal comes by belt)
        gone = fed.get(str(b.get("purpose")), set())
        if "*" in gone:
            continue
        total += hand_work({k: v for k, v in (b.get("machines") or {}).items() if k not in gone}, facts)
    return total / 60.0


def hand_price(site: Mapping[str, Any], facts: Any = None) -> float:
    """What an engineer-second a minute of hand work is worth now: HAND_WEIGHT with its time free, rising
    steeply as its time fills (what its burners take already, or what the game measured, whichever is more).
    Priced flat, burner rows kept coming until 55 burners took most of its time (run 20, 2026-10-25)."""
    load = min(LOAD_CAP, max(busy_share(site), MODEL_DUTY * hand_load(site, facts)))
    return HAND_WEIGHT / (1.0 - load)


def failures(site: Mapping[str, Any]) -> dict[str, int]:
    """How often each goal failed to plan or build lately (the game's tally). A goal that cannot be built here (a
    coal belt for a boiler with no room behind it) was picked first again and again, failing each time (runs 24 to
    27, 2026-10-25): its odds now fall with each failure."""
    out = {}
    for purpose, f in (site.get("failures") or {}).items() if isinstance(site.get("failures"), Mapping) else ():
        count = f.get("count") if isinstance(f, Mapping) else f
        if isinstance(count, (int, float)) and count > 0:
            out[str(purpose)] = int(count)
    return out


def busy_share(site: Mapping[str, Any]) -> float:
    """The share of its measured time lately that went to hand work (time_use: the game's tally)."""
    used = site.get("time_use") if isinstance(site.get("time_use"), Mapping) else {}
    shares = used.get("shares") if isinstance(used.get("shares"), Mapping) else {}
    return min(1.0, sum(float(v) for k, v in shares.items() if str(k).split(":", 1)[0] in HAND_TAGS))


MINE_SECONDS_PER_FUEL = 2.0   # engineer-seconds to mine one piece of fuel by hand (what nothing supplies)

# --- what a robot network saves ----------------------------------------------------------------------------------

ROBOT_SHARE = 0.7        # of placing, walking and rebuilding its robots take off its hands where they reach
WALK_SECONDS = 3.0       # walking to a building, its share, besides BUILD_SECONDS to make and place it
REBUILD_SECONDS = 10.0   # a rebuild by hand: there, the item, placed
REBUILD_WINDOW = 30.0    # game minutes the rebuilds it keeps count over (its rebuild log)
GROWTH_SHARE = 0.25      # of its standing buildings the base is taken to add over HAND_HORIZON, past the steps ahead
ROBOT_CAP = 6.0          # (score at most: never past a starved plate or a defense emergency, which come before)
ALWAYS_WEIGHED = ("robot-network",)   # goals weighed beside the opening's steps whenever they can be built
WEIGHED_BAR = 1.0        # ...and taken after the opening once their score clears this (they save more than they cost)


def buildings(purpose: str, site: Mapping[str, Any], book: Any) -> int:
    """Buildings a goal places: its layout's or spec's machines, else one of each thing its spec requires."""
    from companion import catalog_planner
    try:
        _, _, machines = full_flows(purpose, site, book)
    except Exception:
        machines = {}
    n = sum(int(k) for k in machines.values())
    return n or len((catalog_planner.PURPOSES.get(purpose) or {}).get("requires_recipes") or ())


def robots_saved(site: Mapping[str, Any], book: Any, later: Iterable[str]) -> float:
    """Engineer-seconds a minute a robot network would take off its hands where it does not reach yet: placing and
    walking to the buildings ahead (the opening's steps, and the base growing by GROWTH_SHARE of itself) over
    HAND_HORIZON, and the rebuilds it has been making. Nothing has weighed the network, and in a real game it was never
    chosen (L-3, SO-6, 2026-10-30)."""
    from companion import robo_grid
    try:
        covered, _ = robo_grid.coverage(site)
    except Exception:
        covered = 0.0
    standing = sum(int(b.get("buildings") or 0) for b in site.get("base") or [] if isinstance(b, Mapping))
    ahead = sum(buildings(p, site, book) for p in dict.fromkeys(later)) + GROWTH_SHARE * standing
    tick = float(site.get("tick") or 0)
    log = site.get("rebuilt") if isinstance(site.get("rebuilt"), (list, tuple)) else []
    rebuilt = sum(1 for r in log if isinstance(r, Mapping) and isinstance(r.get("rebuilt"), (int, float))
                  and 0 <= tick - float(r["rebuilt"]) <= REBUILD_WINDOW * 3600)   # (those of the last REBUILD_WINDOW)
    per_minute = ahead * (BUILD_SECONDS + WALK_SECONDS) / HAND_HORIZON + rebuilt * REBUILD_SECONDS / REBUILD_WINDOW
    return per_minute * ROBOT_SHARE * max(0.0, 1.0 - float(covered or 0.0))


def coal_draw_of(machines: Mapping[str, int]) -> float:
    """Coal a second a goal's burners burn, as the coal loop is sized (catalog_planner.COAL_DRAW)."""
    from companion import catalog_planner
    return sum(rate * int(machines.get(name) or 0) for name, rate in catalog_planner.COAL_DRAW.items())


def fuel_spare(site: Mapping[str, Any], books: Any = None) -> float:
    """Coal a second the engineer can collect for more hand-fed burners: what its coal loops spare (LOOP_NET a looped
    drill) and its coal chests fill (drills into chests), over COAL_MARGIN (coal must wait for every trip), less what
    the hand-fed burners standing burn (catalog_planner.coal_draw). Coal on belts to boilers or furnaces is neither:
    it feeds what it is belted to. Negative: short already. (The ledger's coal budget counted none of the burners'
    draw: 22 burners ran on one 4-drill loop while the weighing saw 0.85 coal a second spare, and about 100 coal was
    mined by hand, run 56, 2026-10-26; elsewhere it counts belted boilers.) `books` is not read (kept for callers)."""
    from companion import catalog_planner
    return chest_coal(site) / catalog_planner.COAL_MARGIN + catalog_planner.LOOP_NET * catalog_planner.loop_drills(
        site) / catalog_planner.COAL_MARGIN - catalog_planner.coal_draw(site)


def chest_coal(site: Mapping[str, Any]) -> float:
    """Coal a second its blocks of drills into chests fill (their measured capacity, else their spec's drills)."""
    from companion import catalog_planner, rates
    total = 0.0
    for b in site.get("base") or []:
        spec = catalog_planner.PURPOSES.get(str(b.get("purpose"))) if isinstance(b, Mapping) else None
        if not spec or spec.get("resource") != rates.FUEL or spec.get("output") == "loop":
            continue   # (not coal, or a loop: counted by its drills)
        made = ((b.get("capacity") or {}).get("makes") or {}).get(rates.FUEL) if isinstance(b.get("capacity"),
                                                                                             Mapping) else None
        if isinstance(made, (int, float)) and not isinstance(made, bool):
            total += max(0.0, float(made))   # (measured: nothing, if its coal is mined out)
        elif not catalog_planner.spent(b):
            machines = b.get("machines") or {}
            total += sum(DRILL_RATE.get(n, 0.0) * int(k or 0) for n, k in machines.items())
    return total


def unfed_coal(machines: Mapping[str, int], spare: float) -> float:
    """Coal a second a goal's burners would burn beyond what its fuel makers spare: to be mined by hand."""
    return max(0.0, coal_draw_of(machines) - max(0.0, spare))


def fuel_mined(machines: Mapping[str, int], spare: float) -> float:
    """Engineer-seconds a minute of fuel a goal's burners take that nothing makes: mined by hand before it is
    carried."""
    return unfed_coal(machines, spare) * 60.0 * MINE_SECONDS_PER_FUEL


def fuel_added(purpose: str, makes: Mapping[str, float]) -> float:
    """Coal a second a fuel maker adds for hand-fed burners: a loop what its drills spare after feeding each other;
    over COAL_MARGIN, as fuel_spare counts it."""
    from companion import catalog_planner, rates
    rate = float(makes.get(rates.FUEL) or 0)
    if (catalog_planner.PURPOSES.get(purpose) or {}).get("output") == "loop":
        rate *= catalog_planner.LOOP_NET / DRILL_RATE["burner-mining-drill"]
    return rate / catalog_planner.COAL_MARGIN


def fuel_saved(purpose: str, makes: Mapping[str, float], short: float) -> float:
    """Engineer-seconds a minute a fuel maker saves: the coal its burners lack, now and with the burner goal it is
    weighed against (`short`, coal a second), is mined by hand, several times slower than taken from a loop or a
    chest; up to that, what it adds is that much less hand mining."""
    return min(fuel_added(purpose, makes), max(0.0, short)) * 60.0 * (MINE_SECONDS_PER_FUEL - HAND_SECONDS_PER_FUEL)


def fuel_growable(purpose: str, site: Mapping[str, Any]) -> bool:
    """A fuel maker that adds coal here: a loop only below its drill cap and the burner budget (as coal_enough)."""
    from companion import catalog_planner, rates
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    if spec.get("resource") != rates.FUEL:
        return False
    if spec.get("output") != "loop":
        return True
    drills = catalog_planner.loop_drills(site)
    burners = sum(int(m.get("burner-mining-drill", 0)) for _, m in catalog_planner._machines(site))
    return drills < catalog_planner.LOOP_MAX and burners - drills < catalog_planner.BURNER_BUDGET


def fuel_goals(site: Mapping[str, Any], candidates: Iterable[str], available: Any) -> list[str]:
    """The fuel makers that can be built here (`available(goal)`) and are not among `candidates`: the self-fed ones
    (a coal loop) while one can grow, else the rest (drills into chests, whose own coal is carried to them)."""
    from companion import catalog_planner
    makers = [p for p in catalog_planner.PURPOSES if p not in candidates and fuel_growable(p, site) and available(p)]
    loops = [p for p in makers if (catalog_planner.PURPOSES[p] or {}).get("output") == "loop"]
    return loops or makers


def maker(row: Mapping[str, Any]) -> bool:
    """A weighed goal that makes fuel."""
    from companion import rates
    return bool((row.get("makes") or {}).get(rates.FUEL))


def net_maker(row: Mapping[str, Any]) -> bool:
    """A weighed goal that adds more coal to collect than its own burners burn (drills into chests burn some)."""
    return maker(row) and float(row.get("coal_net") or 0) > 0


HAND_MADE = ("supply:craft:", "supply:mine:", "plan:mine:")   # time_use: making by hand what machines could


def craft_saved(makes: Mapping[str, float], site: Mapping[str, Any]) -> float:
    """Engineer-seconds a minute of hand work a goal's machines take over: what the engineer crafts by hand now for
    its machines and labs, or mines by hand, of what the goal makes (the game's measure, time_use). Red science by
    machine counted for no hand work saved while the engineer crafted packs a quarter of its time, and a coal loop
    for little while it mined coal by hand two thirds of its time (run 35, 2026-10-25)."""
    used = site.get("time_use") if isinstance(site.get("time_use"), Mapping) else {}
    shares = used.get("shares") if isinstance(used.get("shares"), Mapping) else {}
    total = 0.0
    for k, v in shares.items():
        head = next((h for h in HAND_MADE if str(k).startswith(h)), None)
        if head and str(k)[len(head):] in makes:
            total += float(v)
    return min(1.0, total) * 60.0


def spare_income(item: str, books: Any, site: Mapping[str, Any] | None = None) -> float:
    """Items a second of `item` the base makes beyond what its machines claim: what building gets. Where the ledger
    knows nothing of it, what the furnaces making it give (a burner pair a quarter plate a second)."""
    spare = INCOME_FLOOR
    if books is not None:
        try:
            spare = max(spare, books.budget(item).headroom)
        except Exception:
            pass
    if site is not None and spare <= INCOME_FLOOR and item in ("iron-plate", "copper-plate"):
        from companion import catalog_planner
        try:
            spare = max(spare, catalog_planner.smelters(site, item) * DRILL_RATE["burner-mining-drill"])
        except Exception:
            pass
    return spare


ROCK_META = ("count", "radius")   # (the game's `rocks`: how many and how far, not items)


def on_hand(site: Mapping[str, Any]) -> dict[str, float]:
    """What building can draw on without a machine: what the engineer carries, and what the rocks within its reach give
    (the game's `rocks`: their stone and coal). Stone for the opening's furnaces and boilers comes from rocks round the
    spawn (the user, 2026-10-26): a stone drill block counted 155 stone as 50 minutes of hand mining, and came before
    the iron and steam at minute 4 (run 56)."""
    out: dict[str, float] = {}
    for part in (site.get("inventory"), site.get("rocks")):
        if isinstance(part, Mapping):
            for item, amount in part.items():
                if not isinstance(amount, (int, float)) or isinstance(amount, bool) or item in ROCK_META:
                    continue
                try:
                    total = out.get(str(item), 0.0) + float(amount)
                except OverflowError:   # (an integer past any float)
                    continue
                if amount > 0 and math.isfinite(total):
                    out[str(item)] = total
    return out


def waiting_on(purpose: str, site: Mapping[str, Any]) -> list[str]:
    """The goals not built yet that wait on `purpose` built first (only a wait it ends: coal by belt to the boilers
    stands for the steam power already built, and every goal on power counted as opened by it, so it won by 7.9 to
    2.1 over red science at minute 21, run 46)."""
    from companion import catalog_planner
    done = catalog_planner.built_purposes(site)
    stands_for = {purpose, *(catalog_planner.PURPOSES[purpose].get("replaces") or ())}
    out = [other for other, spec in catalog_planner.PURPOSES.items()
           if other not in done and any(not catalog_planner.has_built(n, done)
                                        for n in set(spec.get("requires_built") or ()) & stands_for)]
    if purpose == "bus-base":   # (the science path held for a plate's bus lane waits on the bus: F-20 B, LIVE-7c)
        from companion import tier_builds
        if "science-tier" not in out and tier_builds.lane_first(site, tier_builds.cached_pick(site)):
            out.append("science-tier")
    return out


_PATHS: dict = {}   # (one site's science path, worked out once: the weighing asks for it many times)


def _path_of(site: Mapping[str, Any]):
    from companion import tier_path
    key = (id(site), id(site.get("tiers")), site.get("request_id"), site.get("tick"), site.get("npc_id"))
    if _PATHS.get("key") != key:
        _PATHS.clear()
        _PATHS.update(key=key, path=tier_path.assess(site))
    return _PATHS["path"]


def known_products(purpose: str, site: Mapping[str, Any], book: Any) -> dict[str, float]:
    """What a goal with no layout yet is known to make, as far as the way to space takes it: each recipe it lists
    (those it needs, and those any one of which it makes) whose product the science path says is due, at what the
    tiers that take it will take running at the rate their research wants (no more than one machine of the recipe
    makes); what another of its recipes uses up is not counted (the gas its plastic is made from). Oil processing:
    plastic and sulfur once due; not the refinery it is built from; nothing before the research that makes them due."""
    from companion import catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    if book is None:
        return {}
    try:
        path = _path_of(site)
    except Exception:
        return {}
    recipes = [book.recipes.get(n) for n in [*(spec.get("requires_recipes") or ()), *(spec.get("requires_any") or ())]]
    recipes = [r for r in recipes if r is not None and r.energy > 0 and book.machine_for(r.category)]
    used = {i for r in recipes for i, _ in r.ingredients}   # (made and used inside it: an intermediate, not an output)
    out: dict[str, float] = {}
    for recipe in recipes:
        machine = book.machine_for(recipe.category)
        for item, amount in recipe.products:
            if amount <= 0 or item in used or not path.due(item).due:
                continue
            # (what the tiers that take it will, running at the rate their research wants: the start a supply is due
            # at, a pack a minute, is not what opening its way is worth; one machine at most)
            takes = max((path.wanted(t.pack) * float(row.get("per") or 0) for t in path.tiers for row in t.chain
                         if row.get("name") == item), default=0.0)
            if takes > 0:
                out[item] = max(out.get(item, 0.0), min(machine.speed / recipe.energy * amount, takes))
    return out


def opened_value(purpose: str, site: Mapping[str, Any], book: Any, prio: Any, facts: Any, price: float) -> float:
    """What building `purpose` opens the way to: for each goal waiting on it, UNLOCK_SHARE of what that one makes (its
    layout's products) and the hand work it takes away. A goal with no layout yet (oil processing, whose layout comes
    only once a pumpjack stands) is known by its products, and what it opens in turn counts too, down a chain of such
    goals (UNLOCK_DEPTH deep, each step UNLOCK_SHARE of the one before); a goal reached by two ways counts once, at the
    nearer (walked level by level)."""
    counted, total = {purpose}, 0.0
    level, weight = [purpose], UNLOCK_SHARE
    for _ in range(UNLOCK_DEPTH):
        nearer: list[str] = []
        for goal in level:
            for other in waiting_on(goal, site):
                if other in counted:
                    continue
                counted.add(other)
                theirs, _ = outputs(other, site, book)
                if not theirs:
                    theirs = known_products(other, site, book)
                    nearer.append(other)   # (with no layout: what it opens counts too, a step further)
                total += weight * ((sum(r * item_value(i, prio, book) for i, r in theirs.items()) if prio else 0)
                                   + hand_saved(other, site, facts) * price)
        level, weight = nearer, weight * UNLOCK_SHARE
        if not level:
            break
    return total


def own_cost(purpose: str, site: Mapping[str, Any], book: Any) -> dict[str, float]:
    """What building one goal takes, in construction RAW items: its layout's or spec's buildings, else (a layout
    from the library, not known before it is chosen) one of each thing its spec requires."""
    from companion import catalog_planner, construction
    if book is None:
        return {}
    try:
        _, _, machines = full_flows(purpose, site, book)
    except Exception:
        machines = {}
    names = [n for n, k in machines.items() for _ in range(max(0, int(k)))]
    if not names:
        names = list((catalog_planner.PURPOSES.get(purpose) or {}).get("requires_recipes") or ())
    return construction.cost(names, book)


def steps_ahead(site: Mapping[str, Any]) -> list[str]:
    """The opening's steps not done yet, whether or not they can be built now: what is still to be paid for."""
    from companion import catalog_planner
    out = []
    for purpose, finished in catalog_planner.opening_order(site):
        try:
            if finished(site):
                continue
        except Exception:
            pass
        if purpose in catalog_planner.PURPOSES and purpose not in out:
            out.append(purpose)
    return out


def speedup(makes: Mapping[str, float], bill: Mapping[str, float], books: Any,
            site: Mapping[str, Any] | None = None) -> float:
    """Game minutes taken off building `bill` by what a goal adds (`makes`): the bill over the spare income now,
    less over the spare income with it. It falls as income grows: the first pairs on iron are worth the most."""
    total = 0.0
    for item, amount in bill.items():
        add = float(makes.get(item) or 0)
        if add <= 0 or amount <= 0:
            continue
        spare = spare_income(item, books, site)
        total += (amount / spare - amount / (spare + add)) / 60.0
    return min(SPEED_CAP, total)


def material_minutes(cost: Mapping[str, float], books: Any, site: Mapping[str, Any] | None = None) -> float:
    """Game minutes the plates for a goal take at the spare income now (the longest of them)."""
    longest = max((amount / spare_income(item, books, site) for item, amount in cost.items() if amount > 0),
                  default=0.0)
    return min(MATERIAL_CAP, longest / 60.0)


def hand_saved(purpose: str, site: Mapping[str, Any], facts: Any = None) -> float:
    """Hand work a goal takes away: that of the builds it replaces (its spec's `replaces`, the bus's
    superseded smelting), its burners then fed by belt."""
    from companion import bus_planner, catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    replaces = set(spec.get("replaces") or ())
    if purpose == "bus-base":
        replaces |= set(bus_planner.SUPERSEDED)
    if not replaces:
        return 0.0
    total = 0.0
    for b in site.get("base") or []:
        if isinstance(b, Mapping) and b.get("purpose") in replaces:
            only = spec.get("replaces_machines")
            machines = {k: v for k, v in (b.get("machines") or {}).items() if not only or k in only}
            total += hand_work(machines, facts)
    return total


# --- the weighing --------------------------------------------------------------------------------------------

def weigh(site: Mapping[str, Any], candidates: Iterable[str], opening: Iterable[str] = (),
          planned: str | None = None, asked: str | Iterable[str] | None = None) -> list[dict]:
    """Every candidate goal with its score and parts, best first. `opening`: the opening's steps still to do,
    in its order (the prior); `asked`: goals its strategist asked for (weighed with ASK_PRIOR)."""
    asking = {asked} if isinstance(asked, str) else set(asked or ())
    from companion import catalog_planner, priorities, rates, ratios
    try:
        book = ratios.Book.from_site(site)
    except Exception:
        book = None
    facts = rates.of(site)
    try:
        prio = priorities.assess(site, hold=False)
    except Exception:
        prio = None
    from companion import ledger
    try:
        books = ledger.Ledger.from_site(site)
    except Exception:
        books = None
    order = [p for p in opening]
    price = hand_price(site, facts)
    from companion import pace
    w = weights()
    state = pace.State.of(site)
    try:   # (how soon defenses must go up: a judgment, never in the way of the weighing)
        guard = guard_of(site).value
    except Exception:
        guard = 0.0
    militant = militant_of(site)
    try:
        later = steps_ahead(site)
    except Exception:
        later = list(order)
    costs = {p: own_cost(p, site, book) for p in dict.fromkeys([*later, *order, *candidates])}
    held = on_hand(site)
    weighed = {p: flows(p, site, book) for p in dict.fromkeys(candidates)
               if p in catalog_planner.PURPOSES and not defense(p)}
    # the coal its fuel makers spare, and how short of it the burners standing are
    spare = fuel_spare(site, books)
    coal_short = max(0.0, -spare)
    rows = []
    for purpose, (makes, need, machines) in weighed.items():
        if purpose not in catalog_planner.BELT_FED:   # (its burners' fuel is an input too: carried, or mined by hand)
            from companion import rates
            burn = float(ledger.fuel_takes({"machines": dict(machines)}, facts).get(rates.FUEL) or 0) * MODEL_DUTY
            if burn > float(makes.get(rates.FUEL) or 0):   # (a coal loop feeds itself)
                need = {**need, rates.FUEL: need.get(rates.FUEL, 0.0) + burn - float(makes.get(rates.FUEL) or 0)}
        fed = feed_share(need, books)
        value = fed * sum(rate * item_value(item, prio, book) for item, rate in makes.items()) if prio else 0.0
        unlocks = opened_value(purpose, site, book, prio, facts, price)
        mined = 0.0 if purpose in catalog_planner.BELT_FED else fuel_mined(machines, spare)
        added = 0.0 if purpose in catalog_planner.BELT_FED else (hand_work(machines, facts) + mined) * price
        saved = (hand_saved(purpose, site, facts) + fuel_saved(purpose, makes, coal_short)) * price
        if (catalog_planner.PURPOSES.get(purpose) or {}).get("robot_network"):
            saved += min(ROBOT_CAP, robots_saved(site, book, later) * price)
        # what comes after it: the opening's other steps still to build
        ahead: dict[str, float] = {}
        for other in later:
            if other != purpose:
                for item, amount in (costs.get(other) or {}).items():
                    ahead[item] = ahead.get(item, 0.0) + amount
        ahead = {i: a - float(held.get(i) or 0) for i, a in ahead.items() if a > float(held.get(i) or 0)}
        # (and the engineer's own minutes it frees, counted HAND_HORIZON ahead: what it makes by hand now)
        # (what it adds counts as far as the base can feed it: burners with no coal for them smelt nothing)
        faster = min(SPEED_CAP, fed * speedup(makes, ahead, books, site) + craft_saved(makes, site) / 60.0 * HAND_HORIZON)
        cost = sum(machines.values()) * BUILD_SECONDS / 60.0 + material_minutes(costs.get(purpose) or {}, books, site)
        prior = 1.0 / (1 + order.index(purpose)) if purpose in order else 0.0
        under_way = catalog_planner.unfinished(purpose, site) and purpose in catalog_planner.built_purposes(site)
        failed = failures(site).get(purpose, 0)
        odds = FAIL_ODDS ** failed
        try:   # (how due by the pace targets: a judgment, never in the way of the weighing)
            due, milestone = pace.due(purpose, site, makes, machines, state)
        except Exception:
            due, milestone = 0.0, None
        parts = {"value": value, "unlocks": unlocks, "hand_added": added, "hand_saved": saved, "speedup": faster,
                 "cost": cost, "prior": prior, "planned": purpose == planned, "under_way": under_way, "odds": odds,
                 "due": due, "asked": purpose in asking, "guard": guard * guard_share(purpose, site) if guard else 0.0,
                 "militant": militant}
        rows.append({"goal": purpose, "score": round(score(parts, w), 3), "value": round(value, 3),
                     "unlocks": round(unlocks, 3),
                     "fed": round(fed, 2), "hand_added": round(added, 3), "hand_saved": round(saved, 3),
                     "speedup": round(faster, 2),
                     "cost": round(cost, 2), "prior": round(prior, 3), "planned": purpose == planned,
                     "under_way": under_way, "odds": round(odds, 3), "due": round(due, 3), "milestone": milestone,
                     "asked": purpose in asking, "guard": round(parts["guard"], 3), "militant": militant,
                     "coal_mined": round(mined, 2),
                     "coal_net": round(fuel_added(purpose, makes) - (0.0 if purpose in catalog_planner.BELT_FED
                                                                     else coal_draw_of(machines)), 3),
                     "makes": {k: round(v, 3) for k, v in makes.items()}})
    rows.sort(key=lambda r: -r["score"])
    return rows


HAND_FLOOR = 0.25   # a goal its base cannot feed yet is still worth this much (the engineer can feed it by hand)


def feed_share(need: Mapping[str, float], books: Any) -> float:
    """How much of what a goal needs from the rest of the base the base can spare (the ledger's supply less
    what is already claimed), its least-fed input deciding: a consumer is worth only what can feed it, and a
    producer of what is short is worth the more (its item weighs more in priorities)."""
    if not need or books is None:
        return 1.0
    share = 1.0
    for item, rate in need.items():
        if rate <= 0:
            continue
        try:
            b = books.budget(item)
            spare = max(0.0, b.supply - b.average)
        except Exception:
            continue
        share = min(share, HAND_FLOOR + (1 - HAND_FLOOR) * min(1.0, spare / rate))
    return share


def defense(purpose: str) -> bool:
    """A goal that places defenses: weighed by the defense judgment (companion/defense_brain.py, the wall line's
    rules), not by what it makes."""
    from companion import catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    return any(spec.get(k) for k in ("perimeter", "starter", "brain", "outposts", "ally_help", "ammo_line"))


_held: dict[Any, tuple[str, int]] = {}
_lock = threading.Lock()


def choose(site: Mapping[str, Any], candidates: Iterable[str], opening: Iterable[str] = (),
           planned: str | None = None, asked: str | Iterable[str] | None = None,
           fuel: Any = None) -> tuple[str, list[dict]] | None:
    """The goal to build next and the weighing behind it, or None with nothing to weigh. The goal in hand is
    kept unless another clears it by MARGIN (or it has been held HOLD_TICKS). `fuel(goal)`: whether a fuel maker can
    be built here; given, coal makers are weighed too when its burners are short of coal or the best goal would be,
    and one is built first when the goal chosen would burn coal nothing spares (fuel_first)."""
    candidates = list(dict.fromkeys(candidates))
    rows = weigh(site, candidates, opening, planned, asked)
    if not rows:
        return None
    npc, tick = site.get("npc_id"), int(site.get("tick") or 0)
    with _lock:
        held = _held.get(npc)
        if held and held[1] > tick:   # (a save loaded from before it: nothing held)
            held = None
            _held.pop(npc, None)

    def keep(rows: list[dict]) -> dict:
        best = rows[0]
        kept = next((r for r in rows if r["goal"] == held[0]), None) if held else None
        if kept and kept is not best and best["score"] < kept["score"] + MARGIN and tick - held[1] < HOLD_TICKS:
            return kept
        return best
    best = keep(rows)
    if fuel is not None and (best.get("coal_mined") and not maker(best) or coal_short_now(site)):
        extra = fuel_goals(site, candidates, fuel)
        if extra:
            candidates += extra
            rows = weigh(site, candidates, opening, planned, asked)
            best = keep(rows)
    note = {}
    first = fuel_first(site, best, rows)
    if first is not None:
        note["fuel_first"] = best["goal"]
        best = first
    with _lock:
        if not held or held[0] != best["goal"]:
            _held[npc] = (best["goal"], tick)
    record(site, best, rows, note)
    return best["goal"], rows


def coal_short_now(site: Mapping[str, Any]) -> bool:
    """The burners standing burn more coal than its fuel makers spare."""
    from companion import ledger
    try:
        books = ledger.Ledger.from_site(site)
    except Exception:
        books = None
    return fuel_spare(site, books) < 0


def fuel_first(site: Mapping[str, Any], best: Mapping[str, Any], rows: list[dict]) -> dict | None:
    """The fuel maker to build before `best` when its burners would burn coal nothing spares (to be mined by hand),
    or None: coal grows ahead of the burners it feeds, never hand mining as the plan (the user, 2026-10-26: a coal
    loop of 2, grown to 4 and then 6, more than enough for every furnace and boiler; an iron block of 11 burners went
    up on one 4-drill loop and about 100 coal was mined by hand, run 56). A self-fed maker (a loop) first. Its score
    still prices that coal (coal_mined), for a learned weight to take over."""
    from companion import catalog_planner
    if not best.get("coal_mined") or maker(best):
        return None
    makers = [r for r in rows if net_maker(r) and fuel_growable(r["goal"], site)]
    loops = [r for r in makers if (catalog_planner.PURPOSES.get(r["goal"]) or {}).get("output") == "loop"]
    return max(loops or makers, key=lambda r: r["score"]) if makers else None


# --- what its strategist asked for -------------------------------------------------------------------------------

ASK_HOLD = 108000   # ticks (30 game minutes) a goal its strategist asked for is weighed again when passed over
_asks: dict[Any, dict[str, int]] = {}


def blocks_of(site: Mapping[str, Any], goal: str) -> int:
    return sum(1 for b in site.get("base") or [] if isinstance(b, Mapping) and b.get("purpose") == goal)


def remember_ask(site: Mapping[str, Any], goal: str) -> None:
    """A goal its strategist asked for "next" or "later": weighed with ASK_PRIOR at this request and, passed over or
    failing to plan, at the next ones, until built or ASK_HOLD has passed (one weighed once and passed over was lost,
    the intention counted done)."""
    with _lock:
        _asks.setdefault(site.get("npc_id"), {})[goal] = (int(site.get("tick") or 0), blocks_of(site, goal))


def asks(site: Mapping[str, Any]) -> list[str]:
    """Its strategist's asks still standing, the newest first: none built since it was asked (one more block of it, a
    loop grown again included; a bus with stages left still asked), none older than ASK_HOLD, none asked after this
    tick (a save loaded from before it)."""
    from companion import catalog_planner
    tick = int(site.get("tick") or 0)
    with _lock:
        mine = _asks.get(site.get("npc_id")) or {}
        for goal in [g for g, (t, had) in mine.items() if tick - t > ASK_HOLD or t > tick
                     or (blocks_of(site, g) > had and not catalog_planner.unfinished(g, site))]:
            del mine[goal]
        return sorted(mine, key=lambda g: -mine[g][0])


def forget() -> None:
    with _lock:
        _held.clear()
        _asks.clear()


_LOG_LOCK = threading.Lock()


def record(site: Mapping[str, Any], best: Mapping[str, Any], rows: list[dict],
           note: Mapping[str, Any] | None = None) -> None:
    """One line per choice: every candidate's parts, how busy with hand work, and what was chosen: for these
    weights to be learned from how the factory went on (the ledger's measured rates, the time to the next
    milestone)."""
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    row = {"time": time.time(), "tick": site.get("tick"), "npc": site.get("npc_id"), "request": site.get("request_id"),
           "busy": round(busy_share(site), 3), "chose": best["goal"], "candidates": rows[:12],
           "weights": "fitted" if fitted_weights() else "default", **dict(note or {})}
    try:   # (what the defense urgency saw, for its weight to be learned)
        row["guard"] = guard_of(site).features()
    except Exception:
        pass
    try:   # (the coal its fuel makers spared, and the rocks within reach it counted on)
        from companion import ledger
        row["coal_spare"] = round(fuel_spare(site, ledger.Ledger.from_site(site)), 3)
    except Exception:
        pass
    if isinstance(site.get("rocks"), Mapping):
        row["rocks"] = dict(site["rocks"])
    path = Path(folder) / "decisions" / LOG_NAME
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
