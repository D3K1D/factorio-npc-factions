"""Plate capacity a step ahead of what the science tiers will take (F-13).

The user's recorded play (Play-Analysis-2026-10-28): 35 furnaces and 28 drills by game minute 40, in standard arrays of
24 stone furnaces (7.5 plates a second, ore and coal on one belt) copied as the base grew, science scaled last at a
clean 1/s red and 1/s green. The engineer had 8 furnaces at minute 39: its smelting grew only once a consumer was
already short (the ledger), its electric blocks were small and never offered again once whole, and its science lines
went in early. The user (2026-10-28): green at 1/s, whole, with nice ratios; efficiency, not a rush.

The class: plate capacity follows demand instead of leading it, so every build waits on plates. Its variants: iron or
copper short of the next tier; both; a smelted item no furnace makes at all; waits the ledger does not see (the
engineer waiting on its furnaces: plate_wait, NPC Worker's W-11); a smelted item of a mod's chain (bricks, steel).

So for each smelted product an electric smelting goal makes:
    draw    what the automated tiers and the next one take at SCIENCE_RATE packs a second each (a military tier at
            the team's weapons share of it; ratios.solve with the smelted items as inputs), or what the base claims of
            it now (the ledger), whichever is more
    target  whole arrays (array_rate: its goal's size, 24 furnaces for plates) covering the draw; one array more
            while the engineer waits on it (plate_wait), and at least one array of anything it waits on with no
            furnace making it (`none`)
    have    what its blocks make (tier_path.have_rate) or its electric arrays smelt at full speed, whichever is more

The ladder: an item with no furnace at all comes first; short of what the tiers draw with its smelting buildable, that
smelting (the next part of an array, or a new copy of the module) and the science lines held (green and the science
path's pack rows: catalog_planner.held_for_plates); the draw met but short of whole arrays: the arrays finished as
growth (supply_first, weighed), science going on; short with nothing buildable: said (news), and science goes on. With a bus standing its own smelting grows the plates (bus_planner), not this.
"""
from __future__ import annotations

import contextlib
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

SCIENCE_RATE = 1.0      # packs a second each tier's line is aimed at (the user's: 1/s red and 1/s green)
ARRAY_RATE = 7.5        # one standard array: 24 stone furnaces (growth.LANE_FURNACES), items a second (else its spec's)
WAIT_TICKS = 1800       # waited on lately at least this long (plate_wait, ticks): the engineer outruns its furnaces
NONE_RATE = 1 / 60      # what a wait on an item no furnace makes is taken to draw at least (a minute's worth)
AFFORD_MINUTES = 8.0    # an array's next part is bought from the plates' income within this, else income first
MIN_LABS = 2            # labs kept while the plates are under the draw (the player ran two until minute 81)
FIRST_LINE = "red-science-automation"   # (the first small line runs research while the plates come: not held)
_CACHE: dict = {}


@dataclass
class Lead:
    item: str
    purpose: str          # the electric smelting goal that makes it
    draw: float           # what the tiers (or the base) take, a second
    target: float         # whole arrays covering it (plus what its waits ask)
    have: float
    why: str

    @property
    def short(self) -> float:
        """Short of its whole arrays (another array, or the rest of one, is wanted)."""
        return max(0.0, self.target - self.have)

    @property
    def under(self) -> float:
        """Short of what is drawn itself (what holds the science lines: its arrays are finished as growth later)."""
        return max(0.0, self.draw - self.have)

    def line(self) -> str:
        return (f"{self.item}: {self.have * 60:.0f}/min made of {self.target * 60:.0f} wanted "
                f"({self.why})")


def science_line(purpose: str) -> bool:
    """A science line held while the plates are short: green and later automation, the science path's rows."""
    return purpose == "science-tier" or (purpose.endswith("-science-automation") and purpose != FIRST_LINE)


def is_pack(item: str) -> bool:
    return item.endswith("science-pack")


def products() -> dict[str, str]:
    """{smelted product: the goal that smelts it}: the electric smelting arrays, and the first sources of what a
    furnace makes from plates (steel: catalog_planner.FIRST_SOURCES)."""
    from companion import catalog_planner as cp
    out = {str(spec["electric_smelting"]): p for p, spec in cp.PURPOSES.items() if spec.get("electric_smelting")}
    for item, goal in cp.FIRST_SOURCES.items():
        out.setdefault(item, goal)
    return out


def arrayed(purpose: str) -> bool:
    """Its goal is built in arrays (electric smelting), not a block of its own size."""
    from companion import catalog_planner as cp
    return bool(cp.PURPOSES[purpose].get("electric_smelting"))


def array_rate(purpose: str) -> float:
    """One array of the goal's product, items a second: its spec's rate (ARRAY_RATE for plates; a smaller block for
    what little of it is wanted, as bricks)."""
    from companion import catalog_planner as cp
    return float(cp.PURPOSES[purpose].get("rate") or ARRAY_RATE)


def tier_draw(site: Mapping[str, Any]) -> dict[str, float]:
    """What the automated tiers and the next one take of each smelted product at SCIENCE_RATE packs a second each."""
    from companion import ratios, tier_path
    from companion.ratios import Book
    path = tier_path.assess(site)
    # (a military tier at the team's weapons share of the research, the path's own tiers at the full rate)
    packs = {t.pack: SCIENCE_RATE * (min(1.0, path.military_share()) if t.military else 1.0) for t in path.tiers
             if (t.machine or path.ahead(t)) and not t.unavailable}
    if not packs:
        return {}
    smelted = set(products())
    book = Book.from_site(site)
    # (what a furnace makes from other smelted items, as steel from iron: its own draw, and its plates in theirs)
    secondary = {p for p in smelted if (r := book.recipe_for(p)) and any(i in smelted for i, _ in r.ingredients)}
    out = {k: v for k, v in ratios.solve(packs, book, inputs=smelted).inputs.items() if k in secondary}
    primary = ratios.solve(packs, book, inputs=smelted - secondary).inputs
    out.update({k: v for k, v in primary.items() if k in smelted - secondary})
    return out


def _claimed(site: Mapping[str, Any], item: str) -> float:
    from companion import ledger
    try:
        return float(ledger.Ledger.from_site(site).budget(item).average)
    except Exception:
        return 0.0


def _have(site: Mapping[str, Any], item: str, purpose: str) -> float:
    from companion import electric_start, tier_path
    made = tier_path.have_rate(site, item)
    return max(made, electric_start.built_rate(site, purpose)) if arrayed(purpose) else made


def _waits(site: Mapping[str, Any]) -> Mapping[str, Any]:
    w = site.get("plate_wait")
    return w if isinstance(w, Mapping) else {}


def leads(site: Mapping[str, Any]) -> list[Lead]:
    """Every smelted product's lead (cached per request), the shortest first (by its share of the target)."""
    key = (id(site), site.get("request_id"), site.get("tick"), site.get("npc_id"))
    if _CACHE.get("key") == key:
        return _CACHE["leads"]
    try:
        out = _leads(site)
    except Exception:   # (a judgment, never in the way)
        out = []
    _CACHE.clear()
    _CACHE.update(key=key, leads=out)
    return out


def _leads(site: Mapping[str, Any]) -> list[Lead]:
    draws = tier_draw(site)
    waits = _waits(site)
    from companion import defense_production
    defense = defense_production.plate_draw(site)   # (what its defense is short of takes too, on top: D-18)
    out: list[Lead] = []
    for item, purpose in products().items():
        draw = max(draws.get(item, 0.0), _claimed(site, item)) + defense.get(item, 0.0)
        have = _have(site, item, purpose)
        wait = waits.get(item) if isinstance(waits.get(item), Mapping) else {}
        if draw <= 0 and not wait:
            continue
        array = array_rate(purpose) if arrayed(purpose) else max(draw, 1e-6)
        target = math.ceil(draw / array - 1e-9) * array
        why = f"the tiers take {draw * 60:.0f}/min" if draws.get(item) else f"the base takes {draw * 60:.0f}/min"
        if defense.get(item):
            why += f" (its defense {defense[item] * 60:.0f}/min of it)"
        if wait.get("none"):   # (a missing source: wanted at once, the science lines held for it)
            draw = max(draw, NONE_RATE)
            target, why = max(target, math.ceil(draw / array - 1e-9) * array), "waited on with no furnace making it"
        elif float(wait.get("ticks") or 0) >= WAIT_TICKS and have >= target - 1e-6:
            # (the engineer outruns its furnaces: one array more than stands)
            target = (math.floor(have / array + 1e-9) + 1) * array
            why += f", waited on {float(wait['ticks']) / 60:.0f} s lately"
        out.append(Lead(item, purpose, draw, target, have, why))
    # (the most plates a second under its draw first, then arrays to finish. By the amount, not the share: copper with no income was 100% under and led iron, which the early game
    # draws two to three times as much of, so iron smelting came at minute 24 (D-19, F-21); the user: "drill on iron
    # right when the game starts since you need way more iron than copper in the start of the game")
    # (a source the engineer is waiting on with no furnace making it at all stays first: that blocks its work now)
    out.sort(key=lambda d: (-(1.0 if (_waits(site).get(d.item) or {}).get("none") else 0.0),
                            -d.under, -(d.short / max(d.target, 1e-9)), d.item))
    return out


def wants(site: Mapping[str, Any], item: str) -> bool:
    """`item` is short of its target (another array of it is wanted)."""
    return any(d.item == item and d.short > 1e-6 for d in leads(site))


# -- income first (F-14) ------------------------------------------------------------------------------------------
# An array's first part carries its belts, poles and store: 512 buildings on 30 iron plates a minute took from minute
# 30 to past 65 (fresh run 61, 2026-10-28), while burner pairs, closed once the array could be built, never grew
# iron; the player went to 8 iron pairs by minute 15 and 35 furnaces by 40. So an array is built when its next part is
# bought from the plates' income within AFFORD_MINUTES; until then the income grows with what can be built now, the
# best plates a minute bought per plate spent (payback): the coal its burners burn first when the loop is short of
# them, then drill-furnace pairs on the ore.

def income(site: Mapping[str, Any], item: str) -> float:
    """What the base makes of `item` now, a second."""
    from companion import tier_path
    return tier_path.have_rate(site, item)


def part_cost(site: Mapping[str, Any], purpose: str) -> dict[str, float]:
    """What an array's next part costs, by item (its kit through the recipes down to plates)."""
    from companion import construction, electric_start, growth, handcraft
    product = str(cp_spec(purpose)["electric_smelting"])
    per = growth.FURNACE_RATE["stone-furnace"]
    index = electric_start.next_array(site, purpose)
    total = max(1, math.ceil(electric_start.target(site, purpose, index) / per - 1e-9))
    first = electric_start.kept(site, purpose, index) is None
    kit = electric_start._kit(product, electric_start.part_size(site, purpose), total, first)
    names = [n for n, k in kit.items() for _ in range(int(k))]
    return dict(construction.cost(names, handcraft.book_for(site)))


def limiting(site: Mapping[str, Any], purpose: str) -> str | None:
    """The smelted item the array's next part waits on longest past AFFORD_MINUTES at its income (a copper array's
    first part is 40 copper and 560 iron plates: iron, Codex's F-14 review), or None when the income buys it soon."""
    try:
        cost = part_cost(site, purpose)
    except Exception:   # (a judgment, never in the way: as before, the array)
        return None
    smelted = set(products())
    late = {item: amount / max(60 * income(site, item), 1e-9) for item, amount in cost.items()
            if item in smelted and amount > 0}
    worst = max(late, key=lambda i: late[i], default=None)
    return worst if worst is not None and late[worst] > AFFORD_MINUTES + 1e-9 else None


def affordable(site: Mapping[str, Any], purpose: str) -> bool:
    """The array's next part is bought from the plates' income within AFFORD_MINUTES: every smelted item it costs."""
    return limiting(site, purpose) is None


_LOCAL = threading.local()   # (per thread: the arrays whose gate is being worked out, or asked about ungated)


def _open() -> set:
    if not hasattr(_LOCAL, "open"):
        _LOCAL.open = set()
    return _LOCAL.open


@contextlib.contextmanager
def ungated(purpose: str) -> Iterator[None]:
    """catalog_planner.available of `purpose` without its income gate, within this (this thread's)."""
    opened = _open()
    had = purpose in opened
    opened.add(purpose)
    try:
        yield
    finally:
        if not had:
            opened.discard(purpose)


def available_ungated(site: Mapping[str, Any], purpose: str, *, designer: bool = False) -> bool:
    from companion import catalog_planner as cp
    with ungated(purpose):
        return cp.available(purpose, site, designer=designer)


def gated(site: Mapping[str, Any], purpose: str, *, designer: bool = False) -> bool:
    """An array its income cannot buy soon while something that grows the income it waits on can be built: held, so
    the weighing never picks it over that (catalog_planner.available; Codex's F-14 review). (An array asked about
    again while its own gate is worked out is not held, so the question ends.)"""
    if purpose in _open():
        return False
    with ungated(purpose):
        short = limiting(site, purpose)
        return bool(short) and income_goal(site, lead_of(site, short), designer=designer) is not None


def lead_of(site: Mapping[str, Any], item: str) -> Lead:
    """`item`'s lead (a bare one when the tiers draw none of it)."""
    found = next((d for d in leads(site) if d.item == item), None)
    return found or Lead(item, products().get(item, ""), 0.0, 0.0, income(site, item), "short now")


def cp_spec(purpose: str) -> Mapping[str, Any]:
    from companion import catalog_planner as cp
    return cp.PURPOSES[purpose]


def _payback(site: Mapping[str, Any], goal_: str, item: str) -> float:
    """Plates a minute of `item` a build of `goal_` adds per plate of it spent (goal_brain's flows and the cost)."""
    from companion import construction, goal_brain, handcraft
    from companion.ratios import Book
    makes, _, machines = goal_brain.full_flows(goal_, site, Book.from_site(site))
    spent = construction.cost([n for n, k in (machines or {}).items() for _ in range(int(k))],
                              handcraft.book_for(site)).get(item, 0.0)
    return float(makes.get(item, 0.0)) * 60 / max(spent, 1.0)


def income_goal(site: Mapping[str, Any], d: Lead, *, designer: bool = False) -> tuple[str, str] | None:
    """What grows `d`'s plates now when an array cannot be afforded yet: coal for the burners first while the loop is
    short of them, else its ore's drill-furnace pairs (catalog_planner.SUPPLY_GOALS) or its own array's next part when
    that is bought soon, the best payback first."""
    from companion import catalog_planner as cp
    if not cp.coal_enough(site) and cp.available("coal-loop", site, designer=designer):
        return "coal-loop", "the coal loop short of what its burners burn"
    # (its burner pairs, and its own array when that is bought soon: burner pairs close once it is, Codex's F-14 review)
    array = products().get(d.item)
    options = [g for g in (cp.SUPPLY_GOALS.get(d.item),) if g and cp.available(g, site, designer=designer)]
    if array and arrayed(array) and cp.available(array, site, designer=designer) and affordable(site, array):
        options.append(array)
    if not options:
        return None
    best = max(options, key=lambda g: _payback(site, g, d.item))
    return best, f"{_payback(site, best, d.item):.1f}/min of {d.item} a plate spent"


def goal(site: Mapping[str, Any], *, designer: bool = False) -> tuple[str, Lead] | None:
    """The goal to build next for the plates' lead, with its lead, or None: met, or nothing buildable. An array (its
    next part, or a new copy) when the income buys it soon; else what grows the income now (income_goal)."""
    from companion import catalog_planner as cp
    from companion import tier_builds
    from companion import bus_planner
    plate = tier_builds.lane_first(site, tier_builds.cached_pick(site))
    if (plate or bus_planner.due_now(site)) and cp.available("bus-base", site, designer=designer):
        # (the tiers' plates come by belt from the bus only, F-20 B: once the bus is due, its own smelting heads and
        # lanes are the plates' growth, not arrays apart from it; the first lane came at minute 43 behind them, F-21)
        lead = next((d for d in leads(site) if d.item == plate), None) or \
            next((d for d in leads(site) if d.under > 1e-6), None)
        if lead is not None:
            return "bus-base", Lead(lead.item, lead.purpose, lead.draw, lead.target, lead.have,
                                    f"{lead.why}; through the bus's own smelting and lanes")
    for d in leads(site):
        if d.short <= 1e-6:
            continue
        array = available_ungated(site, d.purpose, designer=designer)   # (its income gate: below)
        if array and (not arrayed(d.purpose) or affordable(site, d.purpose)):
            return d.purpose, d
        if d.under > 1e-6 or array:   # (income first: under the draw, or an array it cannot afford yet)
            # (the income of what the array waits on: iron for a copper array's belts)
            short = limiting(site, d.purpose) if array and arrayed(d.purpose) else None
            grow = income_goal(site, lead_of(site, short) if short else d, designer=designer)
            if grow:
                return grow[0], Lead(d.item, d.purpose, d.draw, d.target, d.have, f"{d.why}; income first: {grow[1]}")
        if array:
            return d.purpose, d
    return None


def under(site: Mapping[str, Any], item: str) -> bool:
    """`item` is made below what the tiers draw of it."""
    return any(d.item == item and d.under > 1e-6 for d in leads(site))


def under_any(site: Mapping[str, Any]) -> bool:
    return any(d.under > 1e-6 for d in leads(site))


def ready_for_arrays(site: Mapping[str, Any]) -> bool:
    """What the arrays wait on to be built stands (their requires_built: power): the lead's income goals are then
    weighed first; before it, the opening's power and lab come in their turn."""
    from companion import catalog_planner as cp
    done = cp.built_purposes(site)
    return all(cp.has_built(g, done) for p in set(products().values()) if arrayed(p)
               for g in cp.PURPOSES[p].get("requires_built") or ())


def offered(site: Mapping[str, Any]) -> bool:
    """The planner offers the plates' lead's goals here (catalog_planner.supply_first): not beside a bus, whose own
    smelting grows the plates (bus_planner's growth)."""
    return not bool((site.get("base_plan") or {}).get("stages"))


def holds(site: Mapping[str, Any], *, designer: bool = False) -> bool:
    """Science lines wait: a smelted item is short of what the tiers draw (not only of its whole arrays) and what
    grows it can be built now (its smelting, or its income), and is offered: a hold always comes with a way to grow
    the plate (on a bus base the iron array was "not useful here", its pairs never offered, and blue science waited
    for good: Research's stage-blue run, F-19)."""
    from companion import catalog_planner as cp
    if not offered(site):
        return False
    return any(d.under > 1e-6 and (cp.available(d.purpose, site, designer=designer)
                                   or income_goal(site, d, designer=designer)) for d in leads(site))


def news(site: Mapping[str, Any], *, designer: bool = False) -> list[str]:
    """What is short with no array buildable for it (said, so a short lead is never silent)."""
    from companion import catalog_planner as cp
    return [f"{d.line()}: no array of it can be built now" for d in leads(site)
            if d.short > 1e-6 and not cp.available(d.purpose, site, designer=designer)]
