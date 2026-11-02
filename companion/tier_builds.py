"""What the science path says is due, built (companion/tier_path.py: the research chat's tier signals, R-1).

The research chat reads the science tiers ahead against the engineer's base and says what is due now and at what
rate: a supply the next tier takes from machines of its own (steel for grey science), the next pack line, more of a
pack already made, more labs. This answers each with a build sized to it:

    pick(site)                      the most pressing due item that can be built here (a Build), or None
    plan(site, world, item=None)    the build: chest-fed rows near home (the engineer fills the input chests and
                                    empties the product chests, as the first grey science rows were), the supplies
                                    the item waits on first, labs with a pack line when labs are due too

**The class of problem**: an item the science path needs is due at a rate, and no goal builds it at that rate.
Its variants: steel for grey science; plastic or sulfur for blue; a new pack line (grey, blue, a mod's pack made in
another category of machine); a pack already made that falls short as the research ahead speeds up; labs too few for
the packs; a due item that waits on a supply nothing makes yet.

**One mechanism**: a builder for the item (the bus's own modules where there is one: steel, piercing rounds and grey
science, red, green and blue science, circuits, gears, engines; else a row of the item's recipe in the machines
that make it, its ingredients from chests), sized to what is due less what is made, at most MAX_BLOCK_RATE a block
(more comes as another block, while it is still due). A pack the bus makes is the bus's to grow (its growth
sections). An item no builder can make here is passed over, and why is said (`skipped`): never dropped silently.

These are goals the weighing and System One see (catalog_planner: science-tier, and the grey line's own goal sized by
the same signal), not overrides: defense comes first.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from companion import belt_lines, blue_modules, layout_planner, military_modules, tier_path
from companion import modules as mods
from companion.modules import Module, Placed
from companion.ratios import Book

GOAL = "science-tier"
MAX_BLOCK_RATE = 0.5    # items a second one chest-fed block makes at most: the engineer carries what it takes
MAX_LABS = 10           # labs one build adds at most
MIN_RATE = tier_path.MIN_RATE


class NoBuilder(ValueError):
    """Nothing here can lay out a block for this item (said, not dropped)."""


@dataclass
class Build:
    item: str
    rate: float                     # items a second (labs: how many)
    why: str
    waits: list[tuple[str, float]] = field(default_factory=list)   # supplies first: (item, items a second)
    labs: int = 0                   # labs added with a pack line
    skipped: list[str] = field(default_factory=list)              # due items passed over, and why
    block: str | None = None        # an imported block from the library that makes it (companion/science_blocks.py)
    block_for: float = 0.0          # the rate it was picked for (its rows judged at that, plan_build)

    def line(self) -> str:
        what = f"{self.rate:g} labs" if self.item == tier_path.LAB else f"{self.item} {self.rate * 60:.1f}/min"
        extra = "".join(f", first {w} {r * 60:.1f}/min" for w, r in self.waits)
        extra += f", {self.labs} labs with it" if self.labs else ""
        return f"{what}{extra} ({self.why})"


def _machine(site: Mapping[str, Any]) -> str:
    machines = site.get("machines") or {}
    return "assembling-machine-2" if "assembling-machine-2" in machines else "assembling-machine-1"


def locked_parts(site: Mapping[str, Any], entities: list) -> list[str]:
    """The buildings of a plan the engineer can neither make (no unlocked recipe, ratios.unlocked) nor holds enough of,
    once each: such a plan is refused here, not in the game (a defense row with an assembler 2 and a fast inserter,
    neither researched, failed the moment it was accepted, and the edge was held by hand again: W-23). Nothing is
    refused when the export lists no recipes (an older game)."""
    from companion import ratios
    if not site.get("recipes"):
        return []
    unlocked = ratios.unlocked(site)
    held = site.get("inventory") if isinstance(site.get("inventory"), Mapping) else {}
    facts = site.get("prototypes") if isinstance(site.get("prototypes"), Mapping) else {}
    wanted: dict[str, int] = {}
    for e in entities:   # (what places it, as the game asks: a modded machine placed by a kit; Codex, W-23)
        name = e.get("prototype") if isinstance(e, Mapping) else None
        item = e.get("item") or (facts.get(name) or {}).get("placement_item") or name if isinstance(e, Mapping) else None
        if isinstance(item, str):
            wanted[item] = wanted.get(item, 0) + 1
    return [i for i, n in wanted.items() if i not in unlocked and float(held.get(i) or 0) < n]


BUS_PACK_STAGES = {"automation-science-pack": "bus-red-science", "logistic-science-pack": "bus-green-science",
                   "chemical-science-pack": "bus-blue-science"}   # (the bus's stage of each pack: modules' names)


BUS_BLOCK_STAGE = "bus-science-block"   # (an imported block making red and green on the bus, U-2)
BLOCK_PACKS = ("automation-science-pack", "logistic-science-pack")


def bus_stages(item: str) -> tuple[str, ...]:
    """The bus stages that may make `item`: its own module's, and the science block's for red and green."""
    own = BUS_MAKES.get(item)
    return (*([own] if own else []), *([BUS_BLOCK_STAGE] if item in BLOCK_PACKS else []))


def on_bus(site: Mapping[str, Any], item: str) -> bool:
    """A pack the engineer's bus makes and grows (its growth sections make the tiers growth.TIERS names): the bus's,
    not a block beside it. A pack the bus makes but cannot grow (grey science in its extension) is not."""
    from companion import growth
    saved = site.get("base_plan") if isinstance(site.get("base_plan"), Mapping) else {}
    if not saved.get("stages") or item not in growth.TIERS:
        return False
    if item not in set(saved.get("packs") or ()) | set(saved.get("extra") or ()):
        return False
    # (only once a bus stage making it stands: a stage still to be built is a plan, not a supply; a grown bus whose
    # green stage was planned but never built passed green over and never built it, F-21)
    if any(s in (saved.get("done") or {}) for s in bus_stages(item)):   # (its stage built: kept past the export's history)
        return True
    book = Book.from_site(site)
    return any(isinstance(b, Mapping) and b.get("purpose") == "bus-base" and int(b.get("buildings") or 0) > 0
               and any(r in book.recipes and book.recipes[r].makes(item) > 0 for r in b.get("recipes") or ())
               for b in site.get("base") or ())


def modules_for(item: str, rate: float, book: Book, machine: str = mods.ASSEMBLER) -> list[Module]:
    """The modules that make `item`, `rate` a second, top to bottom; NoBuilder when none can here."""
    recipe = book.recipe_for(item)
    if recipe is None:
        raise NoBuilder(f"no recipe makes {item}")
    try:
        if item == "steel-plate":
            return [blue_modules.steel(rate, book)]
        if item == military_modules.PACK:
            piercing = military_modules.needs(rate, book).get(military_modules.PIERCING, rate / 2)
            return [military_modules.piercing_rounds(piercing, book, machine),
                    military_modules.military_science(rate, book, machine)]
        if book.machine_for(recipe.category, allowed={"rocket-silo"}) is not None:
            # (the rocket's part: made in the silo, its inputs from chests: R-3, the launch on the way to space)
            from companion import rocket_modules
            m = rocket_modules.silo(rate, book)
            m.makes = {item: rate}
            return [m]
        from companion.module_order import BLOCKS
        if item in BLOCKS:
            lib, name = BLOCKS[item]
            return [getattr(lib, name)(rate, book, machine=machine)]
        # Any other: a row of its recipe in the machines that make it, every ingredient from a chest.
        fluids = [n for n, _ in recipe.ingredients if _fluid(book, n)]
        if fluids:
            raise NoBuilder(f"{item} takes {', '.join(fluids)}, a fluid: no chest feeds it (an oil build's)")
        # (its own machine, or the first assembler: the book holds every machine of the offline data, so an assembler 2
        # allowed here went into a row the engineer could not make, and the row failed in the game at once: W-23)
        maker = book.machine_for(recipe.category, allowed={machine, "assembling-machine-1"})
        if maker is None:
            raise NoBuilder(f"no assembler makes {item} ({recipe.category})")
        speed = book.machines[maker.name].speed
        n = max(1, math.ceil(rate / (speed / recipe.energy * recipe.makes(item)) - 1e-9))
        # Parts made beside it (gears for turrets), as the grey row makes its grenades and walls: an assembler each
        # side of every one making it, so the engineer carries plates, not parts it would craft by hand.
        beside = [n_ for n_, _ in recipe.ingredients if _made_beside(n_, recipe, maker, book)][:2]
        makers = [book.recipe_for(b) for b in beside]
        for b, mk in zip(beside, makers):   # (one maker beside each keeps up, else more of the row)
            per_maker = book.machines[maker.name].speed / mk.energy * mk.makes(b)
            wanted = rate / recipe.makes(item) * float(dict(recipe.ingredients)[b])
            n = max(n, math.ceil(wanted / per_maker - 1e-9))
        left, right = [makers[0].name] if makers else [], [makers[1].name] if len(makers) > 1 else []
        order = [r for _ in range(n) for r in (*left, recipe.name, *right)]
        own = [i for i, _ in recipe.ingredients if i not in beside]
        inputs = list(dict.fromkeys(own + [i for mk in makers for i, _ in mk.ingredients]))
        if not inputs or len(inputs) > 4:
            raise NoBuilder(f"{item} takes {len(inputs)} items from outside: a row takes one to four")
        m = blue_modules.row_module(re.sub(r"[^a-z0-9]+", "-", item), book, order,
                                    a=tuple(inputs[:2]), b=tuple(inputs[2:]), product=item, rate=rate,
                                    machine=maker.name)
        _port_rates(m, book)   # (what each input takes: a loader where an inserter is too slow, _with_chests)
        return [m]
    except (military_modules.MilitaryError, KeyError, ValueError) as exc:
        if isinstance(exc, NoBuilder):
            raise
        raise NoBuilder(f"no block for {item}: {exc}") from exc


_FLUIDS: set[str] = set()


def _made_beside(part: str, recipe, maker, book: Book) -> bool:
    """Whether `part` (an ingredient of `recipe`) is made in the row beside it: an assembler's recipe of one or two
    items (not a fluid, not smelted), made by the same machine."""
    r = book.recipe_for(part)
    if r is None or r.name == recipe.name or r.category not in maker.categories or r.category == "smelting":
        return False
    return 1 <= len(r.ingredients) <= 2 and not any(_fluid(book, n) for n, _ in r.ingredients)


def _fluid(book: Book, item: str) -> bool:
    """Whether `item` is a fluid (taken by pipe: no chest holds it): as the recipe data types it (base and Space
    Age), read once."""
    if not _FLUIDS:
        import json
        from companion.ratios import DATA
        data = json.loads(DATA.read_text(encoding="utf-8"))
        for part in (v for v in data.values() if isinstance(v, dict)):
            for r in (part.get("recipes") or {}).values():
                for row in [*(r.get("ingredients") or []), *(r.get("products") or [])]:
                    if isinstance(row, dict) and row.get("type") == "fluid":
                        _FLUIDS.add(str(row.get("name")))
        _FLUIDS.add("water")
    return item in _FLUIDS


def _note_fluids(site: Mapping[str, Any]) -> None:
    """The fluids the game's own recipe list types (a mod's), added to those the recipe data knows."""
    _fluid(None, "water")   # (the data's first)
    for r in site.get("recipes") if isinstance(site.get("recipes"), list) else ():
        if isinstance(r, Mapping):
            for row in [*(r.get("ingredients") or []), *(r.get("products") or [])]:
                if isinstance(row, Mapping) and row.get("type") == "fluid":
                    _FLUIDS.add(str(row.get("name")))


LOADER_FEED = 7.5   # items a second a loader moves from a chest onto a belt (a lane's worth, conservatively)


def _port_rates(module: Module, book: Book) -> None:
    """Each input port's draw at the module's rate (`makes`): what making its product at that rate takes of the port's
    item, its other inputs made inside it (ratios.solve). Ports a builder left at 0 get theirs."""
    from companion.ratios import solve
    product, rate = next(iter(module.makes.items()), (None, 0.0))
    ins = {p.item for p in module.ports if p.kind == "in"}
    if not product or rate <= 0 or not ins:
        return
    try:
        made = solve({product: rate}, book, inputs=ins)
    except (KeyError, ValueError):
        return
    for port in module.ports:   # (a raw item, coal for grenades, counts as mined: from its chest all the same)
        if port.kind == "in" and not port.rate:
            port.rate = float(made.inputs.get(port.item) or made.raw.get(port.item) or 0.0)


def fed_modules(item: str, rate: float, book: Book, machine: str, site: Mapping[str, Any]) -> tuple[list[Module], float]:
    """modules_for at `rate`, or slower where a chest's port takes more than what feeds it (one inserter; a loader once
    researched, loader_limit): (the modules, the rate built). The rest comes as another block while still due; an item
    no chest can feed even at MIN_RATE has no builder (NoBuilder, said)."""
    from companion.modules import loader_limit
    lift = LOADER_FEED if loader_limit(site) is not None else (mods.inserter_rate("inserter", site) or 0.8)

    def build(r: float) -> tuple[list[Module], float]:
        parts = modules_for(item, r, book, machine)
        for m in parts:
            _port_rates(m, book)
        return parts, max((p.rate for m in parts for p in m.ports if p.kind == "in"), default=0.0)
    parts, most = build(rate)
    if most > lift:
        rate = max(MIN_RATE, rate * lift / most * 0.999)
        parts, most = build(rate)
        if most > lift * 1.001:
            raise NoBuilder(f"{item} takes more of an input than a chest feeds ({most:.2f}/s against {lift:.2f}/s)")
    return parts, rate


def balanced(item: str, rate: float, waits: list[tuple[str, float]], book: Book, machine: str,
             site: Mapping[str, Any]) -> float:
    """`rate`, or slower where the item's rows would draw more of a supply than its new row makes (the waits: nothing
    makes them yet): a line its supply cannot keep up with is built to what it gets."""
    if not waits:
        return rate
    parts, _ = fed_modules(item, rate, book, machine, site)
    draw: dict[str, float] = {}
    for m in parts:
        for p in m.ports:
            if p.kind == "in":
                draw[p.item] = draw.get(p.item, 0.0) + p.rate
    full = rate
    for w, r in waits:
        if draw.get(w, 0.0) > r > 0:
            rate = min(rate, full * r / draw[w])
    if rate < MIN_RATE * 0.999:   # (not even a pack a minute from what its new supply row makes: said, not built)
        raise NoBuilder(f"{item}: its new supply rows make too little for even {MIN_RATE * 60:g} a minute")
    return rate


def buildable(item: str, site: Mapping[str, Any], book: Book | None = None) -> str | None:
    """Why `item` cannot be built here (None: it can)."""
    if item == tier_path.LAB:
        return None
    _note_fluids(site)
    try:
        fed_modules(item, MIN_RATE, book or Book.from_site(site), _machine(site), site)
    except NoBuilder as exc:
        return str(exc)
    return None


FEED_DEPTH = 4   # rows followed down a line's ingredients at most
FITS_PER_ANCHOR = 4   # free spots tried by each anchor for a row power reaches
POWER_ANCHORS = 8   # the network's poles nearest home a row may be placed by, when power cannot reach it by home
SPREAD_ANCHORS = 16  # and poles further out, spread over the whole network: a crowded middle left no spot a pole line
#                      reached, and the green row failed (LIVE-7c)
FEED_BLOCK_RATE = 0.25   # items a second one feeder row makes at most: smaller rows find room on a crowded base; more
                         # come while the line is still short (each pick sees what the last made)


def partial(item: str, rate: float, site: Mapping[str, Any], book: Book,
            waits: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """What a line at `rate` takes that the base makes, but less than its rows draw, and no row of ours can make more of
    (plastic from the oil block): (item, what is made), for `balanced` to size the line to what it gets."""
    try:
        parts, _ = fed_modules(item, rate, book, _machine(site), site)
    except NoBuilder:
        return []
    known = {w for w, _ in waits} | {port.item for m in parts for port in m.ports if port.kind == "out"}
    draw: dict[str, float] = {}
    for m in parts:
        for port in m.ports:
            if port.kind == "in" and port.item not in known:
                draw[port.item] = draw.get(port.item, 0.0) + port.rate
    out = []
    for part, wanted in draw.items():
        made = tier_path.have_rate(site, part)
        if 0 < made < wanted and buildable(part, site, book):
            out.append((part, made))
    return out


FEED_RETRY = 18000   # ticks a feeder row that found no place is passed over (5 game minutes): the line goes ahead
_UNPLACED: dict = {}   # (npc id, item) -> the tick its row found no place


def unplaceable(site: Mapping[str, Any], item: str) -> bool:
    """A feeder row that found no place lately (passed over for FEED_RETRY: the line, or the next feeder, goes ahead)."""
    at = _UNPLACED.get((site.get("npc_id"), item))
    return at is not None and float(site.get("tick") or 0) - at < FEED_RETRY


def _standing(site: Mapping[str, Any], item: str) -> float:
    """What the base's blocks are built to make of `item` (capacity): a row that stands but waits on its chests is
    still a row (its supply is the engineer's to bring, not another row to build)."""
    total = 0.0
    for block in site.get("base") or ():
        capacity = block.get("capacity") if isinstance(block, Mapping) and isinstance(block.get("capacity"), Mapping) else {}
        total += float((capacity.get("makes") or {}).get(item) or 0.0)
    return total


def feeders(item: str, rate: float, site: Mapping[str, Any], book: Book, path: tier_path.Path,
            known: tuple[str, ...] = ()) -> list[tuple[str, float]]:
    """The rows a line for `item` at `rate` needs beside it (R-5): every ingredient its rows take from a chest that
    nothing in the base makes at the rate taken (made, there for the taking, or a row of it standing) and the engineer
    does not have at hand, and a row of ours can make, sized to what its rows take in all (FEED_BLOCK_RATE a row at
    most), its own such ingredients first, down the chain until something already makes them (blue science: red
    circuits and engines; red circuits: green circuits). What a row cannot make (plates, oil's fluids and plastic) is left
    to what makes it: the base, the oil block."""
    machine = _machine(site)
    seen = {item, *known}
    out: list[tuple[str, float]] = []

    def visit(it: str, r: float, depth: int) -> None:
        if depth >= FEED_DEPTH:
            return
        try:
            parts, _ = fed_modules(it, r, book, machine, site)
        except NoBuilder:
            return
        # (what its own rows make for each other: the grey build's piercing row feeds its pack row)
        seen.update(port.item for m in parts for port in m.ports if port.kind == "out")
        need: dict[str, float] = {}
        for m in parts:   # (an ingredient two of its rows take: their draws summed)
            for port in m.ports:
                if port.kind == "in" and port.item not in seen and port.rate > 0:
                    need[port.item] = need.get(port.item, 0.0) + port.rate
        for part, wanted in need.items():
            if part in seen:
                continue
            seen.add(part)
            made = max(tier_path.have_rate(site, part), _standing(site, part))
            if made >= tier_path.SHORT * wanted or path.held(part) >= wanted * tier_path.HELD_MINUTES * 60:
                continue   # (made, a row standing, or at hand: the line is fed)
            if on_bus(site, part) or buildable(part, site, book):
                continue   # (the bus's to make, or no row of ours can: plates, oil's products)
            feed = min(FEED_BLOCK_RATE, max(wanted - made, MIN_RATE))
            visit(part, feed, depth + 1)   # (what its row takes, first)
            try:
                out.append((part, fed_modules(part, feed, book, machine, site)[1]))
            except NoBuilder:
                continue
    visit(item, rate, 0)
    return out


def _block_build(block: Any, d: tier_path.Due, site: Mapping[str, Any], book: Book, path: tier_path.Path,
                 skipped: list[str], asked: float) -> Build | None:
    """The library block's build: the rows for what it needs first (the deepest one at a time), then the block."""
    share = min(1.0, max(asked, MIN_RATE) / block.rate)   # (the gap it was picked to fill, Codex R-5b)
    for w in block.need_rows:
        if unplaceable(site, w):
            continue
        r = min(FEED_BLOCK_RATE, max(block.inputs.get(w, 0.0) * share, MIN_RATE))
        deeper = [f for f in feeders(w, r, site, book, path, (d.item,)) if not unplaceable(site, f[0])]
        try:
            first, first_rate = deeper[0] if deeper else (w, fed_modules(w, r, book, _machine(site), site)[1])
        except NoBuilder as exc:
            skipped.append(f"{w}: {exc}")
            return None
        return Build(first, first_rate, f"{block.label} takes it and nothing makes it", skipped=skipped)
    labs = labs_busy(site, path, d.item, block.rate) if path.tier(d.item) else 0
    return Build(d.item, block.rate, f"{d.why}; the library's {block.line()}", [], labs, skipped, block=block.key,
                 block_for=asked)


def _rate(d: tier_path.Due) -> float:
    return min(MAX_BLOCK_RATE, max(d.rate - d.have, MIN_RATE))


# Labs follow the packs made (F-17): as many as the packs its machines make (with the row being built) keep busy at the
# current research's time a unit, and SPARE_LABS more; more grow as the pack rows grow. What the research would like
# (tier_path's aim) asked 10 labs at minute 17 of a fresh run (long-65) beside a red row of 0.26/s, hand-fed for 15 minutes
# while the first electric drills waited; the player ran 2 labs until minute 81, 6 after green.
SPARE_LABS = 1


def _unit_time(path: tier_path.Path) -> tuple[float, list[str]]:
    """The current research's time a unit and its packs (the nearest researching tier's first research that is not a
    trigger; its time, else the path's own), or (the path's time, []) when nothing is being researched."""
    for t in path.researching():
        for r in t.research:
            if isinstance(r, Mapping) and not r.get("trigger"):
                return float(r.get("time") or path.unit_time), [str(p) for p in r.get("packs") or []]
    return float(path.unit_time), []


def labs_busy(site: Mapping[str, Any], path: tier_path.Path, item: str | None = None, rate: float = 0.0) -> int:
    """Labs to add (of the plain lab) so those standing, at their own speeds, and these keep busy what the packs made
    by machine run (`item` at `rate` more: a row being built) at the current research's time a unit, with SPARE_LABS
    more; none past MAX_LABS. Its packs: the current research's set, else the set of packs made that runs the most."""
    machines = tier_path._machines(site)
    names = set(path.lab_speeds) | {tier_path.LAB}
    bonus = 1.0 + path.lab_bonus
    capacity = sum(path.lab_speeds.get(name, 1.0) * n for name, n in machines.items() if name in names) * bonus
    plain = max(path.lab_speeds.get(tier_path.LAB, 1.0) * bonus, 0.01)
    unit, packs = _unit_time(path)
    made = {t.pack: t.made + (rate if t.pack == item else 0.0) for t in path.tiers}
    if item and item not in made:
        made[item] = rate
    if packs:
        sets = [[p for p in packs]]
    else:   # (nothing researched now: the set of packs made that runs the most, as tier_path's labs)
        sets = [sorted(ps) for ps in path._pack_sets()] or ([[item]] if item else [])
    supply = max((min(made.get(p, 0.0) for p in ps) for ps in sets if ps), default=0.0)
    if supply <= 0:
        return 0
    need = math.ceil((supply * unit - capacity) / plain - 0.05) + SPARE_LABS
    return max(0, min(MAX_LABS, need))


def pick(site: Mapping[str, Any], path: tier_path.Path | None = None, book: Book | None = None,
         skipped: list[str] | None = None) -> Build | None:
    """The most pressing due item that can be built here (tier_path.dues order), with the supplies it waits on that
    can be built, and labs when they are due too with a pack line; None when nothing due can be built."""
    path = path or tier_path.assess(site)
    book = book or Book.from_site(site)
    skipped = [] if skipped is None else skipped   # (the caller's: what was passed over, also when nothing is built)
    lab_due = None
    for d in path.dues():
        if d.item == tier_path.LAB:
            lab_due = d
            continue   # (labs: by what the packs made keep busy, below)
        if on_bus(site, d.item):
            skipped.append(f"{d.item}: the bus makes it (its growth)")
            continue
        # (an imported block from the library for a pack line first, R-5b: before the generated rows' own checks, since
        # it may make inside what they could not; rows only for what it takes in and nothing makes)
        if path.tier(d.item):
            from companion import science_blocks
            asked = max(d.rate - d.have, MIN_RATE)
            block = science_blocks.offer(site, d.item, asked, book=book, path=path)
            built = block and _block_build(block, d, site, book, path, skipped, asked)
            if built:
                return built
        why = buildable(d.item, site, book)
        if why:
            skipped.append(f"{d.item}: {why}")
            continue
        waits, stuck = [], []
        for w in d.waits:
            if on_bus(site, w):
                continue
            why_w = buildable(w, site, book)
            if why_w:
                stuck.append(f"{w} ({why_w})")
                continue
            wd = path.due(w)
            try:
                waits.append((w, fed_modules(w, _rate(wd) if wd.rate > 0 else MIN_RATE, book, _machine(site),
                                             site)[1]))
            except NoBuilder as exc:
                stuck.append(f"{w} ({exc})")
        if stuck:   # (a line its supplies cannot reach would stand idle)
            skipped.append(f"{d.item}: waits on {'; '.join(stuck)}")
            continue
        try:
            rate = fed_modules(d.item, _rate(d), book, _machine(site), site)[1]
            rate = balanced(d.item, rate, waits + partial(d.item, rate, site, book, waits), book, _machine(site), site)
        except NoBuilder as exc:
            skipped.append(f"{d.item}: {exc}")
            continue
        # (and what it takes that nothing makes: a line fed from empty chests stands idle, R-5. Built first, the deepest
        # one at a time, each a small build of its own placed and powered on its own (six rows as one plan found no
        # pole route to a far one); the line comes once they stand, counted then as made)
        fed = [f for f in feeders(d.item, rate, site, book, path, tuple(w for w, _ in waits))
               if not unplaceable(site, f[0])]
        if fed:
            first, first_rate = fed[0]
            return Build(first, first_rate, f"{d.item} takes it and nothing makes it", skipped=skipped)
        # (labs come with a pack line: what its packs, with those made, keep busy)
        labs = labs_busy(site, path, d.item, rate) if path.tier(d.item) else 0
        return Build(d.item, rate, d.why, waits, labs, skipped)
    # (labs alone: what the packs made by machine keep busy and one spare, said when none are wanted)
    n = labs_busy(site, path)
    if (n or lab_due) and not _lab_packs(site, path, ""):
        skipped.append("lab: no pack made by machine for them yet")
    elif n:
        why = lab_due.why if lab_due else "labs for the packs made by machine"
        return Build(tier_path.LAB, float(n), why, skipped=skipped)
    elif lab_due:
        skipped.append("lab: the labs standing keep up with the packs made")
    return None


_PICKED: dict = {}
_SAID: dict = {}     # (what was last said passed over, by engineer: said again only when it changes)   # (one site's pick, asked many times in one request: availability, the log, the plan)


def cached_pick(site: Mapping[str, Any]) -> Build | None:
    """pick(site), worked out once per site (the same request's site object, at its tick)."""
    key = (id(site), site.get("request_id"), site.get("tick"), site.get("npc_id"))
    if _PICKED.get("key") != key:
        skipped: list[str] = []
        try:
            build = pick(site, skipped=skipped)
        except Exception as exc:   # (a signal, never in the way of a goal: nothing due then, and said)
            build, skipped = None, [f"the science path failed: {str(exc)[:80]}"]
        _PICKED.clear()
        _PICKED.update(key=key, build=build, skipped=skipped)
    return _PICKED["build"]


def bus_carries(site: Mapping[str, Any], plate: str) -> bool:
    """Whether a lane of the engineer's bus carries `plate`: one the game records, or its bus's smelting (or feed) of
    it built and a segment of the bus laid with it (a bus built before its lanes were recorded). Its smelting alone
    lays no lane: green rows went in by hand on a bus of smelting and no segment yet (green-pace, F-21)."""
    for t in site.get("belt_lines") or ():
        if isinstance(t, Mapping) and t.get("kind") == "bus" and t.get("item") == plate:
            return True
    done = [str(d) for d in (site.get("base_plan") or {}).get("done") or {}]
    return any(d in (f"smelting-{plate}", f"feed-{plate}") for d in done) and any(d.startswith("bus-") for d in done)


def retrofit_due(site: Mapping[str, Any]) -> bool:
    """Whether a standing line is fed by hand (the game's reading, R-6) with an item belts can bring now: one a
    machine makes, or a plate a bus lane carries (a plate with no lane waits for it: no belt could come, LIVE-7c)."""
    from companion import input_hookup
    book = Book.from_site(site)
    for b in site.get("base") or ():
        for c in (b.get("hand_chests") or ()) if isinstance(b, Mapping) else ():
            for item in (c.get("items") or ([c["item"]] if c.get("item") else [])) if isinstance(c, Mapping) else ():
                if not input_hookup.from_bus(str(item), book) or bus_carries(site, str(item)):
                    return True
    return False


def bus_regime(site: Mapping[str, Any]) -> bool:
    """Whether plates come by belt from the bus only here: its engineer builds one and it has not failed."""
    from companion import bus_planner
    return bus_planner.wants_a_bus(site) and "bus-base" not in {str(p) for p in site.get("failed_purposes") or ()}


def lane_end_missing(site: Mapping[str, Any], world: Mapping[str, Any], build: Build | None) -> str | None:
    """A plate the build's rows take with no bus lane end on the map to feed it from (input_hookup.lane_ends), while
    the build would otherwise wait for its lane (the same release as lane_first): None when each has one."""
    from companion import input_hookup
    if not isinstance(build, Build) or build.item == tier_path.LAB or not world:
        return None
    if "bus-base" in {str(p) for p in site.get("failed_purposes") or ()}:
        return None
    from companion import bus_planner
    if not bus_planner.wants_a_bus(site):
        return None
    ends = input_hookup.lane_ends(world, input_hookup.bus_book(site, world))
    return next((p for p in sorted(_plates_of(site, build)) if not ends.get(p)), None)


def _plates_of(site: Mapping[str, Any], build: Build) -> set[str]:
    """The plates (smelted items) the build's rows take, its waits' rows too."""
    from companion import input_hookup
    book = Book.from_site(site)
    plates: set[str] = set()
    for item, rate in [(build.item, build.rate), *build.waits]:
        try:
            parts, _ = fed_modules(item, rate, book, _machine(site), site)
        except NoBuilder:
            continue
        plates |= {p.item for m in parts for p in m.ports if p.kind == "in" and input_hookup.from_bus(p.item, book)}
    return plates


BUS_MAKES = {"iron-gear-wheel": "bus-gears", "electronic-circuit": "bus-green-circuits",
             "advanced-circuit": "bus-red-circuits", "engine-unit": "bus-engines",
             **BUS_PACK_STAGES}   # (the bus's stage of what its sections make: modules' names)


def _waited(what: str) -> str:
    """What a held build waits for on the bus, said: a section of its own, or a plate's lane."""
    if what.startswith("bus-"):
        return f"{what[4:]} section (it makes this; the science path waits for it)"
    return f"{what} lane (plates by belt from the bus)"


def bus_will_make(site: Mapping[str, Any], build: Build | None) -> str | None:
    """The bus's stage that makes the build's item, planned and not built yet, while the bus goes on (not failed, an
    engineer who builds one): the science path waits for it rather than a line of its own beside the bus, which its
    plates could not reach yet (green circuits tried at minute 46 with the bus's lane not out to them, opening-pace,
    F-21). None when the bus does not plan it, has it, or will not come."""
    if not isinstance(build, Build) or build.item == tier_path.LAB:
        return None
    if "bus-base" in {str(p) for p in site.get("failed_purposes") or ()}:
        return None
    from companion import bus_planner
    if not bus_planner.wants_a_bus(site):
        return None
    saved = site.get("base_plan") or {}
    for stage in bus_stages(build.item):
        if stage in (saved.get("stages") or ()) and stage not in (saved.get("done") or {}):
            return stage
    return None


def lane_first(site: Mapping[str, Any], build: Build | None) -> str | None:
    """The plate a build's rows take that no bus lane carries yet: the line is not taken and the bus's lane comes
    first (F-20 B: plates by belt only from the bus; the user: hand-fed rows are not automation, so no hand bridge:
    four engineers stood ten minutes on green circuits fed by hand, LIVE-7c). Only a bus that failed here releases it.
    None when nothing waits."""
    from companion import input_hookup
    if not isinstance(build, Build) or build.item == tier_path.LAB:
        return None
    if "bus-base" in {str(p) for p in site.get("failed_purposes") or ()}:
        return None
    from companion import bus_planner
    if not bus_planner.wants_a_bus(site):   # (an engineer who never builds a bus: no lane to wait for)
        return None
    stage = bus_will_make(site, build)
    if stage:   # (its item comes from the bus's own section: waited for like a lane)
        return stage
    return next((p for p in sorted(_plates_of(site, build)) if not bus_carries(site, p)), None)


def passed_over(site: Mapping[str, Any]) -> list[str]:
    """The due items passed over in this site's pick, and why (also when nothing could be built)."""
    build = cached_pick(site)
    plate = lane_first(site, build)
    lane = [f"{build.item}: waits for the bus's {_waited(plate)}"] if plate else []
    return list(_PICKED.get("skipped") or []) + lane


def news(site: Mapping[str, Any]) -> list[str]:
    """passed_over, when it differs from what was last said (a log line once, not every request)."""
    now = passed_over(site)
    key = site.get("npc_id")
    if _SAID.get(key) == now:
        return []
    _SAID[key] = now
    return now


def layout(site: Mapping[str, Any], item: str | None = None) -> list[dict]:
    """The machines of the pick's build (or `item`'s: the grey goal's), as layout entities (prototype, recipe): what
    the weighing values it by (its inputs, furnaces and labs, not only its output)."""
    try:
        build = build_for(site, item) if item else cached_pick(site)
    except (layout_planner.PlanError, NoBuilder):
        return []
    return layout_of_build(site, build)


def layout_of_build(site: Mapping[str, Any], build: Build | None) -> list[dict]:
    """A Build's machines as layout entities (prototype, recipe), for the weighing."""
    if build is None:
        return []
    book = Book.from_site(site)
    machine = _machine(site)
    out: list[dict] = []
    try:
        parts = [m for w, r in build.waits for m in modules_for(w, r, book, machine)]
        if build.item == tier_path.LAB:
            out += [{"prototype": "lab"} for _ in range(int(build.rate))]
        else:
            parts += modules_for(build.item, build.rate, book, machine)
            out += [{"prototype": "lab"} for _ in range(build.labs)]
    except NoBuilder:
        return out
    for m in parts:
        made = next(iter(m.makes), None)
        for e in m.entities:
            if e.recipe:
                out.append({"prototype": e.name, "recipe": e.recipe})
            elif "furnace" in e.name and made:   # (a furnace smelts what it is fed: its row's product)
                recipe = book.recipe_for(made)
                out.append({"prototype": e.name, "recipe": recipe.name if recipe else None})
    return out


def build_for(site: Mapping[str, Any], item: str, path: tier_path.Path | None = None,
              book: Book | None = None) -> Build:
    """A build for `item` by the signal (its rate when due; at least MIN_RATE, or `floor`), the supplies it waits
    on first: for a goal that builds a given item (the grey line's own)."""
    path = path or tier_path.assess(site)
    book = book or Book.from_site(site)
    d = path.due(item)
    stuck = [f"{w} ({buildable(w, site, book)})" for w in d.waits if not on_bus(site, w) and buildable(w, site, book)]
    if stuck:   # (the line would stand idle: said, not built)
        raise layout_planner.PlanError(f"{item} waits on {'; '.join(stuck)}"[:160])
    machine = _machine(site)
    try:
        waits = [(w, fed_modules(w, _rate(path.due(w)), book, machine, site)[1]) for w in d.waits
                 if not on_bus(site, w)]
        rate = fed_modules(item, _rate(d) if d.rate > 0 else MIN_RATE, book, machine, site)[1]
        rate = balanced(item, rate, waits, book, machine, site)
    except NoBuilder as exc:
        raise layout_planner.PlanError(str(exc)[:160]) from exc
    waits += feeders(item, rate, site, book, path, tuple(w for w, _ in waits))   # (R-5)
    try:   # (the line no faster than its bundled feeder rows make what it takes)
        rate = balanced(item, rate, waits, book, machine, site)
    except NoBuilder as exc:
        raise layout_planner.PlanError(str(exc)[:160]) from exc
    labs = labs_busy(site, path, item, rate) if path.tier(item) else 0
    return Build(item, rate, d.why, waits, labs)


def _lab_packs(site: Mapping[str, Any], path: tier_path.Path, item: str) -> tuple[str, ...]:
    """The packs new labs take: those made by machine now (fed by hand or not), and the pack line built with them."""
    packs = [t.pack for t in path.tiers if (t.machine or (item and t.pack == item)) and not t.launch]   # (R-3)
    return tuple(dict.fromkeys(packs))[:mods.LAB_PACKS]


def _dump_unfed(site: Mapping[str, Any], world: Mapping[str, Any], hooked: Any) -> None:
    """With NPC_HOOKUP_DUMP set, the site and map of a plan that leaves inputs to the hand, to work it out again
    offline (data/debug/hookup-<request>.json)."""
    import json
    import os
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not (os.environ.get("NPC_HOOKUP_DUMP") and folder and hooked.notes):
        return
    path = Path(folder) / "debug" / f"hookup-{re.sub(r'[^A-Za-z0-9_-]', '-', str(site.get('request_id')))}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"site": site, "world": world, "notes": hooked.notes}, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


RETROFIT_CHESTS = 8   # hand chests fed by belt in one plan at most


def retrofit(site: Mapping[str, Any], world: Mapping[str, Any], *, purpose: str = GOAL) -> dict | None:
    """Lines that stand but are fed by hand (the game's reading: each block's `hand_chests`, a chest an inserter
    empties into its machines and nothing fills, R-6) get belts: an inserter beside each chest lifting from a belt
    brought from what makes the item (input_hookup.into_chest), plates from the bus's lane ends by the lane ledger's
    grant. A plan of what could be connected, or None when nothing could (each chest left said in its notes)."""
    from companion import input_hookup
    # (each chest's items, R-6: [{x, y, items}]; one feed an item, each by an inserter of its own into the chest)
    chests = [(b, c, str(item)) for b in site.get("base") or () if isinstance(b, Mapping)
              for c in b.get("hand_chests") or () if isinstance(c, Mapping)
              for item in (c.get("items") or ([c["item"]] if c.get("item") else []))]
    if not chests or not world.get("area"):
        return None
    book = Book.from_site(site)
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    taken = input_hookup.feed_ground(world)
    lanes = input_hookup.bus_book(site, world)
    ends = input_hookup.lane_ends(world, lanes)
    drawn: dict = {}
    reach = input_hookup.supplies(world)
    powered = [(x, y) for x, y, _ in reach]
    stages, entities, notes = [], [], []
    delivered: dict[str, float] = {}
    for n, (block, c, item) in enumerate(chests[:RETROFIT_CHESTS], start=1):
        at = (math.floor(float(c["x"]) + ox), math.floor(float(c["y"]) + oy))
        takes = ((block.get("capacity") or {}).get("takes") or {}) if isinstance(block.get("capacity"), Mapping) else {}
        rate = float(takes.get(item) or input_hookup.INSERTER_RATE / 2)
        why = input_hookup._skip(item, book, ends)
        if why:
            notes.append(f"{block.get('id')}: {why}, by hand")
            continue
        from companion import defense_production
        token = input_hookup.DEDICATED.set(block.get("purpose") == defense_production.GOAL)   # (D-19: its smelters)
        try:
            sources = input_hookup.sources_for(item, site, world, [], book, ends, lanes, drawn)
        finally:
            input_hookup.DEDICATED.reset(token)
        fed = input_hookup.into_chest(item, rate, at, sources=sources, taken=taken, bounds=tuple(world["area"]))
        if not fed.enough:
            notes.append(f"{block.get('id')}: {item}: {fed.why}, by hand")
            continue
        # (its power first, then its claim on a lane: a feed given up holds nothing)
        poles = input_hookup._power(fed, (powered, reach), taken)
        if poles is None:
            notes.append(f"{block.get('id')}: {item}: no power reaches its feed's inserters, by hand")
            continue
        refused = input_hookup._grant(fed, f"{block.get('id')}@{at[0]},{at[1]}", rate, lanes, drawn)
        if refused:
            notes.append(f"{block.get('id')}: {item}: {refused}, by hand")
            continue
        input_hookup._commit(fed, poles, (powered, reach))
        part = fed.entities
        taken |= {(math.floor(e.x), math.floor(e.y)) for e in part}
        stage = f"hookup-{n}"
        planned = [e.to_plan(f"{stage}-{k}", -ox, -oy) for k, e in enumerate(part, start=1)]
        claims = belt_lines.ops(part)
        first = next(e["id"] for e in planned if e["prototype"] in input_hookup.LIFTS)
        stages.append({"id": stage, "depends_on": [], "entity_ids": [e["id"] for e in planned], "manual_supplies": [],
                       "witnesses": [{"kind": "entity_built", "entity_id": first}],
                       **({"line_ops": claims} if claims else {})})
        entities += planned
        delivered[item] = delivered.get(item, 0.0) + fed.rate
    if not stages:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{purpose}-hookup-{safe}", "source": "tier-builds", "purpose": purpose, "entities": entities,
            "stages": stages, "tier": {"item": "hookup", "rate": 0, "why": "lines fed by hand get their belts",
                                       "waits": [], "labs": 0, "delivered": {k: round(v, 4) for k, v in delivered.items()}},
            **({"notes": notes[:12]} if notes else {})}


def plan(site: Mapping[str, Any], world: Mapping[str, Any], item: str | None = None, *, purpose: str = GOAL,
         floor: float = 0.0) -> dict:
    """The build for the most pressing due item (or `item`), as a plan: its stages are the supplies it waits on, the
    item's rows, and labs; each row in chests near home, powered from the network."""
    from companion.base_plan import all_ore, blocked_tiles, find_fit
    from companion.bus import END_CHEST
    from companion.modules import loader_limit
    from companion.power import connect
    from companion.smelting import footprint
    fix = retrofit(site, world) if not item else None   # (a standing line fed by hand gets its belts first, F-20)
    if fix is not None:
        return fix
    plate = lane_first(site, cached_pick(site)) if not item else None
    if not plate and not item:   # (and on the map: a lane end each of its plates is fed from)
        plate = lane_end_missing(site, world, cached_pick(site))
    if plate:   # (its rows would be fed by hand: not built, the bus's lane first; LIVE-7c)
        raise layout_planner.PlanError(f"waits for the bus's {_waited(plate)}")
    book = Book.from_site(site)
    path = tier_path.assess(site)
    build = build_for(site, item, path, book) if item else pick(site, path, book)
    if build is None:
        raise layout_planner.PlanError("nothing the science path needs can be built here now")
    if floor and build.item != tier_path.LAB:
        build.rate = max(build.rate, floor)
    try:
        return plan_build(site, world, build, purpose=purpose, path=path)
    except layout_planner.PlanError:
        if build.item != tier_path.LAB and path.tier(build.item) is None:   # (a feeder row: set aside a while)
            _UNPLACED[(site.get("npc_id"), build.item)] = float(site.get("tick") or 0)
        raise


def plan_build(site: Mapping[str, Any], world: Mapping[str, Any], build: Build, *, purpose: str = GOAL,
               path: tier_path.Path | None = None, belts: bool = True) -> dict:
    """A Build as a plan (any goal's: the science path's, the defense supply's): the supplies it waits on, the item's
    rows, and labs; each row near home, powered from the network, its inputs by belt from the machines that make
    them where one reaches (companion/input_hookup.py, F-20), else from chests the engineer fills. A library block
    goes in belt-fed (`belts`) when every input of it is reached, else from its chests."""
    from companion import input_hookup
    from companion.base_plan import all_ore, blocked_tiles, find_fit
    from companion.bus import END_CHEST
    from companion.modules import loader_limit
    from companion.power import connect
    from companion.smelting import footprint
    book = Book.from_site(site)
    path = path or tier_path.assess(site)
    machine = _machine(site)
    _note_fluids(site)
    belt_fed = None   # (a library block fed by belts, F-20)
    parts: list[tuple[str, Module]] = []
    for k, (w, r) in enumerate(build.waits):
        made, r = fed_modules(w, r, book, machine, site)
        build.waits[k] = (w, r)
        parts += [(f"tier-{w}", m) for m in made]
    if build.item == tier_path.LAB:
        packs = _lab_packs(site, path, "")
        if not packs:
            raise layout_planner.PlanError("labs are due, but no pack is made by machine for them yet")
        parts.append(("tier-labs", mods.labs(int(build.rate), packs)))
    else:
        block = None
        if build.block:   # (the library's block: belt-fed where it can be, else from chests, R-5b)
            from companion import science_blocks
            # (at the rate it was picked for: what it then needed rows for stays so, Codex R-5b)
            o = science_blocks.chosen(site, build.item, build.block, build.block_for or build.rate, book=book, path=path,
                                      chests=not belts)
            if o is None and belts:
                return plan_build(site, world, build, purpose=purpose, path=path, belts=False)
            if o is None:
                raise layout_planner.PlanError(f"the library's block for {build.item} is no longer to be had here")
            block = science_blocks.rows(o, chests=not belts)
            if block is None and belts:
                return plan_build(site, world, build, purpose=purpose, path=path, belts=False)
            if block is not None and belts:
                belt_fed = block
        if block is not None:
            parts.append((f"tier-{build.item}", block))
        else:
            made, build.rate = fed_modules(build.item, build.rate, book, machine, site)
            parts += [(f"tier-{build.item}", m) for m in made]
        if build.labs:
            parts.append(("tier-labs", mods.labs(build.labs, _lab_packs(site, path, build.item))))
    taken = blocked_tiles(world) | all_ore(world)
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    limit = loader_limit(site)
    live = set(world.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in world.get("entities", [])
               if e.get("t") == "electric-pole" and e.get("f") == world.get("force")
               and (not live or e.get("net") is None or e.get("net") in live)]
    if not powered:
        raise layout_planner.PlanError(f"no electric network to power the {build.item} rows")
    # (where a row may go: nearest home first, then by the network's poles nearest home: a free spot by home on a
    # crowded base had no pole route to it, R-5)
    near = sorted(powered, key=lambda q: ((q[0] - home[0]) ** 2 + (q[1] - home[1]) ** 2, q))
    rest = near[POWER_ANCHORS:]
    spread = rest[::max(1, len(rest) // SPREAD_ANCHORS)][:SPREAD_ANCHORS]
    anchors = [home] + [(int(x), int(y)) for x, y in near[:POWER_ANCHORS] + spread]
    placed: list[list[Placed]] = []
    offsets: list[tuple[int, int]] = []
    ground = input_hookup.feed_ground(world)   # (a feed may pass through the room kept round the rows)
    for _, module in parts:
        fed = getattr(module, "feed_index", None) is not None   # (fed from chests already: the library's block)
        if module is belt_fed:   # (its stubs wait for belts; its product into a chest)
            own = footprint(module.entities)
            rows = list(module.entities) + [e for p in module.ports if p.kind == "out" for e in input_hookup.sink(p, own)]
        else:
            rows = list(module.entities) if fed else military_modules._with_chests(module, limit)
        shape = footprint(rows) | {(x, y) for x in range(-3, module.width + 1) for y in range(-1, module.height + 1)}
        chosen, tried = None, set()
        # (by what feeds it first, as a player puts a line by its inputs: short belts, F-20; then by home)
        by_inputs = input_hookup.inputs_anchor(module, site, world, book, [e for p in placed for e in p], home)
        for anchor in ([by_inputs] if by_inputs else []) + anchors:
            for skip in range(FITS_PER_ANCHOR):   # (the next free spots too: the nearest may have no route)
                fit = find_fit(taken, shape, home, tuple(world["area"]), anchor=anchor, skip=skip)
                if not fit or fit in tried:
                    continue
                tried.add(fit)
                ox, oy = fit
                row = [replace(e, x=e.x + ox, y=e.y + oy) for e in rows]   # (its own settings kept: a library block's)
                here = {(x + ox, y + oy) for x, y in shape}
                extra, problems = connect(row, powered, taken | here)
                if not problems:
                    chosen = (row + extra, here)
                    break
            if chosen:
                break
        if chosen is None:
            raise layout_planner.PlanError(f"no room near home for the {module.name} row "
                                           f"({module.width + 3}x{module.height})" if not tried else
                                           f"no spot for the {module.name} row that power reaches")
        # (its route kept with it: a later row is placed round it and powered from it)
        row, here = chosen
        placed.append(row)
        offsets.append((ox, oy))
        taken |= here | {(math.floor(e.x), math.floor(e.y)) for e in row if (e.name.endswith("electric-pole") or e.name == "substation")}
        powered += [(e.x, e.y) for e in row if (e.name.endswith("electric-pole") or e.name == "substation")]
    # Inputs by belt from what makes them, where one reaches; power for the feeds' inserters (F-20).
    reach = input_hookup.supplies(world, [e for p in placed for e in p])
    from companion import defense_production
    token = input_hookup.DEDICATED.set(purpose == defense_production.GOAL)   # (its own smelters' plates: D-19)
    try:
        hooked = input_hookup.hook_rows([(m, part, off) for (_, m), part, off in zip(parts, placed, offsets)],
                                        world, book, ground, tuple(world["area"]),
                                        lanes=input_hookup.bus_book(site, world), site=site, power=(powered, reach),
                                        avoid=input_hookup.soft_reserved(world))
    finally:
        input_hookup.DEDICATED.reset(token)
    _dump_unfed(site, world, hooked)
    # (no row fed plates by hand while the bus brings them: the user's rule, F-20 B; a lane check that let a green line
    # through left its iron to the hand at minute 50, F-21. Its own smelters' rows, D-19, are fed so already)
    unfed = hooked.unfed_plates([(m, part, off) for (_, m), part, off in zip(parts, placed, offsets)], book) \
        if purpose != defense_production.GOAL else []
    if unfed and bus_regime(site):
        raise layout_planner.PlanError(f"waits for the bus's {unfed[0]} lane (plates by belt from the bus; "
                                       f"no belt reaches its {unfed[0]} input yet)")
    for k, part in enumerate(placed):   # (each feed with its stage: its belts, inserters and poles)
        part.extend(hooked.entities.get(k) or ())
    if belt_fed is not None:
        k = next(i for i, (_, m) in enumerate(parts) if m is belt_fed)
        wanted = {i for i, p in enumerate(belt_fed.ports) if p.kind == "in" and p.rate > 0}
        if not wanted <= hooked.ports.get(k, set()):   # (a stub left without a belt: the block from its chests)
            back = plan_build(site, world, build, purpose=purpose, path=path, belts=False)
            said = [f"{build.block}: not every input reaches it by belt, from its chests"] + hooked.notes
            return {**back, "notes": [*said, *(back.get("notes") or ())][:12]}
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    stages: list[dict] = []
    all_entities: list[dict] = []
    names: dict[str, int] = {}
    for k, ((name, module), part) in enumerate(zip(parts, placed)):
        names[name] = names.get(name, 0) + 1
        stage = name if names[name] == 1 else f"{name}-{names[name]}"
        entities = [e.to_plan(f"{stage}-{n}", -ox, -oy) for n, e in enumerate(part, start=1)]
        if getattr(module, "feed_index", None) is not None:   # (its own chests by index: the block may hold chests)
            chests = [(entities[i], p) for i, p in zip(module.feed_index, module.ports)]
        else:   # (the ports fed by belt have no chest: the rest, in order)
            by_hand = [p for i, p in enumerate(module.ports) if not hooked.fed(k, i) and module is not belt_fed]
            chests = [(e, p) for e, p in zip([x for x in entities if x["prototype"] == END_CHEST], by_hand)]
        out = next((p.item for p in module.ports if p.kind == "out"), None)
        kept = None   # (a product kept in its machine, no port: the silo's rocket parts; R-3)
        if out is None and module.makes:
            kept = out = next(iter(module.makes))
        maker = next((e["id"] for e in entities if out and e.get("recipe") and book.recipes.get(e["recipe"])
                      and book.recipes[e["recipe"]].makes(out) > 0), None)
        maker = maker or next((e["id"] for e in entities if "furnace" in e["prototype"]), None)
        if not maker and kept and book.recipe_for(kept):   # (the machine its recipe is made in: a fixed recipe)
            craft = book.machine_for(book.recipe_for(kept).category)
            maker = next((e["id"] for e in entities if craft and e["prototype"] == craft.name), None)
        if maker and out:
            witnesses = [{"kind": "product_counter_delta", "entity_id": maker, "item": out, "min_delta": 2}]
        else:   # (labs: built is the proof; they work as packs come)
            first = next(e["id"] for e in entities if e["prototype"] == "lab")
            witnesses = [{"kind": "entity_built", "entity_id": first}]
        claims = belt_lines.ops(part)   # (a feed from a bus lane's end: its claim on the lane, lane_ledger)
        stages.append({"id": stage, "depends_on": [s["id"] for s in stages], "entity_ids": [e["id"] for e in entities],
                       **({"line_ops": claims} if claims else {}),
                       "manual_supplies": [{"entity_id": e["id"], "item": p.item, "slot": "input"}
                                           for e, p in chests if p.kind == "in"],
                       "witnesses": witnesses})
        all_entities += entities
    locked = locked_parts(site, all_entities)
    if locked:
        raise layout_planner.PlanError(f"the {build.item} rows want {', '.join(locked[:4])}, which it can neither make "
                                       "nor holds")
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{purpose}-{safe}", "source": "tier-builds", "purpose": purpose, "entities": all_entities,
            "stages": stages, "tier": {"item": build.item, "rate": round(build.rate, 4), "why": build.why,
                                       "waits": [w for w, _ in build.waits], "labs": build.labs,
                                       # (what comes by belt, items/s, and what is left to the hand: tier_builds.feeders)
                                       "delivered": {k: round(v, 4) for k, v in hooked.delivered.items()},
                                       **({"belt_fed": build.block} if belt_fed is not None else {})},
            **({"notes": hooked.notes[:12]} if hooked.notes else {})}
