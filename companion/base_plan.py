"""A player-style base plan: a main bus fed by mining fields and smelting columns.

Given what an engineer can see (an npc-map-v1 dump) and a production goal, this
picks a free strip for the bus near home, puts a smelting column per plate at
the bus head, lays mining fields on the nearest ore patches and routes their
ore belts to the columns, then attaches modules down the bus. Each column's ore
belt carries ore on one lane and coal on the other: the ore belt sideloads into
the column head from the west and a small coal field's belt from the east (fuel
by hand only when no coal route fits). Lane columns are reserved for the lanes
later goals will add. The result is a list of build
stages in world coordinates, ready to submit as plans.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from companion import blue_modules, construction, doorways, growth, land, military_modules, outpost, rocket_modules
from companion import modules as mods
from companion.bus import END_CHEST, Bus
from companion import mall as malls
from companion.mall import mall
from companion.modules import EAST, LOADER, Module, NORTH, Placed, SOUTH, WEST
from companion import belt_lines, plate_feed, power_plant, science_blocks, team_area
from companion.power import connect
from companion.ratios import Book, solve
from companion.smelting import (ORE_PER_PAIR, STEEL_PER_PAIR, field_at, footprint, foreign_tiles, outlets, furnaces_for,
                                ore_tiles, plan_field, route, smelting_column, steel_column, steel_pairs)
from companion.style import Style
from companion import rocket_plan

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore"}
DRILL_RATE = 0.5
COAL_PER_FURNACE = 0.09 / 4.0   # a stone furnace burns 90 kW; coal holds 4 MJ
LANE_FURNACES = 24              # one yellow lane (7.5/s) feeds 24 stone furnaces, and their plates fill one lane
BLOCK_FURNACES = 2 * LANE_FURNACES   # two columns sharing a plate belt: a full belt (15/s)
FIRST_FURNACES = 8   # a plate block's first stage at most: one thin lane, standing in a few minutes (F-21)
BLOCK_W = 11                    # ore | ins | furnace | ins | plates | ins | furnace | ins | ore
FURNACE_PLATES = 1 / 3.2        # plates/s of a stone furnace
BLOCK_GAP = 8                   # between the iron and copper blocks: their feeds come in there
SECOND_ROUTE = 160              # the second column's ore belt at most this long, or the column waits
COAL_ROWS = 5                   # above a block's heads: its ore and coal belts, their splitters and branches
BUS_MARGIN = 2                  # tiles kept clear around the bus and its modules: no belt runs past a module's edge
FIELD_REACH = 150               # a block's mining field no further than this from its ore entry (or a smaller one)
MAX_FIT_TRIES = 5
PLACE_GAP = 48   # tiles between bus heads that are other places for a bus, not the same place moved (find_fit avoid)
# A new bus takes a plate from the base's own electric smelting (companion/plate_feed.py) where half of what that makes
# covers what the bus draws (the other half goes on to its store until the store is full); its own smelting blocks
# come after its science modules then, as growth.
FEED_SHARE = 0.5
# What a tile further from home is worth, in plates, when options for one build are weighed (build_cost): every trip to
# build it and keep it is that much longer.
WALK_PLATES = 2.0
UNKNOWN_PLATES = 10.0   # a building whose recipe the book does not know (the offline data has no pipe-to-ground)
BUS_GROWTH = 24                 # rows past the planned bus end kept free for the modules of later goals
TIERS = ("automation-science-pack", "logistic-science-pack", "chemical-science-pack")


@dataclass
class Stage:
    name: str
    entities: list[Placed]
    supplies: list[dict] = field(default_factory=list)   # manual supplies (fuel)
    proof: str = ""   # "built": its buildings standing prove it (a feed whose source was lost: no plates to wait on)


@dataclass
class Layout:
    bus: Bus
    stages: list[Stage]
    notes: list[str] = field(default_factory=list)
    designs: dict[str, str] = field(default_factory=dict)   # module kind -> library design used
    ahead: bool = False   # smelting laid out for the next science tier
    sites: dict[str, str] = field(default_factory=dict)     # where blocks off the bus went ("x,y"), kept for replays
    power_row: dict | None = None   # the boiler row the power stage builds or extends
    tight: bool = False             # the compact fallback: one belt per plate, columns side by side
    columns: int = 0                # lane columns asked for (the bus adds the belts of plate groups)

    def entities(self) -> list[Placed]:
        return [e for s in self.stages for e in s.entities]


_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def blocked_tiles(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Tiles a new building cannot use: existing buildings (any force) and water, and the tiles where
    existing belts, inserters, loaders and drills put things down or take them from (a new belt there
    would take or give them items: plates from a module's belt end onto an ore belt, ore from a drill
    onto a coal line, a belt's load into a loader's chest). Loaders are two tiles long. Trees and
    rocks are cleared by the engineer, so they do not block. In the team world, also every tile outside
    the engineer's plot (but beyond the team area) and near what players built (team_area), ground the
    factory plan keeps for its other sections (the dump's `reserved` boxes: land.reserved_tiles), and the lanes
    straight through each door of a wall (companion/doorways.py)."""
    tiles: set[tuple[int, int]] = set()
    for y, a, b in dump.get("water", []):
        tiles.update((x, y) for x in range(a, b + 1))
    # Ground the game refused to build on for good (a cliff the map's boxes missed: the game's word stands)
    for box in dump.get("avoid") or []:
        if isinstance(box, Mapping) and all(isinstance(box.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")):
            tiles.update((x, y) for x in range(int(box["x0"]), int(box["x1"]) + 1)
                         for y in range(int(box["y0"]), int(box["y1"]) + 1))
    for e in dump.get("entities", []):
        if e.get("t") in ("simple-entity", "tree"):
            continue
        w, h = e.get("w", 1), e.get("h", 1)
        if (e.get("d") or 0) in (4, 12):
            w, h = h, w
        for i in range(w):
            for j in range(h):
                tiles.add((math.floor(e["x"] - w / 2 + 0.5) + i, math.floor(e["y"] - h / 2 + 0.5) + j))
        dx, dy = _STEP.get(e.get("d") or 0, (0, 0))
        if e.get("t") == "transport-belt":
            tiles.add((math.floor(e["x"]) + dx, math.floor(e["y"]) + dy))
        elif e.get("t") == "inserter":
            reach = 2 if "long" in str(e.get("n")) else 1   # an inserter faces where it picks up
            tiles.add((math.floor(e["x"]) - dx * reach, math.floor(e["y"]) - dy * reach))
        elif e.get("t") == "mining-drill" and e.get("n") != "pumpjack":
            ahead = e.get("w", 2) / 2 + 0.3
            tiles.add((math.floor(e["x"] + dx * ahead), math.floor(e["y"] + dy * ahead)))
        elif e.get("t") == "loader":
            # an output loader puts items on the tile ahead, an input loader takes them from the one behind
            # (its other end is its chest's); both when the dump does not say which end it is
            for sign in {"output": (1,), "input": (-1,)}.get(e.get("io"), (1, -1)):
                tiles.add((math.floor(e["x"] + sign * dx * 1.5), math.floor(e["y"] + sign * dy * 1.5)))
    return tiles | team_area.forbidden(dump) | land.reserved_tiles(dump) | doorways.lanes(dump)


def replaceable(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Tiles of the engineer's own burner-phase buildings (burner drills and the stone furnace in front
    of each): the bus's electric fields and columns go over them, and the engineer picks them up
    when it builds there, as a player replaces burner mining with electric mining."""
    own = [e for e in dump.get("entities", []) if e.get("f") == dump.get("force")]
    drills = [(e["x"], e["y"]) for e in own if e.get("n") == "burner-mining-drill"]
    out: set[tuple[int, int]] = set()
    for e in own:
        if e.get("n") == "burner-mining-drill" or (
                e.get("n") == "stone-furnace" and any(abs(e["x"] - x) <= 2.5 and abs(e["y"] - y) <= 2.5
                                                      for x, y in drills)):
            w, h = e.get("w", 1), e.get("h", 1)
            out.update((math.floor(e["x"] - w / 2 + 0.5) + i, math.floor(e["y"] - h / 2 + 0.5) + j)
                       for i in range(w) for j in range(h))
    # Removing its furnace alone leaves a burner drill dropping ore onto the new belt.
    for e in own:
        if e.get("n") == "burner-mining-drill":
            dx, dy = _STEP.get(e.get("d") or 0, (0, 0))
            reach = e.get("w", 2) / 2 + 0.3
            out.discard((math.floor(e["x"] + dx * reach), math.floor(e["y"] + dy * reach)))
    return out


def all_ore(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for resource in dump.get("resources", {}):
        out |= ore_tiles(dump["resources"], resource)
    return out


def ore_anchor(dump: Mapping[str, Any], home: tuple[int, int], radius: float = 200.0) -> tuple[float, float] | None:
    """Where the engineer's ore is: the middle of the iron, copper and (half weight) coal near home.
    Smelting placed there needs short ore belts; a player builds by the ore, not across the map."""
    points = []
    for resource, weight in (("iron-ore", 1.0), ("copper-ore", 1.0), ("coal", 0.5)):
        near = [t for t in ore_tiles(dump.get("resources", {}), resource)
                if (t[0] - home[0]) ** 2 + (t[1] - home[1]) ** 2 <= radius * radius]
        if near:
            points.append((sum(x for x, _ in near) / len(near), sum(y for _, y in near) / len(near), weight))
    if not points:
        return None
    total = sum(w for *_, w in points)
    return sum(x * w for x, _, w in points) / total, sum(y * w for _, y, w in points) / total


def find_fit(taken: set, shape: set[tuple[int, int]], home: tuple[int, int],
             area: tuple[int, int, int, int], step: int = 2,
             anchor: tuple[float, float] | None = None, skip: int = 0,
             anchor_at: tuple[float, float] | None = None,
             avoid: Iterable[tuple[int, int]] = ()) -> tuple[int, int] | None:
    """The offset nearest `anchor` (default: home; an engineer's style may lean it in one direction)
    at which every tile of `shape` (tiles relative to the bus head) is free: the bus fits around
    obstacles the way a player would lay it, not only in an empty box. `avoid`: offsets already
    weighed; none within PLACE_GAP of one (another place for the bus, not the same one moved)."""
    x0, y0, x1, y1 = area
    ax, ay = anchor or home
    xs, ys = [x for x, _ in shape], [y for _, y in shape]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    # The point of the shape brought nearest the anchor (default its middle; the bus brings its
    # smelting head, so ore belts stay short).
    cx, cy = anchor_at or ((left + right) / 2, (top + bottom) / 2)
    avoid = list(avoid)
    candidates = sorted(((ox + cx - ax) ** 2 + (oy + cy - ay) ** 2, ox, oy)
                        for ox in range(x0 - left, x1 - right + 1, step)
                        for oy in range(y0 - top, y1 - bottom + 1, step)
                        if not any((ox - a) ** 2 + (oy - b) ** 2 < PLACE_GAP ** 2 for a, b in avoid))
    # Check the outline first: most spots fail on it, and it is cheap.
    edge = [(x, y) for x, y in shape if x in (left, right) or y in (top, bottom)]
    for _, ox, oy in candidates:
        if any((x + ox, y + oy) in taken for x, y in edge):
            continue
        if not any((x + ox, y + oy) in taken for x, y in shape):
            if skip == 0:
                return ox, oy
            skip -= 1
    return None


def build_cost(entities: Iterable[Placed], book: Book, home: tuple[float, float]) -> float:
    """What an option for a build costs: the plates (and bricks and the like) its buildings take, through their recipes,
    and the walk out to the farthest of them (WALK_PLATES a tile from home). By pieces, a boiler row by its coal with
    the water piped 390 tiles (78 underground pipes) beat one by the shore, though it took 40% more iron and stood 64
    tiles further out (run 57's map, 2026-10-26)."""
    entities = list(entities)
    plates = 0.0
    for name in (e.name for e in entities):
        plates += sum(construction.cost([name], book).values()) or UNKNOWN_PLATES
    far = max((math.hypot(e.x - home[0], e.y - home[1]) for e in entities), default=0.0)
    return plates + WALK_PLATES * far


def plate_belts(traits: Iterable[str] = ()) -> dict[str, int]:
    """Bus belts per plate, side by side: two of iron and two of copper, as most players start a bus;
    four of iron for engineers who build big."""
    big = bool(set(traits) & {"ambitious", "hoarder", "perfectionist", "bold"})
    return {"iron-plate": 4 if big else 2, "copper-plate": 2}


def pick_module(kind: str, rate: float, book: Book, machine: str, library=None, traits: Iterable[str] = (),
                chosen: Mapping[str, str] | None = None, designs: dict[str, str] | None = None) -> Module:
    """The built-in module or a proven design from the library. Traditionalists keep the built-in one,
    curious and innovative engineers take the design, the rest whichever is cheaper to build. A bus
    already being built keeps the choices it was planned with (`chosen`)."""
    builtin = getattr(mods, kind)(rate, book, machine=machine)
    if chosen is not None:
        key = chosen.get(kind)
        design = library.get(key) if key and library is not None else None
        if design is not None and designs is not None:
            designs[kind] = key
        return design or builtin
    found = library.best(kind, rate) if library is not None else None
    if not found:
        return builtin
    key, design = found
    traits = set(traits)
    if "traditionalist" in traits:
        return builtin
    if not traits & {"curious", "innovative"} and len(design.entities) >= len(builtin.entities):
        return builtin
    if designs is not None:
        designs[kind] = key
    return design


def modules_for(packs: tuple[str, ...], science_per_minute: float, book: Book,
                machine: str = mods.ASSEMBLER, unlocked: Iterable[str] | None = None, *, library=None,
                traits: Iterable[str] = (), chosen: Mapping[str, str] | None = None,
                designs: dict[str, str] | None = None, labs: bool = True,
                mall_early: bool = False, gears_on_site: bool = False, mall_last: bool = False,
                with_mall: bool = True) -> list[Module]:
    """The bus modules for a science goal, top to bottom: shared intermediates first (gears,
    circuits), then science, the mall once green science is in reach, then labs at the end. With
    `mall_last` (a bus planned to make science first, mall.OWN) the mall comes last; without `with_mall`, not
    here (a section of the bus has it: plan_bus_base)."""
    rate = science_per_minute / 60

    def pick(kind: str, r: float) -> Module:
        return pick_module(kind, r, book, machine, library, traits, chosen, designs)

    built: list[Module] = []
    malls = bus_malls(book, unlocked) if with_mall else []
    block = science_block(packs, rate, book, unlocked, chosen, designs)
    if block is not None:
        # (an imported block making red and green: its packs leave on one belt onto one lane, and the labs take both
        # from it; gears and circuits after the science, for the mall here or a later section's, U-2)
        tail = [pick("gears", rate * 1.5), pick("green_circuits", rate), *malls]
        lab = [mods.labs(max(2, math.ceil(science_per_minute * 30 / 60)), (packs[0],))] if labs else []
        return [block, *tail, *lab] if mall_early and not mall_last else [block, *lab, *tail]
    if "logistic-science-pack" in packs:
        built.append(pick("gears", rate * 1.5))
        built.append(pick("green_circuits", rate))
        if mall_early and not mall_last:
            built += malls
    if "automation-science-pack" in packs:
        built.append(pick("red_science", rate))
    if "logistic-science-pack" in packs:
        # Gears made on site: its inserter and belt assemblers get them from gear assemblers beside them.
        built.append(blue_modules.green_science_on_site(rate, book, machine) if gears_on_site
                     else pick("green_science", rate))
        if not mall_early and not mall_last:
            built += malls
    # Labs: enough to use the science at ~30 s per research unit.
    if labs:
        built.append(mods.labs(max(2, math.ceil(science_per_minute * 30 / 60)), packs))
    if mall_last and "logistic-science-pack" in packs:
        built += malls
    return built


def science_block(packs: tuple[str, ...], rate: float, book: Book, unlocked: Iterable[str] | None,
                  chosen: Mapping[str, str] | None, designs: dict[str, str] | None) -> Module | None:
    """The user's science block for red and green on the bus (science_blocks.bus_block), when the library holds one
    the bus can feed: kept by a bus planned with it (`chosen`), never put in a bus planned without it."""
    from companion import science_blocks
    want = ("automation-science-pack", "logistic-science-pack")
    if tuple(p for p in packs if p in want) != want:
        return None
    key = None
    if chosen is not None:
        key = chosen.get(science_blocks.BUS_BLOCK)
        if not key:
            return None
    found = science_blocks.bus_block(want, book, rate, unlocked, key)
    if found is None:
        return None
    if designs is not None:
        designs[science_blocks.BUS_BLOCK] = found[0]
    return found[1]


def bus_malls(book: Book, unlocked: Iterable[str] | None) -> list[Module]:
    """The bus's own mall: its two parts, each a module (companion/mall.py)."""
    return [mall(book, unlocked, "logistics"), mall(book, unlocked, "machines")]


# What a bus extension for a new goal brings: lanes fed from outside, then modules. Each goal gets its
# own section of the bus, below the ones before it, so its new lanes never run beside built taps.
SIDE_LANES = {"chemical-science-pack": ("coal", "plastic-bar", "sulfur"),
              "rocket-part": ("rocket-fuel", "stone-brick", "iron-ore"),
              "military-science-pack": ("coal", "stone-brick")}   # (grenades; walls)
EXTENSIONS = ("chemical-science-pack", "rocket-part", "military-science-pack")
SCIENCE_EXTENSIONS = ("military-science-pack", "chemical-science-pack")   # sections that bring a new pack
# The modules a section's side lanes come in ahead of (their stages are built first).
SECTION_MODULES = {"chemical-science-pack": {"gears-2", "green-circuits-2", "steel", "red-circuits", "engines",
                                            "blue-science", "labs-blue"},
                   "military-science-pack": {"steel-military", "piercing", "military-science", "labs-military"}}
ROCKET_RATE = 0.05   # rocket parts/s: a rocket every 17 minutes


def side_lanes(goal: str, steel_direct: bool = False) -> tuple[str, ...]:
    """The lanes a goal's section starts from outside: blue's steel too when it comes by direct
    insertion from a steel block at an iron field (else a bus module makes it from bus iron)."""
    lanes = SIDE_LANES.get(goal, ())
    return (*lanes, "steel-plate") if steel_direct and goal == "chemical-science-pack" else lanes


class NoDirectSteel(ValueError):
    """No steel block, with its ore, coal and steel belts, fits: the bus makes steel in a module."""


def ordered_goals(extra: Iterable[str]) -> list[str]:
    """Extension goals in the order their sections go down the bus: the order they were added (the
    sections already built stay where they are). The bus's own mall (mall.OWN) is no section: it goes
    among the bus's modules."""
    out = []
    for g in extra:
        if (g in EXTENSIONS or growth.is_growth(g) or growth.is_expansion(g) or malls.is_extension(g)) \
                and not malls.is_own(g) and g not in out:
            out.append(g)
    return out


def section_for(goal: str, science_per_minute: float, book: Book, machine: str = mods.ASSEMBLER,
                packs: tuple[str, ...] = (), steel_direct: bool = False,
                gears_on_site: bool = False) -> list[Module]:
    """Modules for one extension goal. Blue: more gears and green circuits for the new consumers,
    steel, red circuits, engines, blue packs, and labs for every pack. The rocket: more green and red
    circuits, steel and engines, then processing units, LDS, electric engines, concrete, platform
    foundations and the silo (rocket fuel, bricks and iron ore come in from outside)."""
    if growth.is_growth(goal):
        index, grown_packs = growth.parse(goal)
        return growth.section(index, science_per_minute, grown_packs, book, machine)
    if growth.is_expansion(goal):   # a full belt more of one plate
        return growth.expansion(goal)
    if malls.is_extension(goal):   # more of the mall, for what the factory plan wants of it
        return [malls.extension(goal, book)]
    if goal == "military-science-pack":
        # Military science (the team world's grey packs): steel for the piercing rounds from bus iron,
        # piercing rounds onto the bus, the pack row (grenades and walls made beside each pack assembler),
        # and labs for every pack so far.
        rate = science_per_minute / 60
        piercing = military_modules.needs(rate, book).get(military_modules.PIERCING, rate / 2)
        made = book.recipe_for(military_modules.PIERCING)
        steel = blue_modules.steel(piercing / made.makes(military_modules.PIERCING)
                                   * dict(made.ingredients).get("steel-plate", 1.0), book)
        steel.name = "steel-military"
        labs = mods.labs(max(2, math.ceil(science_per_minute * 30 / 60)),
                         packs or ("automation-science-pack", "logistic-science-pack", goal))
        labs.name = "labs-military"
        return [steel, military_modules.piercing_rounds(piercing, book, machine),
                military_modules.military_science(rate, book, machine), labs]
    if goal == "rocket-part":
        r = ROCKET_RATE
        circuits = mods.green_circuits(22 * r, book, machine=machine)   # 20 per processing unit, 2 per engine
        circuits.name = "green-circuits-3"
        red = blue_modules.red_circuits(2 * r, book, machine)
        red.name = "red-circuits-2"
        steel = blue_modules.steel(0.5, book)                          # the silo and foundations take 2200
        steel.name = "steel-2"
        engines = blue_modules.engines(0.075, book, machine)
        engines.name = "engines-2"
        return [circuits, red, steel, engines, rocket_modules.processing_units(r, book, machine),
                rocket_modules.low_density_structures(r, book, machine),
                rocket_modules.electric_engines(0.075, book, machine), rocket_modules.concrete(0.75, book, machine),
                rocket_modules.foundations(r, book, machine), rocket_modules.silo(r)]
    if goal != "chemical-science-pack":
        return []
    rate = science_per_minute / 60
    gears = mods.gears(rate, book, machine=machine)
    gears.name = "gears-2"
    circuits = mods.green_circuits(3 * rate, book, machine=machine)   # two per red circuit, 1.5 per pack
    circuits.name = "green-circuits-2"
    labs = mods.labs(max(2, math.ceil(science_per_minute * 30 / 60)),   # every pack so far
                     packs or ("automation-science-pack", "logistic-science-pack", "chemical-science-pack"))
    labs.name = "labs-blue"
    steel = [] if steel_direct else [blue_modules.steel(rate, book)]   # direct: a side lane brings it
    more_gears = [] if gears_on_site else [gears]   # only engines take more, and those make their own
    return [*more_gears, circuits, *steel, blue_modules.red_circuits(1.5 * rate, book, machine),
            blue_modules.engines(rate, book, machine, gears_on_site=gears_on_site),
            blue_modules.blue_science(rate, book, machine), labs]


def plan_bus_base(dump: Mapping[str, Any], book: Book, *, science_per_minute: float = 15.0,
                  packs: tuple[str, ...] = ("automation-science-pack",), columns: int = 8,
                  machine: str = mods.ASSEMBLER, origin: tuple[int, int] | None = None,
                  unlocked: Iterable[str] | None = None, library=None, traits: Iterable[str] = (),
                  chosen: Mapping[str, str] | None = None, ahead: bool | None = None,
                  extra: tuple[str, ...] = (), base_labs: bool = True, style: Style | None = None,
                  fit_index: int = 0, sites: Mapping[str, str] | None = None,
                  power_rows: Iterable[Mapping[str, Any]] = (), tight: bool = False,
                  steel_direct: bool | None = None, loader_above: float | None = None,
                  area: tuple | None = None, defense: Mapping[str, Any] | None = None,
                  fit_only: bool = False, feeds: Mapping[str, tuple] | None = None,
                  fed: Mapping[str, bool] | Iterable[str] = (), aim: float | None = None,
                  avoid: tuple[tuple[int, int], ...] = ()) -> Layout:
    """`origin` is the bus head of a base already being built (its smelting sits above it). `ahead`:
    lay smelting out as full-belt blocks, for the next science tier too (None: if it fits, else for
    today's goal, else `tight`: the compact layout, one belt per plate). `loader_above`: a lane the
    engineer fills from a chest that needs more items/s than this gets a loader instead of an inserter
    (modules.loader_limit; None: no loaders). `area`: world box a new bus must fit in (the factory plan's
    ground for it), searched from its middle. `fit_only`: only whether the bus's buildings fit somewhere (a
    ValueError if not), not where its fields, belts and power go: a Layout with no stages. `feeds`: the base's own
    electric smelting by plate, {plate: (store port, items/s)} (plate_feed.sources): a new bus takes those plates
    first (FEED_SHARE), its own smelting later; `fed`: the plates a bus being built takes so (its feed-<plate>
    stages), each with whether that stage is done. `aim`: the science a minute the bus aims at, which each growth section adds (default its own rate: a
    first bus may be built for part of it, bus_planner.FIRST_SHARE). `avoid`: bus heads already weighed (a new
    bus: another place than those, find_fit)."""
    if ahead is None:
        kwargs = dict(science_per_minute=science_per_minute, packs=packs, columns=columns, machine=machine,
                      origin=origin, unlocked=unlocked, library=library, traits=traits, chosen=chosen, extra=extra,
                      base_labs=base_labs, style=style, sites=sites, power_rows=power_rows,
                      steel_direct=steel_direct, loader_above=loader_above, area=area, defense=defense,
                      fit_only=fit_only, feeds=feeds, fed=fed, aim=aim, avoid=avoid)
        try:
            return plan_bus_base(dump, book, ahead=True, **kwargs)
        except ValueError:
            try:
                return plan_bus_base(dump, book, ahead=False, **kwargs)
            except ValueError:
                try:
                    return plan_bus_base(dump, book, ahead=False, tight=True, **kwargs)
                except ValueError:
                    # Gears made on site take room (their own assemblers): where the bus does not fit with
                    # them, it puts them on the bus, and keeps it so. Once: with their place settled either
                    # way, a bus that still does not fit does not (retried without end, it held its planner
                    # past four minutes, one-engineer run 2026-10-25).
                    if (style or Style()).gears_on_site and (sites or {}).get("gears") not in ("site", "bus"):
                        return plan_bus_base(dump, book, **dict(kwargs, sites={**(sites or {}), "gears": "bus"}))
                    raise
    style = style or Style()
    if (sites or {}).get("gears") in ("site", "bus"):   # the choice this bus was planned with
        style = replace(style, gears_on_site=sites["gears"] == "site")
    # The call as made (the names `ahead` and `extra` are reused below), for trying the next spot.
    again = dict(science_per_minute=science_per_minute, packs=packs, columns=columns, machine=machine,
                 origin=origin, unlocked=unlocked, library=library, traits=traits, chosen=chosen, ahead=ahead,
                 extra=tuple(extra), base_labs=base_labs, style=style, sites=sites, power_rows=power_rows,
                 tight=tight, steel_direct=steel_direct, loader_above=loader_above, area=area, defense=defense,
                 feeds=feeds, fed=fed, aim=aim, avoid=avoid)
    asked_columns = columns
    home = (int(dump["home"]["x"]), int(dump["home"]["y"]))
    blocked = (blocked_tiles(dump) - replaceable(dump)) | team_area.forbidden(dump)
    ore = all_ore(dump)
    notes = []
    # 1. What the goal needs.
    targets = {p: science_per_minute / 60 for p in packs}
    need = solve(targets, book, inputs=set(PLATES), allowed={machine})
    designs: dict[str, str] = {}
    # Science first (mall.OWN): the bus's mall after its labs, those of the last pack section planned with it
    # where its labs came with one (blue from the start: the sections before the marker), else its own; later
    # extensions go after the marker and never move it.
    own_at = next((k for k, g in enumerate(extra) if malls.is_own(g)), None)
    mall_host = None if own_at is None or "logistic-science-pack" not in packs else \
        next((g for g in reversed(tuple(extra)[:own_at]) if g in SCIENCE_EXTENSIONS), None)
    built = modules_for(packs, science_per_minute, book, machine, unlocked, library=library, traits=traits,
                        chosen=chosen, designs=designs,
                        labs=base_labs or not [g for g in extra if not malls.is_own(g)], mall_early=style.mall_early,
                        gears_on_site=style.gears_on_site, mall_last=own_at is not None and mall_host is None,
                        with_mall=mall_host is None)
    plates = {p: need.inputs.get(p, 0.0) for p in PLATES if need.inputs.get(p, 0.0) > 0}
    # (the science block's smelting is laid out for what it takes flat out, its furnaces grown in place toward that as
    # the lanes run short; its taps ask only the bus's rate of it meanwhile, U-2)
    for m in built:
        if m.name == science_blocks.BUS_BLOCK and m.makes:
            made, asked = min(m.makes.values()), science_per_minute / 60
            scale = made / asked if 0 < asked < made else 1.0
            draw = {p.item: p.rate * scale for p in m.ports if p.kind == "in" and p.item in PLATES}
            for item, r in draw.items():
                plates[item] = max(plates.get(item, 0.0), r)
            notes.append("science block takes " + ", ".join(f"{r:.1f}/s {i}" for i, r in sorted(draw.items()))
                         + f" flat out ({made * 60:.0f} of each pack a minute)")
    # Thinking ahead: smelting is laid out for the next science tier as well, its space kept and
    # its belts built now; the extra furnaces and drills come in a later stage.
    later = next((t for t in TIERS if t not in packs), None) if ahead else None
    full = bool(ahead)   # smelting laid out as full-belt blocks (on a crowded map: today's need only)
    ahead = dict(plates)
    if later:
        try:
            more = solve({p: science_per_minute / 60 for p in (*packs, later)}, book, inputs=set(PLATES),
                         allowed={machine, "chemical-plant", "oil-refinery", "stone-furnace", "steel-furnace"})
            ahead = {p: max(plates.get(p, 0.0), more.inputs.get(p, 0.0)) for p in PLATES
                     if max(plates.get(p, 0.0), more.inputs.get(p, 0.0)) > 0}
        except (KeyError, ValueError):
            pass
    # 2. Smelting blocks above the bus head, then the bus with its modules below. A block is two furnace
    # columns sharing one plate belt: 48 stone furnaces fill it (15/s), as players aim for full belts.
    # It is laid out full, and its furnaces come as they are needed: today's (with the style's spare)
    # first, the rest once the bus runs. Iron's block stands west and copper's east, apart, each growing
    # away from the other.
    # (its first stage thin: a few furnaces, one lane used at once, the rest of the block after as the lane ledger
    # shows it short; copper's whole first stage took 9.5 minutes and held the first lane to minute 54, F-21)
    furnaces_now = {p: min(BLOCK_FURNACES, FIRST_FURNACES, furnaces_for(rate * style.spare)) for p, rate in plates.items()}
    for p, rate in plates.items():
        if furnaces_for(rate * style.spare) > BLOCK_FURNACES:
            notes.append(f"{p} needs {furnaces_for(rate * style.spare)} furnaces; one block feeds {BLOCK_FURNACES}")
    if full:
        furnaces = {p: BLOCK_FURNACES for p in plates}
    else:
        furnaces = {p: min(BLOCK_FURNACES, max(furnaces_now.get(p, 0), furnaces_for(ahead.get(p, 0.0) * style.spare)))
                    for p in plates}
    if tight:   # one column per plate, as before blocks: no room for more
        furnaces = {p: min(n, LANE_FURNACES) for p, n in furnaces.items()}
        furnaces_now = {p: min(n, LANE_FURNACES) for p, n in furnaces_now.items()}
    head_height = 2 * max(min(n, LANE_FURNACES) for n in furnaces.values()) + len(plates) + 4 + COAL_ROWS
    belts = {p: 1 for p in plates} if tight else plate_belts(traits)
    columns = columns + sum(belts.get(p, 1) - 1 for p in plates)

    found = dict(sites or {})   # blocks already placed keep their spot; new ones are added here
    found["gears"] = "site" if style.gears_on_site else "bus"
    # Blue's steel: by direct insertion from a steel block at an iron field, as the guides make it,
    # unless this bus went with a steel module (no block fitted, or it was planned before).
    if steel_direct is None:
        steel_direct = found.get("steel") != "module"
    if "chemical-science-pack" in extra:
        found["steel"] = "direct" if steel_direct else "module"
    sections = []
    science = ["automation-science-pack", "logistic-science-pack", *packs]   # each section's labs take every pack so far
    if designs.get(science_blocks.BUS_BLOCK):   # (the science block's one lane carries red and green both, U-2)
        science = [p for p in science if p != "logistic-science-pack"]
    for goal in ordered_goals(extra):
        if goal in SCIENCE_EXTENSIONS:
            science.append(goal)
        rate = float(aim or science_per_minute) if growth.is_growth(goal) else science_per_minute
        sections.append((goal, section_for(goal, rate, book, machine, steel_direct=steel_direct,
                                           gears_on_site=style.gears_on_site,
                                           packs=tuple(dict.fromkeys(science)))))
        if goal == mall_host:
            sections[-1][1].extend(bus_malls(book, unlocked))
    ext = [m for _, mods_ in sections for m in mods_]
    side_items = [i for p in extra for i in side_lanes(p, steel_direct)]
    section_rows: dict[str, int] = {}

    def side(bus: Bus) -> str:
        if bus.next_west_y == bus.next_module_y:
            return style.first_side
        return "west" if bus.next_west_y < bus.next_module_y else "east"

    def place(bus: Bus) -> int | None:
        """Modules on both sides, each on whichever side is shorter so far; an extension below the
        finished bus, its new lanes fed from outside. Returns the extension's first row."""
        for plate in plates:
            bus.add_lane(plate, belts=belts.get(plate, 1), supply=furnaces[plate] * FURNACE_PLATES)
        for module in built:
            bus.attach(module, side=side(bus))
        if not ext:
            return None
        first = None
        for goal, section in sections:
            row0 = max(bus.next_module_y, bus.next_west_y)
            bus.next_module_y = bus.next_west_y = row0 + 2
            section_rows[goal] = row0
            first = row0 if first is None else first
            for item in side_lanes(goal, steel_direct):
                bus.add_lane(item, start=row0, source="side")
            for module in section:
                bus.attach(module, side=side(bus))
        return first

    # A dry run measures the bus: how far west and east its modules reach and how long it is.
    probe = Bus(0, 0, columns, 100_000)
    place(probe)
    west = max((probe.x - a.x for a in probe.attached if a.side == "west"), default=0)
    east = max((a.x + a.module.width for a in probe.attached if a.side == "east"), default=probe.east)
    bus_rows = max((a.y + a.module.height for a in probe.attached), default=0) + 6
    bus_width = west + east + 2
    probe.length = bus_rows
    # Where each plate's smelting block goes, relative to the bus head: side by side above the lanes,
    # BLOCK_GAP apart, so iron grows west and copper east, away from each other.
    block_dx = {p: (7 * k + 1) if tight else k * (BLOCK_W + BLOCK_GAP) for k, p in enumerate(plates)}
    head_x0 = min(block_dx.values(), default=0) - 1
    head_x1 = max(block_dx.values(), default=0) + BLOCK_W + 1
    if origin is None:
        # Where the buildings go: the smelting head above lane 0, then the bus and its modules.
        shape = footprint(probe.entities())
        shape |= {(x, y) for x in range(head_x0, head_x1) for y in range(-head_height, 0)}
        # The style leans the search toward its favourite direction, further out if it likes room.
        reach = min(dump["area"][2] - home[0], home[0] - dump["area"][0], dump["area"][3] - home[1],
                    home[1] - dump["area"][1]) * 0.6
        anchor = (home[0] + math.cos(style.heading) * style.spread * reach,
                  home[1] + math.sin(style.heading) * style.spread * reach)
        ores = ore_anchor(dump, home)
        if ores:   # by the ore, the style leaning it a little
            anchor = (0.8 * ores[0] + 0.2 * anchor[0], 0.8 * ores[1] + 0.2 * anchor[1])
        if area:   # the ground the factory plan keeps for it
            anchor = ((area[0] + area[2]) / 2, (area[1] + area[3]) / 2)
        fit = find_fit(blocked | ore, shape, home, tuple(int(v) for v in area or dump["area"]), anchor=anchor,
                       skip=fit_index * 4, avoid=avoid,
                       anchor_at=((head_x0 + head_x1) / 2, -head_height / 2))
        if not fit:
            raise ValueError("no room for a bus near home")
        bx, sy = fit[0], fit[1] - head_height
        sx = bx - west
    else:
        bx, sy = origin[0], origin[1] - head_height
        sx = bx - west
    bus = Bus(bx, sy + head_height, columns, bus_rows)
    if fit_only:
        return Layout(bus, [], notes, designs, full, found, None, tight, asked_columns)
    stages: list[Stage] = []
    # The bus, its modules and room to extend it later are kept clear of ore and coal belts:
    # each stage is built as its own plan, and a later one would pick up a belt in its way.
    reserved = {(x, y) for x in range(sx - BUS_MARGIN, sx + bus_width + BUS_MARGIN)
                for y in range(bus.y - 1, bus.y + bus_rows + BUS_GROWTH)}
    # Every column, its plate belt to the bus and its head's entry tiles are reserved before any
    # ore or coal belt is routed, so no route runs through a column planned after it.
    blocks = []
    paths: dict[str, list[Placed]] = {}   # plate -> its plate belt from the block bottom down into its lanes
    ext_row = place(bus)
    for k, (plate, rate) in enumerate(plates.items()):
        column = bus.lane_of(plate)
        n = furnaces[plate]
        left_n, right_n = min(n, LANE_FURNACES), max(0, n - LANE_FURNACES)
        cx, cy = bx + block_dx[plate], sy + 1 + COAL_ROWS   # block top-left; the shared plate belt is at cx + 5
        cols = []
        for side, count in (("west", left_n), ("east", right_n)):
            if count <= 0:
                continue
            col = smelting_column(PLATES[plate], plate, count, mirrored=side == "east")
            entities = [Placed(e.name, e.x + cx, e.y + cy, e.direction, e.recipe) for e in col.entities]
            head_x = cx if side == "west" else cx + 10
            entities.append(Placed("transport-belt", head_x + 0.5, cy - 0.5, SOUTH))   # the column head
            if side == "west":
                # The plate belt leaves the block bottom, turns toward its lanes at its own row (the first
                # plate highest) and runs down into the head of its group.
                out_x, out_y = cx + 5, cy + col.height
                turn_row = bus.y - 1 - (len(plates) - k)
                lane_x = bus.lane_x(column)
                path = paths.setdefault(plate, [])
                for row in range(out_y, turn_row):
                    path.append(Placed("transport-belt", out_x + 0.5, row + 0.5, SOUTH))
                step = WEST if lane_x < out_x else EAST
                lo, hi = sorted((lane_x, out_x))
                for x in range(lo, hi + 1):
                    path.append(Placed("transport-belt", x + 0.5, turn_row + 0.5, step if x != lane_x else SOUTH))
                for row in range(turn_row + 1, bus.y):
                    path.append(Placed("transport-belt", lane_x + 0.5, row + 0.5, SOUTH))
                entities.extend(path)
            cols.append((side, count, col, entities))
        # Feeds, as players feed a smelting array: one ore belt and one coal belt for the block. With two
        # columns both are split at the head (the distributor): ore onto each head's outer lane, coal onto
        # the inner one. With one column they go straight into its head. Short corridors above the
        # entries stay clear, so routes planned first may hop them but never run along them.
        if right_n:
            feed = _distributor(cx, cy)
            ore_entry, ore_last, coal_entry, coal_last = (cx - 1, cy - 5), SOUTH, (cx + 5, cy - 4), SOUTH
            corridors = (_coal_corridor(cx, cy), {(cx - 1, cy - j) for j in range(5, 8)})   # (coal, ore)
        else:
            feed = []
            ore_entry, ore_last, coal_entry, coal_last = (cx - 1, cy - 1), EAST, (cx + 1, cy - 1), WEST
            corridors = (set(), set())
        column = [e for _, _, _, ents in cols for e in ents] + list(feed)
        reserved |= footprint(column) | outlets(column) | corridors[0] | corridors[1] | {ore_entry, coal_entry}
        blocks.append((plate, cx, cy, cols, feed, ore_entry, ore_last, coal_entry, coal_last, corridors))
    extra_stages: list[Stage] = []
    # The bus is planned again for every stage: a block's ore route and coal line, once built, stand on
    # the map, where they would block a new search for themselves. A belt of the engineer's own at the
    # block's entry means that line is built: it is kept, not searched for (or fed by hand) again.
    own_belts = {(math.floor(e["x"]), math.floor(e["y"])) for e in dump.get("entities", [])
                 if e.get("f") == dump.get("force") and e.get("t") in ("transport-belt", "underground-belt")}
    smelting: list[Stage] = []
    for plate, cx, cy, cols, feed, ore_entry, ore_last, coal_entry, coal_last, corridors in blocks:
        now_n = furnaces_now.get(plate, furnaces[plate])
        total = sum(c[1] for c in cols)
        built_n = min(now_n, total)
        # One mining field for the whole block, laid out for all its furnaces; the drills needed now first.
        # Where it was once planned it stays (the bus is planned again for every stage, and its own drills
        # now stand on that ground): its later drills extend it.
        drills_now = max(2, math.ceil(built_n * FURNACE_PLATES / DRILL_RATE))
        drills = max(drills_now, math.ceil(total * FURNACE_PLATES / DRILL_RATE))
        key = f"field-{plate}"
        fld = None
        if found.get(key):
            try:
                fx, fy, fn, fl = (int(v) for v in (str(found[key]).split(",") + ["1"])[:4])
                fld = field_at(PLATES[plate], fx, fy, fn, lines=fl)
            except ValueError:
                fld = None
        if fld is None:
            ore_here = ore_tiles(dump["resources"], PLATES[plate])
            other = foreign_tiles(dump["resources"], PLATES[plate])
            # The biggest field near the block: all the drills, else two thirds, a half, a third (the
            # furnaces built last then wait for more ore), in one line or two side by side (a line of 30
            # is 55 tiles long); what is needed now anywhere it fits.
            sizes = sorted({n for n in (drills, math.ceil(drills * 2 / 3), math.ceil(drills / 2),
                                        math.ceil(drills / 3)) if n > drills_now}, reverse=True)
            for count, lines in [(c, ln) for c in sizes for ln in ((1, 2) if c > 6 else (1,))] + [(drills_now, 1)]:
                fld = plan_field(ore_here, blocked | reserved, count, PLATES[plate], near=ore_entry, foreign=other,
                                 lines=lines)
                far = fld and abs(fld.exit[0] - ore_entry[0]) + abs(fld.exit[1] - ore_entry[1]) > FIELD_REACH
                if fld and (count == drills_now or not far):
                    break
                fld = None
            if fld:
                found[key] = f"{fld.belt_x},{fld.top},{len(fld.drills)},{fld.lines}"
        path = None
        ore_built, coal_built = ore_entry in own_belts, coal_entry in own_belts
        # Ground the factory plan keeps for later sections holds back buildings; a belt may cross it when nothing
        # else gets through (two kept zones lay across the whole way from the bus's ore to its furnaces, and its
        # smelting went without ore or coal, run 31, 2026-10-25).
        kept = land.reserved_tiles(dump)
        if kept:   # (only what the zones alone hold back: water and buildings in them stay in the way)
            kept = kept - blocked_tiles({**dump, "reserved": None})
        if fld:
            reserved |= footprint(fld.entities())
            if not ore_built:
                # (its corridor is kept clear of the bus's own routes only: what stands there is still in the way, a
                # route laid over a standing belt row joins it and mixes the two, green-from-zero U-2)
                path = route(fld.exit, ore_entry, (blocked | (reserved - corridors[1])) - {ore_entry},
                             bounds=tuple(dump["area"]))
                if path is None and kept:
                    path = route(fld.exit, ore_entry, ((blocked - kept) | (reserved - corridors[1])) - {ore_entry},
                                 bounds=tuple(dump["area"]))
                    if path is not None:
                        notes.append(f"the {plate} ore belt crosses ground kept for later sections")
            if path is not None:
                path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, ore_last)
                reserved |= footprint(path) | outlets(path)
        coal = None if coal_built else _coal_line(dump, blocked, reserved - corridors[0], coal_entry, total,
                                                  last=coal_last)
        if coal is None and not coal_built and kept:
            coal = _coal_line(dump, blocked - kept, reserved - corridors[0], coal_entry, total, last=coal_last)
            if coal:
                notes.append(f"the coal belt for the {plate} furnaces crosses ground kept for later sections")
        if coal:
            reserved |= footprint(coal) | outlets(coal)
            short = coal_short(coal, total * COAL_PER_FURNACE)
            if short:
                notes.append(f"coal for the {plate} furnaces falls {short:.2f}/s short: no more coal drills fit")
        elif not coal_built:
            notes.append(f"coal for the {plate} furnaces is fed by hand: no coal field and route fit")
        stage_now: list[Placed] = list(feed) + list(coal or [])
        stage_more: list[Placed] = []
        fuel_now: list[dict] = []
        fuel_more: list[dict] = []
        if not fld:
            notes.append(f"no room for {drills_now} drills on {PLATES[plate]}")
        elif path is None and not ore_built:
            notes.append(f"no belt route from the {PLATES[plate]} field to its furnaces")
        if fld:
            field_entities = fld.entities()
            drills_list, rest = field_entities[:len(fld.drills)], field_entities[len(fld.drills):]
            stage_now += drills_list[:drills_now] + rest + (path or [])
            stage_more += drills_list[drills_now:]
        # (no more furnaces than its field's drills feed: a field that fit only for the first stage left 43 furnaces
        # of the rest without ore, F-21; beyond that the plate grows by a resource expansion)
        fed_n = math.floor(len(fld.drills) * DRILL_RATE / FURNACE_PLATES + 1e-9) if fld else total
        if fld and fed_n < min(now_n, total):   # (its first stage itself: the rest is grown by an expansion, pressed())
            notes.append(f"{plate}: its field feeds {fed_n} of the {min(now_n, total)} furnaces it needs now")
        for side, count, col, entities in cols:
            built = min(count, now_n) if side == "west" else max(0, now_n - LANE_FURNACES)
            ore_fed = min(count, fed_n) if side == "west" else max(0, fed_n - LANE_FURNACES)
            # Furnaces (and their inserters) beyond what is needed now wait for the later stage; a column
            # with none needed now waits whole. Those beyond what its field feeds are not planned at all.
            cut = cy + 2 * built
            end = cy + 2 * ore_fed
            unfed = [e for e in entities if e.y >= end and (e.name.endswith("furnace") or e.name == "inserter")]
            later_part = [e for e in entities if e.y >= cut and e not in unfed
                          and (e.name.endswith("furnace") or e.name == "inserter")]
            if not built:
                later_part = [e for e in entities if e not in unfed]
            stage_now += [e for e in entities if e not in later_part and e not in unfed]
            stage_more += later_part
            if not coal and not coal_built:
                fuel = _fuel(col, cx, cy)
                fuel_now += [f for f in fuel if f["y"] < cut]
                fuel_more += [f for f in fuel if f["y"] >= cut]
        smelting.append(Stage(f"smelting-{plate}", stage_now, fuel_now))
        if stage_more:
            extra_stages.append(Stage(f"smelting-{plate}-more", stage_more, fuel_more))
    # A plate the base's own electric smelting makes enough of comes from there first: a feed stage (the plate belt
    # into its lanes and a belt to it from a splitter on that smelting's store belt) where its smelting stage was,
    # and the bus's own smelting for it after the science modules (plate_feed).
    later: list[Stage] = []
    planned = [e for st in smelting for e in st.entities]
    taken = blocked | reserved | footprint(planned) | outlets(planned)
    fed = dict(fed) if isinstance(fed, Mapping) else {p: False for p in fed}
    for st in smelting:
        plate = st.name[len("smelting-"):]
        fed_by = _plate_feed(dump, plate, paths.get(plate) or [], (feeds or {}).get(plate), plates.get(plate, 0.0),
                             fed.get(plate), taken)
        if fed_by is None:
            stages.append(st)
            continue
        branch, lost = fed_by
        own = {id(e) for e in paths.get(plate) or []}
        rest = Stage(st.name, [e for e in st.entities if id(e) not in own], st.supplies)
        stages.append(Stage(f"feed-{plate}", list(paths.get(plate) or []) + branch, proof="built" if lost else ""))
        taken |= footprint(branch) | outlets(branch)
        # (a bus being built whose source is gone: its own smelting at once, not after the science)
        (stages if lost else later).append(rest)
    for name, part in bus.segments():
        stages.append(Stage(f"bus-{name}", part))
    stages.extend(later)          # its own smelting, where the base's fed it first
    stages.extend(extra_stages)   # more smelting once the bus runs, ready for the next tier
    if "chemical-science-pack" in section_rows:
        try:
            _side_lanes(dump, bus, section_rows["chemical-science-pack"],
                        list(side_lanes("chemical-science-pack", steel_direct)), blocked, stages, notes,
                        science_per_minute, found, loader_above=loader_above)
        except NoDirectSteel:
            return plan_bus_base(dump, book, **dict(again, steel_direct=False))
    if "military-science-pack" in section_rows:
        _side_lanes(dump, bus, section_rows["military-science-pack"], list(side_lanes("military-science-pack")),
                    blocked, stages, notes, science_per_minute, found, section="military-science-pack",
                    loader_above=loader_above)
    if "rocket-part" in section_rows:
        def ore_line(entry, rate, resource, used, last=SOUTH):
            return _coal_line(dump, blocked, used - blocked, entry, rate=rate, last=last, resource=resource)
        rocket_plan.plan_rocket(dump, bus, stages, notes, blocked, blocked | ore | reserved,
                                section_rows["rocket-part"], Stage, ore_line, found)
    for goal in section_rows:
        if growth.is_growth(goal) or growth.is_expansion(goal):
            def ore_line(entry, rate, resource, used, last=SOUTH):
                return _coal_line(dump, blocked, used - blocked, entry, rate=rate, last=last, resource=resource)
            if growth.is_growth(goal):
                growth.plan_supply(dump, bus, stages, notes, blocked, blocked | ore | reserved,
                                   growth.parse(goal)[0], Stage, ore_line, found=found)
            else:
                plate, index, _ = growth.parse_expansion(goal)
                growth.plan_supply(dump, bus, stages, notes, blocked, blocked | ore | reserved, index, Stage,
                                   ore_line, tag=growth.expansion_tag(goal), label=f"the {plate} expansion {index}",
                                   found=found)
    # Outposts: resource expansions by an ore patch, off the bus (companion/outpost.py), after everything on it.
    for goal in extra:
        if outpost.is_outpost(goal):
            def ore_line(entry, rate, resource, used, last=SOUTH):
                return _coal_line(dump, blocked, used - blocked, entry, rate=rate, last=last, resource=resource)
            planned = [e for s in stages for e in s.entities]
            built_now = blocked | footprint(planned) | outlets(planned)
            more, why = outpost.plan(dump, goal, built_now | ore | reserved, blocked, found, ore_line, Stage,
                                     built=built_now, kit=defense)
            stages.extend(more)
            notes.extend(why)
    # 3. Power, planned like the plates: what the base and this bus draw at full work against what
    # the engineer's engines make. A plant by the nearest shore covers the rest, built first.
    # (what already stands is the dump's own draw, counted once: the same plant and place on every replay, F-18)
    standing = {(e.get("n"), round(float(e.get("x", 0)) * 2), round(float(e.get("y", 0)) * 2))
                for e in dump.get("entities", []) if e.get("f") == dump.get("force")}
    to_add = [[e.name for e in s.entities if (e.name, round(e.x * 2), round(e.y * 2)) not in standing]
              for s in stages]
    boilers = power_plant.boilers_needed(dump, (n for names in to_add for n in names))
    power_row = None
    if boilers:
        taken = blocked | ore | reserved | footprint(e for s in stages for e in s.entities)
        # More boilers on the engineer's own row where they fit (one pump feeds a long row), as a
        # player grows power; a new row by the nearest shore otherwise.
        # Or a new row by the coal with the water piped to it from the end of the old one, when that
        # is the smaller job (a long coal belt is a lot of building; an underground pipe spans ten): by what each
        # takes to build and how far out it stands (build_cost).
        built_so_far = footprint(e for s in stages for e in s.entities)
        options = []
        for plant in (power_plant.plan_row(dump, boilers, taken, near=(bx, bus.y), rows=power_rows),
                      power_plant.plan_remote_row(dump, boilers, taken, power_rows, near=(bx, bus.y))):
            if plant is None:
                continue
            entities, coal_in, coal_dir, row, had_belt = plant
            # Belts and coal drills may stand on ore (the plant's buildings may not).
            used = reserved | built_so_far | {t for e in entities for t in power_plant.tiles(e)}
            coal = []
            if not had_belt:   # a row that has its coal line only gets a longer belt
                coal = _coal_line(dump, blocked, used, coal_in, rate=row["boilers"] * power_plant.COAL_PER_BOILER,
                                  last=coal_dir)
            options.append((build_cost([*entities, *(coal or ())], book, home) + (10 ** 6 if coal is None else 0),
                            plant, coal))
        if not options:
            notes.append(f"no shore with room for a {boilers}-boiler power plant: the base will run short of power")
        else:
            _, plant, coal = min(options, key=lambda o: o[0])
            entities, coal_in, coal_dir, power_row, had_belt = plant
            short = coal_short(coal, power_row["boilers"] * power_plant.COAL_PER_BOILER) if coal and not had_belt else 0
            if short:
                notes.append(f"coal for the power plant falls {short:.2f}/s short: no more coal drills fit")
            if coal is None:
                notes.append("no coal route to the power plant: its boilers are fed by hand")
                entities = [e for e in entities if e.name not in ("transport-belt", "burner-inserter")]
                power_row = {**power_row, "belt": False}
                coal = []
            fuel = [{"x": e.x, "y": e.y, "item": "coal", "slot": "fuel"} for e in entities if e.name == "boiler"]
            # (where the network first runs short of what is built so far: the stages before it run on the base's
            # own power; not always first, F-18: a bus whose first lane needs no more power starts on its lane)
            at = next((k for k in range(len(stages))
                       if power_plant.boilers_needed(dump, (n for names in to_add[:k + 1] for n in names))), 0)
            stages.insert(at, Stage("power", entities + coal, fuel))
    # Power: each stage, in build order, gets the poles that join it to the network so far.
    # Only poles on a network a generator feeds count: a pole group never joined to the base is no use.
    live = set(dump.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in dump.get("entities", [])
               if e.get("t") == "electric-pole" and e.get("f") == dump.get("force")
               and (not live or e.get("net") is None or e.get("net") in live)]
    taken = blocked | footprint(e for s in stages for e in s.entities)
    for stage in stages:
        extra, problems = connect(stage.entities, powered, taken)
        stage.entities.extend(extra)
        notes.extend(f"{stage.name}: {p}" for p in problems)
        powered.extend((e.x, e.y) for e in stage.entities if e.name == "small-electric-pole")
    # A spot where the ore cannot reach its furnaces is no good: try the next places that fit.
    broken = [n for n in notes if n.startswith(("no room for", "no belt route from"))]
    if broken and origin is None:
        retry = None
        if fit_index < MAX_FIT_TRIES:
            retry = dict(again, fit_index=fit_index + 1)
        elif style.spread > 0:   # its favourite direction did not work out: closer to home, then
            retry = dict(again, fit_index=0, style=replace(style, spread=0.0))
        if retry:
            try:
                return plan_bus_base(dump, book, **retry)
            except ValueError:
                pass
    return Layout(bus, stages, notes, designs, full, found, power_row, tight, asked_columns)


def _plate_feed(dump: Mapping[str, Any], plate: str, path: list[Placed], source: tuple | None, need: float,
                kept: bool | None, taken: set) -> tuple[list[Placed], bool] | None:
    """A feed for `plate` from the base's own smelting into its plate belt `path` (plate_feed.plan): (its belts, none
    where they stand already; whether it is lost), or None for no feed. A new bus (`kept` None) takes it where
    FEED_SHARE of what that smelting makes covers what the bus draws (`need`, items/s). A bus being built keeps the
    feeds it was planned with (`kept`: whether its feed stage is done): planned again until its belt stands
    (splitters were researched when it was planned), and lost when its source is gone (standing or not), when no way
    is found, or when its stage was done without it (its stage is then the plate belt alone, its own smelting next)."""
    start = (math.floor(path[0].x), math.floor(path[0].y)) if path and path[0].direction == SOUTH else None
    if kept is not None and (start is None or source is None):
        return [], True
    if start is None:
        return None
    if kept is not None and plate_feed.built(dump, start):
        return [], False
    if kept:   # (done without its belt: the source came back after; the stage is not built again)
        return [], True
    if kept is None and (source is None or source[1] * FEED_SHARE < need):
        return None
    if kept is not None:
        with belt_lines.taps(True):
            branch = plate_feed.plan(dump, plate, source[0], source[1], start, taken - {start})
    else:
        branch = plate_feed.plan(dump, plate, source[0], source[1], start, taken - {start})
    if branch is None:
        return ([], True) if kept is not None else None
    return branch, False


MAX_COAL_FIELDS = 4


def _coal_line(dump: Mapping[str, Any], blocked: set, reserved: set, entry: tuple[int, int],
               furnaces: int = 0, *, rate: float | None = None, last: int = WEST,
               resource: str = "coal") -> list[Placed] | None:
    """Coal fields making `rate` coal/s (default: enough for `furnaces` stone furnaces) and their belt
    into `entry`; the last belt points `last` (for a column head: west, so the coal sideloads onto
    the head's east lane). On small or crowded patches the biggest field that fits feeds the trunk
    belt and more fields sideload onto straight stretches of it, as players merge coal; whatever is
    still missing is fed by hand."""
    rate = furnaces * COAL_PER_FURNACE if rate is None else rate
    from companion import belt_lines
    if resource == "coal":
        # A branch off a coal line the base has with the spare for it (belt_lines: not a field and a belt each, the
        # user, 2026-10-25), else a new line from a field of its own, sized with room for the next consumers.
        branch = belt_lines.tap(dump, "coal", entry, rate, set(blocked) | set(reserved), last=last)
        if branch:
            return branch
    # (room for the next consumers only where they can tap it: splitters researched)
    sized = belt_lines.trunk_rate(rate) if resource == "coal" and belt_lines.tapping() else rate
    need = max(1, math.ceil(sized / DRILL_RATE))
    drills = 0
    coal = ore_tiles(dump["resources"], resource)
    other = foreign_tiles(dump["resources"], resource)
    bounds = tuple(dump["area"])
    taken = set(blocked | reserved)
    out: list[Placed] = []
    trunk: list[Placed] = []
    for _ in range(MAX_COAL_FIELDS):
        if need <= 0:
            break
        # Full spots first; then drills over the edges of small patches, as players place them.
        fld = next((f for min_ore in (15, 6) for n in range(need, 0, -1)
                    for f in [plan_field(coal, taken, n, resource, min_ore=min_ore, near=entry, foreign=other)]
                    if f), None)
        if not fld:
            break
        field_entities = fld.entities()
        here = taken | footprint(field_entities)
        if not trunk:
            path = route(fld.exit, entry, here - {entry}, bounds=bounds)
            if path is None:
                # no way from this field: the next nearest is tried (a field boxed in by what stands near the
                # entry left every expansion fed by hand, 2026-10-25)
                taken |= footprint(field_entities)
                continue
            path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, last)
            trunk = path
        else:
            path = _sideload(fld.exit, trunk, here, bounds)
            if path is None:
                taken |= footprint(field_entities)   # this field cannot join: leave its spot and try another
                continue
        out += field_entities + path
        taken = here | footprint(path) | outlets(path)
        need -= len(fld.drills)
        drills += len(fld.drills)
    if out and resource == "coal":   # (recorded by the game once it stands: the next consumers tap it)
        belt_lines.record_line(trunk, "coal", drills * DRILL_RATE, rate, entry)
    return out or None


def coal_short(line: list[Placed] | None, rate: float) -> float:
    """What a coal line from _coal_line falls short of `rate` coal/s, rounded to the hundredth: its own fields' drills
    make DRILL_RATE each (a branch off a standing line, no drills of its own, has the spare: belt_lines.tap). Fields
    that fit too few drills leave the rest to be fed by hand."""
    drills = sum(1 for e in line or () if e.name.endswith("mining-drill"))
    return round(max(0.0, rate - drills * DRILL_RATE), 2) if drills else 0.0


def _side_lanes(dump: Mapping[str, Any], bus: Bus, row0: int, items: list[str], blocked: set,
                stages: list[Stage], notes: list[str], science_per_minute: float,
                found: dict | None = None, section: str = "chemical-science-pack",
                loader_above: float | None = None) -> None:
    """Belts that start the extension's new lanes from outside the bus: coal from a coal field,
    plastic and sulfur from the engineer's chemical plants (an inserter lifts each product onto a
    belt). Each source is its own stage, built before the modules that need it. A lane started from a
    chest the engineer fills gets a loader instead of the inserter when its modules take more than
    `loader_above` items/s (what one inserter moves)."""
    first = next(i for i, s in enumerate(stages) if s.name.startswith("bus-") and s.name[4:] in
                 SECTION_MODULES[section])
    entities = [e for s in stages for e in s.entities]
    # An extension route beside an older module must not collect its belt overflow.
    taken = blocked | footprint(entities) | outlets(entities)
    rate = science_per_minute / 60
    # Every new lane's entry tile is kept for its own belt before any belt is routed.
    # (this section's own lane: an item may have one from an earlier section too, coal for blue and military)
    own = {item: next((c for c, lane in sorted(bus.lanes.items()) if lane.item == item and lane.start == row0),
                      bus.lane_of(item)) for item in items}
    entries = {item: (bus.lane_x(own[item]), row0 - 1) for item in items}
    taken |= set(entries.values())
    for item in items:
        entry = entries[item]
        supplies: list[dict] = []
        if item == "coal":
            line = _coal_line(dump, blocked, taken - blocked, entry, rate=0.1 + rate * 0.36, last=SOUTH)
        elif item == "steel-plate":   # one steel per blue pack (its engines)
            line = _steel_line(dump, blocked, taken, entry, rate, found if found is not None else {})
            if line is None:
                raise NoDirectSteel("no room for a steel block and its belts")
        else:
            line = _export_line(dump, item, entry, taken)
        if not line and item != "coal":
            # No machine of the engineer's makes it with room for an inserter and a belt to the bus: the
            # engineer carries it to a chest at the lane head, as a player would, until one does. A lane
            # nothing feeds would leave the modules that need it waiting for good. The chest goes above
            # the head, or in the gap beside it with an inserter loading the lane from the side; a lane
            # its modules take from faster than one inserter moves gets a loader, the chest a tile further.
            x, head = entry[0], entry[1] + 1
            need = bus.taken.get(item, 0.0)
            if loader_above is not None and need > loader_above:
                for chest_at, loader_at, faces in (((x, head - 3), (x + 0.5, head - 1.0), SOUTH),
                                                   ((x + 3, head), (x + 2.0, head + 0.5), WEST),
                                                   ((x - 3, head), (x - 1.0, head + 0.5), EAST)):
                    loader = Placed(LOADER, loader_at[0], loader_at[1], faces, kind="output")
                    if not (({chest_at} | footprint([loader])) & (taken - {entry})):
                        chest = Placed(END_CHEST, chest_at[0] + 0.5, chest_at[1] + 0.5, NORTH)
                        line = [chest, loader]
                        supplies = [{"x": chest.x, "y": chest.y, "item": item, "slot": "input"}]
                        notes.append(f"{item} is carried to the bus by hand, a loader unloading its chest "
                                     f"({need:.1f}/s, more than an inserter's {loader_above:.1f}/s)")
                        break
            for chest_at, ins_at, picks in (((x, head - 2), (x, head - 1), NORTH),
                                            ((x + 2, head), (x + 1, head), EAST),
                                            ((x - 2, head), (x - 1, head), WEST)):
                if line:
                    break
                if not ({chest_at, ins_at} & (taken - {entry})):
                    chest = Placed(END_CHEST, chest_at[0] + 0.5, chest_at[1] + 0.5, NORTH)
                    line = [chest, Placed("inserter", ins_at[0] + 0.5, ins_at[1] + 0.5, picks)]
                    supplies = [{"x": chest.x, "y": chest.y, "item": item, "slot": "input"}]
                    notes.append(f"{item} is carried to the bus by hand: no belt from a machine making it fits")
                    break
        if not line:
            notes.append(f"no belt brings {item} to the bus")
            continue
        taken |= footprint(line) | outlets(line)
        suffix = "" if section == "chemical-science-pack" else "-military"   # (both may bring coal)
        stages.insert(first, Stage(f"lane-{item}{suffix}", line, supplies))
        first += 1


STEEL_SPOTS = 6    # places beside the iron field tried for the steel block (each costs three route searches)


def _steel_line(dump: Mapping[str, Any], blocked: set, taken: set, entry: tuple[int, int], rate: float,
                found: dict) -> list[Placed] | None:
    """Steel for a side lane by direct insertion: a field on iron ore, a steel block beside it (iron
    furnaces handing plates straight to steel furnaces), a coal line into the block, and a belt from
    its steel output to `entry`. The block's spot is kept in `found`; once the lane is built (a belt of
    the engineer's own at its entry) the block is planned where it stands, not searched for again."""
    pairs = steel_pairs(rate)
    column = steel_column(pairs)

    def at(cx: int, cy: int) -> list[Placed]:
        return [Placed(e.name, e.x + cx, e.y + cy, e.direction, e.recipe, e.kind) for e in column.entities]

    saved = None
    try:
        saved = tuple(int(v) for v in str(found.get("steel-site", "")).split(",")[:2]) or None
    except ValueError:
        saved = None
    own = {(math.floor(e["x"]), math.floor(e["y"])) for e in dump.get("entities", [])
           if e.get("f") == dump.get("force") and e.get("t") in ("transport-belt", "underground-belt")}
    if saved and len(saved) == 2 and entry in own:
        return at(*saved)
    resources = dump.get("resources", {})
    drills = max(1, math.ceil(pairs * ORE_PER_PAIR / DRILL_RATE))
    fld = plan_field(ore_tiles(resources, "iron-ore"), taken, drills, "iron-ore", near=entry,
                     foreign=foreign_tiles(resources, "iron-ore"))
    if fld is None:
        return None
    used = taken | footprint(fld.entities()) | outlets(fld.entities())
    ex, ey = fld.exit
    spots = [saved] if saved and len(saved) == 2 else []
    spots += sorted(((ex + dx, ey + dy) for dx in range(-30, 31, 3) for dy in range(-30, 31, 3)),
                    key=lambda p: abs(p[0] - ex) + abs(p[1] - ey))
    bounds = tuple(dump["area"])
    tried = 0
    for cx, cy in spots:
        block = at(cx, cy)
        ore_in, coal_in, steel_out = (cx - 1, cy - 1), (cx + 9, cy - 3), (cx + 8, cy + column.height)
        box = footprint(block) | outlets(block) | {ore_in, coal_in, steel_out}
        if box & used:
            continue
        tried += 1
        if tried > STEEL_SPOTS:
            break
        here = used | box
        ore = route(fld.exit, ore_in, here - {ore_in}, bounds=bounds)
        if ore is None:
            continue
        ore[-1] = Placed("transport-belt", ore[-1].x, ore[-1].y, EAST)   # side-loads onto the block's belt A
        here |= footprint(ore) | outlets(ore)
        coal = _coal_line(dump, blocked, here - blocked, coal_in, rate=pairs * 0.045, last=SOUTH)
        if not coal:
            continue
        here |= footprint(coal) | outlets(coal)
        out = None
        for goal, last in _joins(entry, here):
            out = route(steel_out, goal, here - {steel_out, goal}, bounds=bounds)
            if out is not None:
                out[-1] = Placed("transport-belt", out[-1].x, out[-1].y, last)
                break
        if out is None:
            continue
        found["steel-site"] = f"{cx},{cy}"
        return fld.entities() + block + ore + coal + out
    return None


def _joins(entry: tuple[int, int], taken: set) -> list[tuple[tuple[int, int], int]]:
    """Where a belt can join a side lane that starts below `entry`: into the entry from above, or
    (lines routed before may have walled the entry in) onto the side of the lane's first belt from
    the gap beside it; only goals a belt can still reach (a free tile next to them)."""
    x, y = entry
    out = []
    for goal, last, back in (((x, y), SOUTH, (0, 1)), ((x + 1, y + 1), WEST, (-1, 0)), ((x - 1, y + 1), EAST, (1, 0))):
        if goal != entry and goal in taken:
            continue
        around = [(goal[0] + dx, goal[1] + dy) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)) if (dx, dy) != back]
        if any(t not in taken for t in around):
            out.append((goal, last))
    return out


def _export_line(dump: Mapping[str, Any], item: str, entry: tuple[int, int], taken: set) -> list[Placed] | None:
    """An inserter at one of the engineer's machines making `item`, a pole for it, and a belt from there
    to `entry` (the tile above a new lane), ending south into the lane."""
    sources = [e for e in dump.get("entities", []) if e.get("f") == dump.get("force") and e.get("r") == item]
    step = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
    for src in sorted(sources, key=lambda e: (e["x"] - entry[0]) ** 2 + (e["y"] - entry[1]) ** 2):
        half = src.get("w", 3) / 2
        for side, (dx, dy) in step.items():
            # Inserter on the middle of this side, picking from the machine and dropping outward.
            ix, iy = math.floor(src["x"] + dx * (half + 0.5)), math.floor(src["y"] + dy * (half + 0.5))
            bx, by = ix + dx, iy + dy
            px, py = ix + dy, iy + dx   # the pole beside the inserter
            if {(ix, iy), (bx, by), (px, py)} & taken:
                continue
            path = None
            for goal, last in _joins(entry, taken | {(ix, iy), (px, py), (bx, by)}):
                path = route((bx, by), goal, (taken | {(ix, iy), (px, py)}) - {goal},
                             bounds=tuple(dump["area"]), limit=600)
                if path is not None:
                    path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, last)
                    break
            if path is None:
                continue
            picks = {NORTH: SOUTH, SOUTH: NORTH, EAST: WEST, WEST: EAST}[side]   # it picks from the machine's side
            return [Placed("inserter", ix + 0.5, iy + 0.5, picks), Placed("small-electric-pole", px + 0.5, py + 0.5)] \
                + path
    return None


def _sideload(start: tuple[int, int], trunk: list[Placed], taken: set, bounds) -> list[Placed] | None:
    """A belt from `start` into the side of a straight stretch of `trunk` (nearest first)."""
    step = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    straight = []
    for i in range(1, len(trunk) - 2):
        a, b = trunk[i - 1], trunk[i]
        if a.name == b.name == "transport-belt" and a.direction == b.direction:
            straight.append(b)
    straight.sort(key=lambda b: (b.x - start[0]) ** 2 + (b.y - start[1]) ** 2)
    for b in straight[:24]:
        tx, ty = math.floor(b.x), math.floor(b.y)
        dx, dy = step[b.direction]
        for sx, sy in ((tx - dy, ty + dx), (tx + dy, ty - dx)):   # the two tiles beside it
            if (sx, sy) in taken:
                continue
            path = route(start, (sx, sy), taken - {(sx, sy)}, bounds=bounds)
            if path is None:
                continue
            into = next(d for d, v in step.items() if v == (tx - sx, ty - sy))
            path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, into)
            return path
    return None


def _distributor(cx: int, cy: int) -> list[Placed]:
    """A two-column block's feeds, split at its head (the guide's distributor). Ore comes down at tile
    (cx-1, cy-5) into a splitter: one half drops to the west head's outer lane, the other runs east along
    row cy-3 (under the coal splitter) and down onto the east head's outer lane. Coal: _shared_coal."""
    out = [Placed("splitter", cx + 0.0, cy - 3.5, SOUTH),
           Placed("transport-belt", cx - 0.5, cy - 2.5, SOUTH), Placed("transport-belt", cx - 0.5, cy - 1.5, SOUTH),
           Placed("transport-belt", cx - 0.5, cy - 0.5, EAST)]
    for x in range(cx, cx + 3):
        out.append(Placed("transport-belt", x + 0.5, cy - 2.5, EAST))
    out.append(Placed("underground-belt", cx + 3.5, cy - 2.5, EAST, kind="input"))
    out.append(Placed("underground-belt", cx + 7.5, cy - 2.5, EAST, kind="output"))
    for x in range(cx + 8, cx + 11):
        out.append(Placed("transport-belt", x + 0.5, cy - 2.5, EAST))
    out += [Placed("transport-belt", cx + 11.5, cy - 2.5, SOUTH), Placed("transport-belt", cx + 11.5, cy - 1.5, SOUTH),
            Placed("transport-belt", cx + 11.5, cy - 0.5, WEST)]
    return out + _shared_coal(cx, cy)


def _coal_corridor(cx: int, cy: int) -> set[tuple[int, int]]:
    return {(cx + 5, cy - k) for k in range(4, 7)}


def _shared_coal(cx: int, cy: int) -> list[Placed]:
    """A two-column block's coal: the belt comes down at the block's middle (tile cx+5, cy-4) into a
    splitter; one half runs west and turns down onto the west head's inner lane, the other east onto the
    east head's. Rows cy-3 to cy-1, between the heads."""
    out = [Placed("splitter", cx + 6.0, cy - 2.5, SOUTH)]
    for x in range(cx + 2, cx + 6):
        out.append(Placed("transport-belt", x + 0.5, cy - 1.5, WEST))
    out += [Placed("transport-belt", cx + 1.5, cy - 1.5, SOUTH), Placed("transport-belt", cx + 1.5, cy - 0.5, WEST)]
    for x in range(cx + 6, cx + 9):
        out.append(Placed("transport-belt", x + 0.5, cy - 1.5, EAST))
    out += [Placed("transport-belt", cx + 9.5, cy - 1.5, SOUTH), Placed("transport-belt", cx + 9.5, cy - 0.5, EAST)]
    return out


def _fuel(column: Module, cx: int, cy: int) -> list[dict]:
    """Stone furnaces fuelled by hand, when no coal reaches their ore belt."""
    return [{"x": e.x + cx, "y": e.y + cy, "item": "coal", "slot": "fuel"}
            for e in column.entities if e.name.endswith("furnace")]
