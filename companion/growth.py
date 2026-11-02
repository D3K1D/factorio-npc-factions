"""Growing the base: each growth section adds the science the bus already makes, once more.

When the bus and its extensions are finished, the engineer keeps going, as players do: a growth
section goes below the last one with its own smelting, gears, circuits, science and labs. Its
plates come from plate modules, rows of furnaces beside the bus fed from outside: a new mining
field's ore belt comes down onto the row's head from one side and a coal belt from the other (the
smelting columns at the bus head are fed the same way), and the plates go onto the bus's existing
plate lanes. Everything else in the section also feeds existing lanes, so growing needs no new bus
columns; the section's labs at its end use what it makes.

Plate module (module-local tiles, the bus to the west):

    row 4      ore comes down onto the head (port at the east end)
    row 5      belt A, west: ore on one lane, coal on the other
    row 6      inserters into the furnaces, poles         rows 7-8  stone furnaces (pitch 3)
    row 6 east coal comes up onto the head
    row 9      long-handed inserters, over to row 11      row 11  plates, west, down to the port
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import blue_modules
from companion import modules as mods
from companion.blue_modules import FIRST_X, _output
from companion.modules import EAST, Module, NORTH, Placed, Port, SOUTH, WEST, _drop_overlaps
from companion.ratios import Book, solve
from companion.smelting import FURNACE_RATE, field_at, footprint, foreign_tiles, ore_tiles, outlets, plan_field, route

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore"}
MAX_GROWTH = 4            # sections; each adds the science the bus aims at once more
DRILL_RATE = 0.5
LANE_FURNACES = 24        # one ore lane (7.5/s) feeds 24 stone furnaces
COAL_PER_FURNACE = 0.09 / 4.0


TIERS = ("automation-science-pack", "logistic-science-pack", "chemical-science-pack")

BELT = 15.0               # items/s on a full yellow belt, the belt the bus is built of


def is_growth(goal: str) -> bool:
    return goal.startswith("growth-")


# Resource expansions: a full belt more of one plate, as players expand, not a few furnaces when short (the user,
# 2026-10-25). An expansion is plate modules beside the bus (24 stone furnaces each, one ore lane) enough to fill a
# belt, each with an ore field and a coal belt of its own (plan_supply), the plates onto the plate's bus lane, down
# to its end: what its machines below and the engineer's building take. Goals are numbered across all plates, so
# each expansion's tag ("-x<n>") is its own.

def is_expansion(goal: str) -> bool:
    return goal.startswith("expand-")


def expansion_name(plate: str, index: int, rate: float = BELT) -> str:
    """The goal of expansion `index`: `rate` items/s more of `plate` (in the name, so a replay is exact)."""
    return f"expand-{plate}-{index}-{rate:g}"


def parse_expansion(goal: str) -> tuple[str, int, float]:
    rest, _, rate = goal[len("expand-"):].rpartition("-")
    plate, _, index = rest.rpartition("-")
    return plate, int(index), float(rate)


def expansion_tag(goal: str) -> str:
    return f"-x{parse_expansion(goal)[1]}"


def expansion(goal: str) -> list[Module]:
    """The plate modules of resource expansion `goal`: enough for its rate (a full belt, or half of one)."""
    plate, index, rate = parse_expansion(goal)
    per = LANE_FURNACES * FURNACE_RATE["stone-furnace"]
    out = [plate_module(PLATES[plate], plate, per) for _ in range(max(1, math.ceil(rate / per - 1e-9)))]
    for k, m in enumerate(out, start=1):
        m.name = f"smelt-{plate}-x{index}" + (f"-{k}" if k > 1 else "")
    return out


def in_section(stage: str, tag: str) -> bool:
    """Whether a bus stage belongs to the extension section tagged `tag` ("-g1", "-x2")."""
    return stage.endswith(tag) or f"{tag}-" in stage


def goal_name(index: int, packs) -> str:
    """A growth goal names its section and the science it was planned for, so it never changes later."""
    return f"growth-{index}-{sum(1 for p in TIERS if p in set(packs))}"


def parse(goal: str) -> tuple[int, tuple[str, ...]]:
    _, index, tiers = goal.split("-")
    return int(index), TIERS[:int(tiers)]


def plate_module(ore: str, plate: str, rate: float, ratio: float = 1.0) -> Module:
    """Stone furnaces for `rate` plates/s, fed ore and coal from the far (east) edge; `ratio` ore a plate (a
    stone brick takes two stone)."""
    n = min(LANE_FURNACES, max(1, math.ceil(rate / FURNACE_RATE["stone-furnace"] - 1e-9)))
    m = Module(f"smelt-{plate}")
    end = FIRST_X + 3 * n
    head = end
    for x in range(FIRST_X, head + 2):
        m.entities.append(Placed("transport-belt", x + 0.5, 5.5, WEST))   # (head+1, 5): straight behind the head
    m.ports.append(Port(ore, "ext", head, 4, SOUTH, n * FURNACE_RATE["stone-furnace"] * ratio))
    m.ports.append(Port("coal", "ext", head, 6, NORTH, n * COAL_PER_FURNACE))
    for k in range(n):
        x0 = FIRST_X + 3 * k
        m.entities.append(Placed("stone-furnace", x0 + 1.0, 8.0, NORTH))                 # rows 7-8
        m.entities.append(Placed("inserter", x0 + 0.5, 6.5, NORTH))                      # belt A into it
        m.entities.append(Placed("long-handed-inserter", x0 + 0.5, 9.5, NORTH))          # out to row 11
        m.entities.append(Placed("small-electric-pole", x0 + 2.5, 6.5))
        m.entities.append(Placed("small-electric-pole", x0 + 2.5, 10.5))
    m.entities.append(Placed("small-electric-pole", FIRST_X - 0.5, 6.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))
    _output(m, end, plate, n * FURNACE_RATE["stone-furnace"])
    m.width, m.height = head + 2, 14
    m.makes = {plate: n * FURNACE_RATE["stone-furnace"]}
    return _drop_overlaps(m)


def iron_smelting(rate: float, book: Book | None = None) -> Module:
    """An iron plate module (for the module sandbox)."""
    return plate_module("iron-ore", "iron-plate", rate)


def section(index: int, science_per_minute: float, packs: tuple[str, ...], book: Book,
            machine: str = mods.ASSEMBLER) -> list[Module]:
    """The modules of growth section `index`: plates, intermediates, science and labs for the packs."""
    rate = science_per_minute / 60
    need = solve({p: rate for p in packs}, book, inputs=set(PLATES), allowed={machine})
    out: list[Module] = []
    for plate, ore in PLATES.items():
        r = need.inputs.get(plate, 0.0)
        if r > 0:
            out.append(plate_module(ore, plate, r * 1.1))
    built = []
    if "logistic-science-pack" in packs:
        built += [mods.gears(rate * 1.5, book, machine=machine), mods.green_circuits(rate, book, machine=machine)]
    if "chemical-science-pack" in packs:
        built += [mods.gears(rate, book, machine=machine), mods.green_circuits(3 * rate, book, machine=machine),
                  blue_modules.steel(rate, book), blue_modules.red_circuits(1.5 * rate, book, machine),
                  blue_modules.engines(rate, book, machine)]
    if "automation-science-pack" in packs:
        built.append(mods.red_science(rate, book, machine=machine))
    if "logistic-science-pack" in packs:
        built.append(mods.green_science(rate, book, machine=machine))
    if "chemical-science-pack" in packs:
        built.append(blue_modules.blue_science(rate, book, machine))
    built.append(mods.labs(max(2, math.ceil(science_per_minute * 30 / 60)), packs))
    names: dict[str, int] = {}
    for m in out + built:
        base = m.name
        names[base] = names.get(base, 0) + 1
        m.name = f"{base}-g{index}" + (f"-{names[base]}" if names[base] > 1 else "")
    return out + built


def plan_supply(dump: Mapping[str, Any], bus, stages: list, notes: list[str], blocked: set, taken: set,
                index: int, Stage, coal_line, tag: str | None = None, label: str | None = None,
                found: dict | None = None) -> None:
    """Ore fields and coal belts for the plate modules of growth section `index` (or the extension section
    tagged `tag`, named `label` in the notes), each its own stage, built before the section's first module.
    `taken`: tiles new buildings may not use (incl. ore and the bus area); belts may cross the bus area but
    not buildings."""
    tag = tag or f"-g{index}"
    label = label or f"growth {index}"
    # The bus is planned again for every stage: a field once placed keeps its spot (in `found`, the bus's sites: its
    # own drills stand there now and would push a new search elsewhere), and a feed whose belt stands at its
    # module's entry is built, not routed again (as the smelting blocks at the bus head keep theirs).
    own_belts = own_belts_of(dump)
    mine = [s for s in stages if s.name.startswith("bus-") and in_section(s.name, tag)]
    if not mine:
        return
    first = stages.index(mine[0])
    entities = [e for s in stages for e in s.entities]
    built = blocked | footprint(entities) | outlets(entities)
    out = []
    named = re.compile(r"smelt-(.+?)" + re.escape(tag) + r"(-\d+)?")
    for placed in bus.attached:
        m = named.fullmatch(placed.module.name)
        if not m:
            continue
        ports = {p.item: p for p in placed.module.ports if p.kind == "ext"}
        plate, suffix = m.group(1), m.group(2) or ""
        ore = PLATES.get(plate)
        if ore not in ports or "coal" not in ports:
            continue
        ore_at = (placed.x + ports[ore].x, placed.y + ports[ore].y)
        coal_at = (placed.x + ports["coal"].x, placed.y + ports["coal"].y)
        feeds, ore_only, why = plan_feeds(dump, ore, ore_at, coal_at, ports[ore].rate, ports["coal"].rate, built,
                                          taken, blocked, coal_line, found=found, key=f"field-{plate}{tag}{suffix}",
                                          own_belts=own_belts)
        if feeds is None:
            notes.append(f"{label}: {why}")
            if ore_only is None:
                continue
            feeds = ore_only   # (ore without its coal belt: as growth sections always had it)
        built = feeds[1]
        out.append(Stage(f"supply-{plate}{tag}{suffix}", feeds[0]))
    stages[first:first] = out


def own_belts_of(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """Tiles under the engineer's own belts (a feed whose belt stands at its module's entry is built)."""
    return {(math.floor(e["x"]), math.floor(e["y"])) for e in dump.get("entities", [])
            if e.get("f") == dump.get("force") and e.get("t") in ("transport-belt", "underground-belt")}


def plan_feeds(dump: Mapping[str, Any], ore: str, ore_at: tuple[int, int], coal_at: tuple[int, int], ore_rate: float,
               coal_rate: float, built: set, taken: set, blocked: set, coal_line, *, found: dict | None = None,
               key: str | None = None, own_belts: set | None = None, patch: set | None = None):
    """A plate module's feeds: an ore field (on `patch`, else any of its ore) and its belt down onto `ore_at`, and a
    coal belt up onto `coal_at`. Ore first, then coal; where coal finds no way in past what the ore took (an ore
    field laid hard against a module walled its coal entry in, 2026-10-25), coal first, then ore. A field once
    placed keeps its spot (`found[key]`: the bus is planned again for every stage, and its own drills would push a
    new search elsewhere), and a feed whose belt stands at its entry is built, not routed again. Returns (feeds,
    ore_only, why): feeds (entities, tiles now used) when both fit, else None and the ore feed alone if it did."""
    own_belts = own_belts_of(dump) if own_belts is None else own_belts
    drills = max(2, math.ceil(ore_rate / DRILL_RATE))

    def ore_feed(here):
        fld = None
        if found is not None and key and found.get(key):
            try:
                fx, fy, fn, fl = (int(v) for v in (str(found[key]).split(",") + ["1"])[:4])
                fld = field_at(ore, fx, fy, fn, lines=fl)
            except ValueError:
                fld = None
        if fld is None:
            tiles = ore_tiles(dump["resources"], ore) if patch is None else patch
            # Drills stand on their own ore, not on buildings, nor on what this plan has laid there already: a coal
            # belt routed first across the ore had the field laid over it, and the game dropped the belts under the
            # drills, so the furnaces never got coal (run 45, 2026-10-26).
            room = ((taken | here) - tiles) | (here & tiles)
            other = foreign_tiles(dump["resources"], ore)
            fld = plan_field(tiles, room, drills, ore, foreign=other, near=ore_at) or \
                plan_field(tiles, room, drills, ore, min_ore=6, foreign=other, near=ore_at)
        if fld is None:
            return None, f"no room for {drills} drills on {ore}"
        if found is not None and key:
            found[key] = f"{fld.belt_x},{fld.top},{len(fld.drills)},{fld.lines}"
        field_entities = fld.entities()
        here = here | footprint(field_entities)
        if ore_at in own_belts:   # its ore belt stands: built, kept
            return (field_entities, here), ""
        # (the coal belt's way in stays clear of an ore belt laid before it)
        way_in = {(coal_at[0] + d * k, coal_at[1]) for d in (-1, 1) for k in (1, 2, 3)}
        path = route(fld.exit, ore_at, (here | way_in) - {ore_at}, bounds=tuple(dump["area"]), limit=4000)
        if path is None:
            return None, f"no belt from the {ore} field to its furnaces"
        path[-1] = Placed("transport-belt", path[-1].x, path[-1].y, SOUTH)   # down onto the head
        return (field_entities + path, here | footprint(path) | outlets(path)), ""

    def coal_feed(here):
        if coal_at in own_belts:   # its coal belt stands: built, kept
            return ([], here), ""
        coal = coal_line(coal_at, coal_rate, "coal", here - blocked, NORTH)
        if not coal:
            return None, "no coal belt for its furnaces"
        return (coal, here | footprint(coal) | outlets(coal)), ""

    start = built | {ore_at, coal_at}
    ore_only, why = None, ""
    for one, two in ((ore_feed, coal_feed), (coal_feed, ore_feed)):
        a, why = one(start)
        if a is None:
            continue
        b, why = two(a[1])
        if b is None:
            ore_only = ore_only or (a if one is ore_feed else None)
            continue
        return (a[0] + b[0], b[1]), None, ""
    return None, ore_only, why
