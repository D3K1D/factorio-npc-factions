"""The science tiers ahead and what is due on the way to each: the research chat's milestone signals.

The game exports every science pack its labs take that can be made on the engineer's surface (site["tiers"],
`Progression.tiers` in mod/scripts/progression.lua): the research still to do before each can be made, and what
one pack is made of down to what is mined. Read against the engineer's base (what its blocks make, measured), this
says what is due now, so a goal can ask:

    path = tier_path.assess(site)
    path.due("steel-plate")             # a supply the next tier needs from other machines (furnaces here)
    path.due("military-science-pack")   # the next pack line, or more of a pack already made
    path.due("lab")                     # more labs
    path.dues()                         # everything due now, the most pressing first
    path.research()                     # the technologies the next tiers wait on, prerequisites first

**Which tiers.** A pack is wanted when the way to space researches with it, or, in the team world, at war or for
an aggressive engineer, when it is the military tier: the one extra pack a weapons technology needs beside the
rocket's (grey science). Tiers go in the order of the research before them.

**A military focus** (the traits choose_research arms first for) brings the military tier forward: its aim nearer,
a larger share of research for weapons, its research and pack line alongside the next tier rather than after it, and
labs with its line.

**When.** Each tier has an aim, the game minute it should be automated by: the user's pace targets for the packs
they name (an experienced player's faster half), and an hour after the one before for any other. "Research the
next tier while building it": the next tier's research comes as soon as the tier before is made by machines, its
supplies (what its chain takes from machines other than its own assemblers) as soon as their recipes are
researched, its pack line once its own recipe and its chain are.

**How much.** The research ahead sets the rate: the units of a pack the next tiers' research takes before their
aims, the rocket's research before the rocket's aim, and for the military tier the weapons research it serves,
whichever is the most a minute, with some slack (research off the path: bonuses, the players' picks), shared
between the team's engineers (their labs all work on the team's research). The military tier is sized by the team's
military share of research (more as the biters evolve) beside the way to space. Labs: as many as keep up with the
packs.

Every value is a measured or read quantity against a stated aim, so a learned model can replace the aims and the
slack later (decisions are logged by the research weighing, companion/research_brain.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

MINUTE = 3600   # game ticks

# The user's pace targets (memory: user-pace-targets): the game minute each pack should be made by machines.
AIMS = {"automation-science-pack": 25, "logistic-science-pack": (50, 100), "military-science-pack": 120,
        "chemical-science-pack": 210, "rocket-part": 480}
# (rocket-part: the launch, the last milestone, made in the silo: the first rocket at 7 to 12 hours, its faster half)
# (green: when it can run fully automated at clean ratios, not rushed; the user, 2026-10-28: a range, late end the aim)
NEXT_GAP = 60        # minutes after the tier before, for a pack the targets do not name
ROCKET_AIM = 360     # the rocket silo's research done (the targets: silo 5 to 10 hours, its faster half)
BUILD_LEAD = 30      # minutes before a tier's aim its research should be done, to build its line
SUPPLY_LEAD = 30     # minutes before a tier's aim its supplies should run
MIN_LEFT = 60        # research behind its aim is wanted within this many minutes
SLACK = 1.25         # research off the path (bonuses, the team's military share, the players' picks)
SHORT = 0.85         # made below this share of what is wanted: due
MIN_RATE = 1 / 60    # packs a second a new pack line is wanted at, at least (one a minute)
UNIT_TIME = 30.0     # seconds a lab takes per research unit, when the game does not say
FOCUS_AIM = 0.75     # a military focus brings the military tier's aim this much nearer
FOCUS_SHARE = 0.5    # and gives weapons research at least this share of the research
LAB = "lab"
HELD_MINUTES = 10.0  # a supply held (pockets, chests) for this long of its line is not waited on


@dataclass(frozen=True)
class Due:
    """Whether `item` is due now, and why: `rate` is what is wanted (items a second; for labs, how many), `have` what
    is made now (labs: built), `aim` the game minute it should be there by, `late` the minutes past it (negative:
    ahead), `tier` the pack it serves, `waits` the supplies of its chain not made yet."""
    item: str
    due: bool
    why: str
    rate: float = 0.0
    have: float = 0.0
    aim: float | None = None
    late: float = 0.0
    tier: str | None = None
    waits: tuple[str, ...] = ()

    @property
    def shortfall(self) -> float:
        """The share of what is wanted not made (0 to 1)."""
        return max(0.0, 1.0 - self.have / self.rate) if self.rate > 0 else 0.0

    def line(self) -> str:
        rate = f"{self.have:g} of {self.rate:g}" if self.item == LAB else f"{self.have:.3f}/s of {self.rate:.3f}/s"
        aim = f", aim {self.aim:.0f}m" if self.aim is not None else ""
        return f"{self.item} {'due' if self.due else 'not due'} ({self.why}; {rate}{aim})"

    def as_dict(self) -> dict:
        return {"item": self.item, "due": self.due, "why": self.why, "rate": round(self.rate, 4),
                "have": round(self.have, 4), "aim": self.aim, "late": round(self.late, 1), "tier": self.tier,
                "waits": list(self.waits)}


@dataclass
class Tier:
    pack: str
    recipe: str
    category: str | None
    research: list[dict]          # still to research before it can be made, prerequisites first
    chain: list[dict]             # what one pack takes (per pack), each with its recipe and whether it is unlocked
    rocket: bool = False
    military: bool = False
    serves: float = 0.0           # weapons research units it is the military tier for
    launch: bool = False          # the rocket's part, made in the silo (the last milestone, not a lab pack)
    cost: float = 0.0
    aim: float = 0.0              # the game minute it should be made by machines by (a range's late end)
    aim_from: float = 0.0         # and the earliest that is worth it (a range's early end)
    made: float = 0.0             # packs a second its machines make now
    machine: bool = False         # a block makes it by machine (fed by belts or by hand)
    automated: bool = False       # and nothing of it fed by hand: inserters from a belt, a machine or the bus (hand_fed)
    hand_fed: tuple[str, ...] = ()   # what keeps it from automated: what its line, or all makers of its chain's, take by hand
    hand_chests: list[dict] = field(default_factory=list)   # those blocks' hand chests ({block, x, y, items}: belts to)
    hand_since: float | None = None   # the game minute the oldest of those blocks was built (how long the bridge stands)
    unlocked: bool = False        # its recipe and every recipe of its chain are researched
    unavailable: tuple[str, ...] = ()   # what it needs that can be had neither here nor by research

    def left(self) -> list[str]:
        return [r["name"] for r in self.research if not r.get("trigger")]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _traits(site: Mapping[str, Any]) -> set[str]:
    return {str(t) for t in _list((site.get("personality") or {}).get("traits"))}


def military_focus(site: Mapping[str, Any]) -> bool:
    """An engineer whose traits put arms first (those choose_research arms first for: catalog_planner.AGGRESSIVE,
    and a death world's survivors, catalog_planner.MILITANT)."""
    from companion.catalog_planner import AGGRESSIVE, MILITANT
    return bool(_traits(site) & (AGGRESSIVE | MILITANT))


def military_wanted(site: Mapping[str, Any]) -> bool:
    """The military tier is wanted in the team world (a death world), at war, or with a military focus."""
    if isinstance(site.get("team"), Mapping) and site["team"].get("area"):
        return True
    focus = site.get("focus") if isinstance(site.get("focus"), Mapping) else {}
    return bool(focus.get("foe")) or military_focus(site)


def _blocks(site: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [b for b in _list(site.get("base")) if isinstance(b, Mapping)]


def _block_rates(block: Mapping[str, Any], item: str, recipe: str | None) -> tuple[float, float]:
    """What one block makes of `item` a second, and what more it could make if its output were taken.

    A measured block counts what it made lately: an item it has not made then is 0 (base_memory leaves zero rates out
    and keeps only a block's biggest few, so a missing one is taken as none rather than guessed), so a stopped block is
    not taken for a working one. A block not measured yet (just built) counts what it is built to make. The spare is
    the time the block stood backed up times what it is built to make, never more than that less what it makes."""
    recipes = block.get("recipes")
    names = set(recipes) if isinstance(recipes, (list, Mapping)) else set()
    capacity = block.get("capacity") if isinstance(block.get("capacity"), Mapping) else {}
    built = _num((capacity.get("makes") or {}).get(item)) if (recipe in names or item in names) else 0.0
    measured = block.get("measured") if isinstance(block.get("measured"), Mapping) else None
    if measured is None:
        return built, 0.0
    recent = measured.get("out5") if isinstance(measured.get("out5"), Mapping) else {}
    made = _num(recent.get(item))
    shares = measured.get("shares") if isinstance(measured.get("shares"), Mapping) else {}
    # (the backed-up share is the whole block's: it counts for an item only when the block has made that item in its
    # measured window, so a starved furnace among backed-up assemblers has no spare)
    shown = [measured.get(k) for k in ("out", "peak") if isinstance(measured.get(k), Mapping)]
    proven = made > 0 or any(_num(m.get(item)) > 0 for m in shown)
    spare = min(built * min(1.0, max(0.0, _num(shares.get("backed_up")))), max(0.0, built - made)) if proven else 0.0
    return made, spare


def made_rate(site: Mapping[str, Any], item: str, recipe: str | None = None) -> float:
    """Items a second the base's blocks make of `item` (see _block_rates)."""
    return sum(_block_rates(b, item, recipe)[0] for b in _blocks(site))


def spare_rate(site: Mapping[str, Any], item: str, recipe: str | None = None) -> float:
    """Items a second the base's blocks could make of `item` beside what they make, standing backed up (their
    output not taken): that is there for the taking, not to build."""
    return sum(_block_rates(b, item, recipe)[1] for b in _blocks(site))


def have_rate(site: Mapping[str, Any], item: str, recipe: str | None = None) -> float:
    """What the base makes of `item`, or could make if it were taken."""
    return sum(sum(_block_rates(b, item, recipe)) for b in _blocks(site))


def _machines(site: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for block in _blocks(site):
        for name, n in (block.get("machines") or {}).items():
            out[name] = out.get(name, 0) + int(_num(n))
    return out


def _recipes(block: Mapping[str, Any]) -> set[str]:
    recipes = block.get("recipes")
    return set(recipes) if isinstance(recipes, (list, Mapping)) else set()


def _makers(site: Mapping[str, Any], recipe: str, labs: Iterable[str] = ()) -> list[Mapping[str, Any]]:
    """The blocks with `recipe` and a machine besides labs (one that crafts it)."""
    labs = set(labs) | {LAB}
    return [b for b in _blocks(site) if recipe in _recipes(b) and any(m not in labs for m in (b.get("machines") or {}))]


def _hand_blocks(site: Mapping[str, Any], recipes: Iterable[str], labs: Iterable[str] = ()) -> list[Mapping[str, Any]]:
    """The blocks making any of `recipes` whose machines of it take something by hand."""
    out: list[Mapping[str, Any]] = []
    for r in recipes:
        for b in _makers(site, r, labs):
            fed = b.get("hand_fed")
            if isinstance(fed, Mapping) and _list(fed.get(r)) and all(b is not o for o in out):
                out.append(b)
    return out


def bridged(site: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """The packs made by machine but fed by hand somewhere down their chain (bridge chests standing): {pack: what
    comes by hand}. What connects them is pressed for as their milestone (companion/pace.py)."""
    data = site.get("tiers") if isinstance(site.get("tiers"), Mapping) else {}
    out = {}
    for row in _list(data.get("tiers")):
        pack = row.get("pack") if isinstance(row, Mapping) else None
        if pack and _makers(site, str(row.get("recipe") or pack)):
            items = pack_hand_fed(site, str(pack))
            if items:
                out[str(pack)] = items
    return out


def pack_hand_fed(site: Mapping[str, Any], pack: str) -> tuple[str, ...]:
    """hand_fed for a pack's line down its chain, as the tier path reads it (the pack alone where the game sent no
    tiers): what the pace milestones wait on."""
    tier = assess(site).tier(pack)
    if tier is not None and tier.machine:
        return tier.hand_fed
    # (a tier off the path, the military one where arms do not matter: its chain as the game sent it all the same)
    data = site.get("tiers") if isinstance(site.get("tiers"), Mapping) else {}
    row = next((r for r in _list(data.get("tiers")) if isinstance(r, Mapping) and r.get("pack") == pack), {})
    chain = [c for c in _list(row.get("chain")) if isinstance(c, Mapping) and c.get("name") and not c.get("raw")]
    return hand_fed(site, str(row.get("recipe") or pack), [str(c.get("recipe") or c["name"]) for c in chain])


def _automated(site: Mapping[str, Any], tier: Tier, labs: Iterable[str]) -> bool:
    """A block with the pack's recipe and a machine besides labs (one that crafts it)."""
    return bool(_makers(site, tier.recipe, labs))


def hand_fed(site: Mapping[str, Any], recipe: str, through: Iterable[str] = (), labs: Iterable[str] = ()
             ) -> tuple[str, ...]:
    """What keeps `recipe`'s product from being made without the engineer (the user, 2026-10-29: "chests with items
    mean it won't be automated"): what its makers take by hand when every one of them does (a block's `hand_fed`, as
    base_memory reads the world: an input from a chest nothing but the engineer fills, or a machine nothing feeds); and
    of `through` (the recipes of its chain), each one whose every maker takes something by hand. A recipe no block
    makes is not judged here (a belt from the bus or another force brings it, or nothing does and it is not made)."""
    def items(block, r):   # (what this block's machines of `r` take by hand: {recipe: items}, every one of them)
        fed = block.get("hand_fed")
        return [str(i) for i in _list(fed.get(r))] if isinstance(fed, Mapping) else []

    def by_hand(r):
        makers = _makers(site, r, labs)
        return makers and all(items(b, r) for b in makers)
    makers = _makers(site, recipe, labs)
    out = sorted({i for b in makers for i in items(b, recipe)}) if by_hand(recipe) else []
    out += [r for r in through if r != recipe and by_hand(r)]
    return tuple(dict.fromkeys(out))


@dataclass
class Path:
    minute: float
    tiers: list[Tier] = field(default_factory=list)      # the wanted tiers, in order
    made_in: dict[str, list[str]] = field(default_factory=dict)
    lab_speeds: dict[str, float] = field(default_factory=dict)
    lab_bonus: float = 0.0
    unit_time: float = UNIT_TIME
    share: int = 1
    rocket_packs: dict[str, float] = field(default_factory=dict)   # units of each pack the rocket's research takes
    focus: bool = False           # a military focus: the military tier comes alongside the next one, sooner
    sets: list[dict] = field(default_factory=list)   # the pack sets of the research ahead ({packs, units})
    site: Mapping[str, Any] = field(default_factory=dict)

    # -- the tiers --------------------------------------------------------------------------------------------
    def tier(self, pack: str) -> Tier | None:
        return next((t for t in self.tiers if t.pack == pack), None)

    @property
    def next(self) -> Tier | None:
        """The first wanted tier its machines do not make yet (fed by hand or not: a bridge chest costs the path no
        progress, it only never counts as automated)."""
        return next((t for t in self.tiers if not t.machine), None)

    def ahead(self, tier: Tier) -> bool:
        """Whether `tier` is being reached for now: the next tier; any tier whose research is done (its research
        done, the production it needs is the next step on the way: oil and blue science once researched, the silo
        and its parts once the silo is); or with a military focus the military tier as soon as every tier before it
        can be made (grey science alongside green, not after it)."""
        if tier.machine:
            return False
        if tier is self.next or tier.unlocked:
            return True
        if not (self.focus and tier.military):
            return False
        return all(t.unlocked for t in self.tiers[:self.tiers.index(tier)])

    @staticmethod
    def _researched(row: Mapping[str, Any]) -> bool:
        """A chain item that can be made now: its recipe and a machine for it researched."""
        return bool(row.get("enabled")) and not row.get("machine_tech") and not row.get("unavailable")

    def early(self, tier: Tier, row: Mapping[str, Any]) -> bool:
        """A supply researched ahead of its tier, due now as the first step toward it: its own recipe researched, and
        every tier on the way to space before it made by machines (plastic once researched, red and green running;
        not steel for blue science before red science is automated). Military tiers are a side road: not waited on."""
        if tier.machine or self.ahead(tier) or not self._researched(row):
            return False
        before = self.tiers[:self.tiers.index(tier)]
        return all(t.machine for t in before if t.rocket)

    def held(self, item: str) -> float:
        """How many of `item` the engineer has at hand: its pockets and its chests near it, as the game counts them
        with the tiers (else its pockets, as the site says)."""
        data = self.site.get("tiers") if isinstance(self.site.get("tiers"), Mapping) else {}
        if isinstance(data.get("held"), Mapping):
            return _num(data["held"].get(item))
        inventory = self.site.get("inventory") if isinstance(self.site.get("inventory"), Mapping) else {}
        return _num(inventory.get(item))

    def _driving(self, pack: str) -> tuple[float, float | None]:
        """Packs a second the research ahead wants of `pack` (this engineer's share), and the aim that sets it."""
        best, aim = 0.0, None
        for t in self.tiers:   # the research before each tier not unlocked yet, done a while before its aim
            if t.unlocked:
                continue
            units = sum(_num(r.get("units")) for r in t.research if pack in _list(r.get("packs")))
            when = t.aim - BUILD_LEAD
            rate = units / max(when - self.minute, MIN_LEFT)
            if units and rate > best:
                best, aim = rate, when
        rate = self._rocket(pack)
        if rate > best:
            best, aim = rate, float(ROCKET_AIM)
        tier = self.tier(pack)
        if tier and tier.military and tier.serves > 0:
            # the weapons research it serves, as the team's military share of research beside the way to space (no
            # more than what is left of it)
            share = self.military_share()
            rate = min(self._rocket(None) * share / (1.0 - share), tier.serves / MIN_LEFT)
            if rate > best:
                best, aim = rate, float(ROCKET_AIM)
        return SLACK * best / 60.0 / max(1, self.share), aim

    def _rocket(self, pack: str | None) -> float:
        """Units a minute of `pack` (None: of the most used one) the rocket's research wants to be done by its aim."""
        units = self.rocket_packs.get(pack, 0.0) if pack else max(self.rocket_packs.values(), default=0.0)
        return units / max(ROCKET_AIM - self.minute, MIN_LEFT)

    def military_share(self) -> float:
        """The team's share of research for weapons: more as the biters evolve (catalog_planner.military_due), and
        at least FOCUS_SHARE with a military focus."""
        from companion.catalog_planner import MILITARY_SHARE, MILITARY_SHARE_EVOLVED
        board = self.site.get("threat_board") if isinstance(self.site.get("threat_board"), Mapping) else {}
        evolution = min(1.0, max(0.0, _num(board.get("evolution"))))
        share = MILITARY_SHARE + (MILITARY_SHARE_EVOLVED - MILITARY_SHARE) * evolution
        return max(share, FOCUS_SHARE) if self.focus else share

    def wanted(self, pack: str) -> float:
        """Packs a second of `pack` this engineer's share of the research ahead wants."""
        return self._driving(pack)[0]

    # -- what is due ------------------------------------------------------------------------------------------
    def due(self, item: str) -> Due:
        if item == LAB or item in self.lab_speeds:
            return self._labs()
        tier = self.tier(item)
        if tier:
            return self._pack(tier)
        return self._supply(item)

    def dues(self) -> list[Due]:
        """Everything due now: the next tier and the packs made already, their supplies and the labs, the most
        pressing first (most late, then most short)."""
        items: list[str] = []
        for t in self.tiers:
            if not (t.machine or self.ahead(t)):
                items += [row["name"] for row in t.chain if self._separate(t, row) and self.early(t, row)]
                continue
            if t.machine or self.ahead(t):
                items.append(t.pack)
                items += [row["name"] for row in t.chain if self._separate(t, row)]
        items.append(LAB)
        out = [d for d in (self.due(i) for i in dict.fromkeys(items)) if d.due]
        out.sort(key=lambda d: (-d.late, -d.shortfall, d.item))
        return out

    def _pack(self, tier: Tier) -> Due:
        rate, aim = self._driving(tier.pack)
        if tier.launch:   # (no research takes the rocket's part: its own floor, a part a minute toward the launch)
            rate, aim = max(rate, MIN_RATE), tier.aim
        if tier.machine and tier.launch and tier.made < SHORT * rate:
            # (the silo stands: more parts come from feeding it, not from another silo)
            waits = self._waits(tier, rate)
            return Due(tier.pack, False, "the silo stands; its parts wait on "
                       + (", ".join(waits) if waits else "its inputs being brought"), rate, tier.made, aim,
                       self.minute - aim, tier.pack, waits)
        if tier.machine and not tier.automated:
            # (its line stands, fed by hand: not automated, and no second line for it; belts to it are what is wanted,
            # the hookup's, NPC Factory)
            return Due(tier.pack, False, f"its line stands fed by hand ({', '.join(tier.hand_fed[:4])}): it waits on "
                       "belts from a maker or the bus", rate, tier.made, aim, self.minute - aim if aim is not None
                       else 0.0, tier.pack)
        if tier.automated:
            have = tier.made + spare_rate(self.site, tier.pack, tier.recipe)   # (a line held back is no line short)
            short = have < SHORT * rate
            why = (f"more {tier.pack}: the research ahead wants {rate * 60:.1f} a minute" if short
                   else f"{tier.pack} keeps up with the research ahead")
            return Due(tier.pack, short, why, rate, have, aim, self.minute - aim if aim is not None else 0.0,
                       tier.pack)
        aim = tier.aim
        late = self.minute - aim
        nxt = self.next
        if tier.unavailable:
            return Due(tier.pack, False, f"cannot be had here: {', '.join(tier.unavailable[:3])}", rate, 0.0, aim,
                       late, tier.pack)
        if not self.ahead(tier):
            return Due(tier.pack, False, f"after {nxt.pack if nxt else 'the tiers before it'}", rate, 0.0, aim, late,
                       tier.pack)
        if not tier.unlocked:
            left = [r["name"] + (" (the game triggers it)" if r.get("trigger") else "") for r in tier.research]
            return Due(tier.pack, False, f"research first: {', '.join(left[:3]) or 'its recipes'}", rate, 0.0, aim,
                       late, tier.pack)
        waits = self._waits(tier, max(rate, MIN_RATE))
        what = f"the launch: make {tier.pack}" if tier.launch else f"the next science tier: automate {tier.pack}"
        return Due(tier.pack, True, what, max(rate, MIN_RATE), 0.0, aim, late, tier.pack, waits)

    def _short(self, tier: Tier, name: str, line: float) -> bool:
        """Less of `name` made (or there for the taking) than a line at `line` packs a second takes, and not at hand
        for HELD_MINUTES of it."""
        row = next((r for r in tier.chain if r.get("name") == name), None)
        if row is None or row.get("raw"):
            return False
        need = line * _num(row.get("per"))
        return (have_rate(self.site, name, row.get("recipe")) < SHORT * need
                and self.held(name) < need * HELD_MINUTES * 60)

    def needed(self, tier: Tier, name: str, line: float) -> bool:
        """Whether `name` is needed for `tier`'s packs: it goes straight into them, or into something of the chain
        that is itself short and needed (`by`, as the game lists it). Processing units at hand: what they are made of is
        not needed for the silo's parts. An older export without `by`: everything is."""
        rows = {str(r.get("name")): r for r in tier.chain}
        memo: dict[str, bool] = {}

        def walk(item: str, depth: int) -> bool:
            if item in memo:
                return memo[item]
            memo[item] = False   # (a loop: not needed through itself)
            by = _list(rows[item].get("by")) if item in rows else []
            out = not by or any(b == tier.pack or (b in rows and depth < 12 and self._short(tier, b, line)
                                                  and walk(b, depth + 1)) for b in by)
            memo[item] = out
            return out
        return walk(name, 0)

    def _waits(self, tier: Tier, line: float) -> tuple[str, ...]:
        """The supplies of its chain a new line at `line` packs a second waits on: made by other machines, none made
        (some made does not stop it: tier_builds sizes the line to what is made, tier_builds.partial), not at hand for
        HELD_MINUTES (the silo's parts in a chest by home count as at hand), and needed."""
        def none(name: str, row: Mapping[str, Any]) -> bool:
            return (have_rate(self.site, name, row.get("recipe")) <= 0
                    and self.held(name) < line * _num(row.get("per")) * HELD_MINUTES * 60)
        return tuple(str(row["name"]) for row in tier.chain
                     if self._separate(tier, row) and not row.get("raw") and none(str(row["name"]), row)
                     and self.needed(tier, str(row["name"]), line))

    def _separate(self, tier: Tier, row: Mapping[str, Any]) -> bool:
        """A chain item made by other machines than its pack's own (steel in furnaces, plastic in chemical plants):
        a supply of its own, not part of the pack line."""
        if row.get("raw") or not row.get("recipe"):
            return False
        # (a category of its own, and no machine it can make now crafting both: a locked one joins nothing yet)
        if row.get("category") == tier.category:
            return False
        mine = set(_list(self.made_in.get(tier.category)))
        theirs = set(_list(self.made_in.get(row.get("category"))))
        return not (mine & theirs)

    def _supply(self, item: str) -> Due:
        need, aim, tiers, row_of = 0.0, None, [], None
        for t in self.tiers:
            for row in t.chain:
                # (for a tier made or reached for; or, its own research done, ahead of its tier: plastic once
                # researched, before blue science's own)
                early = self.early(t, row)
                if not (t.machine or self.ahead(t) or early):
                    continue
                if row.get("name") == item and self._separate(t, row):
                    # (its packs as made now or as the research wants them, whichever is more; ahead of its tier, a
                    # start: a pack a minute's worth)
                    rate = MIN_RATE if early else max(t.made, self.wanted(t.pack), 0.0 if t.machine else MIN_RATE)
                    if not t.machine and not self.needed(t, item, max(rate, MIN_RATE)):
                        continue   # (what it goes into is at hand: no need of it for this tier now)
                    need += rate * _num(row.get("per"))
                    tiers.append(t.pack)
                    row_of = row_of or row
                    when = t.aim - SUPPLY_LEAD if not t.machine else None
                    if when is not None and (aim is None or when < aim):
                        aim = when
        if not row_of:
            return Due(item, False, "no tier ahead needs it from machines of its own")
        have = have_rate(self.site, item, row_of.get("recipe"))
        late = self.minute - aim if aim is not None else 0.0
        if row_of.get("unavailable"):
            return Due(item, False, "cannot be had here (no recipe this surface can make, no research left for it)",
                       need, have, aim, late, tiers[0])
        if not row_of.get("enabled") or row_of.get("machine_tech"):
            first = row_of.get("tech") or row_of.get("machine_tech") or "its recipe"
            return Due(item, False, f"research first: {first}", need, have, aim, late, tiers[0])
        short = have < SHORT * need
        at_hand = self.held(item) >= need * HELD_MINUTES * 60   # (enough at hand for a while: no line for it yet)
        why = (f"{', '.join(tiers)} {'takes' if len(tiers) == 1 else 'take'} {need * 60:.1f} a minute"
               + ("" if have > 0 else ", none made yet"))
        if short and at_hand:
            return Due(item, False, f"at hand for now ({self.held(item):g} held)", need, have, aim, late, tiers[0])
        return Due(item, short, why if short else f"made enough for {', '.join(tiers)}", need, have, aim, late,
                   tiers[0])

    def _pack_sets(self) -> list[frozenset]:
        """The sets of packs the research ahead takes (each unit takes every pack of its set): the tiers' research,
        the way to space's and the weapons research the military tier serves (as the game counts them)."""
        sets = {frozenset(_list(r.get("packs"))) for t in self.tiers for r in t.research if _list(r.get("packs"))}
        sets |= {frozenset(_list(r.get("packs"))) for r in self.sets if _list(r.get("packs"))}
        return [s for s in sets if s]

    def _labs(self) -> Due:
        """More labs, when the packs made now, with the pack lines due now (labs come with a new line), keep more labs
        busy than are built: for the set of packs that keeps the most busy (a slow new tier does not hide the labs
        its neighbours' research needs)."""
        machines = _machines(self.site)
        names = set(self.lab_speeds) | {LAB}
        built = sum(n for name, n in machines.items() if name in names)
        speed = (sum(self.lab_speeds.get(name, 1.0) * n for name, n in machines.items() if name in names) / built
                 if built else self.lab_speeds.get(LAB, 1.0)) * (1.0 + self.lab_bonus)
        coming: dict[str, float] = {}
        for t in self.tiers:
            supply = t.made
            if not t.machine:
                d = self._pack(t)
                supply = d.rate if d.due else 0.0
            if supply > 0:
                coming[t.pack] = supply
        best, which = 0.0, None
        for packs in self._pack_sets():
            if all(p in coming for p in packs):
                units = min(coming[p] for p in packs)
                if units > best:
                    best, which = units, packs
        if which is None:
            return Due(LAB, False, "no research its machines' packs can run yet", 0.0, float(built))
        work = best * self.unit_time / max(speed, 0.01)
        need = math.ceil(work - 0.05)
        target = math.ceil(min(self.wanted(p) for p in which) * self.unit_time / max(speed, 0.01) - 0.05)
        if built < need:
            return Due(LAB, True, f"packs wait for labs: {work:.1f} labs' work", float(max(need, target)), float(built))
        return Due(LAB, False, f"labs keep up with the packs ({work:.1f} labs' work)", float(max(need, target)),
                   float(built))

    # -- research ---------------------------------------------------------------------------------------------
    def researching(self) -> list[Tier]:
        """The tiers whose research is due now: up to the next one, and the one after it once the next can be made
        (research the next tier while building this one)."""
        nxt = self.next
        if nxt is None:
            return []
        at = self.tiers.index(nxt) + (1 if nxt.unlocked else 0)
        out = [t for t in self.tiers[:at + 1] if not t.unlocked and not t.unavailable]
        # (a military focus: the military tier's research beside the next tier's, once the tiers before it can be made)
        out += [t for t in self.tiers if self.focus and t.military and t not in out and not t.unlocked
                and not t.unavailable and all(b.unlocked for b in self.tiers[:self.tiers.index(t)])]
        return out

    def research(self) -> list[str]:
        """The technologies those tiers still wait on, the nearest tier's first, prerequisites first."""
        out: list[str] = []
        for t in self.researching():
            out += [n for n in t.left() if n not in out]
        return out

    def describe(self) -> str:
        nxt = self.next
        if not nxt:
            return "every wanted science tier is made by machines"
        state = ("unlocked" if nxt.unlocked else f"research {', '.join(nxt.left()[:3])}")
        return f"next tier {nxt.pack} (aim {nxt.aim:.0f}m, now {self.minute:.0f}m): {state}"

    def as_dict(self) -> dict:
        return {"minute": round(self.minute, 1), "next": self.next.pack if self.next else None,
                "tiers": [{"pack": t.pack, "aim": t.aim, "automated": t.automated, "machine": t.machine,
                           "hand_fed": list(t.hand_fed), "hand_chests": t.hand_chests, "hand_since": t.hand_since,
                           "unlocked": t.unlocked,
                           "made": round(t.made, 4), "wanted": round(self.wanted(t.pack), 4), "left": t.left()[:6]}
                          for t in self.tiers],
                "due": [d.as_dict() for d in self.dues()]}


def assess(site: Mapping[str, Any] | None) -> Path:
    """The tier path of an engineer's site (an empty one when the game sent no tiers)."""
    site = site if isinstance(site, Mapping) else {}
    data = site.get("tiers") if isinstance(site.get("tiers"), Mapping) else {}
    minute = _num(site.get("tick")) / MINUTE
    labs = data.get("labs") if isinstance(data.get("labs"), Mapping) else {}
    rocket = data.get("rocket") if isinstance(data.get("rocket"), Mapping) else {}
    space = site.get("space_path") if isinstance(site.get("space_path"), Mapping) else {}
    path = Path(minute=minute, made_in={k: _list(v) for k, v in (data.get("made_in") or {}).items()},
                lab_speeds={str(k): _num(v, 1.0) for k, v in (labs.get("speeds") or {}).items()},
                lab_bonus=_num(labs.get("bonus")), unit_time=_num(rocket.get("time"), UNIT_TIME) or UNIT_TIME,
                share=max(1, int(_num(data.get("share"), 1))),
                rocket_packs={str(k): _num(v) for k, v in (space.get("packs") or {}).items()}, site=site)
    military = military_wanted(site)
    path.focus = military_focus(site)
    path.sets = [r for r in _list(data.get("sets")) if isinstance(r, Mapping)]
    previous = 0.0
    for row in _list(data.get("tiers")):
        if not isinstance(row, Mapping) or not row.get("pack"):
            continue
        rocket_pack, military_pack = bool(row.get("rocket")), bool(row.get("military"))
        if not (rocket_pack or (military_pack and military)):
            continue
        chain = [c for c in _list(row.get("chain")) if isinstance(c, Mapping) and c.get("name")]
        research = [r for r in _list(row.get("research")) if isinstance(r, Mapping) and r.get("name")]
        tier = Tier(pack=str(row["pack"]), recipe=str(row.get("recipe") or row["pack"]), category=row.get("category"),
                    launch=bool(row.get("launch")),
                    research=research, chain=chain, rocket=rocket_pack, military=military_pack,
                    serves=_num(row.get("serves")), cost=_num(row.get("cost")))
        aim = AIMS.get(tier.pack, previous + NEXT_GAP)
        tier.aim_from, tier.aim = (float(aim[0]), float(aim[1])) if isinstance(aim, tuple) else (float(aim), float(aim))
        if path.focus and military_pack:
            tier.aim *= FOCUS_AIM
            tier.aim_from *= FOCUS_AIM
        previous = max(previous, tier.aim)
        tier.unavailable = tuple(c["name"] for c in chain if c.get("unavailable")) + (
            (tier.pack,) if row.get("unavailable") else ())
        tier.unlocked = (bool(row.get("enabled")) and row.get("machine_ready") is not False and not tier.unavailable
                         and all((c.get("enabled") or c.get("raw")) and not c.get("machine_tech") for c in chain))
        tier.made = made_rate(site, tier.pack, tier.recipe)
        tier.machine = _automated(site, tier, path.lab_speeds)
        if tier.machine:   # (automated only when nothing of it comes by hand, from the pack line down its chain)
            chain_recipes = [str(c.get("recipe") or c["name"]) for c in chain if not c.get("raw")]
            tier.hand_fed = hand_fed(site, tier.recipe, chain_recipes, path.lab_speeds)
        tier.automated = tier.machine and not tier.hand_fed
        if tier.hand_fed:
            chain_recipes = [tier.recipe] + [str(c.get("recipe") or c["name"]) for c in chain if not c.get("raw")]
            blocks = _hand_blocks(site, chain_recipes, path.lab_speeds)
            tier.hand_chests = [{"block": b.get("id"), **c} for b in blocks for c in _list(b.get("hand_chests"))
                                if isinstance(c, Mapping)]
            built = [_num(b.get("accepted")) / 3600 for b in blocks if b.get("accepted") is not None]
            tier.hand_since = round(min(built), 1) if built else None
        path.tiers.append(tier)
    for tier in path.tiers:   # (what is not needed, its consumers at hand, blocks nothing: held processing units)
        raw_row = next(r for r in _list(data.get("tiers")) if isinstance(r, Mapping) and r.get("pack") == tier.pack)
        if not tier.unlocked or tier.unavailable:
            needed = [c for c in tier.chain if path.needed(tier, str(c["name"]), MIN_RATE)]
            tier.unavailable = tuple(str(c["name"]) for c in needed if c.get("unavailable")) + (
                (tier.pack,) if raw_row.get("unavailable") else ())
            tier.unlocked = (bool(raw_row.get("enabled")) and raw_row.get("machine_ready") is not False
                             and not tier.unavailable
                             and all((c.get("enabled") or c.get("raw")) and not c.get("machine_tech") for c in needed))
    return path


def due(site: Mapping[str, Any] | None, item: str) -> Due:
    """`assess(site).due(item)` in one call."""
    return assess(site).due(item)
