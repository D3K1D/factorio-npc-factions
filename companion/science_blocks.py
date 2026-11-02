"""Imported science blocks first (R-5b): when the science path picks a tier's line and the layout library holds an
imported block for that pack the engineer can build now, the block is offered before the generated rows.

The user's blueprint books (F-16, companion/blueprint_packs.py) hold proven science blocks ("1.0 Science Book -
Tileable v3.7", the mega book's Science section at AM1/AM2/AM3). A big one makes some of its own ingredients inside
(the 45/min chemical block makes its engines): rows are built only for what it takes in at its edges and nothing makes.

**The class**: a ready-made block for a line is passed over for generated rows. Its variants: a block that makes some
of its ingredients inside; one whose research or buildings are still locked; several blocks for one pack at different
rates; a block whose edge belts the import annotation cannot name (one belt carries two items, one per lane); a block
that needs a fluid piped in; a base with a bus (NPC Factory's F-18b: taps on its lanes) or without (fed from chests).

**One mechanism**: the block's real entries are worked out from the block itself: belts at its edge pointing in, each
followed through belts, undergrounds and splitters to the inserters that take from it and the recipes of the machines
they feed; an entry carries what those recipes take that the block does not make inside. (F-16's own port list counts
every belt touching an edge.) What the block takes in all, less what it makes, is checked against what the base makes,
what is at hand and what a row of ours can make: rows only for what nothing makes.

    use_catalog(catalog)                     the layout library to look in (the planner sets it)
    offer(site, pack, rate)                  the best block for the pack now (an Offer), or None: the rows then
    Offer.need_rows / .elsewhere / .entries  what needs rows, what the base or the oil block makes, its entry belts
    rows(offer)                              the block placed with a belt stub, chests and inserters at each entry
                                             (no bus: the engineer fills the chests), and a chest at its output

NPC Factory places an offered block on a bus (F-18b) from the same Offer.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from companion import tier_path
from companion import modules as mods
from companion.modules import Module, Placed, Port
from companion.ratios import Book

VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
INWARD = {"west": 4, "north": 8, "east": 12, "south": 0}
OUTWARD = {"west": 12, "north": 0, "east": 4, "south": 8}
BELTS = ("transport-belt", "underground-belt", "splitter")
UNDERGROUND_REACH = 10      # tiles an underground belt may jump (the fastest's)
MAX_ENTRY_ITEMS = 2         # items one entry may carry: a lane each, never two on one (the user, 2026-10-29)
MAX_ENTRIES = 8             # entries a block fed from chests may have
PROVEN = 1.25               # a library block is taken over the generated rows unless it costs more than this many times
#                             their buildings for the same line (the user's proven blueprints, a little preferred)
CHEST = "iron-chest"
POLE = "small-electric-pole"
POLE_SUPPLY = 2.5           # the small pole's supply reach (a 5x5 area)
MAX_STUB = 24               # tiles a feed's belt stub may run out from the block (a staircase of them)
SETTINGS = ("pickup_position", "drop_position", "input_priority", "output_priority", "filter", "filters",
            "use_filters", "filter_mode", "spoil_priority")
_LIBRARY: dict = {}


def use_catalog(catalog: Any) -> None:
    """The layout library the science path looks in (CatalogPlanner sets it)."""
    _LIBRARY["catalog"] = catalog


@dataclass
class Kept(Placed):
    """A library block's building with its own settings (inserter pickup and drop, filters, splitter priorities)."""
    settings: dict = field(default_factory=dict)

    def to_plan(self, key: str, dx: float = 0, dy: float = 0) -> dict:
        out = super().to_plan(key, dx, dy)
        out.update(self.settings)
        return out


def _dir(e: Mapping[str, Any]) -> int:
    return (int(e.get("direction") or 0) // 4 * 4) % 16


def _tile(x: float, y: float) -> tuple[int, int]:
    return int(x // 1), int(y // 1)


@dataclass
class Entry:
    x: int
    y: int
    edge: str
    items: list[str] = field(default_factory=list)
    draw: dict[str, float] = field(default_factory=dict)   # what its machines take a second flat out (its share)
    lanes: dict[str, str] = field(default_factory=dict)    # the lane each item comes in on, "left" or "right" as seen
    #                                                         along the stub inward (the block's inserters take from
    #                                                         either: a fixed choice, the first item left)


@dataclass
class Offer:
    key: str
    label: str
    pack: str
    rate: float                                   # packs a second the block makes flat out
    entries: list[Entry]
    inputs: dict[str, float]                      # what it takes in from outside, items a second flat out
    need_rows: list[str]                          # of those, what nothing makes and a row of ours can
    elsewhere: list[str]                          # made by the base, at hand, or by another goal (oil, plates)
    output: tuple[int, int, str] | None           # its output belt at the edge (x, y, edge)
    record: Mapping[str, Any] = field(default_factory=dict, repr=False)
    lift: float = 0.8                             # items a second one chest's inserter moves (more chests past it)
    chests: bool = True                           # fed from chests (False: belts brought to its stubs, the hookup's)

    def line(self) -> str:
        rows = f", rows for {', '.join(self.need_rows)}" if self.need_rows else ""
        return f"{self.label} ({self.rate * 60:.0f}/min, {len(self.entries)} entries{rows})"


def flows(record: Mapping[str, Any], book: Book) -> tuple[dict[str, float], dict[str, float]]:
    """(what it makes a second flat out, what it takes from outside a second), from its machines' recipes and speeds:
    what it makes and uses inside cancels."""
    made: dict[str, float] = {}
    used: dict[str, float] = {}
    for e in record.get("entities") or ():
        recipe = book.recipes.get(str(e.get("recipe") or ""))
        machine = book.machines.get(str(e.get("prototype")))
        if recipe is None or recipe.energy <= 0:
            continue
        crafts = (machine.speed if machine else 1.0) / recipe.energy
        for item, amount in recipe.products:
            made[item] = made.get(item, 0.0) + crafts * amount
        for item, amount in recipe.ingredients:
            used[item] = used.get(item, 0.0) + crafts * amount
    takes = {i: r - made.get(i, 0.0) for i, r in used.items() if r > made.get(i, 0.0) + 1e-9}
    makes = {i: r - used.get(i, 0.0) for i, r in made.items() if r > used.get(i, 0.0) + 1e-9}
    return makes, takes


def _ends(e: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    """An inserter's pickup and drop tiles: its own settings where it has them, else facing its pickup (reach 2 for a
    long-handed one)."""
    x, y = float(e["position"]["x"]), float(e["position"]["y"])
    if isinstance(e.get("pickup_position"), Mapping) and isinstance(e.get("drop_position"), Mapping):
        pu, dr = e["pickup_position"], e["drop_position"]
        return _tile(x + float(pu["x"]), y + float(pu["y"])), _tile(x + float(dr["x"]), y + float(dr["y"]))
    reach = 2 if str(e.get("prototype", "")).startswith("long-handed") else 1
    dx, dy = VEC[_dir(e)]
    return _tile(x + dx * reach, y + dy * reach), _tile(x - dx * reach, y - dy * reach)


def _layout(record: Mapping[str, Any]):
    ents = list(record.get("entities") or ())
    belts: dict[tuple[int, int], Mapping[str, Any]] = {}
    sibling: dict[tuple[int, int], tuple[int, int]] = {}
    for e in ents:
        if any(str(e.get("prototype", "")).endswith(b) for b in BELTS):
            x, y = float(e["position"]["x"]), float(e["position"]["y"])
            if str(e["prototype"]).endswith("splitter"):
                dx, dy = VEC[_dir(e)]
                a = _tile(x - 0.5 * abs(dy), y - 0.5 * abs(dx))
                b = _tile(x + 0.5 * abs(dy), y + 0.5 * abs(dx))
                belts[a], belts[b] = e, e
                sibling[a], sibling[b] = b, a
            else:
                belts[_tile(x, y)] = e
    machines: dict[tuple[int, int], Mapping[str, Any]] = {}
    for e in ents:
        if e.get("recipe"):
            x, y = float(e["position"]["x"]), float(e["position"]["y"])
            r = 1 if any(k in str(e.get("prototype")) for k in ("assembling", "chemical", "furnace")) else 0
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    machines[_tile(x + dx, y + dy)] = e
    takes_from: dict[tuple[int, int], list[tuple[int, int]]] = {}
    drops_on: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for e in ents:
        if "inserter" in str(e.get("prototype", "")):
            pick, drop = _ends(e)
            takes_from.setdefault(pick, []).append(drop)
            drops_on.setdefault(drop, []).append(pick)
    return belts, sibling, machines, takes_from, drops_on


def _downstream(c: tuple[int, int], belts, sibling) -> list[tuple[int, int]]:
    b = belts[c]
    dx, dy = VEC[_dir(b)]
    nxt = [(c[0] + dx, c[1] + dy)]
    if b.get("belt_to_ground_type") == "input":
        jump = [(c[0] + dx * k, c[1] + dy * k) for k in range(1, UNDERGROUND_REACH + 1)]
        nxt = [n for n in jump if n in belts and belts[n].get("belt_to_ground_type") == "output"][:1]
    if c in sibling:   # (a splitter sends on both sides)
        o = sibling[c]
        nxt.append((o[0] + dx, o[1] + dy))
    return nxt


def entries(record: Mapping[str, Any], book: Book, pack: str | None = None
            ) -> tuple[list[Entry], tuple[int, int, str] | None, list[str]]:
    """Its belts at the edge pointing in, each with what it carries (what the machines it feeds take that the block
    does not make inside); its output belt at the edge (the one the pack leaves on); and what it takes that no entry was
    found to carry (a block with such is not fed from chests)."""
    w, h = (record.get("size") or [0, 0])[:2]
    belts, sibling, machines, takes_from, drops_on = _layout(record)
    makes, takes = flows(record, book)

    def edge_of(t):
        x, y = t
        return "west" if x == 0 else "east" if x == w - 1 else "north" if y == 0 else "south" if y == h - 1 else None
    upstream: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for c in belts:
        for n in _downstream(c, belts, sibling):
            upstream.setdefault(n, []).append(c)
    out_belt = None
    for t, e in sorted(belts.items()):   # (the exit whose belt carries the pack: what is dropped on it upstream)
        edge = edge_of(t)
        if not edge or _dir(e) != OUTWARD[edge] or e.get("belt_to_ground_type") == "input":
            continue
        seen, queue, carries = {t}, deque([t]), set()
        while queue:
            c = queue.popleft()
            for pick in drops_on.get(c, ()):
                m = machines.get(pick)
                recipe = m and book.recipes.get(str(m.get("recipe")))
                if recipe:
                    carries |= {i for i, _ in recipe.products}
            for u in upstream.get(c, ()):
                if u not in seen:
                    seen.add(u)
                    queue.append(u)
        if pack is None or pack in carries:
            out_belt = (t[0], t[1], edge)
            break
    found: list[Entry] = []
    reached: list[dict[int, Mapping[str, Any]]] = []
    inward = [t for t, e in sorted(belts.items()) if edge_of(t) and _dir(e) == INWARD[edge_of(t)]
              and e.get("belt_to_ground_type") != "output"]
    fed: set[tuple[int, int]] = set()   # (edge belts another entry's belts run into: a turn back at the edge, no entry)
    for start in inward:
        seen, queue = {start}, deque([start])
        while queue:
            for n in _downstream(queue.popleft(), belts, sibling):
                if n in belts and n not in seen:
                    seen.add(n)
                    queue.append(n)
        fed |= {t for t in inward if t != start and t in seen}
    for t, e in sorted(belts.items()):
        if t not in inward or t in fed:
            continue   # (U-2's block 3: its copper runs along the bottom edge and turns in there)
        edge = edge_of(t)
        seen, queue, items, mine = {t}, deque([t]), set(), {}
        while queue:
            c = queue.popleft()
            for drop in takes_from.get(c, ()):
                m = machines.get(drop)
                recipe = m and book.recipes.get(str(m.get("recipe")))
                if recipe:
                    items |= {i for i, _ in recipe.ingredients if i in takes}
                    mine[id(m)] = m
            for n in _downstream(c, belts, sibling):
                if n in belts and n not in seen:
                    seen.add(n)
                    queue.append(n)
        found.append(Entry(t[0], t[1], edge, sorted(items)))
        reached.append(mine)
    sharing: dict[int, int] = {}
    for mine in reached:
        for k in mine:
            sharing[k] = sharing.get(k, 0) + 1
    for en, mine in zip(found, reached):   # (each entry's draw: its machines' take, shared where several reach one)
        for k, m in mine.items():
            recipe, machine = book.recipes[str(m.get("recipe"))], book.machines.get(str(m.get("prototype")))
            crafts = (machine.speed if machine else 1.0) / recipe.energy if recipe.energy > 0 else 0.0
            for i, amount in recipe.ingredients:
                if i in en.items:
                    en.draw[i] = en.draw.get(i, 0.0) + crafts * amount / sharing[k]
    # (an entry the walk could not follow carries what no followed entry does; what is still left, no entry carries)
    covered = {i for en in found for i in en.items}
    rest = sorted(i for i in takes if i not in covered)
    empty = [en for en in found if not en.items]
    for en in empty:
        en.items = list(rest)
    uncovered = [] if empty else rest
    found = [en for en in found if en.items]
    for en in found:
        en.lanes = dict(zip(en.items, ("left", "right")))
    return found, out_belt, uncovered


def _locked(record: Mapping[str, Any], site: Mapping[str, Any]) -> bool:
    from companion import blueprints
    try:
        return bool(blueprints.locked_items(record, site))
    except (KeyError, TypeError):
        return True


BUS_BLOCK = "science-block"   # the bus module of an imported block making several packs (its stage: bus-science-block)


def bus_block(packs: tuple[str, ...], book: Book, rate: float, unlocked=None, key: str | None = None
              ) -> tuple[str, Module] | None:
    """An imported block of the library that makes every one of `packs`, as a bus module (U-2: the user's tileable
    science book on the bus): its own buildings, a port at each of its entries on its west edge (a bus tap brings a
    belt of the entry's one item to it) and one at its output there (its packs onto a bus lane, all on one belt). Only
    a block whose entries and output all sit on that edge, one item each, with what it is built of unlocked
    (`unlocked`: recipe names, None: all). `rate`: packs a second the bus asks of each (the taps' draw scaled to it).
    `key`: the block a bus was planned with (no other taken). (key, module), or None."""
    from companion import tier_builds
    catalog = _LIBRARY.get("catalog")
    if catalog is None or not packs:
        return None
    with catalog._lock:
        saved = list(catalog.blueprints.items())
    # (the packs' own recipes may still be coming: a bus is planned whole, green with red, its stage built once it lands)
    have = None if unlocked is None else set(unlocked) | set(packs)
    found = []
    for k, record in sorted(saved, key=lambda kv: str(kv[0])):
        if not record.get("import") or (key is not None and str(k) != key):
            continue
        if have is not None and any(r not in have for r in record.get("recipes_needed") or ()):
            continue
        makes, takes = flows(record, book)
        if any(makes.get(p, 0.0) <= 0 for p in packs) or any(tier_builds._fluid(book, i) for i in takes):
            continue
        ins, out, uncovered = entries(record, book, packs[0])
        if (uncovered or not ins or out is None or out[2] != "west" or any(en.edge != "west" for en in ins)
                or any(len(en.items) != 1 for en in ins)):
            continue
        if any(entries(record, book, p)[1] != out for p in packs[1:]):
            continue   # (every pack on the one output belt: the bus gives it one lane)
        made = min(makes[p] for p in packs)
        size = len(record.get("entities") or ())
        # (one making the rate asked first, the smallest of those: fewest buildings to lay by hand; else the fastest)
        found.append(((made < rate - 1e-9, size if made >= rate - 1e-9 else -made, str(k)), str(k), record, ins, out,
                      made))
    if not found:
        return None
    _, k, record, ins, out, made = min(found, key=lambda f: f[0])
    m = Module(BUS_BLOCK, width=int(record["size"][0]), height=int(record["size"][1]))
    for e in record.get("entities") or ():
        m.entities.append(Kept(str(e["prototype"]), float(e["position"]["x"]), float(e["position"]["y"]), _dir(e),
                               e.get("recipe"), e.get("belt_to_ground_type"),
                               settings={s: e[s] for s in SETTINGS if s in e}))
    share = min(1.0, rate / made) if made > 0 else 1.0   # (the taps draw what the bus asks of it, not its flat out)
    for en in sorted(ins, key=lambda en: en.y):
        item = en.items[0]
        m.ports.append(Port(item, "in", 0, en.y, mods.EAST, en.draw.get(item, 0.0) * share))
    m.ports.append(Port(packs[0], "out", 0, out[1], mods.WEST, made * share * len(packs)))
    m.makes = {p: made for p in packs}
    return k, m


def candidates(site: Mapping[str, Any], pack: str) -> list[Mapping[str, Any]]:
    """The library's imported blocks that make `pack` and can be built now (their research and buildings had)."""
    catalog = _LIBRARY.get("catalog")
    if catalog is None:
        return []
    with catalog._lock:
        saved = list(catalog.blueprints.values())
    return [b for b in saved if b.get("import") and pack in (b.get("outputs") or ()) and not _locked(b, site)]


def _offer_of(record: Mapping[str, Any], site: Mapping[str, Any], pack: str, rate: float, book: Book,
              path: tier_path.Path, chests: bool = True) -> Offer | None:
    from companion import tier_builds
    makes, takes = flows(record, book)
    made = makes.get(pack, 0.0)
    if made <= 0 or any(tier_builds._fluid(book, i) for i in takes):
        return None   # (a fluid piped in: no chest feeds it)
    found, out, uncovered = entries(record, book, pack)
    if (not found or not out or uncovered or len(found) > MAX_ENTRIES
            or any(len(en.items) > MAX_ENTRY_ITEMS for en in found)):
        return None
    # (fed from chests, one inserter into its output chest: the block run no faster than that, what it takes scaled
    # with it; belts brought to it carry what it makes flat out)
    lift = mods.inserter_rate("inserter", site) or 0.8
    run = min(made, lift) if chests else made
    takes = {i: r * run / made for i, r in takes.items()}
    need, elsewhere = [], []
    for item, r in sorted(takes.items()):
        share = r * min(1.0, rate / run)
        have = max(tier_path.have_rate(site, item), tier_builds._standing(site, item))
        if (have >= tier_path.SHORT * share or path.held(item) >= share * tier_path.HELD_MINUTES * 60
                or tier_builds.buildable(item, site, book)):
            elsewhere.append(item)
        else:
            need.append(item)
    o = Offer(str(record.get("id")), str((record.get("import") or {}).get("label") or record.get("id")),
              pack, run, found, takes, need, elsewhere, out, record, lift, chests)
    return o if rows(o, chests) is not None else None   # (its feeds laid out without overlapping)


def offer(site: Mapping[str, Any], pack: str, rate: float, *, book: Book | None = None,
          path: tier_path.Path | None = None, chests: bool = True) -> Offer | None:
    """The block to build for `pack` at `rate`: of those that make enough, the one with the fewest buildings in all
    (its chests and the rows for what it needs too), else the one that makes the most; and only while it costs no more
    than PROVEN times the generated rows for the same line. None: the generated rows then."""
    from companion import tier_builds
    tier_builds._note_fluids(site)   # (the site's own fluids, a mod's among them, before any input is judged)
    book = book or Book.from_site(site)
    path = path or tier_path.assess(site)
    offers = [o for o in (_offer_of(r, site, pack, rate, book, path, chests) for r in candidates(site, pack)) if o]
    if not offers:
        return None
    enough = [o for o in offers if o.rate >= rate]
    if enough:
        priced = sorted(((cost(o, site, book, path), o.key, o) for o in enough), key=lambda t: t[:2])
        theirs, _, pick = priced[0]
    else:
        pick = max(offers, key=lambda o: (o.rate, o.key))
        theirs = cost(pick, site, book, path)
    # (one engineer builds it by hand: a block three times the line's rate took it from minute 29 to past 39 in proving
    # stage-blue; taken unless it costs much more than the rows for the same line)
    mine = generated_cost(site, pack, rate, book, path)
    if mine is not None and theirs > PROVEN * mine:
        return None
    return pick


def chosen(site: Mapping[str, Any], pack: str, key: str, rate: float, *, book: Book | None = None,
           path: tier_path.Path | None = None, chests: bool = True) -> Offer | None:
    """The block `key` offered for `pack` at `rate` once more (to plan the build picked: no second judgment of cost,
    the same rate it was picked at), or None when it can no longer be had here."""
    from companion import tier_builds
    tier_builds._note_fluids(site)
    book = book or Book.from_site(site)
    path = path or tier_path.assess(site)
    record = next((r for r in candidates(site, pack) if str(r.get("id")) == key), None)
    return _offer_of(record, site, pack, rate, book, path, chests) if record is not None else None


def _rows_cost(site: Mapping[str, Any], item: str, r: float, book: Book) -> int:
    """Buildings a generated row takes, with the chests and inserters (or loaders) that feed it, as it is built."""
    from companion import military_modules, tier_builds
    from companion.modules import loader_limit
    try:
        parts, _ = tier_builds.fed_modules(item, r, book, tier_builds._machine(site), site)
    except tier_builds.NoBuilder:
        return 0
    limit = loader_limit(site)
    return sum(len(military_modules._with_chests(m, limit)) for m in parts)


def cost(o: Offer, site: Mapping[str, Any], book: Book, path: tier_path.Path) -> int:
    """Buildings the block takes, its chests, stubs and poles, and the rows for what it needs."""
    from companion import tier_builds
    module = rows(o, o.chests)
    total = len(module.entities) if module is not None else len(o.record.get("entities") or ())
    for w in o.need_rows:
        r = min(tier_builds.FEED_BLOCK_RATE, max(o.inputs.get(w, 0.0), tier_builds.MIN_RATE))
        total += _rows_cost(site, w, r, book)
        total += sum(_rows_cost(site, f, fr, book) for f, fr in tier_builds.feeders(w, r, site, book, path, (o.pack,)))
    return total


def generated_cost(site: Mapping[str, Any], pack: str, rate: float, book: Book, path: tier_path.Path) -> int | None:
    """Buildings the generated rows take for the same line at `rate` (None when they cannot be built here)."""
    from companion import tier_builds
    line = _rows_cost(site, pack, rate, book)
    if not line:
        return None
    return line + sum(_rows_cost(site, f, r, book) for f, r in tier_builds.feeders(pack, rate, site, book, path))


_AROUND = sorted(((dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if (dx, dy) != (0, 0)),
                 key=lambda t: (abs(t[0]) + abs(t[1]), t))   # (a small pole's supply area round a tile, nearest first)
SUPPLY = {"small-electric-pole": 2.5, "medium-electric-pole": 3.5, "substation": 9.0, "big-electric-pole": 2.0}
ELECTRIC = {"assembling-machine": 1.5, "chemical-plant": 1.5, "oil-refinery": 2.5, "electric-furnace": 1.5, "lab": 1.5,
            "beacon": 1.5, "centrifuge": 1.5, "electric-mining-drill": 1.5, "inserter": 0.5}   # (half its width)
REFUSED = ""   # (why rows() last gave up on a block, for the log)


def _half(name: str) -> float | None:
    """Half the width of a building that runs on power (None: one that does not)."""
    return next((h for k, h in ELECTRIC.items() if k in name), None)


def _powered(m: Module, x: float, y: float, half: float = 0.5) -> bool:
    """A pole of the module's own whose supply area reaches the building centred at (x, y)."""
    return any(abs(e.x - x) < SUPPLY.get(e.name, POLE_SUPPLY) + half
               and abs(e.y - y) < SUPPLY.get(e.name, POLE_SUPPLY) + half
               for e in m.entities if e.name in SUPPLY or e.name.endswith("electric-pole"))


def _entry_rate(o: Offer, en: Entry, item: str) -> float:
    """This entry's share of what the block takes of `item`: by what its own machines draw, else an even share."""
    carriers = [e for e in o.entries if item in e.items]
    total = sum(e.draw.get(item, 0.0) for e in carriers)
    share = en.draw.get(item, 0.0) / total if total > 0 else 1.0 / max(1, len(carriers))
    return o.inputs.get(item, 0.0) * share


def _refuse(why: str) -> None:
    global REFUSED
    REFUSED = why
    return None


def rows(o: Offer, chests: bool = True) -> Module | None:
    """The block as a module fed from chests (no bus): its own buildings with their settings; at each entry a belt
    stub leading in, a chest and inserter beside it for each item (more where one inserter is too slow for it) and a
    pole for them; at its output an inserter into a chest, and a pole; and poles for any of its own buildings no pole of
    its own reaches. Ports, one a chest, in the chests' order (module.feed_index: the chests' indices, since the block
    may hold chests of its own). chests=False: only the stubs, a port for each item at its stub's outer end and the
    output's port on the tile past its belt end, for a hookup that brings belts to them (no feed_index). None when there
    is no room for a feed, said in REFUSED."""
    m = Module(f"import-{o.pack}")
    taken: set[tuple[int, int]] = set()
    for t in o.record.get("footprint") or ():
        taken.add((int(t[0]), int(t[1])))
    taken |= set(_layout(o.record)[2])   # (its machines' whole areas)
    for e in o.record.get("entities") or ():
        taken.add(_tile(float(e["position"]["x"]), float(e["position"]["y"])))
        m.entities.append(Kept(str(e["prototype"]), float(e["position"]["x"]), float(e["position"]["y"]), _dir(e),
                               e.get("recipe"), e.get("belt_to_ground_type"),
                               settings={k: e[k] for k in SETTINGS if k in e}))
    kept = len(m.entities)

    def put(name: str, tx: int, ty: int, direction: int = 0) -> bool:
        if (tx, ty) in taken:
            return False
        taken.add((tx, ty))
        m.entities.append(Placed(name, tx + 0.5, ty + 0.5, direction))
        return True

    def pole_for(tile: tuple[int, int], ok=lambda t: True) -> bool:   # (a pole reaching it, anywhere in its area)
        return _powered(m, tile[0] + 0.5, tile[1] + 0.5) or any(
            ok(t) and put(POLE, *t) for t in ((tile[0] + dx, tile[1] + dy) for dx, dy in _AROUND))
    ports: list[Port] = []
    feed: list[int] = []
    for en in o.entries:   # (every entry's first two stub tiles first: the output's or another's feed never lands on one)
        vx, vy = VEC[INWARD[en.edge]]
        for k in (1, 2):
            if not put("transport-belt", en.x - vx * k, en.y - vy * k, INWARD[en.edge]):
                return _refuse(f"the entry at {(en.x, en.y)}: its stub tile {(en.x - vx * k, en.y - vy * k)} is taken")
    if any(len(en.items) > 2 for en in o.entries):   # (a lane each: never two items on one, either mode)
        bad = next(en for en in o.entries if len(en.items) > 2)
        return _refuse(f"the entry at {(bad.x, bad.y)} carries {len(bad.items)} items on two lanes")
    if not chests:
        for en in o.entries:
            vx, vy = VEC[INWARD[en.edge]]
            for item in en.items:
                ports.append(Port(item, "in", en.x - vx * 2, en.y - vy * 2, INWARD[en.edge], _entry_rate(o, en, item),
                                  en.lanes.get(item)))
        if o.output:
            x, y, edge = o.output
            vx, vy = VEC[OUTWARD[edge]]
            if (x + vx, y + vy) in taken:
                return _refuse(f"its output at {(x, y)} runs into the block")
            ports.append(Port(o.pack, "out", x + vx, y + vy, OUTWARD[edge], o.rate))
    out_port, out_index = None, None   # (the output next: the entries' feeds find room round it)
    if chests and o.output:
        x, y, edge = o.output
        vx, vy = VEC[OUTWARD[edge]]
        if not put("inserter", x + vx, y + vy, INWARD[edge]):   # (faces the belt end)
            return _refuse(f"its output's inserter at {(x + vx, y + vy)} stands on the block")
        out_index = len(m.entities)
        if not (put(CHEST, x + 2 * vx, y + 2 * vy) and pole_for((x + vx, y + vy))):
            return _refuse(f"no room for its output's chest and pole by {(x, y)}")
        out_port = Port(o.pack, "out", x + 2 * vx, y + 2 * vy, OUTWARD[edge], o.rate)
    # (entries may lie a tile or two apart, R-5b: each edge's taken in turn along it, and an entry's chests stand beyond
    # the stub ends of the ones before it, never on the line of one still to come; a staircase out from the block)
    order = sorted(o.entries, key=lambda en: (en.edge, en.y if en.edge in ("west", "east") else en.x)) if chests else []
    for i, en in enumerate(order):
        vx, vy = VEC[INWARD[en.edge]]
        ax, ay = (0, 1) if en.edge in ("west", "east") else (1, 0)   # (along the edge)
        later = {(l.edge, l.y if l.edge in ("west", "east") else l.x) for l in order[i + 1:]}

        def free_line(t, en=en, ax=ax, later=later):
            return (en.edge, t[1] if ax == 0 else t[0]) not in later
        length = 2
        # (each item its own lane, every chest of it on that side: two items on one lane fed from chests may stop it for
        # good, the one ahead waiting on the other; more chests where one inserter is too slow for this entry's share,
        # Codex R-5b. An entry with no lane to spare waits for belts brought to it, chests=False)
        feeds = []
        for j, item in enumerate(en.items):
            r = _entry_rate(o, en, item)
            n = max(1, math.ceil(r / o.lift - 1e-9))
            # (fed from chests the lanes are free: one item either, two one each, every chest of an item on its side)
            feeds += [(item, (-1, 1) if len(en.items) == 1 else ((-1,), (1,))[j % 2], r / n)] * n
        right = (-vy, vx)   # (an inserter standing on the right, seen inward, drops on the left lane: its far one)
        for item, sides, rate in feeds:
            spot = None
            for side, d in ((side, d) for side in sides for d in range(1, MAX_STUB + 1)):   # (its own lane first)
                sx, sy = en.x - vx * d, en.y - vy * d
                it, ch = (sx + ax * side, sy + ay * side), (sx + 2 * ax * side, sy + 2 * ay * side)
                if it in taken or ch in taken or not (free_line(it) and free_line(ch)):
                    continue
                spot = d, it, ch, (ax * side, ay * side)
                break
            if spot is None:
                return _refuse(f"the entry at {(en.x, en.y)}: no room for the {item} chest on a lane of its own")
            d, it, ch, toward = spot
            while length < d:   # (its stub runs out to the chest)
                length += 1
                tile = (en.x - vx * length, en.y - vy * length)
                if not put("transport-belt", *tile, INWARD[en.edge]):
                    return _refuse(f"the entry at {(en.x, en.y)}: its stub runs into {tile}")
            facing = {(-1, 0): 12, (1, 0): 4, (0, -1): 0, (0, 1): 8}[toward]
            feed.append(len(m.entities))
            put(CHEST, *ch)
            put("inserter", *it, facing)   # (faces the chest it takes from)
            if not pole_for(it, free_line):   # (its pole with it, off later lines)
                return _refuse(f"no room for a pole by the inserter at {it}")
            lane = "left" if toward == right else "right"
            ports.append(Port(item, "in", ch[0], ch[1], facing, rate, lane))
    for e in m.entities[:kept]:   # (its own buildings that no pole of its own reaches)
        half = _half(e.name)
        if half is None or _powered(m, e.x, e.y, half):
            continue
        span = int(POLE_SUPPLY + half) + 1
        near = sorted(((math.floor(e.x) + dx, math.floor(e.y) + dy) for dx in range(-span, span + 1)
                       for dy in range(-span, span + 1)),
                      key=lambda t: (abs(t[0] + 0.5 - e.x) + abs(t[1] + 0.5 - e.y), t))
        if not any(abs(t[0] + 0.5 - e.x) < POLE_SUPPLY + half and abs(t[1] + 0.5 - e.y) < POLE_SUPPLY + half
                   and put(POLE, *t) for t in near):
            return _refuse(f"no room for a pole to power its {e.name} at {(e.x, e.y)}")
    if out_port is not None:   # (its chest last among the ports, as the stages read them)
        ports.append(out_port)
        feed.append(out_index)
    m.ports = ports
    m.feed_index = feed if chests else None
    w, h = (o.record.get("size") or [0, 0])[:2]
    m.width, m.height = int(w), int(h)
    m.makes = {o.pack: o.rate}
    return m
