"""Defense items made by machines, at the rate the pressure asks for (F-12; NPC Defense's proving runs and rehearsal 3:
on a death world the iron-start economy hand-crafted turrets, magazines and walls from hand-smelted plates while the
waves grew every few minutes, and walls never came).

**The class of problem**: what defense consumes (turrets, ammunition, walls; later better ammunition and repair
packs) is not made by machines early enough, or at the rate the pressure asks for. Its variants: magazines made by
hand while the turrets run dry between waves; turrets crafted by hand for every wave; walls never made (no bricks);
piercing rounds once researched; electric turrets (no ammunition, but the turrets themselves); a mod's turret or
ammunition; repair packs for what the waves damage.

**One mechanism**: the expected draw of each defense item the engineer can make (perimeter.arms: its best turret, the
ammunition that turret takes, its walls), from the defense judgment (companion/defense_brain.py):

    turrets     what its areas lack now (their need), wanted within HORIZON
    ammunition  the next wave on the base as a whole (defense_brain.base_wave, or the biggest wave a part's pollution
                draws, grown with evolution, if bigger) times MAGS_PER_UNIT magazines a small biter (less with better ammunition: ammo_factor),
                once a wave gap (the gap measured between the waves that came, else WAVE_GAP); and the turrets' first
                loads (LOAD each) within HORIZON
    walls       WALLS_PER_UNIT walls a small biter of the waves coming, a wave gap (what a wave breaks), while no wall
                row stands (wall_block: the walls' own machine supply, bricks first)

against what its blocks make of it (tier_path.have_rate: measured, else built). What is short is built at its
shortfall in a chest-fed row (companion/tier_builds.py: its parts made beside it, sized to what a chest feeds),
the plates going in from chests the engineer fills (the ledger counts what the row takes, so smelting grows for it).
Walls go to the wall machinery (brick smelting, then the wall row) where it can be built. What cannot be built is
passed over, and why is said. Every number here is a weight to learn from the runs (the decisions are logged by the
goal weighing).

A goal the weighing sees (a guarding goal, catalog_planner: "defense-production"), urgent while the waves grow and the
supply is far short: not an override of the defense placements, which come first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

GOAL = "defense-production"
HORIZON = 15 * 60.0       # seconds: turrets wanted, and their first loads, within this
WAVE_GAP = 5 * 60.0       # seconds between waves when none have come twice yet
MIN_GAP, MAX_GAP = 120.0, 15 * 60.0
MAGS_PER_UNIT = 0.5       # plain magazines a small biter takes, misses and armour counted (10 rounds of 5)
LOAD = 10                 # magazines a new turret is loaded with
WALLS_PER_UNIT = 0.5      # walls a small biter of a wave breaks
REPAIRS_PER_UNIT = 0.2    # repair packs a small biter of a wave's damage takes to mend
SHORT = 0.85              # made below this share of the draw: short
URGENT_SHARE = 0.5        # made below this share while the waves grow: urgent
MIN_DRAW = 1 / 600        # items a second below which nothing is worth a machine (one in ten minutes)
MIN_INCOME = 0.05         # plates a second (3 a minute) below which a row of them has nothing to run on (D-18)


@dataclass
class Need:
    item: str
    rate: float           # items a second expected
    have: float           # items a second made by its blocks
    why: str

    @property
    def short(self) -> float:
        return max(0.0, self.rate - self.have)

    def line(self) -> str:
        return f"{self.item} {self.have * 60:.1f} of {self.rate * 60:.1f}/min ({self.why})"


@dataclass
class Draw:
    needs: list[Need] = field(default_factory=list)
    growing: bool = False   # the waves grow (defense_brain.coming over the last)
    gap: float = WAVE_GAP

    def short(self) -> list[Need]:
        return [n for n in self.needs if n.rate >= MIN_DRAW and n.have < SHORT * n.rate]


def wave_gap(site: Mapping[str, Any]) -> float:
    """Seconds between the waves that came (their median gap), else WAVE_GAP."""
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    ticks = sorted(float(a.get("tick")) for a in board.get("attacks") or []
                   if isinstance(a, Mapping) and a.get("tick") is not None)
    gaps = [b - a for a, b in zip(ticks, ticks[1:]) if b > a]
    if not gaps:
        return WAVE_GAP
    gaps.sort()
    return min(MAX_GAP, max(MIN_GAP, gaps[len(gaps) // 2] / 60.0))


def waves_grow(site: Mapping[str, Any]) -> bool:
    """The last wave bigger than the one before it (in small-biter equivalents), the last a half-life old at most."""
    from companion import defense_brain
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    hits = sorted((float(a.get("tick") or 0), defense_brain.wave(a)) for a in board.get("attacks") or []
                  if isinstance(a, Mapping))
    if len(hits) < 2:
        return False
    tick = float(site.get("tick") or hits[-1][0])
    return hits[-1][1] > hits[-2][1] and tick - hits[-1][0] < defense_brain.HALF_LIFE


def draw(site: Mapping[str, Any]) -> Draw:
    """The expected draw of the defense items the engineer can make, against what its blocks make."""
    from companion import defense_brain, perimeter, tier_path
    arms = perimeter.arms(site)
    turrets = arms.get("turrets") or []
    turret = turrets[0] if turrets else {}   # (none it can make: walls and repairs still)
    areas = defense_brain.assess(site)
    lacking = sum(a.need for a in areas)
    # (one wave on the base as a whole, wherever it lands: every exposed part carries the full next wave now, D-13, so
    # their sum counted it once a part; the bigger of the waves seen and the biggest a part's pollution draws, grown
    # with evolution)
    waves = max([defense_brain.base_wave(site)]
                + [a.pressure for a in areas if a.pressure >= defense_brain.MIN_PRESSURE])
    growing = waves_grow(site)
    gap = wave_gap(site)
    standing = sum(1 for g in site.get("guards") or [] if isinstance(g, Mapping))
    out = Draw(growing=growing, gap=gap)
    name = str(turret.get("name") or "gun-turret")
    if lacking and turrets:
        out.needs.append(Need(name, lacking / HORIZON, have(site, name),
                              f"{lacking} turrets lacking within {HORIZON / 60:.0f} min"))
    # Ammunition for the ammo turrets that will fight: the new ones when they take ammo, and those standing (an
    # electric turret first where it can be made leaves the gun turrets standing still to be fed).
    gun = next((t for t in turrets if t.get("kind") == "ammo"), None) if turrets else None
    feeding = turret.get("kind") == "ammo" or any(isinstance(g, Mapping) and g.get("kind") == "ammo-turret"
                                                   for g in site.get("guards") or [])
    if gun and feeding:
        from companion.perimeter import _ammo_for
        ammo = _ammo_for(gun, arms.get("ammo") or {}, site)
        per_unit = MAGS_PER_UNIT / defense_brain.ammo_factor(ammo)
        rate = waves * per_unit / gap + (lacking * LOAD / HORIZON if turret.get("kind") == "ammo" else 0.0)
        if waves or lacking:
            out.needs.append(Need(ammo, rate, have(site, ammo),
                                  f"waves of {waves:.0f} every {gap / 60:.1f} min, {standing} turrets standing"))
    walls = arms.get("walls") or []
    if walls and waves:
        out.needs.append(Need(walls[0], waves * WALLS_PER_UNIT / gap, have(site, walls[0]),
                              f"what waves of {waves:.0f} break"))
    from companion import ratios
    if waves and "repair-pack" in ratios.unlocked(site):   # (what the waves damage, mended)
        out.needs.append(Need("repair-pack", waves * REPAIRS_PER_UNIT / gap, have(site, "repair-pack"),
                              f"mending what waves of {waves:.0f} damage"))
    return out


def have(site: Mapping[str, Any], item: str) -> float:
    """What its blocks make of `item` a second: measured (tier_path.have_rate), and for its own defense rows at least
    what they are built to make (a row whose input chest ran empty is fed again, not built twice)."""
    from companion import tier_path
    total = 0.0
    for b in site.get("base") or []:
        if not isinstance(b, Mapping):
            continue
        rate = sum(tier_path._block_rates(b, item, item))
        if b.get("purpose") == GOAL:
            makes = ((b.get("capacity") or {}).get("makes") or {}) if isinstance(b.get("capacity"), Mapping) else {}
            rate = max(rate, float(makes.get(item) or 0))
        total += rate
    return total


def plates(site: Mapping[str, Any], item: str, rate: float, book: Any = None) -> dict[str, float]:
    """The smelted plates `rate` of `item` a second is made from (a second), through its intermediates (gears)."""
    from companion import plate_lead, ratios
    from companion.ratios import Book
    smelted = set(plate_lead.products()) - {item}   # (steel's own: the iron it is smelted from)
    try:
        got = ratios.solve({item: rate}, book or Book.from_site(site), inputs=smelted).inputs
    except Exception:   # (a judgment, never in the way)
        return {}
    return {k: float(v) for k, v in got.items() if k in smelted and v > 0}


def starved(site: Mapping[str, Any], item: str, book: Any = None, rate: float = 0.0) -> str | None:
    """A plate `item` is made from that the base makes about none of (MIN_INCOME): a row of it would stand empty, its
    chests filled from plates the engineer has not got (D-18: death-world-waves-power, 0 to 41 iron plates in its
    pockets all run, no iron smelting; the turret row went up and made nothing, and no turret stood at any wave)."""
    from companion import plate_lead
    took = plates(site, item, 1.0, book)
    for plate in sorted(took, key=lambda p: (-took[p], p)):   # (what it takes most of first: iron for a turret)
        # (its smelters sized to the row: under what `rate` of it takes, the smelting grows first; D-19)
        if plate_lead.income(site, plate) < max(MIN_INCOME, took[plate] * rate):
            return plate
    return None


def feeder(site: Mapping[str, Any], plate: str, available) -> str | None:
    """What grows `plate`'s income now (plate_lead.income_goal: coal for the burners, its smelting pairs, its array)
    where `available` says it can be built; None when nothing can."""
    from companion import catalog_planner as cp, plate_lead
    try:
        grow = plate_lead.income_goal(site, plate_lead.lead_of(site, plate))
    except Exception:
        grow = None
    # (else what smelts it at all: its first source, as steel smelting, its burner pairs, its electric array; Codex's
    # D-18 review: steel had none, and the hand-back none to go to)
    options = [grow[0] if grow else None, plate_lead.products().get(plate), cp.SUPPLY_GOALS.get(plate),
               cp.ELECTRIC_SUPPLY_GOALS.get(plate)]
    return next((g for g in options if g and g in cp.PURPOSES and available(g)), None)


def plate_draw(site: Mapping[str, Any]) -> dict[str, float]:
    """The plates a second what its defense supply is short of takes (its shortfall only: what rows make already is
    counted where it is made; bounded by the brain's need, D-16), for the plates' lead (plate_lead: D-18)."""
    out: dict[str, float] = {}
    try:
        from companion.ratios import Book
        book = Book.from_site(site)
        for need in draw(site).short():
            for plate, rate in plates(site, need.item, need.short, book).items():
                out[plate] = out.get(plate, 0.0) + rate
                # (a plate smelted from plates, as steel from iron: those too, Codex's D-18 review)
                for under, more in plates(site, plate, rate, book).items():
                    out[under] = out.get(under, 0.0) + more
    except Exception:   # (a judgment, never in the way)
        return {}
    return out


@dataclass
class Pick:
    goal: str                 # GOAL (a row from tier_builds) or a goal of its own (the wall machinery)
    need: Need
    build: Any = None         # tier_builds.Build for GOAL
    skipped: list[str] = field(default_factory=list)


def pick(site: Mapping[str, Any], available=None, items=None) -> Pick | None:
    """The most short defense item that can be made here: its row (at its shortfall), or for walls the wall
    machinery's next goal where `available(goal)` says it can be built; None when nothing short can be. `items`: only
    those (what a plan handed back was making: companion/plan_hold.py)."""
    from companion import tier_builds, wall_block
    from companion.ratios import Book
    if available is None:   # (the goals' own availability: catalog_planner.available)
        from companion import catalog_planner

        def available(goal: str) -> bool:
            return catalog_planner.available(goal, site)
    if items is None:   # (a plan handed back lately: what it was making first, while short; companion/plan_hold.py)
        from companion import plan_hold
        for focus in plan_hold.focuses(site):
            got = pick(site, available, items=focus)
            if got:
                return got
    d = draw(site)
    book = Book.from_site(site)
    skipped: list[str] = []
    for need in sorted(d.short(), key=lambda n: (-(n.short / max(n.rate, 1e-9)), n.item)):
        if items is not None and need.item not in items:
            continue
        if need.item in (perimeter_walls(site)):
            # (walls: their own machine supply where it can be built: the wall row, bricks first while none are made;
            # else a row of walls from the bricks its furnaces make)
            bricks = have(site, "stone-brick") > 0
            if available(wall_block.PURPOSE):
                return Pick(wall_block.PURPOSE, need, skipped=skipped)
            if not bricks and available(wall_block.BRICKS):
                return Pick(wall_block.BRICKS, need, skipped=skipped)
            if not bricks:
                skipped.append(f"{need.item}: no bricks made, and no brick smelting can be built yet")
                continue
        why = tier_builds.buildable(need.item, site, book)
        if why:
            skipped.append(f"{need.item}: {why}")
            continue
        try:
            made, rate = tier_builds.fed_modules(need.item, min(tier_builds.MAX_BLOCK_RATE, need.short), book,
                                                 tier_builds._machine(site), site)
        except tier_builds.NoBuilder as exc:
            skipped.append(f"{need.item}: {exc}")
            continue
        plate = starved(site, need.item, book, rate)   # (what the row it can build takes: Codex's review)
        if plate:
            # (a row with nothing to run on: what grows its plates first, as an assembler row waits on power; a hand-back
            # goes there too, never to a row that cannot run, D-18)
            grow = feeder(site, plate, available)
            if grow:
                return Pick(grow, need, skipped=skipped + [f"{need.item}: no {plate} made yet ({grow} first)"])
            if starved(site, need.item, book):   # (none made at all: the row would stand empty)
                skipped.append(f"{need.item}: no {plate} made, and nothing that makes it can be built yet")
                continue
            # (some made, short of its draw, and nothing more to grow it now: the row all the same, slower)
        build = tier_builds.Build(need.item, rate, f"defense: {need.line()}", skipped=skipped)
        return Pick(GOAL, need, build, skipped)
    return None


def perimeter_walls(site: Mapping[str, Any]) -> list[str]:
    from companion import perimeter
    return list(perimeter.arms(site).get("walls") or [])


def urgent(site: Mapping[str, Any]) -> bool:
    """The waves grow and the supply is far short of what they will take (URGENT_SHARE)."""
    d = draw(site)
    return d.growing and any(n.have < URGENT_SHARE * n.rate for n in d.short())


_PICKED: dict = {}


def cached_pick(site: Mapping[str, Any], available=None) -> Pick | None:
    """pick(site), worked out once per site (the same request's site object, at its tick)."""
    key = (id(site), site.get("request_id"), site.get("tick"), site.get("npc_id"))
    if _PICKED.get("key") != key:
        try:
            got = pick(site, available)
        except Exception:   # (a judgment, never in the way of a goal)
            got = None
        _PICKED.clear()
        _PICKED.update(key=key, pick=got)
    return _PICKED["pick"]


def layout(site: Mapping[str, Any]) -> list[dict]:
    """The row's machines as layout entities, for the weighing (tier_builds.layout_of_build)."""
    from companion import tier_builds
    got = cached_pick(site)
    return tier_builds.layout_of_build(site, got.build) if got and got.goal == GOAL else []


def plan(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """The row for the most short defense item (walls are the wall machinery's goals, not this)."""
    from companion import layout_planner, tier_builds
    got = pick(site)
    if got is None or got.goal != GOAL:
        raise layout_planner.PlanError("no defense item short that a row here can make")
    out = tier_builds.plan_build(site, world, got.build, purpose=GOAL)
    # (its output chests, the ones nothing is supplied into: a turret line's belt starts from one, companion/ammo_line.py)
    from companion.bus import END_CHEST
    supplied = {s["entity_id"] for st in out.get("stages") or [] for s in st.get("manual_supplies") or []}
    out["keep"] = {"item": got.need.item, "outputs": [[e["position"]["x"], e["position"]["y"]]
                                                     for e in out.get("entities") or []
                                                     if e.get("prototype") == END_CHEST
                                                     and e.get("id") not in supplied]}
    out["defense_production"] = {"item": got.need.item, "rate": round(got.build.rate, 4), "draw": round(got.need.rate, 4),
                             "have": round(got.need.have, 4), "why": got.need.why}
    return out
