"""The mall: a bus module that makes buildings into chests, for the engineer to build from.

Players build one around late green science: a row of assemblers along two belts,
each filling a chest the engineer takes from instead of hand crafting. Later the
chests become logistic chests for robots.

    rows 0-3   bus inputs merge: copper and circuits onto belt B, iron and gears onto belt A
    row 4      belt B (circuits, copper), reached by long-handed inserters
    row 5      belt A (iron, gears)
    row 6      input inserters (A: plain, B: long-handed), poles in the gaps
    rows 7-9   assemblers, one tile apart; the gap inserter on row 8 feeds a neighbour
    row 10     output inserters, poles
    row 11     chests, limited to a few slots so the mall stops when stocked

Each belt gets its two items from a T-shaped head: the belt starts with nothing
behind it and one input belt on each side, so each item fills one lane. Chained
recipes take their belt or inserter ingredient straight from the neighbouring
assembler (a splitter and an underground belt either side of the belt assembler,
fast and long-handed inserters either side of the inserter assembler). Only
recipes the engineer has unlocked are built; walls, grenades, poles and furnaces
need stone, coal or wood, which are not on the bus.

The mall comes in two parts, each a module of its own so the bus stays narrow:
logistics (belts and inserters) and machines (drills, assemblers, turrets, and
the bulk items). A part taps only the bus lanes its recipes use.
"""

from __future__ import annotations

from typing import Iterable

from companion.modules import EAST, Module, NORTH, Placed, Port, SOUTH, WEST, _belt, _drop_overlaps
from companion.ratios import Book

MACHINE = "assembling-machine-1"   # speed hardly matters: the chests fill and the mall stops
CHEST = "iron-chest"
CHEST_SLOTS = 2
BULK_SLOTS = {"pipe": 1, "iron-chest": 1, "firearm-magazine": 1}   # cheap, plentiful: one stack is plenty
FIRST_X = 6
BELT_B = {"electronic-circuit", "copper-plate"}
# Groups of neighbours: (west, centre, east). The centre feeds both sides through gap inserters.
# What the engineer builds most comes first; cheap bulk items last, so they cannot take the
# whole iron lane from the rest while their chests fill.
GROUPS = [
    ("splitter", "transport-belt", "underground-belt"),
    ("fast-inserter", "inserter", "long-handed-inserter"),
    ("electric-mining-drill", "assembling-machine-1", "gun-turret"),
    ("pipe", "iron-chest", "firearm-magazine"),
]
PARTS = {"logistics": GROUPS[:2], "machines": GROUPS[2:]}
CHAINED = {"splitter", "underground-belt", "fast-inserter", "long-handed-inserter"}


def recipes_for(unlocked: Iterable[str] | None, groups=GROUPS) -> list[str]:
    """Mall recipes in placement order, keeping chained recipes beside the assembler that feeds them."""
    have = None if unlocked is None else set(unlocked)
    out = []
    for west, centre, east in groups:
        chained_group = west in CHAINED or east in CHAINED
        if chained_group and have is not None and centre not in have:
            continue   # nothing to feed the chained recipes
        for recipe in (west, centre, east):
            if have is None or recipe in have:
                out.append(recipe)
    return out


def mall(book: Book, unlocked: Iterable[str] | None = None, part: str | None = None) -> Module:
    """The whole mall in one row, or one `part` of it ("logistics" or "machines")."""
    m = Module(f"mall-{part}" if part else "mall")
    recipes = recipes_for(unlocked, PARTS[part] if part else GROUPS)
    end = FIRST_X + 4 * len(recipes)
    used = {name for r in recipes for name, _ in book.recipes[r].ingredients}
    # Belt B: copper from the north port loops round to the head's east side, circuits come
    # straight in from the west; the head runs south and turns east along row 4.
    if "copper-plate" in used:
        for x in range(0, 4):
            _belt(m, x, 0, EAST)
        _belt(m, 4, 0, SOUTH); _belt(m, 4, 1, SOUTH); _belt(m, 4, 2, WEST); _belt(m, 3, 2, WEST)
        m.ports.append(Port("copper-plate", "in", 0, 0, EAST, 0.2))
    if "electronic-circuit" in used:
        _belt(m, 0, 2, EAST); _belt(m, 1, 2, EAST)
        m.ports.append(Port("electronic-circuit", "in", 0, 2, EAST, 0.2))
    if used & BELT_B:
        _belt(m, 2, 2, SOUTH); _belt(m, 2, 3, SOUTH)
        for x in range(2, end):
            _belt(m, x, 4, EAST)
    # Belt A: iron straight in from the west, gears loop round to the east side; the head runs
    # north and turns east along row 5.
    _belt(m, 0, 8, EAST); _belt(m, 1, 8, EAST)
    m.ports.append(Port("iron-plate", "in", 0, 8, EAST, 0.5))
    if "iron-gear-wheel" in used:
        for x in range(0, 4):
            _belt(m, x, 10, EAST)
        _belt(m, 4, 10, NORTH); _belt(m, 4, 9, NORTH); _belt(m, 4, 8, WEST); _belt(m, 3, 8, WEST)
        m.ports.append(Port("iron-gear-wheel", "in", 0, 10, EAST, 0.2))
    _belt(m, 2, 8, NORTH); _belt(m, 2, 7, NORTH); _belt(m, 2, 6, NORTH)
    for x in range(2, end):
        _belt(m, x, 5, EAST)
    position = {}
    for k, recipe in enumerate(recipes):
        x0 = FIRST_X + 4 * k
        position[recipe] = x0
        m.entities.append(Placed(MACHINE, x0 + 1.5, 8.5, NORTH, recipe))
        ingredients = {name for name, _ in book.recipes[recipe].ingredients}
        if ingredients - BELT_B - {"transport-belt", "inserter"}:
            m.entities.append(Placed("inserter", x0 + 0.5, 6.5, NORTH))               # belt A into the machine
        if ingredients & BELT_B:
            m.entities.append(Placed("long-handed-inserter", x0 + 1.5, 6.5, NORTH))  # belt B, two rows up
        m.entities.append(Placed("inserter", x0 + 1.5, 10.5, NORTH))                  # machine into its chest
        m.entities.append(Placed(CHEST, x0 + 1.5, 11.5, NORTH, bar=BULK_SLOTS.get(recipe, CHEST_SLOTS)))
        m.entities.append(Placed("small-electric-pole", x0 - 0.5, 6.5))
        m.entities.append(Placed("small-electric-pole", x0 - 0.5, 10.5))
    # Gap inserters: a chained recipe takes its belt or inserter ingredient from its neighbour.
    for recipe in recipes:
        if recipe not in CHAINED:
            continue
        x0 = position[recipe]
        source = next(n for n, _ in book.recipes[recipe].ingredients if n in ("transport-belt", "inserter"))
        if position.get(source) == x0 + 4:     # the feeder is east: pick east, drop west
            m.entities.append(Placed("inserter", x0 + 3.5, 8.5, EAST))
        elif position.get(source) == x0 - 4:   # the feeder is west: pick west, drop east
            m.entities.append(Placed("inserter", x0 - 0.5, 8.5, WEST))
    # The last assembler's east side and an anchor pole by the bus edge.
    m.entities.append(Placed("small-electric-pole", end - 0.5, 6.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))
    m.width, m.height = end, 12
    m.makes = {r: 0.0 for r in recipes}
    return _drop_overlaps(m)


# Mall extensions: a further mall module down the bus when the factory plan wants products the bus's malls
# do not make yet, or more of them. The goal's name says what it makes (each product by its place in
# GROUPS), so every later stage of the bus lays the extension out the same way.
FLAT = tuple(r for group in GROUPS for r in group)
CODES = "0123456789abcdefghijklmnopqrstuvwxyz"
# Extension 1 is the bus's own mall: a bus planned with it in its extensions builds its science and labs first and
# its mall's two parts after them (base_plan.modules_for: the user, 2026-10-26, a bus producing sooner). A bus
# planned before has its mall where its style put it (its saved `mall`); the factory plan's extensions, each a
# section of its own down the bus, are numbered after either.
OWN = 1


def extension_goal(index: int, recipes: Iterable[str]) -> str:
    want = set(recipes)
    for west, centre, east in GROUPS:   # a chained recipe is fed from the assembler beside it
        if (west in want and west in CHAINED) or (east in want and east in CHAINED):
            want.add(centre)
    return f"mall-{index}-" + "".join(CODES[k] for k, r in enumerate(FLAT) if r in want)


def is_extension(goal: str) -> bool:
    parts = str(goal).split("-")
    return (len(parts) == 3 and parts[0] == "mall" and parts[1].isdigit() and bool(parts[2])
            and all(c in CODES[:len(FLAT)] for c in parts[2]))


def extension_recipes(goal: str) -> list[str]:
    return [FLAT[CODES.index(c)] for c in str(goal).split("-")[2]] if is_extension(goal) else []


def extension(goal: str, book: Book) -> Module:
    m = mall(book, extension_recipes(goal))
    m.name = f"mall-{goal.split('-')[1]}"
    return m


def is_own(goal: str) -> bool:
    """Whether `goal` is the bus's own mall after its science (OWN)."""
    return is_extension(goal) and str(goal).split("-")[1] == str(OWN)
